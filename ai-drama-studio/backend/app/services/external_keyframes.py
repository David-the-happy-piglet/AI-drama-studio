"""Atomically adopt externally generated images; never submit a generation job."""

import hashlib
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..ids import new_id
from ..models import Episode, Shot
from ..models.entities import ShotImageJob, ShotKeyframe
from . import shot_images
from .character_elements import validate_images
from .storyboard_revision import production_editable


class ExternalFrame(BaseModel):
    shot_id: str
    expected_version: int = Field(ge=1)
    context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    path: str = Field(min_length=1, max_length=2000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt: str = Field(default="", max_length=20000)


class ExternalFrameBatch(BaseModel):
    request_token: UUID
    confirmed: Literal[True]
    source: Literal["codex-imagegen", "external-image"] = "codex-imagegen"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    items: list[ExternalFrame] = Field(min_length=1, max_length=200)


def import_frames(db, episode_id, payload, provider):
    episode = db.get(Episode, episode_id)
    if not episode:
        raise HTTPException(404, "分集不存在")
    if len({item.shot_id for item in payload.items}) != len(payload.items):
        raise HTTPException(422, "镜头不能重复")
    # Store the full batch digest on every job: a token cannot be reused for a
    # changed/subset batch. Existing jobs may be returned after a later revision.
    fingerprint = shot_images.digest(payload.model_dump(mode="json"))
    rows = []
    for item in payload.items:
        shot = db.get(Shot, item.shot_id)
        if not shot or shot.scene.episode_id != episode_id:
            raise HTTPException(422, "镜头不属于当前分集")
        token = str(
            uuid5(
                NAMESPACE_URL,
                f"external-frame:{episode_id}:{payload.request_token}:{shot.id}",
            )
        )
        job = db.scalar(
            select(ShotImageJob).where(
                ShotImageJob.shot_id == shot.id,
                ShotImageJob.request_token == token,
            )
        )
        if job:
            if job.account_scope != fingerprint:
                raise HTTPException(409, "同一导入标识已用于不同图片，请使用新的标识")
            rows.append((shot, item, token, job))
            continue
        try:
            production_editable(episode, shot)
            if shot.generation_mode not in {"image_to_video", "first_last_frame"}:
                raise ValueError("只有首帧/首尾帧镜头可以绑定首帧")
            if shot.version != item.expected_version:
                raise HTTPException(409, "镜头版本已变化，请重新核对图片")
            context = shot_images.source_context(db, shot, provider)
            if context["errors"]:
                raise ValueError("；".join(context["errors"]))
            if context["context_hash"] != item.context_hash:
                raise HTTPException(409, "人物、场景或镜头已变化，请重新核对图片")
            content = validate_images(
                provider.data_root, episode.project_id, [item.path]
            )[0]
            if hashlib.sha256(content).hexdigest() != item.sha256:
                raise ValueError("图片内容与清单指纹不一致")
        except (ValueError, OSError) as exc:
            raise HTTPException(
                422, str(exc) if isinstance(exc, ValueError) else "图片读取失败"
            ) from exc
        rows.append((shot, item, token, None))

    try:
        for shot, item, token, job in rows:
            if job:
                continue
            claimed = db.execute(
                update(Shot)
                .where(
                    Shot.id == shot.id,
                    Shot.version == item.expected_version,
                )
                .values(version=Shot.version + 1)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                raise HTTPException(409, "镜头版本冲突，本批次未导入")
            job = ShotImageJob(
                id=new_id("EXTFRAME"),
                shot_id=shot.id,
                project_id=episode.project_id,
                request_token=token,
                account_scope=fingerprint,
                model=payload.source,
                source_version=item.expected_version,
                context_hash=item.context_hash,
                prompt=item.prompt,
                effective_prompt=item.prompt,
                negative_prompt="",
                # Do not falsely claim the external generator consumed references.
                references_snapshot=[],
                count=1,
                resolution="original",
                aspect_ratio=payload.aspect_ratio,
                status="succeeded",
                active_slot=None,
                candidates=[{"index": 0, "path": item.path, "sha256": item.sha256}],
            )
            db.add(job)
            db.flush()
            selected = db.get(ShotKeyframe, shot.id) or ShotKeyframe(shot_id=shot.id)
            selected.job_id, selected.candidate_index = job.id, 0
            selected.selection_token = new_id("FRAME")
            selected.shot_version = item.expected_version + 1
            selected.context_hash = item.context_hash
            selected.path, selected.sha256 = item.path, item.sha256
            db.add(selected)
        db.commit()
    except (HTTPException, IntegrityError):
        db.rollback()
        raise HTTPException(
            409, "导入发生并发冲突，本批次已回滚；可用相同清单重试"
        ) from None
    return {
        "items": [
            {
                "shot_id": shot.id,
                "already_imported": job is not None,
                "selected": _selected(db, shot, provider),
            }
            for shot, _, _, job in rows
        ]
    }


def _selected(db, shot, provider):
    db.refresh(shot)
    return shot_images.selected_read(db, shot, provider)
