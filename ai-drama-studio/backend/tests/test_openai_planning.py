import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.planning import (
    StoryDraft,
    generate_storyboard,
    planning_api_key,
    planning_status,
)


@pytest.fixture
def openai_config(monkeypatch):
    for key in ("PLANNING_API_KEY", "OPENAI_API_KEY", "PLANNING_OPENAI_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLANNING_API_URL", "https://api.openai.com/v1/chat/completions")
    monkeypatch.setenv("PLANNING_MODEL", "gpt-5.6-sol")


def test_explicit_key_source_and_precedence(openai_config, monkeypatch, tmp_path):
    source = tmp_path / "test.env"
    source.write_text("OPENAI_API_KEY=test-secret\nUNRELATED_KEY=not-loaded\n")
    monkeypatch.setenv("PLANNING_OPENAI_ENV_FILE", str(source))
    assert planning_api_key() == "test-secret"
    status = planning_status()
    assert status == {"configured": True, "missing": [], "model": "gpt-5.6-sol"}
    assert "secret" not in json.dumps(status)
    monkeypatch.setenv("OPENAI_API_KEY", "direct")
    assert planning_api_key() == "direct"
    monkeypatch.setenv("PLANNING_API_KEY", "dedicated")
    assert planning_api_key() == "dedicated"


@pytest.mark.parametrize(
    "url",
    [
        "https://third-party.test/v1/chat/completions",
        "http://api.openai.com/v1/chat/completions",
        "https://api.openai.com.evil.test/v1/chat/completions",
    ],
)
def test_never_reuse_openai_key_for_other_destinations(openai_config, monkeypatch, url):
    monkeypatch.setenv("OPENAI_API_KEY", "never-leak")
    monkeypatch.setenv("PLANNING_API_URL", url)
    assert planning_api_key() == ""
    assert planning_status()["missing"] == ["PLANNING_API_KEY"]


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_sol_request_and_truncated_output(openai_config, monkeypatch, finish):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    scenes = [
        {
            "location": "室内",
            "shots": [
                {
                    "order_index": 1,
                    "duration": 5,
                    "shot_type": "近景",
                    "visual_description": "她望着手机",
                    "dialogue": "沈青：就一天。",
                    "generation_prompt": "只说：就一天。无配乐。",
                }
            ],
        }
    ]

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.6-sol"
        assert body["max_completion_tokens"] == 32000 and "max_tokens" not in body
        assert body["reasoning_effort"] == "medium"
        assert body["store"] is False and body["service_tier"] == "default"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"content": json.dumps({"scenes": scenes})},
                    }
                ]
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.planning.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    script = SimpleNamespace(title="测试", episode_duration=5, structured_content={})
    if finish == "length":
        with pytest.raises(ValueError):
            asyncio.run(generate_storyboard("原始剧本不能改", script, []))
    else:
        result = asyncio.run(generate_storyboard("原始剧本不能改", script, []))
        assert (
            result.screenplay == "原始剧本不能改"
            and result.scenes[0].shots[0].dialogue == "沈青：就一天。"
        )


def test_autofill_then_manual_tweak_without_publishing(client, project, monkeypatch):
    data = {
        "screenplay": "这是原文剧本",
        "scenes": [
            {
                "location": "屋内",
                "shots": [
                    {
                        "order_index": 1,
                        "duration": 5,
                        "shot_type": "近景",
                        "visual_description": "望向手机",
                        "generation_prompt": "电话声音，人物不动嘴",
                    }
                ],
            }
        ],
    }
    script = client.post(
        f"/api/v1/projects/{project['id']}/scripts",
        json={
            "title": "测试微调",
            "source_idea": "一段完整故事",
            "structured_content": {"draft": data},
        },
    ).json()

    async def fake(*args):
        return StoryDraft.model_validate(data)

    monkeypatch.setattr("app.api.planning.generate_storyboard", fake)
    endpoint = f"/api/v1/scripts/{script['id']}"
    generated = client.post(
        endpoint + "/generate-storyboard",
        json={
            "expected_version": script["version"],
            "screenplay": "外部模型修改的完整剧本",
            "confirmed": True,
        },
    )
    assert generated.status_code == 200, generated.text
    current = generated.json()
    edited = current["structured_content"]["draft"]
    edited["scenes"][0]["shots"][0].update(visual_description="她缓缓抬眼", duration=7)
    saved = client.put(
        endpoint + "/review",
        json={
            "expected_version": current["version"],
            "draft": edited,
            "screenplay_approved": False,
        },
    )
    assert saved.status_code == 200, saved.text
    saved_draft = client.get(endpoint).json()["structured_content"]["draft"]
    assert saved_draft["scenes"][0]["shots"][0]["duration"] == 7
    assert (
        saved_draft["scenes"][0]["shots"][0]["generation_prompt"]
        == "电话声音，人物不动嘴"
    )
    assert saved_draft["screenplay"] == "外部模型修改的完整剧本"
    assert client.get(f"/api/v1/projects/{project['id']}/episodes").json() == []
    assert client.get("/api/v1/generations").json() == []
