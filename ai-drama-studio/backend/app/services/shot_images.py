"""Durable, explicitly approved shot keyframes. No automatic video submission."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..ids import new_id
from ..models import Character, Outfit, Shot
from ..models.entities import SceneAsset, SceneAssetBinding, ShotImageJob, ShotKeyframe
from ..providers.image.kling import KlingImageProvider
from ..providers.video.base import ProviderConfigurationError, ProviderError
from ..providers.video.kling import KlingRequestRejected
from .character_elements import BindingConflict, account_scope, validate_images
from .character_images import refresh_image_job, response_fields

NEGATIVE = "拼图，多格分镜，水印，字幕，乱码，面部变形，多余手指，身份混淆"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class KeyframeCreate(BaseModel):
    expected_version: int = Field(ge=1)
    context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_token: UUID
    prompt: str = Field(min_length=1, max_length=2500)
    negative_prompt: str = Field(default=NEGATIVE, max_length=2500)
    count: int = Field(default=1, ge=1, le=4)
    resolution: Literal["1k", "2k"] = "1k"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    confirmed: Literal[True]
    references_confirmed: bool = False

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("首帧提示词不能为空")
        return value.strip()


class KeyframeSelect(BaseModel):
    expected_version: int = Field(ge=1)
    candidate_index: int = Field(ge=0, le=3)
    confirmed: Literal[True]


def source_context(db, shot, provider):
    project_id = shot.scene.episode.project_id
    references, errors, costumes = [], [], []
    seen = set()
    for item in shot.characters:
        if item["character_id"] in seen:
            continue
        seen.add(item["character_id"])
        character = db.get(Character, item["character_id"])
        if not character or character.project_id != project_id:
            errors.append("镜头角色不属于当前项目")
            continue
        reference = {
            "character_id": character.id,
            "name": character.name,
            "version": character.version,
            "path": character.master_reference_path,
            "sha256": None,
            "error": None,
        }
        try:
            if not character.master_reference_path:
                raise ValueError("请先到角色资产上传或选定母版图片")
            content = validate_images(
                provider.data_root, project_id, [character.master_reference_path]
            )[0]
            reference["sha256"] = hashlib.sha256(content).hexdigest()
        except (ValueError, OSError) as exc:
            reference["error"] = (
                str(exc) if isinstance(exc, ValueError) else "母版文件读取失败"
            )
            errors.append(f"{character.name}：{reference['error']}")
        references.append(reference)
        if item.get("outfit_id"):
            outfit = db.get(Outfit, item["outfit_id"])
            if not outfit or outfit.character_id != character.id:
                errors.append(f"{character.name}：镜头造型关联无效")
            else:
                costumes.append(
                    f"{character.name}穿着：{outfit.name}，{outfit.description}，{'，'.join(outfit.components)}，{'，'.join(outfit.color_palette)}。"
                )
    # Product cap is deliberately smaller than the API's ten-reference limit.
    if len(references) > 3:
        errors.append("首帧工作台当前最多参考 3 位角色，请先拆分镜头")
    identity = "\n".join(
        f"{ref['name']}的身份对应 <<<image_{i}>>>，保持脸型、五官、年龄与辨识特征；不要把参考图背景照搬进镜头。"
        for i, ref in enumerate(references, 1)
    )
    prefix = "\n".join(filter(None, [identity, *costumes]))
    binding = db.get(SceneAssetBinding, shot.scene_id)
    if binding and binding.asset_id:
        asset = db.get(SceneAsset, binding.asset_id)
        if not asset or asset.project_id != project_id:
            errors.append("场景母版无效或不属于当前项目，请重新关联")
        else:
            for index, path in enumerate(
                [asset.master_reference_path, *asset.additional_reference_paths]
            ):
                reference = {
                    "kind": "scene",
                    "scene_asset_id": asset.id,
                    "name": f"{asset.name} · {'主视角' if index == 0 else f'补充视角 {index}'}",
                    "version": asset.version,
                    "path": path,
                    "sha256": None,
                    "error": None,
                }
                try:
                    content = validate_images(provider.data_root, project_id, [path])[0]
                    reference["sha256"] = hashlib.sha256(content).hexdigest()
                except (ValueError, OSError) as exc:
                    reference["error"] = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "场景母版文件读取失败"
                    )
                    errors.append(f"{reference['name']}：{reference['error']}")
                references.append(reference)
                prefix += f"\n场景环境参考 <<<image_{len(references)}>>>：{reference['name']}。"
            prefix += (
                "\n以上场景图片描述同一个物理空间。保持建筑、门窗、家具与主要道具的相对位置、材质及照明设定；"
                "只按当前镜头改变机位、景别和人物动作，不重新设计房间。不复制环境参考图中的人物，不增添未指定人物。"
                + ("\n固定场景设定：" + asset.description if asset.description else "")
            )
    fingerprint = digest(
        {
            "shot": {
                key: getattr(shot, key)
                for key in (
                    "visual_description",
                    "generation_prompt",
                    "negative_prompt",
                    "characters",
                    "composition",
                    "camera_angle",
                    "shot_type",
                    "action",
                    "emotion",
                    "continuity_requirements",
                )
            },
            "scene": [shot.scene.location_description, shot.scene.time_of_day],
            "references": references,
            "costumes": costumes,
        }
    )
    prompt = (
        "单幅静态电影首帧，不是角色定妆照或分镜拼图。画面停在动作开始前，所有可读文字后期制作，屏幕使用干净占位面。\n"
        + shot.visual_description
    )
    if shot.composition:
        prompt += "\n构图：" + shot.composition
    return {
        "shot_version": shot.version,
        "context_hash": fingerprint,
        "prompt": prompt,
        "prompt_source": "当前镜头画面描述与构图（可编辑）",
        "negative_prompt": shot.negative_prompt or NEGATIVE,
        "reference_prefix": prefix,
        "references": references,
        "errors": errors,
        "model": "kling-v3-omni" if references else "kling-v3",
    }


def job_read(job):
    return {
        key: getattr(job, key)
        for key in (
            "id",
            "shot_id",
            "source_version",
            "context_hash",
            "request_token",
            "model",
            "prompt",
            "negative_prompt",
            "effective_prompt",
            "references_snapshot",
            "count",
            "resolution",
            "aspect_ratio",
            "task_id",
            "status",
            "error_message",
            "created_at",
        )
    } | {
        "candidates": [
            {"index": c["index"], "path": c.get("path"), "error": c.get("error")}
            for c in job.candidates
        ]
    }


def selected_read(db, shot, provider, context=None):
    selected = db.get(ShotKeyframe, shot.id)
    if not selected:
        return None
    context = context or source_context(db, shot, provider)
    error = None
    if (
        shot.version != selected.shot_version
        or selected.context_hash != context["context_hash"]
    ):
        error = "镜头、角色或场景母版已更新，旧首帧需重新生成或重新审阅"
    try:
        content = validate_images(
            provider.data_root, shot.scene.episode.project_id, [selected.path]
        )[0]
        if hashlib.sha256(content).hexdigest() != selected.sha256:
            error = "首帧文件内容已变化，请重新选择"
    except (ValueError, OSError):
        error = "首帧文件已丢失或损坏，请重新选择"
    return {
        key: getattr(selected, key)
        for key in (
            "job_id",
            "candidate_index",
            "selection_token",
            "path",
            "sha256",
            "shot_version",
        )
    } | {"usable": error is None, "error": error}


def studio_context(db, shot, provider):
    context = source_context(db, shot, provider)
    jobs = db.scalars(
        select(ShotImageJob)
        .where(ShotImageJob.shot_id == shot.id)
        .order_by(ShotImageJob.created_at.desc())
        .limit(50)
    ).all()
    capabilities = provider.capabilities()
    return context | {
        "configured": capabilities.configured,
        "configuration_issues": capabilities.configuration_issues,
        "jobs": [job_read(job) for job in jobs],
        "selected": selected_read(db, shot, provider, context),
    }


def get_job(db, shot, job_id):
    job = db.get(ShotImageJob, job_id)
    if (
        not job
        or job.shot_id != shot.id
        or job.project_id != shot.scene.episode.project_id
    ):
        raise ValueError("未找到当前镜头的首帧任务")
    return job


async def create_job(db, shot, payload, provider):
    from .storyboard_revision import production_editable

    production_editable(shot.scene.episode, shot)
    fields = {
        "source_version": payload.expected_version,
        "context_hash": payload.context_hash,
        "prompt": payload.prompt,
        "negative_prompt": payload.negative_prompt,
        "count": payload.count,
        "resolution": payload.resolution,
        "aspect_ratio": payload.aspect_ratio,
    }
    existing = db.scalar(
        select(ShotImageJob).where(
            ShotImageJob.shot_id == shot.id,
            ShotImageJob.request_token == str(payload.request_token),
        )
    )
    if existing:
        if existing.account_scope != account_scope(provider) or any(
            getattr(existing, key) != value for key, value in fields.items()
        ):
            raise BindingConflict("同一提交标识已用于不同请求，请刷新原任务")
        return job_read(existing)
    if shot.version != payload.expected_version:
        raise BindingConflict("镜头已修改，请刷新首帧工作台后重新确认")
    context = source_context(db, shot, provider)
    if context["errors"]:
        raise ValueError("；".join(context["errors"]))
    if context["context_hash"] != payload.context_hash:
        raise BindingConflict("镜头、造型或角色/场景母版已变化，请刷新预览并重新确认")
    if context["references"] and not payload.references_confirmed:
        raise ValueError("请明确确认把预览中的角色/场景母版图片发送给可灵")
    actual_prompt = payload.prompt
    if context["references"]:
        actual_prompt = (
            context["reference_prefix"]
            + "\n"
            + payload.prompt
            + ("\n避免：" + payload.negative_prompt if payload.negative_prompt else "")
        )
    if len(actual_prompt) > 2500:
        raise ValueError("首帧提示词、角色身份及负向约束合计不能超过 2500 字，请缩短")
    if not provider.capabilities().configured:
        raise ProviderConfigurationError(
            "；".join(provider.capabilities().configuration_issues)
        )
    if db.scalar(
        select(ShotImageJob.id).where(
            ShotImageJob.shot_id == shot.id, ShotImageJob.active_slot == 1
        )
    ):
        raise BindingConflict(
            "本镜头已有待完成或结果不确定的首帧任务，请查询原任务，勿重复生成"
        )
    token = new_id("LOCK")
    job = ShotImageJob(
        id=new_id("KEYFRAME"),
        shot_id=shot.id,
        project_id=shot.scene.episode.project_id,
        request_token=str(payload.request_token),
        account_scope=account_scope(provider),
        model=context["model"],
        effective_prompt=actual_prompt,
        status="submitting",
        active_slot=1,
        refresh_token=token,
        refresh_until=datetime.now(UTC) + timedelta(seconds=provider.timeout + 60),
        **fields,
    )
    references = []
    for index, reference in enumerate(context["references"], 1):
        content = validate_images(
            provider.data_root, job.project_id, [reference["path"]]
        )[0]
        if hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise BindingConflict("母版内容变化，请重新预览")
        folder = (
            provider.data_root
            / "assets"
            / "projects"
            / job.project_id
            / "shot_keyframes"
            / job.id
        ).resolve()
        if not folder.is_relative_to(
            (provider.data_root / "assets" / "projects").resolve()
        ):
            raise ValueError("首帧快照目录无效")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"input-{index}{Path(reference['path']).suffix.lower()}"
        with target.open("xb") as stream:
            stream.write(content)
        references.append(
            reference
            | {
                "snapshot_path": target.relative_to(
                    provider.data_root.resolve()
                ).as_posix()
            }
        )
    job.references_snapshot = references
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BindingConflict("已有提交正在处理，请刷新历史，不要重复生成") from exc
    try:
        result = response_fields(
            await KlingImageProvider(provider).create_keyframe(job), job
        )
    except KlingRequestRejected as exc:
        result = {"status": "failed", "active_slot": None, "error_message": str(exc)}
    except (ProviderError, OSError, ValueError):
        result = {
            "status": "unknown",
            "error_message": "提交结果不确定，请查询原任务，不会自动重新生成",
        }
    db.execute(
        update(ShotImageJob)
        .where(ShotImageJob.id == job.id, ShotImageJob.refresh_token == token)
        .values(**result, refresh_token=None, refresh_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    return job_read(job)


async def refresh_job(db, shot, job, provider):
    return await refresh_image_job(
        db, job, provider, ShotImageJob, job_read, asset_kind="shot_keyframes"
    )


def select_candidate(db, shot, job, payload, provider):
    from .storyboard_revision import production_editable

    production_editable(shot.scene.episode, shot)
    if shot.version != payload.expected_version:
        raise BindingConflict("镜头版本已变化，请刷新后重新选择")
    context = source_context(db, shot, provider)
    if context["errors"] or context["context_hash"] != job.context_hash:
        raise BindingConflict("候选图依据的镜头或角色母版已变化，请按最新内容生成首帧")
    candidate = next(
        (c for c in job.candidates if c["index"] == payload.candidate_index), None
    )
    if not candidate or not candidate.get("path"):
        raise ValueError("图片尚未保存到本地，请先刷新任务完成下载")
    content = validate_images(provider.data_root, job.project_id, [candidate["path"]])[
        0
    ]
    claimed = db.execute(
        update(Shot)
        .where(Shot.id == shot.id, Shot.version == payload.expected_version)
        .values(version=Shot.version + 1)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise BindingConflict("镜头版本冲突，请刷新")
    selected = db.get(ShotKeyframe, shot.id) or ShotKeyframe(shot_id=shot.id)
    selected.job_id, selected.candidate_index = job.id, payload.candidate_index
    selected.selection_token = new_id("FRAME")
    selected.shot_version = payload.expected_version + 1
    selected.context_hash = context["context_hash"]
    selected.path, selected.sha256 = (
        candidate["path"],
        hashlib.sha256(content).hexdigest(),
    )
    db.add(selected)
    db.commit()
    db.refresh(shot)
    return selected_read(db, shot, provider)


def resolve_for_video(db, shot, provider, token):
    selected = selected_read(db, shot, provider)
    if not selected or selected["selection_token"] != token or not selected["usable"]:
        raise ValueError("已选首帧已变化或失效，请刷新并重新审阅首帧")
    if shot.generation_mode not in {"image_to_video", "first_last_frame"}:
        raise ValueError("当前镜头不是首帧或首尾帧模式，不能附带已选首帧")
    return selected
