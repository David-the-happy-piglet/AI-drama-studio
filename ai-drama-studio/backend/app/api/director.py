"""Director workflow endpoints: lint, prompt compilation, cold-reader reviews."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..agents.qa_agent.reviewer import review_status
from ..agents.storyboard_agent.lint import LintFinding, lint_storyboard, lint_summary
from ..models.entities import Episode, Project, Review, Scene, Script, Shot
from ..providers.video.kling import KlingProvider
from ..schemas import ShotRead
from ..services import director
from .routes import (
    DB,
    Registry,
    _apply_versioned_update,
    _assert_production_editable,
    _assert_version,
    _get_or_404,
)

router = APIRouter(tags=["director"])


class LintRead(BaseModel):
    summary: dict[str, int]
    findings: list[LintFinding]


class DirectorSettings(BaseModel):
    visual_style: str = Field(default="", max_length=1000)
    palette: str = Field(default="", max_length=2000)


class CompiledPromptRead(BaseModel):
    prompt: str
    sections: dict[str, str]
    notes: list[str]
    current_prompt: str | None


class ApplyCompiledPrompt(BaseModel):
    expected_version: int = Field(ge=1)


class ShotReviewCreate(BaseModel):
    prompt: str | None = Field(default=None, max_length=20000)


class GateQuery(BaseModel):
    prompt: str = Field(max_length=20000)
    # "selected": the shot's saved keyframe; "upload": a manually uploaded frame; "none".
    first_frame: Literal["selected", "upload", "none"] = "none"


class ScriptReviewCreate(BaseModel):
    stage: Literal["story", "storyboard"]


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    stage: str
    target_id: str
    target_version: int
    content_sha256: str
    verdict: str
    report: dict[str, Any]
    model: str
    created_at: datetime


def _lint_read(findings: list[LintFinding]) -> LintRead:
    return LintRead(summary=lint_summary(findings), findings=findings)


@router.get("/director/status")
def status() -> dict[str, Any]:
    return {"review_model": review_status(), "review_gate": director.review_gate_mode()}


@router.put("/projects/{project_id}/director-settings")
def update_settings(project_id: str, payload: DirectorSettings, db: DB) -> dict[str, Any]:
    project = _get_or_404(db, Project, project_id)
    # Reassign so SQLAlchemy notices the JSON change.
    project.settings_json = {**(project.settings_json or {}), **payload.model_dump()}
    db.commit()
    return project.settings_json


class LooseStoryboard(BaseModel):
    """Unsaved drafts may be incomplete, so shots are linted as plain mappings."""

    scenes: list[dict[str, Any]] = Field(max_length=60)


@router.post("/storyboard-lint", response_model=LintRead)
def lint_draft(payload: LooseStoryboard) -> LintRead:
    return _lint_read(lint_storyboard(payload.scenes))


@router.get("/scripts/{script_id}/storyboard-lint", response_model=LintRead)
def lint_script(script_id: str, db: DB) -> LintRead:
    script = _get_or_404(db, Script, script_id)
    draft = (script.structured_content or {}).get("draft") or {}
    return _lint_read(lint_storyboard(draft.get("scenes") or []))


@router.get("/episodes/{episode_id}/storyboard-lint", response_model=LintRead)
def lint_episode(episode_id: str, db: DB) -> LintRead:
    episode = db.scalar(
        select(Episode)
        .where(Episode.id == episode_id)
        .options(selectinload(Episode.scenes).selectinload(Scene.shots))
    )
    if episode is None:
        raise HTTPException(404, "Episode not found")
    return _lint_read(director.lint_episode(db, episode))


@router.get("/shots/{shot_id}/compiled-prompt", response_model=CompiledPromptRead)
def compiled_prompt(shot_id: str, db: DB) -> CompiledPromptRead:
    shot = _get_or_404(db, Shot, shot_id)
    compiled = director.compile_for_shot(db, shot)
    return CompiledPromptRead(**compiled.model_dump(), current_prompt=shot.generation_prompt)


@router.post("/shots/{shot_id}/compiled-prompt/apply", response_model=ShotRead)
def apply_compiled_prompt(shot_id: str, payload: ApplyCompiledPrompt, db: DB) -> Shot:
    shot = _get_or_404(db, Shot, shot_id)
    _assert_production_editable(shot.scene.episode, shot)
    _assert_version(shot, payload.expected_version)
    compiled = director.compile_for_shot(db, shot)
    _apply_versioned_update(db, shot, {"generation_prompt": compiled.prompt})
    return shot


@router.post("/scripts/{script_id}/reviews", response_model=ReviewRead)
async def review_script(script_id: str, payload: ScriptReviewCreate, db: DB):
    script = _get_or_404(db, Script, script_id)
    try:
        return await director.review_script(db, script, payload.stage)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/shots/{shot_id}/reviews", response_model=ReviewRead)
async def review_shot(
    shot_id: str, db: DB, registry: Registry, payload: ShotReviewCreate | None = None
):
    shot = _get_or_404(db, Shot, shot_id)
    kling = _kling_or_none(registry)
    try:
        return await director.review_shot(
            db, shot, kling, prompt=payload.prompt if payload else None
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _kling_or_none(registry):
    try:
        provider = registry.get("kling")
    except (KeyError, ValueError):
        return None
    return provider if isinstance(provider, KlingProvider) else None


@router.post("/shots/{shot_id}/review-gate")
def review_gate(shot_id: str, payload: GateQuery, db: DB, registry: Registry) -> dict[str, Any]:
    """What the submission gate will say for this prompt and frame source."""

    from ..services.shot_images import selected_read

    shot = _get_or_404(db, Shot, shot_id)
    frame = None
    if payload.first_frame == "selected":
        kling = _kling_or_none(registry)
        selected = selected_read(db, shot, kling) if kling else None
        frame = selected["sha256"] if selected and selected["usable"] else "unusable"
    elif payload.first_frame == "upload":
        frame = "upload"
    status = director.gate_status(db, shot, prompt=payload.prompt, first_frame_sha256=frame)
    if status.get("review_id"):
        review = db.get(Review, status["review_id"])
        status["report"] = review.report if review else None
    return status


@router.get("/reviews", response_model=list[ReviewRead])
def list_reviews(target_id: str, db: DB):
    return director.list_reviews(db, target_id)
