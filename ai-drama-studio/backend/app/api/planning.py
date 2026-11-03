from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ids import new_id
from ..models import Character, Episode, Project, Scene, Script, Shot
from ..schemas import ScriptRead
from ..services import storyboard_revision as revision
from ..services.planning import (
    PlanningRequest,
    StoryDraft,
    generate_draft,
    generate_storyboard,
    planning_status,
)
from .dependencies import get_db
from .routes import _assert_character_ids, _draft_episode, _get_or_404

router = APIRouter(tags=["story-review"])
DB = Annotated[Session, Depends(get_db)]


class SaveDraft(BaseModel):
    expected_version: int = Field(ge=1)
    draft: StoryDraft
    screenplay_approved: bool = False


class ReviewVersion(BaseModel):
    expected_version: int = Field(ge=1)


class ConfirmedReviewVersion(ReviewVersion):
    confirmed: bool = False


class StoryboardRequest(ReviewVersion):
    screenplay: str = Field(min_length=5, max_length=60000)
    confirmed: bool = False


class ExternalStoryboard(BaseModel):
    request_token: UUID
    title: str = Field(min_length=1, max_length=200)
    source_idea: str = Field(min_length=1, max_length=15000)
    draft: StoryDraft

    @model_validator(mode="after")
    def validate_import(self):
        if not self.title.strip() or not self.source_idea.strip():
            raise ValueError("标题和故事简介不能为空")
        total = 0
        for scene in self.draft.scenes:
            if scene.id:
                raise ValueError("外部分镜新建审阅稿，请省略场景和镜头的数据库 id")
            for index, shot in enumerate(scene.shots, 1):
                if shot.id or shot.order_index != index:
                    raise ValueError(
                        "每场镜头 order_index 须从 1 连续编号，并省略数据库 id"
                    )
                if not math.isfinite(shot.duration):
                    raise ValueError("镜头时长必须是有限数值")
                total += shot.duration
        if not 1 <= total <= 7200:
            raise ValueError("分镜总时长须为 1–7200 秒")
        return self


@router.post("/projects/{project_id}/external-storyboards", response_model=ScriptRead)
def import_external_storyboard(
    project_id: str, payload: ExternalStoryboard, db: DB, episode_id: str | None = None
):
    """Import authoritative external shots without invoking a text/image provider."""
    _get_or_404(db, Project, project_id)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"episode_id": episode_id, **payload.model_dump(mode="json")},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    script_id = (
        "SCRIPT_EXT_"
        + uuid5(NAMESPACE_URL, f"{project_id}:{payload.request_token}").hex
    )

    def existing_import():
        existing = db.get(Script, script_id)
        if (
            existing
            and existing.structured_content.get("external_import_sha256") != fingerprint
        ):
            raise HTTPException(409, "此导入标识已用于不同分镜，请为新稿生成新的标识")
        return existing

    if existing := existing_import():
        return existing
    episode = _draft_episode(db, project_id, episode_id)
    validate_characters(db, project_id, payload.draft)
    character_ids = sorted(
        {
            c.character_id
            for s in payload.draft.scenes
            for shot in s.shots
            for c in shot.characters
        }
    )
    content = {
        "draft": payload.draft.model_dump(mode="json"),
        "screenplay_approved": False,
        "external_import_sha256": fingerprint,
        "source": "external_storyboard",
    }
    if episode:
        content["episode_id"] = episode.id
    script = Script(
        id=script_id,
        project_id=project_id,
        title=payload.title,
        source_idea=payload.source_idea,
        character_ids=character_ids,
        episode_duration=math.ceil(
            sum(shot.duration for s in payload.draft.scenes for shot in s.shots)
        ),
        status="draft",
        structured_content=content,
    )
    db.add(script)
    try:
        db.flush()
        if episode:
            claimed = db.execute(
                update(Episode)
                .where(
                    Episode.id == episode.id,
                    Episode.version == episode.version,
                    Episode.status == "draft",
                )
                .values(script_id=script.id, version=Episode.version + 1)
            )
            if claimed.rowcount != 1:
                db.rollback()
                raise HTTPException(409, "本集已更新，请刷新后重新导入")
        db.commit()
    except IntegrityError:
        db.rollback()
        if existing := existing_import():
            return existing
        raise HTTPException(409, "导入冲突，请刷新后重试") from None
    db.refresh(script)
    return script


@router.get("/scripts/{script_id}", response_model=ScriptRead)
def get_review_script(script_id: str, db: DB):
    return _get_or_404(db, Script, script_id)


@router.post("/scripts/{script_id}/reopen-storyboard", response_model=ScriptRead)
def reopen_storyboard(script_id: str, payload: ConfirmedReviewVersion, db: DB):
    if not payload.confirmed:
        raise HTTPException(422, "请确认撤回分镜；已有素材和远端任务保留")
    return revision.reopen(
        db, _get_or_404(db, Script, script_id), payload.expected_version
    )


@router.post("/scripts/{script_id}/cancel-revision", response_model=ScriptRead)
def cancel_revision(script_id: str, payload: ConfirmedReviewVersion, db: DB):
    if not payload.confirmed:
        raise HTTPException(422, "请确认放弃本次修订，恢复已发布状态")
    return revision.cancel(
        db, _get_or_404(db, Script, script_id), payload.expected_version
    )


@router.post("/scripts/{script_id}/generate-storyboard", response_model=ScriptRead)
async def storyboard_from_script(script_id: str, payload: StoryboardRequest, db: DB):
    script = _get_or_404(db, Script, script_id)
    if not payload.confirmed or not payload.screenplay.strip():
        raise HTTPException(422, "请填写完整剧本，并确认调用文本模型生成分镜")
    if script.version != payload.expected_version or script.structured_content.get(
        "published_episode_id"
    ):
        raise HTTPException(409, "稿件已更新或仍处于发布状态，请刷新或先撤回分镜")
    characters = db.scalars(
        select(Character).where(Character.project_id == script.project_id)
    ).all()
    try:
        draft = await generate_storyboard(
            payload.screenplay,
            script,
            [
                {
                    "character_id": c.id,
                    "name": c.name,
                    "face_description": c.face_description,
                    "visual_style": c.visual_style,
                    "outfits": [
                        {
                            "outfit_id": o.id,
                            "name": o.name,
                            "description": o.description,
                        }
                        for o in c.outfits
                    ],
                }
                for c in characters
            ],
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(
            502,
            str(exc)
            if isinstance(exc, ValueError)
            else "文本模型连接失败，原分镜未修改",
        ) from exc
    # The user's submitted screenplay is authoritative, even if the model rewrites it.
    draft.screenplay = payload.screenplay
    return save_review(
        script_id,
        SaveDraft(
            expected_version=payload.expected_version,
            draft=draft,
            screenplay_approved=False,
        ),
        db,
    )


def validate_characters(db: Session, project_id: str, draft: StoryDraft):
    for scene in draft.scenes:
        for shot in scene.shots:
            _assert_character_ids(
                db, project_id, [item.character_id for item in shot.characters]
            )
            if any(item.outfit_id for item in shot.characters):
                from ..models import Outfit

                for item in shot.characters:
                    if item.outfit_id:
                        outfit = _get_or_404(db, Outfit, item.outfit_id)
                        if outfit.character_id != item.character_id:
                            raise HTTPException(422, "造型不属于该角色")


@router.get("/planning/status")
def get_status():
    return planning_status()


@router.post("/projects/{project_id}/story-drafts", response_model=ScriptRead)
async def create_draft(
    project_id: str, payload: PlanningRequest, db: DB, episode_id: str | None = None
):
    _get_or_404(db, Project, project_id)
    episode = _draft_episode(db, project_id, episode_id)
    characters = db.scalars(
        select(Character).where(Character.project_id == project_id)
    ).all()
    try:
        draft = await generate_draft(
            payload,
            [
                {
                    "character_id": c.id,
                    "name": c.name,
                    "face_description": c.face_description,
                    "visual_style": c.visual_style,
                }
                for c in characters
            ],
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(
            502, str(exc) if isinstance(exc, ValueError) else "文本模型连接超时或失败"
        ) from exc
    validate_characters(db, project_id, draft)
    script = Script(
        id=new_id("SCRIPT"),
        project_id=project_id,
        title=payload.title,
        source_idea=payload.synopsis,
        genre=payload.genre,
        episode_duration=payload.duration,
        status="draft",
        structured_content={
            "draft": draft.model_dump(mode="json"),
            "screenplay_approved": False,
        },
    )
    db.add(script)
    if episode:
        script.structured_content = {
            **script.structured_content,
            "episode_id": episode.id,
        }
        db.flush()
        episode.script_id = script.id
    db.commit()
    db.refresh(script)
    return script


@router.put("/scripts/{script_id}/review", response_model=ScriptRead)
def save_review(script_id: str, payload: SaveDraft, db: DB):
    script = _get_or_404(db, Script, script_id)
    if script.structured_content.get("published_episode_id"):
        raise HTTPException(409, "此稿已发布，请先撤回分镜再修改审阅稿。")
    if script.structured_content.get("reopen"):
        revision.validate_ids(payload.draft, script.structured_content["reopen"])
    validate_characters(db, script.project_id, payload.draft)
    result = db.execute(
        update(Script)
        .where(Script.id == script_id, Script.version == payload.expected_version)
        .values(
            structured_content={
                **script.structured_content,
                "draft": payload.draft.model_dump(mode="json"),
                "screenplay_approved": payload.screenplay_approved,
            },
            status="screenplay_approved" if payload.screenplay_approved else "draft",
            version=Script.version + 1,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "草稿已更新，请重新加载后审阅。")
    db.commit()
    db.refresh(script)
    return script


@router.post("/scripts/{script_id}/publish-storyboard")
def publish_storyboard(script_id: str, payload: ReviewVersion, db: DB):
    script = _get_or_404(db, Script, script_id)
    content = script.structured_content
    if content.get("published_episode_id"):
        return {"episode_id": content["published_episode_id"]}
    if (
        not content.get("screenplay_approved")
        or script.version != payload.expected_version
    ):
        raise HTTPException(409, "请先保存并确认当前版本剧本，再批准分镜。")
    draft = StoryDraft.model_validate(content["draft"])
    validate_characters(db, script.project_id, draft)
    if content.get("reopen"):
        return revision.republish(db, script, payload.expected_version, draft)
    episode = _draft_episode(db, script.project_id, content.get("episode_id"))
    episode_id = episode.id if episode else new_id("EP")
    if episode:
        # Claim the draft episode too: two script versions must not publish into it twice.
        claimed = db.execute(
            update(Episode)
            .where(
                Episode.id == episode.id,
                Episode.status == "draft",
                Episode.version == episode.version,
            )
            .values(
                status="in_production", script_id=script_id, version=Episode.version + 1
            )
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "本集已被另一稿发布，请刷新")
    # Claim the version in the same transaction as all materialized records.
    result = db.execute(
        update(Script)
        .where(Script.id == script_id, Script.version == payload.expected_version)
        .values(
            version=Script.version + 1,
            status="approved",
            structured_content={**content, "published_episode_id": episode_id},
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "草稿版本冲突，请刷新。")
    number = (
        db.scalar(
            select(func.max(Episode.episode_number)).where(
                Episode.project_id == script.project_id
            )
        )
        or 0
    ) + 1
    if not episode:
        db.add(
            Episode(
                id=episode_id,
                project_id=script.project_id,
                script_id=script_id,
                episode_number=number,
                title=script.title,
                duration_target=script.episode_duration,
                summary=script.source_idea,
                status="in_production",
            )
        )
    db.flush()
    for scene_index, planned in enumerate(draft.scenes, 1):
        scene_id = new_id("SC")
        ids = sorted(
            {c.character_id for shot in planned.shots for c in shot.characters}
        )
        db.add(
            Scene(
                id=scene_id,
                episode_id=episode_id,
                order_index=scene_index,
                location_description=planned.location,
                time_of_day=planned.time_of_day,
                summary=planned.summary,
                character_ids=ids,
            )
        )
        db.flush()
        for shot_index, shot in enumerate(planned.shots, 1):
            data = shot.model_dump(mode="json", exclude={"id", "order_index"})
            db.add(
                Shot(
                    id=new_id("SH"),
                    scene_id=scene_id,
                    order_index=shot_index,
                    status="approved_for_generation",
                    **data,
                )
            )
    db.commit()
    return {"episode_id": episode_id}
