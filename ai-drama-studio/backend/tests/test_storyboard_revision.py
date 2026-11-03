import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_planning import draft

from app.models import Shot
from app.services.planning import StoryDraft, generate_storyboard
from app.services.storyboard_revision import production_editable


def pending(client, project):
    response = client.post(
        f"/api/v1/projects/{project['id']}/scripts",
        json={
            "title": "外部改稿",
            "source_idea": "原始故事简介",
            "structured_content": {
                "draft": draft(),
                "screenplay_approved": True,
            },
        },
    )
    assert response.status_code == 201, response.text
    return f"/api/v1/scripts/{response.json()['id']}"


def published(client, project):
    endpoint = pending(client, project)
    result = client.post(endpoint + "/publish-storyboard", json={"expected_version": 1})
    assert result.status_code == 200, result.text
    board_url = f"/api/v1/episodes/{result.json()['episode_id']}/storyboard"
    return endpoint, board_url, client.get(board_url).json()


def reopen(client, endpoint):
    script = client.get(endpoint).json()
    result = client.post(
        endpoint + "/reopen-storyboard",
        json={"expected_version": script["version"], "confirmed": True},
    )
    assert result.status_code == 200, result.text
    return result.json()


def save_and_publish(client, endpoint, script, data):
    saved = client.put(
        endpoint + "/review",
        json={
            "expected_version": script["version"],
            "draft": data,
            "screenplay_approved": True,
        },
    )
    assert saved.status_code == 200, saved.text
    result = client.post(
        endpoint + "/publish-storyboard",
        json={"expected_version": saved.json()["version"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_reopen_live_content_unchanged_republish_and_cancel(client, project):
    endpoint, board_url, board = published(client, project)
    shot = board["scenes"][0]["shots"][0]
    shot_url = f"/api/v1/shots/{shot['id']}"
    live = client.patch(
        shot_url,
        json={
            "expected_version": shot["version"],
            "generation_prompt": "沈青不动嘴；电话男声；无配乐",
        },
    ).json()
    opened = reopen(client, endpoint)
    assert (
        opened["structured_content"]["draft"]["scenes"][0]["shots"][0][
            "generation_prompt"
        ]
        == live["generation_prompt"]
    )
    assert (
        client.get(board_url).json()["scenes"][0]["shots"][0]["version"]
        == live["version"]
    )
    assert client.get(board_url).json()["status"] == "revising"
    assert (
        client.patch(
            shot_url, json={"expected_version": live["version"], "dialogue": "不可覆盖"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            shot_url + "/generations",
            json={
                "request_token": str(uuid4()),
                "confirmed": True,
                "provider": "mock",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/scenes/{shot['scene_id']}/shots",
            json=draft()["scenes"][0]["shots"][0],
        ).status_code
        == 409
    )
    result = save_and_publish(
        client, endpoint, opened, opened["structured_content"]["draft"]
    )
    assert result["changed_shots"] == [] and result["added_shots"] == []
    assert (
        client.get(board_url).json()["scenes"][0]["shots"][0]["version"]
        == live["version"]
    )
    original_board = client.get(board_url).json()
    opened = reopen(client, endpoint)
    cancelled = client.post(
        endpoint + "/cancel-revision",
        json={"expected_version": opened["version"], "confirmed": True},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert (
        cancelled.json()["structured_content"]["review_history"][-1]["outcome"]
        == "cancelled"
    )
    restored = client.get(board_url).json()
    assert (
        restored["scenes"] == original_board["scenes"]
        and restored["status"] == "in_production"
    )


def test_changed_added_removed_shots_preserve_media_and_ids(client, project):
    endpoint, board_url, board = published(client, project)
    shot = board["scenes"][0]["shots"][0]
    generation = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
        },
    ).json()
    opened = reopen(client, endpoint)
    data = opened["structured_content"]["draft"]
    data["scenes"][0]["shots"][0]["dialogue"] = "新的对白"
    data["scenes"][0]["shots"].append(draft()["scenes"][0]["shots"][0])
    result = save_and_publish(client, endpoint, opened, data)
    assert result["changed_shots"] == [shot["id"]] and len(result["added_shots"]) == 1
    assert client.get(board_url).json()["scenes"][0]["id"] == board["scenes"][0]["id"]
    history = client.get("/api/v1/generations").json()[0]
    assert (
        history["id"] == generation["id"]
        and history["review_warning"] == "镜头已修改，旧素材需复核"
    )
    opened = reopen(client, endpoint)
    data = opened["structured_content"]["draft"]
    data["scenes"][0]["shots"].pop(0)
    result = save_and_publish(client, endpoint, opened, data)
    assert result["archived_shots"] == [shot["id"]]
    active = client.get(board_url).json()["scenes"][0]["shots"]
    assert len(active) == 1 and active[0]["id"] != shot["id"]
    with client.app.state.database.session_factory() as db:
        assert db.get(Shot, shot["id"]).status == "archived"
    history = client.get("/api/v1/generations").json()[0]
    assert history["id"] == generation["id"] and "移出" in history["review_warning"]
    assert (
        client.post(
            f"/api/v1/shots/{shot['id']}/generations",
            json={
                "request_token": str(uuid4()),
                "confirmed": True,
                "provider": "mock",
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "kind", ["foreign_scene", "foreign_shot", "duplicate_scene", "duplicate_shot"]
)
def test_revision_rejects_invalid_stable_ids(client, project, kind):
    endpoint, _, _ = published(client, project)
    opened = reopen(client, endpoint)
    data = opened["structured_content"]["draft"]
    if kind == "foreign_scene":
        data["scenes"][0]["id"] = "FOREIGN"
    elif kind == "foreign_shot":
        data["scenes"][0]["shots"][0]["id"] = "FOREIGN"
    elif kind == "duplicate_scene":
        data["scenes"].append(deepcopy(data["scenes"][0]))
    else:
        data["scenes"][0]["shots"].append(deepcopy(data["scenes"][0]["shots"][0]))
    assert (
        client.put(
            endpoint + "/review",
            json={"expected_version": opened["version"], "draft": data},
        ).status_code
        == 422
    )


def test_revision_rejects_concurrent_production_change(client, project):
    endpoint, board_url, board = published(client, project)
    opened = reopen(client, endpoint)
    saved = client.put(
        endpoint + "/review",
        json={
            "expected_version": opened["version"],
            "draft": opened["structured_content"]["draft"],
            "screenplay_approved": True,
        },
    ).json()
    with client.app.state.database.session_factory() as db:
        shot = db.get(Shot, board["scenes"][0]["shots"][0]["id"])
        shot.dialogue = "并发修改"
        shot.version += 1
        db.commit()
    assert (
        client.post(
            endpoint + "/publish-storyboard",
            json={"expected_version": saved["version"]},
        ).status_code
        == 409
    )
    assert (
        client.get(board_url).json()["scenes"][0]["shots"][0]["dialogue"] == "并发修改"
    )
    assert (
        client.post(
            endpoint + "/cancel-revision",
            json={"expected_version": saved["version"], "confirmed": True},
        ).status_code
        == 200
    )


def test_generate_from_pasted_screenplay_not_synopsis(
    client, project, character, monkeypatch
):
    endpoint = pending(client, project)
    screenplay = "  外部模型改好的完整剧本\n沈青：我想注销。\n零七：明天可以吗？\n"

    async def fake(text, script, characters):
        assert text == screenplay and text != script.source_idea
        assert characters[0]["character_id"] == character["id"]
        return StoryDraft.model_validate(draft())

    monkeypatch.setattr("app.api.planning.generate_storyboard", fake)
    payload = {"expected_version": 1, "screenplay": screenplay, "confirmed": True}
    response = client.post(endpoint + "/generate-storyboard", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["structured_content"]["draft"]["screenplay"] == screenplay
    assert response.json()["structured_content"]["screenplay_approved"] is False
    assert client.get(f"/api/v1/projects/{project['id']}/episodes").json() == []
    assert client.get("/api/v1/generations").json() == []
    assert (
        client.post(endpoint + "/generate-storyboard", json=payload).status_code == 409
    )


def test_storyboard_failure_and_confirmation_preserve_original(
    client, project, monkeypatch
):
    endpoint = pending(client, project)
    original = client.get(endpoint).json()
    calls = []

    async def fail(*args):
        calls.append(1)
        raise ValueError("文本模型未配置")

    monkeypatch.setattr("app.api.planning.generate_storyboard", fail)
    payload = {"expected_version": 1, "screenplay": "外部修改过的完整剧本"}
    assert (
        client.post(endpoint + "/generate-storyboard", json=payload).status_code == 422
    )
    assert calls == []
    assert (
        client.post(
            endpoint + "/generate-storyboard", json={**payload, "confirmed": True}
        ).status_code
        == 502
    )
    assert client.get(endpoint).json() == original
    client.post(endpoint + "/publish-storyboard", json={"expected_version": 1})
    assert (
        client.post(
            endpoint + "/generate-storyboard",
            json={**payload, "expected_version": 2, "confirmed": True},
        ).status_code
        == 409
    )
    assert calls == [1]


def test_storyboard_transport_only_scenes_and_exact_script(monkeypatch):
    monkeypatch.setenv("PLANNING_API_URL", "https://model.example/chat/completions")
    monkeypatch.setenv("PLANNING_API_KEY", "test")
    monkeypatch.setenv("PLANNING_MODEL", "text-model")
    screenplay = "用户的原文，不能改动。\n"
    scenes = draft()["scenes"]

    def respond(request):
        body = json.loads(request.content)
        content = json.loads(body["messages"][1]["content"])
        assert content["screenplay"] == screenplay
        assert content["current_scenes"] == scenes
        assert "不生成背景配乐" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"scenes": scenes})}}]
            },
        )

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.planning.httpx.AsyncClient",
        lambda **kw: async_client(transport=httpx.MockTransport(respond), **kw),
    )
    script = SimpleNamespace(
        title="标题",
        episode_duration=180,
        structured_content={
            "reopen": {"episode_id": "EP"},
            "draft": {"scenes": scenes},
        },
    )
    result = asyncio.run(generate_storyboard(screenplay, script, []))
    assert result.screenplay == screenplay


def test_production_guard():
    with pytest.raises(ValueError, match="修订"):
        production_editable(SimpleNamespace(status="revising"))
    with pytest.raises(ValueError, match="移出"):
        production_editable(
            SimpleNamespace(status="in_production"), SimpleNamespace(status="archived")
        )
