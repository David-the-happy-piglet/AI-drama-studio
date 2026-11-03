"""Consolidate explicitly selected, unproduced story projects into draft episodes."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Character, Episode, Generation, Project, Script


def consolidate_draft_series(
    db: Session, target_id: str, name: str, sources: list[dict]
) -> dict:
    existing = db.get(Project, target_id)
    if existing:
        expected_sources = [item["project_id"] for item in sources]
        if (
            existing.settings_json.get("consolidated_source_projects")
            != expected_sources
        ):
            raise ValueError("目标项目已存在且不是本次整理结果，停止避免覆盖")
        episodes = list(
            db.scalars(select(Episode).where(Episode.project_id == target_id)).all()
        )
        if {item.id for item in episodes} != {item["episode_id"] for item in sources}:
            raise ValueError("整理结果已变化，请人工检查，不自动重复执行")
        return {
            "project_id": target_id,
            "already_applied": True,
            "episodes": len(episodes),
        }
    if not sources or len({item["project_id"] for item in sources}) != len(sources):
        raise ValueError("来源项目必须非空且不重复")
    validated = []
    for source in sources:
        project = db.get(Project, source["project_id"])
        if not project or project.status != "active":
            raise ValueError(f"来源项目不存在或不活跃：{source['project_id']}")
        if db.scalar(
            select(Episode.id).where(Episode.project_id == project.id)
        ) or db.scalar(
            select(Generation.id).where(Generation.project_id == project.id)
        ):
            raise ValueError("来源已有制作记录，需要单独迁移，不能套用草稿整理")
        if db.get(Episode, source["episode_id"]):
            raise ValueError("分集 ID 已存在")
        scripts = list(
            db.scalars(select(Script).where(Script.project_id == project.id)).all()
        )
        current = next(
            (item for item in scripts if item.id == source["script_id"]), None
        )
        if not current or any(
            item.status != "draft"
            or item.structured_content.get("published_episode_id")
            or item.structured_content.get("screenplay_approved")
            for item in scripts
        ):
            raise ValueError("只允许整理未批准、未发布的草稿，且当前稿必须属于来源项目")
        characters = list(
            db.scalars(
                select(Character).where(Character.project_id == project.id)
            ).all()
        )
        if any(
            item.master_reference_path
            or item.provider_bindings
            or item.reference_assets
            for item in characters
        ):
            raise ValueError("来源已有图片或远端主体，需要单独迁移资产归属")
        validated.append((source, project, scripts, current, characters))
    target = Project(
        id=target_id,
        name=name,
        description="近未来科技与人性寓言单元剧。每集一个独立故事，保留原始三分钟版和对白增强版，全部待审阅。",
        settings_json={
            "project_type": "series",
            "format": "anthology",
            "genre": "科幻寓言 / 科技悬疑",
            "consolidated_source_projects": [item["project_id"] for item in sources],
        },
    )
    db.add(target)
    db.flush()
    character_count = script_count = 0
    for number, (source, project, scripts, current, characters) in enumerate(
        validated, 1
    ):
        for character in characters:
            character.project_id = target_id
        for script in scripts:
            script.project_id = target_id
            script.structured_content = {
                **script.structured_content,
                "episode_id": source["episode_id"],
                "episode_number": number,
                "source_project_id": project.id,
            }
            script.version += 1
        db.add(
            Episode(
                id=source["episode_id"],
                project_id=target_id,
                script_id=current.id,
                episode_number=number,
                title=source["title"],
                duration_target=current.episode_duration,
                summary=current.source_idea,
                status="draft",
            )
        )
        project.status = "archived"
        project.settings_json = {
            **project.settings_json,
            "merged_into_project_id": target_id,
            "merged_into_episode_id": source["episode_id"],
        }
        character_count += len(characters)
        script_count += len(scripts)
    db.flush()
    return {
        "project_id": target_id,
        "already_applied": False,
        "episodes": len(sources),
        "characters": character_count,
        "scripts": script_count,
        "archived_projects": [item["project_id"] for item in sources],
    }
