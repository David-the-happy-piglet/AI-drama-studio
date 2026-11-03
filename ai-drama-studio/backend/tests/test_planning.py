import asyncio

import httpx
import pytest

from app.services.planning import PlanningRequest, StoryDraft, generate_draft


def draft():
    return {
        "screenplay": "林夏在雨夜收到一封信。她说：终于找到了。",
        "scenes": [
            {
                "location": "公寓",
                "summary": "收到来信",
                "shots": [
                    {
                        "order_index": 1,
                        "duration": 5,
                        "shot_type": "特写",
                        "visual_description": "林夏拆开信封",
                        "dialogue": "终于找到了。",
                    }
                ],
            }
        ],
    }


def test_generate_review_publish_without_duplicates(client, project, monkeypatch):
    async def fake_generate(payload, characters):
        return StoryDraft.model_validate(draft())

    monkeypatch.setattr("app.api.planning.generate_draft", fake_generate)
    created = client.post(
        f"/api/v1/projects/{project['id']}/story-drafts",
        json={
            "title": "雨夜来信",
            "synopsis": "林夏在雨夜收到一封匿名来信。",
            "duration": 30,
        },
    )
    assert created.status_code == 200, created.text
    script = created.json()
    endpoint = f"/api/v1/scripts/{script['id']}"
    assert client.get(f"/api/v1/projects/{project['id']}/episodes").json() == []
    assert (
        client.post(
            endpoint + "/publish-storyboard", json={"expected_version": 1}
        ).status_code
        == 409
    )
    saved = client.put(
        endpoint + "/review",
        json={"expected_version": 1, "draft": draft(), "screenplay_approved": True},
    )
    assert saved.status_code == 200, saved.text
    assert (
        client.put(
            endpoint + "/review", json={"expected_version": 1, "draft": draft()}
        ).status_code
        == 409
    )
    published = client.post(
        endpoint + "/publish-storyboard", json={"expected_version": 2}
    )
    assert published.status_code == 200, published.text
    assert (
        client.post(
            endpoint + "/publish-storyboard", json={"expected_version": 2}
        ).json()
        == published.json()
    )
    board = client.get(
        f"/api/v1/episodes/{published.json()['episode_id']}/storyboard"
    ).json()
    assert board["scenes"][0]["shots"][0]["status"] == "approved_for_generation"
    assert len(client.get(f"/api/v1/projects/{project['id']}/episodes").json()) == 1
    assert (
        client.put(
            endpoint + "/review", json={"expected_version": 3, "draft": draft()}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            endpoint, json={"expected_version": 3, "structured_content": {}}
        ).status_code
        == 409
    )


def test_invalid_draft_and_unknown_characters_do_not_publish(client, project):
    script = client.post(
        f"/api/v1/projects/{project['id']}/scripts",
        json={"title": "测试", "source_idea": "测试故事"},
    ).json()
    data = draft()
    data["scenes"][0]["shots"][0]["characters"] = [{"character_id": "not-in-project"}]
    response = client.put(
        f"/api/v1/scripts/{script['id']}/review",
        json={"expected_version": 1, "draft": data, "screenplay_approved": True},
    )
    assert response.status_code == 422
    data["scenes"] = []
    assert (
        client.put(
            f"/api/v1/scripts/{script['id']}/review",
            json={"expected_version": 1, "draft": data},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("valid", [True, False])
def test_text_model_transport_and_schema_validation(monkeypatch, valid):
    import json

    monkeypatch.setenv("PLANNING_API_URL", "https://model.example/chat/completions")
    monkeypatch.setenv("PLANNING_API_KEY", "test-key")
    monkeypatch.setenv("PLANNING_MODEL", "test-model")

    def respond(request):
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["response_format"] == {"type": "json_object"}
        content = json.dumps(draft()) if valid else "invalid JSON"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.planning.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    payload = PlanningRequest(title="雨夜来信", synopsis="林夏在雨夜收到一封匿名来信。")
    if valid:
        assert (
            asyncio.run(generate_draft(payload, [])).screenplay == draft()["screenplay"]
        )
    else:
        with pytest.raises(ValueError, match="合法的剧本"):
            asyncio.run(generate_draft(payload, []))
