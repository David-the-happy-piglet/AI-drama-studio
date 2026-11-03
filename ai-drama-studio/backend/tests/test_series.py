import pytest
from sqlalchemy import func, select

from app.models import Character, Episode, Project, Script, Shot
from app.services.series import consolidate_draft_series


def draft():
    return {
        "screenplay": "主角推开门，发现未来的自己。",
        "scenes": [
            {
                "location": "门口",
                "shots": [
                    {
                        "order_index": 1,
                        "duration": 5,
                        "shot_type": "中景",
                        "visual_description": "主角推开门",
                        "generation_mode": "image_to_video",
                    }
                ],
            }
        ],
    }


def test_consolidation_is_draft_only_preserves_versions_and_is_idempotent(client):
    sources = []
    for number in range(1, 3):
        project = client.post(
            "/api/v1/projects", json={"name": f"Story {number}"}
        ).json()
        char = client.post(
            f"/api/v1/projects/{project['id']}/characters", json={"name": "Hero"}
        ).json()
        scripts = []
        for version in range(1, 3):
            scripts.append(
                client.post(
                    f"/api/v1/projects/{project['id']}/scripts",
                    json={
                        "title": f"Version {version}",
                        "source_idea": "科幻故事设定",
                        "character_ids": [char["id"]],
                        "structured_content": {
                            "draft": draft(),
                            "screenplay_approved": False,
                            "original_marker": version,
                        },
                    },
                ).json()
            )
        sources.append(
            {
                "project_id": project["id"],
                "episode_id": f"SERIES_EP_{number}",
                "script_id": scripts[-1]["id"],
                "title": f"Story {number}",
            }
        )
    with client.app.state.database.session_factory() as db:
        result = consolidate_draft_series(db, "SERIES", "科幻单元剧", sources)
        db.commit()
        assert result["scripts"] == 4 and result["characters"] == 2
        assert consolidate_draft_series(db, "SERIES", "科幻单元剧", sources)[
            "already_applied"
        ]
        assert db.scalar(select(func.count()).select_from(Shot)) == 0
        assert all(c.project_id == "SERIES" for c in db.scalars(select(Character)))
        for script in db.scalars(select(Script)):
            assert (
                script.status == "draft"
                and script.structured_content["draft"] == draft()
            )
            assert not script.structured_content["screenplay_approved"]
            assert "published_episode_id" not in script.structured_content
        for episode in db.scalars(select(Episode)):
            assert episode.status == "draft" and not episode.scenes
    assert len(client.get("/api/v1/projects").json()) == 1
    assert len(client.get("/api/v1/projects?include_archived=true").json()) == 3
    scripts = client.get("/api/v1/projects/SERIES/scripts").json()
    current = next(s for s in scripts if s["id"] == sources[0]["script_id"])
    saved = client.put(
        f"/api/v1/scripts/{current['id']}/review",
        json={
            "expected_version": current["version"],
            "draft": draft(),
            "screenplay_approved": True,
        },
    ).json()
    assert saved["structured_content"]["episode_id"] == "SERIES_EP_1"
    assert saved["structured_content"]["original_marker"] == 2
    response = client.post(
        f"/api/v1/scripts/{saved['id']}/publish-storyboard",
        json={"expected_version": saved["version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["episode_id"] == "SERIES_EP_1"
    assert len(client.get("/api/v1/projects/SERIES/episodes").json()) == 2
    assert (
        len(client.get("/api/v1/episodes/SERIES_EP_1/storyboard").json()["scenes"]) == 1
    )
    other = next(
        s
        for s in scripts
        if s["structured_content"]["episode_id"] == "SERIES_EP_1"
        and s["id"] != saved["id"]
    )
    approved = client.put(
        f"/api/v1/scripts/{other['id']}/review",
        json={
            "expected_version": other["version"],
            "draft": draft(),
            "screenplay_approved": True,
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/scripts/{other['id']}/publish-storyboard",
            json={"expected_version": approved["version"]},
        ).status_code
        == 409
    )


def test_new_draft_is_associated_with_selected_episode_and_rejects_cross_project(
    client, project
):
    episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes", json={"title": "第一集"}
    ).json()
    result = client.post(
        f"/api/v1/projects/{project['id']}/scripts?episode_id={episode['id']}",
        json={
            "title": "本集初稿",
            "source_idea": "一个短篇故事",
            "structured_content": {"draft": draft()},
        },
    )
    assert result.status_code == 201, result.text
    assert result.json()["structured_content"]["episode_id"] == episode["id"]
    assert (
        client.get(f"/api/v1/projects/{project['id']}/episodes").json()[0]["script_id"]
        == result.json()["id"]
    )
    another = client.post("/api/v1/projects", json={"name": "另一剧集"}).json()
    assert (
        client.post(
            f"/api/v1/projects/{another['id']}/scripts?episode_id={episode['id']}",
            json={"title": "无效", "source_idea": "不应跨项目绑定"},
        ).status_code
        == 422
    )


def test_consolidation_rejects_before_mutating_any_source(client, project):
    with client.app.state.database.session_factory() as db:
        with pytest.raises(ValueError):
            consolidate_draft_series(
                db,
                "UNSAFE",
                "Bad",
                [
                    {
                        "project_id": project["id"],
                        "episode_id": "E",
                        "script_id": "missing",
                        "title": "Bad",
                    }
                ],
            )
        assert db.get(Project, "UNSAFE") is None
        assert db.get(Project, project["id"]).status == "active"
