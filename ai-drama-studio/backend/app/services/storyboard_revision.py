"""Reopen published storyboards without destroying production IDs or media."""

from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update

from ..ids import new_id
from ..models import Episode, Scene, Script, Shot
from ..schemas import ShotCreate
from .planning import StoryDraft


def production_editable(episode, shot=None):
    if episode.status == "revising":
        raise ValueError("本集分镜已撤回修订，请在故事与审阅中重新批准后再制作")
    if shot and (shot.status == "archived" or shot.scene.status == "archived"):
        raise ValueError("此镜头已移出当前分镜，素材与历史保留，但不能继续生成")


def board_rows(db, episode_id):
    scenes = db.scalars(
        select(Scene)
        .where(Scene.episode_id == episode_id)
        .order_by(Scene.order_index, Scene.id)
    ).all()
    shots = db.scalars(
        select(Shot)
        .join(Scene)
        .where(Scene.episode_id == episode_id)
        .order_by(Shot.order_index, Shot.id)
    ).all()
    return scenes, shots


def versions(scenes, shots):
    return {
        "scenes": {s.id: s.version for s in scenes},
        "shots": {h.id: h.version for h in shots},
    }


def claim_script(db, script, expected, content, status):
    result = db.execute(
        update(Script)
        .where(Script.id == script.id, Script.version == expected)
        .values(
            structured_content=content,
            status=status,
            version=Script.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(409, "审阅稿已被修改，请刷新后重试")


def reopen(db, script, expected):
    content = deepcopy(script.structured_content)
    if script.version != expected:
        raise HTTPException(409, "稿件版本已变化，请刷新")
    if content.get("reopen"):
        return script
    episode_id = content.get("published_episode_id")
    episode = db.get(Episode, episode_id) if episode_id else None
    if (
        not episode
        or episode.project_id != script.project_id
        or episode.script_id != script.id
    ):
        raise HTTPException(409, "只能撤回本集当前已发布的稿件")
    scenes, shots = board_rows(db, episode.id)
    draft = {
        "screenplay": content.get("draft", {}).get("screenplay") or script.source_idea,
        "scenes": [],
    }
    for scene in scenes:
        if scene.status == "archived":
            continue
        items = []
        for shot in shots:
            if shot.scene_id == scene.id and shot.status != "archived":
                items.append(
                    ShotCreate.model_validate(
                        {key: getattr(shot, key) for key in ShotCreate.model_fields}
                    ).model_dump(mode="json")
                )
        if items:
            draft["scenes"].append(
                {
                    "id": scene.id,
                    "location": scene.location_description,
                    "time_of_day": scene.time_of_day,
                    "summary": scene.summary,
                    "shots": items,
                }
            )
    try:
        draft = StoryDraft.model_validate(draft).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(
            409, "当前制作分镜无法转为有效审阅稿，请先检查空场景或镜头数据"
        ) from exc
    snapshot = {
        "at": datetime.now(UTC).isoformat(),
        "script_version": script.version,
        "published_content": {
            k: v for k, v in content.items() if k != "review_history"
        },
        "live_draft": deepcopy(draft),
        "episode_status": episode.status,
    }
    content["review_history"] = [*content.get("review_history", []), snapshot]
    content["reopen"] = {
        "episode_id": episode.id,
        "episode_version": episode.version + 1,
        **versions(scenes, shots),
    }
    content["episode_id"] = episode.id
    content.pop("published_episode_id", None)
    content.update(draft=draft, screenplay_approved=False)
    claim_script(db, script, expected, content, "draft")
    claimed = db.execute(
        update(Episode)
        .where(
            Episode.id == episode.id,
            Episode.version == episode.version,
            Episode.script_id == script.id,
        )
        .values(status="revising", version=Episode.version + 1)
    )
    if claimed.rowcount != 1:
        raise HTTPException(409, "本集状态已变化，请刷新")
    db.commit()
    db.refresh(script)
    return script


def validate_ids(draft, baseline):
    scene_ids, shot_ids = set(), set()
    for scene in draft.scenes:
        if scene.id:
            if scene.id not in baseline["scenes"] or scene.id in scene_ids:
                raise HTTPException(422, "场景 ID 不属于本次修订，或被重复使用")
            scene_ids.add(scene.id)
        for shot in scene.shots:
            if shot.id:
                if shot.id not in baseline["shots"] or shot.id in shot_ids:
                    raise HTTPException(422, "镜头 ID 不属于本次修订，或被重复使用")
                shot_ids.add(shot.id)


def current_revision(db, script):
    baseline = script.structured_content.get("reopen")
    if not baseline:
        raise HTTPException(409, "当前稿件不在撤回修订状态")
    episode = db.get(Episode, baseline["episode_id"])
    if (
        not episode
        or episode.project_id != script.project_id
        or episode.script_id != script.id
        or episode.status != "revising"
        or episode.version != baseline["episode_version"]
    ):
        raise HTTPException(409, "本集修订状态已变化，请刷新")
    scenes, shots = board_rows(db, episode.id)
    actual = versions(scenes, shots)
    if any(actual[key] != baseline[key] for key in ("scenes", "shots")):
        raise HTTPException(
            409,
            "制作镜头已被其他操作修改；为避免覆盖，请先取消本次修订，再重新撤回获取最新内容",
        )
    return baseline, episode, scenes, shots


def cancel(db, script, expected):
    if script.version != expected:
        raise HTTPException(409, "稿件版本已变化，请刷新")
    baseline = script.structured_content.get("reopen")
    if not baseline:
        raise HTTPException(409, "当前稿件不在撤回修订状态")
    episode = db.get(Episode, baseline["episode_id"])
    if (
        not episode
        or episode.script_id != script.id
        or episode.project_id != script.project_id
        or episode.status != "revising"
    ):
        raise HTTPException(409, "本集修订状态已变化，请刷新")
    history = deepcopy(script.structured_content["review_history"])
    previous = history[-1]
    content = deepcopy(previous["published_content"])
    history[-1]["discarded_revision_draft"] = deepcopy(
        script.structured_content["draft"]
    )
    history[-1]["outcome"] = "cancelled"
    content["review_history"] = history
    claim_script(db, script, expected, content, "approved")
    claimed = db.execute(
        update(Episode)
        .where(
            Episode.id == episode.id,
            Episode.version == episode.version,
            Episode.status == "revising",
            Episode.script_id == script.id,
        )
        .values(status=previous["episode_status"], version=Episode.version + 1)
    )
    if claimed.rowcount != 1:
        raise HTTPException(409, "本集状态已变化，请刷新")
    db.commit()
    db.refresh(script)
    return script


def republish(db, script, expected, draft):
    baseline, episode, scenes, shots = current_revision(db, script)
    validate_ids(draft, baseline)
    scene_map, shot_map = {s.id: s for s in scenes}, {h.id: h for h in shots}
    content = deepcopy(script.structured_content)
    # Claim script before any production changes. Transaction rollback protects every row.
    claim_script(db, script, expected, content, "approved")
    kept_scenes, kept_shots = set(), set()
    changed, added, archived = [], [], []
    for si, planned in enumerate(draft.scenes, 1):
        scene = scene_map.get(planned.id) if planned.id else None
        scene_values = {
            "order_index": si,
            "location_description": planned.location,
            "time_of_day": planned.time_of_day,
            "summary": planned.summary,
            "character_ids": sorted(
                {c.character_id for h in planned.shots for c in h.characters}
            ),
        }
        scene_visual_changed = bool(
            scene
            and (
                scene.location_description != planned.location
                or scene.time_of_day != planned.time_of_day
                or scene.status == "archived"
            )
        )
        if scene is None:
            scene = Scene(id=new_id("SC"), episode_id=episode.id, **scene_values)
            db.add(scene)
            db.flush()
        else:
            diff = {
                key: value
                for key, value in scene_values.items()
                if getattr(scene, key) != value
            }
            if scene.status == "archived":
                diff["status"] = "draft"
            if diff:
                for key, value in diff.items():
                    setattr(scene, key, value)
                scene.version += 1
        planned.id = scene.id
        kept_scenes.add(scene.id)
        for oi, planned_shot in enumerate(planned.shots, 1):
            shot = shot_map.get(planned_shot.id) if planned_shot.id else None
            values = planned_shot.model_dump(mode="json", exclude={"id", "order_index"})
            values.update(scene_id=scene.id, order_index=oi)
            if shot is None:
                shot = Shot(id=new_id("SH"), status="approved_for_generation", **values)
                db.add(shot)
                added.append(shot.id)
            else:
                # JSON may omit optional outfit_id on older rows; compare via schema normalization.
                previous = ShotCreate.model_validate(
                    {key: getattr(shot, key) for key in ShotCreate.model_fields}
                ).model_dump(mode="json")
                previous["scene_id"] = shot.scene_id
                diff = {
                    key: value
                    for key, value in values.items()
                    if previous.get(key) != value
                }
                if diff or scene_visual_changed or shot.status == "archived":
                    for key, value in diff.items():
                        setattr(shot, key, value)
                    shot.version += 1
                    shot.status = "approved_for_generation"
                    changed.append(shot.id)
            planned_shot.id, planned_shot.order_index = shot.id, oi
            kept_shots.add(shot.id)
    for shot in shots:
        if shot.id not in kept_shots and shot.status != "archived":
            shot.status = "archived"
            shot.version += 1
            archived.append(shot.id)
    for scene in scenes:
        if scene.id not in kept_scenes and scene.status != "archived":
            scene.status = "archived"
            scene.version += 1
    content.pop("reopen")
    content["published_episode_id"] = episode.id
    content["draft"] = draft.model_dump(mode="json")
    content["review_history"][-1]["outcome"] = "republished"
    content["last_republish"] = {
        "changed_shots": changed,
        "added_shots": added,
        "archived_shots": archived,
    }
    if content.get("preproduction"):
        content["preproduction"] = {
            **content["preproduction"],
            "review_status": "historical_after_republish",
        }
    db.execute(
        update(Script)
        .where(Script.id == script.id)
        .values(
            structured_content=content,
            episode_duration=round(
                sum(h.duration for s in draft.scenes for h in s.shots)
            ),
        )
    )
    episode.status = "in_production"
    episode.version += 1
    episode.duration_target = round(
        sum(h.duration for s in draft.scenes for h in s.shots)
    )
    db.commit()
    return {"episode_id": episode.id, **content["last_republish"]}
