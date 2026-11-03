"""Durable, deliberately sequential paid submissions. Never retry ambiguous writes."""

import asyncio
import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from ..ids import new_id
from ..models import Episode, Generation, Shot
from ..models.entities import ProductionBatch, ProductionBatchItem, ShotImageJob
from ..providers.video.base import ProviderError
from ..schemas import GenerationCreate
from . import shot_images
from .character_elements import preview_bindings
from .generations import GenerationService
from .storyboard_revision import production_editable

ACTIVE = ("queued", "submitting", "waiting", "waiting_keyframe", "submitted")
CLAIMABLE = ("queued", "waiting", "waiting_keyframe", "submitted")
LEASE_DURATION = timedelta(minutes=30)
logger = logging.getLogger(__name__)


class BatchRequest(BaseModel):
    kind: Literal["keyframes", "videos", "workflow"]
    shot_ids: list[str] = Field(min_length=1, max_length=200)
    model: Literal["kling-3.0", "kling-3.0-turbo", "kling-3.0-omni"] = "kling-3.0"
    resolution: Literal["720p", "1080p", "4k"] = "720p"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    audio: Literal["native", "off"] = "native"
    image_count: int = Field(default=1, ge=1, le=4)
    image_resolution: Literal["1k", "2k"] = "1k"
    reuse_selected: bool = True
    use_character_bindings: bool = False
    fallback_provider: Literal["none", "seedance"] = "none"
    kling_retries: int = Field(default=3, ge=0, le=3)
    sequential: bool = True
    voice_style: str = "女主使用清冷、忧郁、轻柔克制的年轻女声，普通话自然清晰，略带气声；回忆中按台词需要自然明快，维持同一声线。男主使用自然随意的年轻男声。"
    request_token: UUID
    confirmed: bool = False
    preview_hash: str = ""


def _batch_item_token(batch_token: UUID, shot_id: str, stage: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"ai-drama-studio:production-batch:{batch_token}:{shot_id}:{stage}",
    )


def _provider_prompt_catalog(data_root: Path, project_id: str) -> dict:
    """Optional project source notes; the approved shot remains authoritative for frames."""
    projects_root = (data_root / "assets" / "projects").resolve()
    path = (projects_root / project_id / "storyboard_details.json").resolve()
    if not path.is_relative_to(projects_root):
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value.get("shots", {}) if isinstance(value, dict) else {}


def audio_prompt(prompt: str, fields: dict, options: dict) -> str:
    if options.get("audio") != "native" or not fields:
        return prompt
    lines = ["原生音频：根据画面生成同步环境声。" + options.get("voice_style", "")]
    dialogue = [(key, value) for key, value in fields.items() if key.startswith("对白") and value not in {"无", "无。"}]
    if dialogue:
        lines.extend(f"{key}：{value}" for key, value in dialogue)
        lines.append("只说上述台词；画外音不让画面人物对口型。严格保留指定停顿与沉默，不添加其他对白。")
    else:
        lines.append("本镜没有台词，不添加人声或旁白。")
    if fields.get("环境音"):
        lines.append("环境音：" + fields["环境音"])
    return prompt + "\n" + "\n".join(lines)


def _source_frame(db, catalog, source_id, provider):
    shot_id = next((key for key, value in catalog.items() if value.get("source_shot_id") == source_id), None)
    source_shot = db.get(Shot, shot_id) if shot_id else None
    selected = shot_images.selected_read(db, source_shot, provider) if source_shot else None
    if not selected or not selected["usable"]:
        raise ValueError(f"缺少 {source_id} 已审核首帧")
    return selected


def preview(db, episode_id, payload, registry):
    episode = db.get(Episode, episode_id)
    if not episode:
        raise HTTPException(404, "未找到分集")
    if len(set(payload.shot_ids)) != len(payload.shot_ids):
        raise HTTPException(422, "镜头不能重复选择")
    if payload.kind != "keyframes" and len(payload.shot_ids) > 3:
        raise HTTPException(422, "小批量审核模式：每批最多 3 个视频镜头。请先审核本批成片，再手动提交下一批。")
    provider = registry.get("kling")
    prompt_catalog = _provider_prompt_catalog(Path(provider.data_root), episode.project_id)
    if (
        payload.fallback_provider == "seedance"
        and payload.kind != "keyframes"
        and not registry.get("seedance").capabilities().configured
    ):
        raise HTTPException(422, "Seedance 2.0 API 未配置，无法启用额度用尽后切换")
    rows = []
    for shot_id in payload.shot_ids:
        shot = db.get(Shot, shot_id)
        if not shot or shot.scene.episode_id != episode_id:
            raise HTTPException(422, "镜头不属于当前分集")
        row = {
            "shot_id": shot.id,
            "label": (
                f"{prompt_catalog[shot.id]['source_shot_id']} · SC {shot.scene.order_index} / SH {shot.order_index}"
                if shot.id in prompt_catalog else f"SC {shot.scene.order_index} / SH {shot.order_index}"
            ),
            "version": shot.version,
            "error": None,
            "skip": False,
        }
        try:
            production_editable(episode, shot)
            if not provider.capabilities().configured:
                raise ValueError("可灵 API 未配置")
            row["account_scope"] = shot_images.account_scope(provider)
            if db.scalar(
                select(ProductionBatchItem.id).where(
                    ProductionBatchItem.active_slot.in_(
                        [f"keyframes:{shot.id}", f"videos:{shot.id}"]
                        if payload.kind == "workflow"
                        else [f"{payload.kind}:{shot.id}"]
                    )
                )
            ):
                raise ValueError("镜头已有进行中的同类批量任务")
            context = shot_images.source_context(db, shot, provider)
            needs_image_stage = payload.kind == "keyframes" or (
                payload.kind == "workflow" and shot.generation_mode == "image_to_video"
            )
            if context["errors"] and needs_image_stage:
                raise ValueError("；".join(context["errors"]))
            selected = shot_images.selected_read(db, shot, provider, context)
            row["context_hash"] = context["context_hash"]
            if needs_image_stage:
                row["keyframe_request_token"] = str(
                    _batch_item_token(payload.request_token, shot.id, "keyframe")
                )
                row.update(
                    prompt=context["prompt"],
                    negative_prompt=context["negative_prompt"],
                    references=context["references"],
                )
                row["skip"] = bool(
                    payload.reuse_selected and selected and selected["usable"]
                )
                if row["skip"]:
                    row["selected_token"] = selected["selection_token"]
                elif db.scalar(
                    select(ShotImageJob.id).where(
                        ShotImageJob.shot_id == shot.id, ShotImageJob.active_slot == 1
                    )
                ):
                    raise ValueError(
                        "镜头已有待完成或结果不确定的首帧任务，请查询原任务"
                    )
                actual_prompt = context["prompt"]
                if context["references"]:
                    actual_prompt = context["reference_prefix"] + "\n" + actual_prompt
                    if context["negative_prompt"]:
                        actual_prompt += "\n避免：" + context["negative_prompt"]
                if not row["skip"] and len(actual_prompt) > 2500:
                    raise ValueError("首帧提示词超过上限，请先逐镜头精简")
            if payload.kind == "workflow":
                row["needs_keyframe"] = needs_image_stage and not row["skip"]
                row["reuse_keyframe"] = needs_image_stage and row["skip"]
                row["skip"] = False
            if payload.kind != "keyframes":
                if shot.generation_mode == "first_last_frame" and prompt_catalog.get(shot.id, {}).get("source_shot_id") != "S42":
                    raise ValueError("此首尾帧镜头尚未配置尾帧来源，请单独提交")
                if shot.generation_mode not in ("image_to_video", "text_to_video", "first_last_frame"):
                    raise ValueError(
                        "批量视频支持首帧生视频/文生视频；首尾帧及多模态镜头请单独提交"
                    )
                if not 0 < shot.duration <= 15:
                    raise ValueError("镜头时长须大于 0 且不超过 15 秒")
                if payload.model == "kling-3.0-turbo" and (
                    payload.resolution == "4k" or payload.audio == "off"
                ):
                    raise ValueError("Turbo 不支持 4K 或关闭原生声音")
                if (
                    payload.kind != "workflow"
                    and shot.generation_mode in {"image_to_video", "first_last_frame"}
                    and not (selected and selected["usable"])
                ):
                    raise ValueError("缺少可用的已选首帧，请先完成批量首帧")
                bindings = (
                    preview_bindings(db, shot, provider, payload.model)
                    if payload.use_character_bindings else {"elements": [], "errors": []}
                )
                if bindings["errors"]:
                    raise ValueError("；".join(bindings["errors"]))
                row["bindings"] = bindings["elements"]
                source_fields = prompt_catalog.get(shot.id, {}).get("fields", {})
                kling_prompt = audio_prompt(source_fields.get("可灵提示词") or shot.generation_prompt or shot.visual_description, source_fields, payload.model_dump())
                row["seedance_prompt"] = audio_prompt(source_fields.get("Seedance提示词") or shot.generation_prompt or shot.visual_description, source_fields, payload.model_dump())
                source_id = prompt_catalog.get(shot.id, {}).get("source_shot_id")
                row["source_shot_id"] = source_id
                last_frame = None
                if shot.generation_mode == "first_last_frame":
                    if source_id != "S42":
                        raise ValueError("本首尾帧镜头尚未配置尾帧来源")
                    last_frame = _source_frame(db, prompt_catalog, "S39", provider)["path"]
                if source_id == "S52":
                    row["continuation_shot_id"] = next(key for key, value in prompt_catalog.items() if value.get("source_shot_id") == "S22")
                effective_prompt = kling_prompt
                if bindings["elements"]:
                    effective_prompt += (
                        "\n角色身份对应（保持参考主体的身份特征）：\n"
                        + "\n".join(
                            f"{item['name']}（{item['character_id']}）对应 @{item['id']}。"
                            for item in bindings["elements"]
                        )
                    )
                if shot.negative_prompt:
                    effective_prompt += "\n避免：" + shot.negative_prompt
                if len(effective_prompt) > 3072:
                    raise ValueError(
                        "视频提示词与角色、负向约束合计超过 3072 字，请先精简"
                    )
                row["video"] = GenerationCreate(
                    request_token=_batch_item_token(
                        payload.request_token, shot.id, "video"
                    ),
                    confirmed=True,
                    provider="kling",
                    model=payload.model,
                    prompt=kling_prompt,
                    negative_prompt=shot.negative_prompt or "",
                    duration=max(3, math.ceil(shot.duration)),
                    resolution=payload.resolution,
                    aspect_ratio=payload.aspect_ratio,
                    keyframe_selection_token=selected["selection_token"]
                    if shot.generation_mode in {"image_to_video", "first_last_frame"}
                    and selected
                    and selected["usable"]
                    and not row.get("needs_keyframe")
                    else None,
                    last_frame=last_frame,
                    api_parameters={"audio": payload.audio},
                    use_character_bindings=payload.use_character_bindings,
                ).model_dump(mode="json")
        except (ValueError, OSError) as exc:
            row["error"] = (
                str(exc) if isinstance(exc, ValueError) else "参考素材读取失败"
            )
        rows.append(row)
    return {
        "rows": rows,
        "preview_hash": shot_images.digest(
            {
                "rows": rows,
                "options": payload.model_dump(
                    mode="json", exclude={"confirmed", "preview_hash", "request_token"}
                ),
            }
        ),
    }


def read_batch(db, batch):
    items = db.scalars(
        select(ProductionBatchItem)
        .where(ProductionBatchItem.batch_id == batch.id)
        .order_by(ProductionBatchItem.order_index)
    ).all()
    providers = {}
    for item in items:
        generation = db.get(Generation, item.result_id) if item.result_id else None
        providers[item.id] = (
            generation.provider if generation else
            "seedance" if item.payload.get("stage", batch.kind) == "videos"
            and batch.options.get("fallback_active") and item.status in ACTIVE else "kling"
        )
    return {
        "id": batch.id,
        "episode_id": batch.episode_id,
        "kind": batch.kind,
        "fallback_provider": batch.options.get("fallback_provider", "none"),
        "fallback_active": bool(batch.options.get("fallback_active")),
        "created_at": batch.created_at,
        "active": any(i.status in ACTIVE for i in items),
        "items": [
            {
                "id": i.id,
                "shot_id": i.shot_id,
                "label": i.payload.get("label", i.shot_id),
                "status": i.status,
                "stage": i.payload.get("stage", batch.kind),
                "provider": providers[i.id],
                "result_id": i.result_id,
                "error": i.error,
            }
            for i in items
        ],
    }


def create_batch(db, episode_id, payload, registry):
    if not payload.confirmed:
        raise HTTPException(
            422, "请确认任务数量、母版传输、自动选第一张及可灵 API 费用"
        )
    options = payload.model_dump(mode="json", exclude={"confirmed", "preview_hash"})
    existing = db.scalar(
        select(ProductionBatch).where(
            ProductionBatch.request_token == str(payload.request_token)
        )
    )
    if existing:
        saved_options = {key: value for key, value in existing.options.items() if key not in {"fallback_active", "fallback_activated_at", "fallback_trigger_shot_id"}}
        if existing.episode_id != episode_id or saved_options != options:
            raise HTTPException(409, "此提交标识已用于另一批任务")
        return read_batch(db, existing)
    plan = preview(db, episode_id, payload, registry)
    if plan["preview_hash"] != payload.preview_hash:
        raise HTTPException(409, "镜头或参考资产已变化，请重新预检并确认")
    if not any(not r["error"] and not r["skip"] for r in plan["rows"]):
        raise HTTPException(422, "没有可提交的镜头")
    batch = ProductionBatch(
        id=new_id("BATCH"),
        episode_id=episode_id,
        kind=payload.kind,
        request_token=str(payload.request_token),
        options=options,
    )
    db.add(batch)
    db.flush()
    for index, row in enumerate(plan["rows"]):
        status = "failed" if row["error"] else "skipped" if row["skip"] else "queued"
        if payload.kind == "workflow":
            dependency_id = None
            if status == "queued" and row.get("needs_keyframe"):
                dependency_id = new_id("BI")
                db.add(
                    ProductionBatchItem(
                        id=dependency_id,
                        batch_id=batch.id,
                        shot_id=row["shot_id"],
                        order_index=index * 2,
                        status="queued",
                        active_slot=f"keyframes:{row['shot_id']}",
                        payload={**row, "stage": "keyframes"},
                    )
                )
            db.add(
                ProductionBatchItem(
                    id=new_id("BI"),
                    batch_id=batch.id,
                    shot_id=row["shot_id"],
                    order_index=index * 2 + 1,
                    status="waiting_keyframe" if dependency_id else status,
                    active_slot=f"videos:{row['shot_id']}"
                    if status == "queued"
                    else None,
                    payload={**row, "stage": "videos", "dependency_id": dependency_id},
                    error=row["error"],
                )
            )
            continue
        db.add(
            ProductionBatchItem(
                id=new_id("BI"),
                batch_id=batch.id,
                shot_id=row["shot_id"],
                order_index=index,
                status=status,
                active_slot=f"{payload.kind}:{row['shot_id']}"
                if status == "queued"
                else None,
                payload=row,
                error=row["error"],
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "已有重叠任务或重复提交，请刷新批量历史，不要重新扣费提交"
        ) from exc
    return read_batch(db, batch)


def finish(db, item, status, error=None):
    item.status, item.error, item.active_slot = status, error, None
    item.lease_owner = None
    item.lease_until = None
    db.commit()


def release_lease(db, item) -> None:
    item.lease_owner = None
    item.lease_until = None
    db.commit()


def mark_submitted(db, item, generation: Generation) -> None:
    item.status = "submitted"
    item.result_id = generation.id
    item.error = generation.error_message
    item.lease_owner = None
    item.lease_until = None
    db.commit()


def _seedance_video_payload(batch, item, shot) -> dict:
    """Use the same approved frame/prompt with a distinct, stable request token."""
    original = GenerationCreate.model_validate(item.payload["video"])
    return original.model_copy(update={
        "request_token": _batch_item_token(UUID(batch.request_token), shot.id, "seedance-fallback" + (f"-recovery-{item.payload['recovery_attempt']}" if item.payload.get("recovery_attempt") else "")),
        "provider": "seedance",
        "model": "doubao-seedance-2-0-260128",
        "prompt": item.payload.get("seedance_prompt") or shot.generation_prompt or shot.visual_description,
        "duration": max(4, math.ceil(shot.duration)),
        "aspect_ratio": "adaptive" if shot.generation_mode in {"image_to_video", "first_last_frame"} else batch.options["aspect_ratio"],
        "api_parameters": {"generate_audio": batch.options["audio"] == "native", "watermark": False},
        "use_character_bindings": False,
    }).model_dump(mode="json")


def _activate_seedance_fallback(db, batch, item, result, reason="可灵余额或资源包不足") -> None:
    """Only a definite 1101/1102 rejection can switch a batch; never a timeout."""
    batch.options = {
        **batch.options,
        "fallback_active": True,
        "fallback_activated_at": datetime.now(UTC).isoformat(),
        "fallback_trigger_shot_id": item.shot_id,
    }
    item.payload = {**item.payload, "kling_rejected_generation_id": result.id}
    item.result_id = None
    item.status = "queued"
    item.error = reason + "；此镜及后续视频将转由 Seedance 2.0 提交"
    item.lease_owner = None
    item.lease_until = None
    db.commit()


def retry_or_fallback(db, batch, item, result) -> bool:
    # Only definite rejection or a remotely confirmed failed task is replayable.
    if result.provider != "kling" or result.status != "failed":
        return False
    if batch.options.get("fallback_provider") != "seedance":
        return False
    if (result.error_message or "").startswith("KlingQuotaExhausted:"):
        _activate_seedance_fallback(db, batch, item, result)
        return True
    failures = [*item.payload.get("kling_failures", []), result.id]
    retries = batch.options.get("kling_retries", 0)
    if len(failures) > retries:
        item.payload = {**item.payload, "kling_failures": failures}
        _activate_seedance_fallback(db, batch, item, result, f"可灵首次提交及 {retries} 次重试均失败")
        return True
    video = {**item.payload["video"], "request_token": str(_batch_item_token(UUID(batch.request_token), item.shot_id, f"kling-retry-{len(failures)}"))}
    item.payload = {**item.payload, "video": video, "kling_failures": failures}
    item.status = "queued"
    item.result_id = None
    item.error = f"可灵明确失败，将进行第 {len(failures)}/{retries} 次重试"
    item.lease_owner = None
    item.lease_until = datetime.now(UTC) + timedelta(seconds=15 * len(failures))
    db.commit()
    return True


def _ffmpeg_binary():
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def continuation_payload(db, batch, item, video, data_root):
    dependency = item.payload.get("continuation_shot_id")
    if not dependency:
        return video
    previous = db.scalar(select(ProductionBatchItem).where(ProductionBatchItem.batch_id == batch.id, ProductionBatchItem.shot_id == dependency))
    generation = db.get(Generation, previous.result_id) if previous and previous.result_id else None
    if not generation or generation.status != "ready":
        raise ValueError("S52 等待 S22 成片成功保存；不会用不连续的首帧代替")
    target = data_root / "assets" / "generations" / generation.id / "last_frame.png"
    if not target.exists():
        subprocess.run([_ffmpeg_binary(), "-y", "-sseof", "-0.1", "-i", str(data_root / generation.output_path), "-frames:v", "1", str(target)], check=True, capture_output=True)
    return {**video, "keyframe_selection_token": None, "first_frame": str(target)}


def export_video(db, item, generation, data_root):
    batch = db.get(ProductionBatch, item.batch_id)
    root = data_root / "assets" / "projects" / generation.project_id / "videos" / batch.id
    root.mkdir(parents=True, exist_ok=True)
    sid = item.payload.get("source_shot_id") or item.shot_id
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
    source = data_root / generation.output_path
    target = root / f"{safe_name}_{generation.provider}.mp4"
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        shutil.copy2(source, temporary)
        temporary.replace(target)
    metadata = {"shot_id": sid, "generation_id": generation.id, "provider": generation.provider, "model": generation.model, "generated_seconds": generation.duration, "edit_seconds": db.get(Shot, item.shot_id).duration, "file": target.name, "sha256": generation.output_sha256, "prompt": generation.prompt}
    target.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))


def _asset_root(registry, data_root: Path | None) -> Path:
    if data_root is not None:
        return data_root
    provider_root = getattr(registry.get("kling"), "data_root", None)
    if provider_root is None:
        raise RuntimeError("batch worker requires DATA_ROOT for result persistence")
    return Path(provider_root)


async def persist_video_result(db, generation: Generation, registry, data_root: Path):
    if not generation.external_id:
        raise ValueError("视频任务缺少 provider task id")
    provider = registry.get(generation.provider)
    directory = (data_root / "assets" / "generations" / generation.id).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    staging = directory / "result.download.mp4"
    downloaded = Path(
        await provider.download_result(generation.external_id, staging)
    ).resolve()
    if not downloaded.is_relative_to(directory) or not downloaded.is_file():
        raise ValueError("provider 下载结果不在任务素材目录")
    suffix = downloaded.suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm"}:
        suffix = ".mp4"
    final = directory / f"result{suffix}"
    if downloaded != final:
        downloaded.replace(final)
    digest = hashlib.sha256()
    size = 0
    with final.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if not size:
        raise ValueError("provider 下载了空视频文件")
    generation.output_path = final.relative_to(data_root.resolve()).as_posix()
    generation.output_sha256 = digest.hexdigest()
    generation.output_bytes = size
    generation.status = "ready"
    generation.completed_at = datetime.now(UTC)
    generation.error_message = None
    db.commit()
    db.refresh(generation)
    return generation


async def advance_submitted_video(db, item, registry, data_root: Path) -> None:
    generation = db.get(Generation, item.result_id)
    if generation is None:
        finish(db, item, "unknown", "视频生成记录缺失；不会自动重新提交")
        return
    if generation.status == "ready":
        export_video(db, item, generation, data_root)
        finish(db, item, "ready")
        return
    if generation.status in {"unknown"}:
        finish(db, item, "unknown", generation.error_message)
        return
    if generation.status in {
        "configuration_required",
        "failed",
        "cancelled",
        "canceled",
    }:
        if retry_or_fallback(db, db.get(ProductionBatch, item.batch_id), item, generation):
            return
        finish(db, item, "failed", generation.error_message)
        return
    if generation.status not in {"succeeded", "completed", "downloading"}:
        await GenerationService(registry).refresh(db, generation)
    if generation.status in {"unknown"}:
        finish(db, item, "unknown", generation.error_message)
        return
    if generation.status in {
        "configuration_required",
        "failed",
        "cancelled",
        "canceled",
    }:
        if retry_or_fallback(db, db.get(ProductionBatch, item.batch_id), item, generation):
            return
        finish(db, item, "failed", generation.error_message)
        return
    if generation.status not in {"succeeded", "completed", "downloading"}:
        release_lease(db, item)
        return
    generation.status = "downloading"
    db.commit()
    try:
        await persist_video_result(db, generation, registry, data_root)
    except (ProviderError, OSError, ValueError) as exc:
        generation.status = "download_failed"
        generation.error_message = f"{type(exc).__name__}: {exc}"
        db.commit()
        finish(db, item, "failed", "视频已生成但下载持久化失败；不会重新提交")
        return
    export_video(db, item, generation, data_root)
    finish(db, item, "ready")


async def process_item(
    db,
    item,
    registry,
    lease_owner: str | None = None,
    data_root: Path | None = None,
):
    if item is None or (lease_owner and item.lease_owner != lease_owner):
        return
    batch = db.get(ProductionBatch, item.batch_id)
    shot = db.get(Shot, item.shot_id)
    provider = registry.get("kling")
    if item.status == "submitted":
        await advance_submitted_video(
            db, item, registry, _asset_root(registry, data_root)
        )
        return
    if item.status == "waiting_keyframe":
        advance_workflow(db, item, shot, provider, lease_owner)
        return
    if item.status == "queued":
        conditions = [
            ProductionBatchItem.id == item.id,
            ProductionBatchItem.status == "queued",
        ]
        if lease_owner:
            conditions.append(ProductionBatchItem.lease_owner == lease_owner)
        claimed = db.execute(
            update(ProductionBatchItem).where(*conditions).values(status="submitting")
        )
        db.commit()
        if claimed.rowcount != 1:
            return
        try:
            production_editable(shot.scene.episode, shot)
            video_stage = item.payload.get("stage", batch.kind) == "videos"
            use_seedance = video_stage and bool(batch.options.get("fallback_active"))
            if not use_seedance and item.payload["account_scope"] != shot_images.account_scope(provider):
                raise ValueError("可灵账户或区域已变化，本项未提交；请重新预检")
            if shot.version != item.payload["version"]:
                raise ValueError("镜头已修改，本项未提交；请重新审阅")
            if (
                shot_images.source_context(db, shot, provider)["context_hash"]
                != item.payload["context_hash"]
            ):
                raise ValueError("参考资产已变化，本项未提交；请重新预检")
            if video_stage:
                if use_seedance:
                    if item.payload.get("seedance_input_blocked"):
                        raise ValueError("Seedance 已明确拒绝此人像输入，需先完成官方人像资产入库或补充可灵额度；未重复提交")
                    if not registry.get("seedance").capabilities().configured:
                        raise ValueError("Seedance 2.0 API 配置已失效，本项未提交")
                    video_payload = _seedance_video_payload(batch, item, shot)
                else:
                    bindings = (
                        preview_bindings(db, shot, provider, batch.options["model"])
                        if batch.options.get("use_character_bindings", False)
                        else {"elements": [], "errors": []}
                    )
                    if (
                        bindings["errors"]
                        or bindings["elements"] != item.payload["bindings"]
                    ):
                        raise ValueError("角色主体绑定已变化，本项未提交")
                    video_payload = item.payload["video"]
                video_payload = continuation_payload(db, batch, item, video_payload, _asset_root(registry, data_root))
                result = await GenerationService(registry).submit(
                    db, shot, GenerationCreate.model_validate(video_payload)
                )
                if result.status in {"configuration_required", "failed"}:
                    if retry_or_fallback(db, batch, item, result):
                        pass
                    else:
                        item.result_id = result.id
                        finish(db, item, "failed", result.error_message)
                elif result.status == "unknown" or not result.external_id:
                    item.result_id = result.id
                    finish(db, item, "unknown", result.error_message)
                else:
                    mark_submitted(db, item, result)
                return
            request = shot_images.KeyframeCreate(
                expected_version=item.payload["version"],
                context_hash=item.payload["context_hash"],
                request_token=UUID(item.payload["keyframe_request_token"]),
                prompt=item.payload["prompt"],
                negative_prompt=item.payload["negative_prompt"],
                count=batch.options["image_count"],
                resolution=batch.options["image_resolution"],
                aspect_ratio=batch.options["aspect_ratio"],
                confirmed=True,
                references_confirmed=True,
            )
            result = await shot_images.create_job(db, shot, request, provider)
            item.result_id = result["id"]
            if result["status"] in ("failed", "unknown"):
                finish(db, item, result["status"], result.get("error_message"))
                return
            item.status = "waiting"
            item.lease_owner = None
            item.lease_until = None
            db.commit()
        except ValueError as exc:
            finish(db, item, "failed", str(exc))
        except Exception:  # noqa: BLE001 - Never replay a possibly accepted paid write.
            db.rollback()
            finish(
                db,
                item,
                "unknown",
                "提交结果不确定，请检查镜头生成历史；不会自动重新提交",
            )
        return
    if item.status == "waiting":
        try:
            item.updated_at = datetime.now(UTC)
            db.commit()
            job = shot_images.get_job(db, shot, item.result_id)
            result = await shot_images.refresh_job(db, shot, job, provider)
            if result["status"] in ("failed", "unknown", "cancelled", "canceled"):
                finish(
                    db,
                    item,
                    "failed" if result["status"] != "unknown" else "unknown",
                    result.get("error_message") or "首帧任务未完成",
                )
            elif result["status"] in ("succeeded", "partial", "download_failed"):
                first = next((c for c in result["candidates"] if c["index"] == 0), None)
                if not first or not first.get("path"):
                    finish(
                        db,
                        item,
                        "failed",
                        "第一张候选下载失败；未选择其他候选，请在首帧工作台重试下载",
                    )
                    return
                selected = shot_images.selected_read(db, shot, provider)
                # Adoption and item completion are separate commits. Recover an
                # already adopted first candidate after a crash without selecting twice.
                if not matches_first_selection(selected, item):
                    shot_images.select_candidate(
                        db,
                        shot,
                        job,
                        shot_images.KeyframeSelect(
                            expected_version=item.payload["version"],
                            candidate_index=0,
                            confirmed=True,
                        ),
                        provider,
                    )
                finish(db, item, "succeeded")
            elif (
                datetime.now(UTC) - item.created_at.replace(tzinfo=UTC)
            ).total_seconds() > 1800:
                finish(
                    db,
                    item,
                    "unknown",
                    "等待超过 30 分钟，请在首帧工作台查询；不会重提",
                )
            else:
                release_lease(db, item)
        except ValueError as exc:
            finish(db, item, "failed", str(exc))
        except Exception:  # noqa: BLE001 - Isolate each durable job without exposing provider data.
            db.rollback()
            finish(
                db,
                item,
                "unknown",
                "首帧查询或选用未完成，请打开首帧工作台检查；不会重提",
            )


def matches_first_selection(selected, image_item):
    return bool(
        selected
        and selected["usable"]
        and selected["job_id"] == image_item.result_id
        and selected["candidate_index"] == 0
        and selected["shot_version"] == image_item.payload["version"] + 1
    )


def advance_workflow(db, item, shot, provider, lease_owner: str | None = None):
    """Release a video only for the exact first frame produced by its dependency."""
    dependency = db.get(ProductionBatchItem, item.payload["dependency_id"])
    if dependency and dependency.status in ACTIVE:
        item.updated_at = datetime.now(UTC)
        release_lease(db, item)
        return
    status, error, payload = (
        "skipped",
        "首帧阶段未完成，未提交视频；请核对首帧任务",
        item.payload,
    )
    if dependency and dependency.status == "succeeded":
        try:
            production_editable(shot.scene.episode, shot)
            selected = shot_images.selected_read(db, shot, provider)
            if not matches_first_selection(selected, dependency):
                raise ValueError("首帧或镜头已变化，未提交视频；请重新审阅")
            payload = {
                **item.payload,
                "version": selected["shot_version"],
                "video": {
                    **item.payload["video"],
                    "keyframe_selection_token": selected["selection_token"],
                },
            }
            status, error = "queued", None
        except (ValueError, OSError) as exc:
            status, error = (
                "failed",
                str(exc) if isinstance(exc, ValueError) else "首帧读取失败，未提交视频",
            )
    # A stop request may win while the dependency was being inspected.
    conditions = [
        ProductionBatchItem.id == item.id,
        ProductionBatchItem.status == "waiting_keyframe",
    ]
    if lease_owner:
        conditions.append(ProductionBatchItem.lease_owner == lease_owner)
    db.execute(
        update(ProductionBatchItem)
        .where(*conditions)
        .values(
            status=status,
            error=error,
            payload=payload,
            active_slot=item.active_slot if status == "queued" else None,
            lease_owner=None,
            lease_until=None,
        )
    )
    db.commit()


def claim_next_item(db, owner: str, now: datetime | None = None, batch_id: str | None = None) -> str | None:
    """Lease one item; PostgreSQL locks the candidate and SQLite uses a CAS update."""
    now = now or datetime.now(UTC)
    available = or_(
        ProductionBatchItem.lease_until.is_(None),
        ProductionBatchItem.lease_until < now,
    )
    db.execute(
        update(ProductionBatchItem)
        .where(
            ProductionBatchItem.status == "submitting",
            ProductionBatchItem.batch_id == batch_id if batch_id else True,
            available,
        )
        .values(
            status="unknown",
            active_slot=None,
            lease_owner=None,
            lease_until=None,
            error="worker 在提交期间失去租约，请核对远端及生成历史；不会自动重提",
        )
    )
    earlier = aliased(ProductionBatchItem)
    has_earlier = select(earlier.id).where(earlier.batch_id == ProductionBatchItem.batch_id, earlier.order_index < ProductionBatchItem.order_index, earlier.status.in_(ACTIVE)).exists()
    sequential_batch = select(ProductionBatch.id).where(ProductionBatch.id == ProductionBatchItem.batch_id, ProductionBatch.options["sequential"].as_boolean() == True).exists()
    query = (
        select(ProductionBatchItem.id)
        .where(ProductionBatchItem.status.in_(CLAIMABLE), available, or_(~sequential_batch, ~has_earlier), ProductionBatchItem.batch_id == batch_id if batch_id else True)
        .order_by(ProductionBatchItem.updated_at, ProductionBatchItem.order_index)
        .limit(1)
    )
    if db.bind.dialect.name != "sqlite":
        query = query.with_for_update(skip_locked=True)
    item_id = db.scalar(query)
    if item_id is None:
        db.commit()
        return None
    claimed = db.execute(
        update(ProductionBatchItem)
        .where(
            ProductionBatchItem.id == item_id,
            ProductionBatchItem.status.in_(CLAIMABLE),
            available,
        )
        .values(
            lease_owner=owner,
            lease_until=now + LEASE_DURATION,
        )
    )
    db.commit()
    return item_id if claimed.rowcount == 1 else None


async def worker(
    database, registry, poll_seconds: float = 5, data_root: Path | None = None, batch_id: str | None = None
):
    owner = f"batch-worker-{uuid4()}"
    while True:
        try:
            with database.session_factory() as db:
                item_id = claim_next_item(db, owner, batch_id=batch_id)
            if item_id:
                with database.session_factory() as db:
                    await process_item(
                        db,
                        db.get(ProductionBatchItem, item_id),
                        registry,
                        owner,
                        data_root,
                    )
                await asyncio.sleep(poll_seconds)
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - Keep other batches alive.
            logger.error("Batch worker deferred a cycle (%s)", type(exc).__name__)
        await asyncio.sleep(poll_seconds)


async def stop_worker(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
