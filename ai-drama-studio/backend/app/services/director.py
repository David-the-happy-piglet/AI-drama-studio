"""Director workflow: storyboard lint, prompt compilation and cold-reader reviews.

Principle: catch errors before money is spent. Mistakes visible in text should
not reach images; mistakes visible in images should not reach video.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.qa_agent.reviewer import Stage, run_review
from ..agents.storyboard_agent.lint import lint_storyboard
from ..ids import new_id
from ..models.entities import Character, Outfit, Project, Review, Script, Shot
from ..prompts.seven_section import CompiledPrompt, compile_shot_prompt
from ..schemas import ShotCreate

GATE_MODES = {"off", "warn", "required"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def review_gate_mode() -> str:
    mode = os.getenv("REVIEW_GATE", "warn").strip().lower()
    return mode if mode in GATE_MODES else "warn"


# ---------------------------------------------------------------- compile


def shot_mapping(shot: Shot) -> dict[str, Any]:
    data = {key: getattr(shot, key) for key in ShotCreate.model_fields if hasattr(shot, key)}
    data["id"] = shot.id
    return data


def _character_inputs(db: Session, shot: Shot) -> list[dict[str, Any]]:
    result = []
    for binding in shot.characters or []:
        character = db.get(Character, binding.get("character_id"))
        if character is None:
            continue
        item = {
            key: getattr(character, key)
            for key in (
                "id",
                "name",
                "gender",
                "apparent_age",
                "body_type",
                "face_description",
                "hairstyle",
                "hair_color",
                "distinctive_features",
            )
        }
        outfit = db.get(Outfit, binding.get("outfit_id")) if binding.get("outfit_id") else None
        if outfit is not None:
            item["outfit"] = {
                "name": outfit.name,
                "description": outfit.description,
                "components": outfit.components,
                "color_palette": outfit.color_palette,
            }
        result.append(item)
    return result


def compile_for_shot(db: Session, shot: Shot) -> CompiledPrompt:
    scene = shot.scene
    project = db.get(Project, scene.episode.project_id)
    settings = (project.settings_json if project else None) or {}
    return compile_shot_prompt(
        shot_mapping(shot),
        scene={
            "location_description": scene.location_description,
            "time_of_day": scene.time_of_day,
        },
        characters=_character_inputs(db, shot),
        style=str(settings.get("visual_style") or ""),
        palette=str(settings.get("palette") or ""),
    )


# ---------------------------------------------------------------- lint


def lint_episode(db: Session, episode) -> list:
    scenes = []
    for scene in episode.scenes:
        if scene.status == "archived":
            continue
        scenes.append(
            {
                "shots": [
                    shot_mapping(shot) for shot in scene.shots if shot.status != "archived"
                ]
            }
        )
    return lint_storyboard(scenes)


# ---------------------------------------------------------------- reviews


def _storyboard_material(scenes: list[dict[str, Any]]) -> str:
    """Only the shot table: no screenplay, no prior conclusions."""

    lines = []
    for scene_index, scene in enumerate(scenes, 1):
        lines.append(
            f"## 第{scene_index}场 {scene.get('location') or scene.get('location_description', '')}"
            f" {scene.get('time_of_day') or ''}"
        )
        for shot_index, shot in enumerate(scene.get("shots") or [], 1):
            row = {
                key: shot.get(key)
                for key in (
                    "duration",
                    "shot_type",
                    "camera_angle",
                    "camera_movement",
                    "composition",
                    "visual_description",
                    "action",
                    "emotion",
                    "dialogue",
                    "continuity_requirements",
                    "generation_mode",
                    "direction",
                )
                if shot.get(key)
            }
            lines.append(f"### 第{scene_index}场-第{shot_index}镜\n" + json.dumps(row, ensure_ascii=False))
    return "\n".join(lines)


def _record(db: Session, *, project_id, stage, target_id, target_version, content_hash, report, model) -> Review:
    review = Review(
        id=new_id("REV"),
        project_id=project_id,
        stage=stage,
        target_id=target_id,
        target_version=target_version,
        content_sha256=content_hash,
        verdict=report.verdict,
        report=report.model_dump(mode="json"),
        model=model,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


async def review_script(db: Session, script: Script, stage: Stage, **kwargs) -> Review:
    draft = (script.structured_content or {}).get("draft") or {}
    if stage == "story":
        material = str(draft.get("screenplay") or "").strip()
        if not material:
            raise ValueError("剧本为空，无法审核")
    elif stage == "storyboard":
        if not draft.get("scenes"):
            raise ValueError("分镜为空，无法审核")
        material = _storyboard_material(draft["scenes"])
    else:
        raise ValueError("剧本只支持 story / storyboard 审核")
    report, model = await run_review(stage, material, **kwargs)
    return _record(
        db,
        project_id=script.project_id,
        stage=stage,
        target_id=script.id,
        target_version=script.version,
        content_hash=sha256_text(material),
        report=report,
        model=model,
    )


def _neighbour_summary(shot: Shot) -> str:
    siblings = [s for s in shot.scene.shots if s.status != "archived"]
    index = next((i for i, s in enumerate(siblings) if s.id == shot.id), None)
    parts = []
    if index is not None and index > 0:
        prev = siblings[index - 1]
        parts.append(f"上一镜：{prev.shot_type}，{prev.visual_description}")
    if index is not None and index + 1 < len(siblings):
        nxt = siblings[index + 1]
        parts.append(f"下一镜：{nxt.shot_type}，{nxt.visual_description}")
    return "\n".join(parts)


def pre_submit_fingerprint(prompt: str, first_frame_sha256: str | None) -> str:
    return sha256_text(json.dumps([prompt, first_frame_sha256 or ""], ensure_ascii=False))


def effective_prompt(shot: Shot) -> str:
    return shot.generation_prompt or shot.visual_description


async def review_shot(
    db: Session, shot: Shot, kling_provider, prompt: str | None = None, **kwargs
) -> Review:
    from .shot_images import selected_read

    prompt = prompt or effective_prompt(shot)
    images: list[tuple[str, Path]] = []
    keyframe = selected_read(db, shot, kling_provider) if kling_provider else None
    frame_sha = None
    if keyframe and keyframe["usable"]:
        frame_sha = keyframe["sha256"]
        images.append(("首帧", _resolve(kling_provider.data_root, keyframe["path"])))
    for character in _character_inputs(db, shot)[:3]:
        record = db.get(Character, character["id"])
        if record and record.master_reference_path:
            path = _resolve(kling_provider.data_root, record.master_reference_path) if kling_provider else None
            if path and path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                images.append((f"角色设定图：{record.name}", path))
    row = {k: v for k, v in shot_mapping(shot).items() if v and k not in {"generation_prompt", "id"}}
    material = "\n\n".join(
        part
        for part in (
            "【提示词】\n" + prompt,
            "【分镜行】\n" + json.dumps(row, ensure_ascii=False),
            "【人物设定】\n"
            + json.dumps(_character_inputs(db, shot), ensure_ascii=False),
            "【前后镜头】\n" + _neighbour_summary(shot),
            "" if frame_sha else "注意：此镜头没有可用的已选首帧，只能审提示词。",
        )
        if part
    )
    report, model = await run_review("shot_pre_submit", material, images, **kwargs)
    return _record(
        db,
        project_id=shot.scene.episode.project_id,
        stage="shot_pre_submit",
        target_id=shot.id,
        target_version=shot.version,
        content_hash=pre_submit_fingerprint(prompt, frame_sha),
        report=report,
        model=model,
    )


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def gate_status(
    db: Session,
    shot: Shot,
    *,
    prompt: str,
    first_frame_sha256: str | None,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Snapshot of the pre-submit review state for this exact prompt and frame."""

    fingerprint = pre_submit_fingerprint(prompt, first_frame_sha256)
    review = db.scalar(
        select(Review)
        .where(
            Review.stage == "shot_pre_submit",
            Review.target_id == shot.id,
            Review.target_version == shot.version,
            Review.content_sha256 == fingerprint,
        )
        .order_by(Review.created_at.desc())
    )
    snapshot: dict[str, Any] = {"mode": review_gate_mode()}
    if review is not None:
        snapshot |= {"review_id": review.id, "verdict": review.verdict}
    else:
        snapshot["verdict"] = "missing"
    if override_reason:
        snapshot["override_reason"] = override_reason
    return snapshot


def gate_check(
    db: Session,
    shot: Shot,
    *,
    prompt: str,
    first_frame_sha256: str | None,
    override_reason: str | None,
) -> dict[str, Any]:
    """Return the review snapshot; raise if the gate blocks submission."""

    snapshot = gate_status(
        db,
        shot,
        prompt=prompt,
        first_frame_sha256=first_frame_sha256,
        override_reason=override_reason,
    )
    if snapshot["mode"] == "required" and snapshot["verdict"] != "pass" and not override_reason:
        if snapshot["verdict"] == "missing":
            raise ValueError("提交前审核未完成：请先对当前提示词和首帧运行审核，或填写人工放行理由")
        raise ValueError("提交前审核未通过：请按审核意见修改后重新审核，或填写人工放行理由")
    return snapshot


def list_reviews(db: Session, target_id: str) -> list[Review]:
    return list(
        db.scalars(
            select(Review).where(Review.target_id == target_id).order_by(Review.created_at.desc())
        ).all()
    )

