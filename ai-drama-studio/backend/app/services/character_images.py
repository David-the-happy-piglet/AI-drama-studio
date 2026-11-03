from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ids import new_id
from ..models import Character, Episode, Script
from ..models.entities import (
    CharacterImageInput,
    CharacterImageJob,
    CharacterProviderBinding,
)
from ..providers.image.kling import KlingImageProvider
from ..providers.video.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
)
from ..providers.video.kling import KlingProvider, KlingRequestRejected
from ..schemas import CharacterReferencesUpdate
from .character_elements import (
    ACTIVE,
    BindingConflict,
    account_scope,
    save_references,
    validate_images,
)

PENDING = {"submitting", "submitted", "processing", "unknown"}
DEFAULT_NEGATIVE = "多人，拼图，多视图，文字，水印，面部畸形，多余五官，模糊，过度磨皮"
REFERENCE_PREFIX = "以 <<<image_1>>> 中的人物为唯一身份参考，保留其脸型、五官比例、肤色、年龄感和辨识特征。"
REFERENCE_PROMPT = "生成同一人物的正面头肩肖像，浅灰背景，柔和均匀棚光，中性表情，头肩完整入镜，保留参考图中的发型与服装。单人单幅，无拼图。"


def effective_prompt(prompt: str, negative: str, mode: str) -> str:
    if mode == "text":
        return prompt
    return (
        REFERENCE_PREFIX + "\n" + prompt + ("\n避免：" + negative if negative else "")
    )


class PortraitCreate(BaseModel):
    expected_version: int = Field(ge=1)
    request_token: UUID
    prompt: str = Field(min_length=1, max_length=2500)
    negative_prompt: str = Field(default=DEFAULT_NEGATIVE, max_length=2500)
    count: int = Field(default=4, ge=1, le=4)
    resolution: Literal["1k", "2k"] = "1k"
    aspect_ratio: Literal["3:4", "1:1", "2:3"] = "3:4"
    confirmed: Literal[True]
    mode: Literal["text", "reference"] = "text"
    reference_path: str | None = Field(default=None, max_length=2000)
    reference_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reference_confirmed: bool = False

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "reference":
            if (
                not self.reference_confirmed
                or not self.reference_path
                or not self.reference_sha256
            ):
                raise ValueError("请预览并明确确认向可灵发送当前母版图片")
            if (
                len(effective_prompt(self.prompt, self.negative_prompt, self.mode))
                > 2500
            ):
                raise ValueError(
                    "参考图模式的身份约束、生成要求与负向约束合计不能超过 2500 字"
                )
        elif self.reference_path or self.reference_sha256 or self.reference_confirmed:
            raise ValueError("纯文字模式不能携带图片或图片发送确认")
        return self

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("母版 prompt 不能为空")
        return value.strip()


class PortraitSelect(BaseModel):
    expected_version: int = Field(ge=1)
    candidate_index: int = Field(ge=0, le=3)
    confirmed: Literal[True]
    accept_stale: bool = False


def job_read(job: CharacterImageJob) -> dict:
    source = job.input_snapshot
    return {
        "id": job.id,
        "request_token": job.request_token,
        "character_id": job.character_id,
        "source_version": job.source_version,
        "provider": "kling",
        "model": job.model,
        "mode": source.mode if source else "text",
        "effective_prompt": source.effective_prompt if source else job.prompt,
        "reference_path": source.reference_path if source else None,
        "reference_sha256": source.reference_sha256 if source else None,
        "reference_snapshot_path": source.snapshot_path if source else None,
        "prompt": job.prompt,
        "negative_prompt": job.negative_prompt,
        "count": job.count,
        "resolution": job.resolution,
        "aspect_ratio": job.aspect_ratio,
        "task_id": job.task_id,
        "status": job.status,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "candidates": [
            {"index": c["index"], "path": c.get("path"), "error": c.get("error")}
            for c in job.candidates
        ],
    }


def studio_context(db: Session, character: Character, provider: KlingProvider) -> dict:
    jobs = list(
        db.scalars(
            select(CharacterImageJob)
            .where(CharacterImageJob.character_id == character.id)
            .order_by(CharacterImageJob.created_at.desc())
            .limit(50)
        ).all()
    )
    scripts = list(
        db.scalars(
            select(Script)
            .where(Script.project_id == character.project_id)
            .order_by(Script.updated_at.desc())
        ).all()
    )
    current_ids = set(
        db.scalars(
            select(Episode.script_id).where(Episode.project_id == character.project_id)
        ).all()
    )
    scripts.sort(key=lambda script: script.id not in current_ids)
    prompt, source = "", "根据角色资料整理"
    for script in scripts:
        content = script.structured_content
        preproduction = (
            content.get("preproduction") if isinstance(content, dict) else None
        )
        items = (
            preproduction.get("character_prompts")
            if isinstance(preproduction, dict)
            else None
        )
        for item in items if isinstance(items, list) else []:
            if (
                isinstance(item, dict)
                and item.get("character_id") == character.id
                and isinstance(item.get("portrait"), str)
                and item["portrait"].strip()
            ):
                prompt, source = item["portrait"], f"{script.title} · 母版 prompt"
                break
        if prompt:
            break
    if not prompt:
        features = "；".join(
            filter(
                None,
                [
                    character.name,
                    character.gender,
                    character.apparent_age,
                    character.ethnicity,
                    character.face_description,
                    character.hairstyle,
                    character.hair_color,
                    character.eye_characteristics,
                    character.height,
                    character.body_type,
                    "，".join(character.distinctive_features),
                    character.visual_style,
                ],
            )
        )
        prompt = f"单人角色母版静态肖像，正面平视，浅灰背景，均匀柔光，中性表情，头肩完整入镜。{features}。单幅，无文字，不使用名人脸。"
    capabilities = provider.capabilities()
    reference = {"path": character.master_reference_path, "sha256": None, "error": None}
    if character.master_reference_path:
        try:
            content = validate_images(
                provider.data_root,
                character.project_id,
                [character.master_reference_path],
            )[0]
            reference["sha256"] = hashlib.sha256(content).hexdigest()
        except (ValueError, OSError) as exc:
            reference["error"] = (
                str(exc)
                if isinstance(exc, ValueError)
                else "无法读取母版文件，请重新上传"
            )
    return {
        "prompt": prompt,
        "prompt_source": source,
        "negative_prompt": DEFAULT_NEGATIVE,
        "reference": reference,
        "reference_prompt": REFERENCE_PROMPT,
        "reference_prefix": REFERENCE_PREFIX,
        "character_version": character.version,
        "configured": capabilities.configured,
        "configuration_issues": capabilities.configuration_issues,
        "jobs": [job_read(j) for j in jobs],
    }


def get_job(db: Session, character: Character, job_id: str) -> CharacterImageJob:
    job = db.get(CharacterImageJob, job_id)
    if (
        not job
        or job.character_id != character.id
        or job.project_id != character.project_id
    ):
        raise ValueError("候选图任务不属于当前角色")
    return job


def submission_fields(payload: PortraitCreate) -> dict:
    return {
        "source_version": payload.expected_version,
        "prompt": payload.prompt,
        "negative_prompt": payload.negative_prompt,
        "count": payload.count,
        "resolution": payload.resolution,
        "aspect_ratio": payload.aspect_ratio,
    }


def response_fields(raw: dict, job: CharacterImageJob) -> dict:
    data = raw.get("data")
    if not isinstance(data, dict):
        return {
            "status": "unknown",
            "error_message": "可灵返回格式异常，请查询原任务，勿重复提交",
        }
    remote = data.get("task_status")
    status = {
        "submitted": "submitted",
        "processing": "processing",
        "succeed": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
    }.get(remote if isinstance(remote, str) else "", "unknown")
    fields = {"status": status, "error_message": None}
    if isinstance(data.get("task_id"), (str, int)) and not isinstance(
        data["task_id"], bool
    ):
        task_id = str(data["task_id"])
        if job.task_id and task_id != job.task_id:
            raise ProviderResponseError("可灵返回的任务 ID 不匹配，已保留原任务")
        fields["task_id"] = task_id
    if status == "failed":
        fields.update(
            active_slot=None,
            error_message=str(data.get("task_status_msg") or "可灵图片生成失败"),
        )
    elif status == "unknown":
        fields["error_message"] = "任务状态不确定，请查询原任务，不要重新生成"
    elif status == "succeeded":
        result = data.get("task_result") or {}
        images = result.get("images") if isinstance(result, dict) else None
        if (
            not isinstance(images, list)
            or not images
            or len(images) > job.count
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("url"), str)
                or not item["url"]
                for item in images
            )
        ):
            fields.update(
                status="unknown",
                error_message="任务完成但未返回有效图片列表，请查询原任务",
            )
        else:
            fields.update(
                active_slot=None,
                candidates=[
                    {"index": i, "url": item["url"], "path": None, "error": None}
                    for i, item in enumerate(images)
                ],
            )
    return fields


async def create_job(
    db: Session, character: Character, payload: PortraitCreate, provider: KlingProvider
) -> dict:
    fields = submission_fields(payload)
    existing = db.scalar(
        select(CharacterImageJob).where(
            CharacterImageJob.character_id == character.id,
            CharacterImageJob.request_token == str(payload.request_token),
        )
    )
    if existing:
        source = existing.input_snapshot
        if (source.mode if source else "text") != payload.mode or (
            payload.mode == "reference"
            and (
                source.reference_path != payload.reference_path
                or source.reference_sha256 != payload.reference_sha256
            )
        ):
            raise BindingConflict("此提交标识的生成模式或母版图片不一致，请重新确认")
        if existing.account_scope != account_scope(provider) or any(
            getattr(existing, k) != v for k, v in fields.items()
        ):
            raise BindingConflict("此提交标识已用于不同请求，请刷新后重新确认")
        return job_read(existing)
    if character.version != payload.expected_version:
        raise BindingConflict("角色已更新，请刷新后检查母版 prompt")
    if character.master_reference_path and "mode" not in payload.model_fields_set:
        raise BindingConflict(
            "此角色已有母版图片，请刷新新版界面并明确选择参考图或纯文字模式"
        )
    reference_content = None
    if payload.mode == "reference":
        if (
            not character.master_reference_path
            or payload.reference_path != character.master_reference_path
        ):
            raise BindingConflict("母版图片已变化或尚未保存，请刷新并重新确认")
        reference_content = validate_images(
            provider.data_root, character.project_id, [character.master_reference_path]
        )[0]
        if hashlib.sha256(reference_content).hexdigest() != payload.reference_sha256:
            raise BindingConflict("母版文件内容已变化，请刷新预览并重新确认")
    if not provider.capabilities().configured:
        raise ProviderConfigurationError(
            "; ".join(provider.capabilities().configuration_issues)
        )
    if db.scalar(
        select(CharacterImageJob.id).where(
            CharacterImageJob.character_id == character.id,
            CharacterImageJob.active_slot == 1,
        )
    ):
        raise BindingConflict(
            "此角色已有待完成或结果不确定的候选图任务，请先刷新原任务"
        )
    token = new_id("LOCK")
    job = CharacterImageJob(
        id=new_id("PORTRAIT"),
        character_id=character.id,
        project_id=character.project_id,
        request_token=str(payload.request_token),
        account_scope=account_scope(provider),
        model="kling-v3-omni" if payload.mode == "reference" else "kling-v3",
        status="submitting",
        active_slot=1,
        refresh_token=token,
        refresh_until=datetime.now(UTC) + timedelta(seconds=provider.timeout + 60),
        **fields,
    )
    source = CharacterImageInput(
        mode=payload.mode,
        effective_prompt=effective_prompt(
            payload.prompt, payload.negative_prompt, payload.mode
        ),
        reference_path=payload.reference_path,
        reference_sha256=payload.reference_sha256,
    )
    if reference_content is not None:
        # Immutable local evidence of exactly which bytes were approved and sent.
        root = (
            provider.data_root / "assets" / "projects" / character.project_id
        ).resolve()
        folder = (root / "character_candidates" / job.id).resolve()
        if not folder.is_relative_to(root):
            raise ValueError("母版快照路径无效")
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / (
            "input-master" + Path(payload.reference_path).suffix.lower()
        )
        with destination.open("xb") as stream:
            stream.write(reference_content)
        source.snapshot_path = destination.relative_to(
            provider.data_root.resolve()
        ).as_posix()
    job.input_snapshot = source
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BindingConflict(
            "已有提交正在处理，请刷新候选图历史，不要重复提交"
        ) from exc
    db.refresh(job)
    try:
        fields = response_fields(await KlingImageProvider(provider).create(job), job)
    except KlingRequestRejected as exc:
        fields = {"status": "failed", "active_slot": None, "error_message": str(exc)}
    except ProviderError:
        fields = {
            "status": "unknown",
            "error_message": "提交结果不确定，请刷新原任务。不会自动重新生成。",
        }
    db.execute(
        update(CharacterImageJob)
        .where(CharacterImageJob.id == job.id, CharacterImageJob.refresh_token == token)
        .values(**fields, refresh_token=None, refresh_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    return job_read(job)


async def refresh_job(
    db: Session, character: Character, job: CharacterImageJob, provider: KlingProvider
) -> dict:
    return await refresh_image_job(db, job, provider, CharacterImageJob, job_read)


async def refresh_image_job(
    db, job, provider, job_model, read_job, *, asset_kind="character_candidates"
) -> dict:
    if job.account_scope != account_scope(provider):
        raise BindingConflict("API Key 或区域与生成时不一致，请切回原配置后查询")
    if (
        job.status == "failed"
        or job.status == "succeeded"
        and job.candidates
        and all(c.get("path") for c in job.candidates)
    ):
        return read_job(job)
    now, token = datetime.now(UTC), new_id("LOCK")
    claimed = db.execute(
        update(job_model)
        .where(
            job_model.id == job.id,
            or_(
                job_model.refresh_until.is_(None),
                job_model.refresh_until < now,
            ),
        )
        .values(
            refresh_token=token,
            refresh_until=now + timedelta(seconds=provider.timeout * 5 + 60),
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        db.refresh(job)
        return read_job(job)
    db.commit()
    db.refresh(job)
    image_provider = KlingImageProvider(provider)
    try:
        if not job.candidates:
            fields = response_fields(await image_provider.query(job), job)
            db.execute(
                update(job_model)
                .where(
                    job_model.id == job.id,
                    job_model.refresh_token == token,
                )
                .values(**fields)
                .execution_options(synchronize_session=False)
            )
            db.commit()
            db.refresh(job)
        candidates = [dict(item) for item in job.candidates]
        for item in candidates:
            if item.get("path"):
                continue
            try:
                item["path"] = await image_provider.download(
                    item["url"],
                    job.project_id,
                    job.id,
                    item["index"],
                    **(
                        {"asset_kind": asset_kind}
                        if asset_kind != "character_candidates"
                        else {}
                    ),
                )
                item["error"] = None
            except (ProviderError, OSError, ValueError) as exc:
                item["error"] = (
                    str(exc)
                    if isinstance(exc, ProviderError)
                    else "本地保存失败，请检查素材目录及图片地址"
                )
        if candidates:
            status = (
                "succeeded"
                if all(item.get("path") for item in candidates)
                else "partial"
                if any(item.get("path") for item in candidates)
                else "download_failed"
            )
            fields = {
                "status": status,
                "candidates": candidates,
                "error_message": None
                if status == "succeeded"
                else "部分候选图尚未保存；刷新只重试下载，不会重新生成",
            }
        else:
            fields = {}
    except ProviderError as exc:
        fields = {"error_message": f"查询失败，原任务已保留：{exc}"}
    db.execute(
        update(job_model)
        .where(job_model.id == job.id, job_model.refresh_token == token)
        .values(**fields, refresh_token=None, refresh_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    return read_job(job)


def select_candidate(
    db: Session,
    character: Character,
    job: CharacterImageJob,
    payload: PortraitSelect,
    provider: KlingProvider,
) -> Character:
    if character.version != payload.expected_version:
        raise BindingConflict("角色已更新，请刷新后重新选择")
    if job.source_version != character.version and not payload.accept_stale:
        raise BindingConflict("候选图来自较早角色版本，请明确确认仍要采用")
    candidate = next(
        (item for item in job.candidates if item["index"] == payload.candidate_index),
        None,
    )
    if not candidate or not candidate.get("path"):
        raise ValueError("候选图尚未保存到本地，不能设为母版")
    if character.master_reference_path == candidate["path"]:
        return character
    binding = db.get(CharacterProviderBinding, (character.id, "kling"))
    if binding and binding.status in ACTIVE:
        raise BindingConflict("可灵主体创建尚未结束，请先确认该任务再更换母版")
    history = {
        "previous_master": character.master_reference_path,
        "previous_additional": character.reference_assets.additional_reference_paths
        if character.reference_assets
        else [],
        "previous_version": character.version,
        "selected_index": payload.candidate_index,
        "selected_at": datetime.now(UTC).isoformat(),
    }
    job.selection_history = [*job.selection_history, history]
    return save_references(
        db,
        character,
        CharacterReferencesUpdate(
            expected_version=character.version,
            master_reference_path=candidate["path"],
            additional_reference_paths=[],
        ),
        provider.data_root,
    )
