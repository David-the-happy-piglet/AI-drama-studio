import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.providers.video.base import (
    ProviderConfigurationError,
    ProviderResponseError,
    VideoGenerationRequest,
)
from app.providers.video.minimax import MiniMaxProvider


def request(**changes):
    values = {
        "generation_id": "GEN_MM",
        "prompt": "雨夜空镜 [固定]",
        "model": "MiniMax-Hailuo-2.3",
        "generation_mode": "text_to_video",
        "duration": 6,
        "resolution": "768P",
        "api_parameters": {"token_plan_confirmed": True, "material_purpose": "broll"},
    }
    return VideoGenerationRequest(**(values | changes))


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_TOKEN_PLAN_KEY", "subscription-test")
    monkeypatch.delenv("MINIMAX_VIDEO_BASE_URL", raising=False)
    return MiniMaxProvider(tmp_path)


def test_protocol_submit_poll_retrieve_download(provider, tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.host == "cdn.example.invalid":
            assert "authorization" not in req.headers
            return httpx.Response(200, content=b"test-video")
        assert req.headers["authorization"] == "Bearer subscription-test"
        raw = {"base_resp": {"status_code": 0}}
        if req.method == "POST":
            assert req.url.path == "/v1/video_generation"
            body = json.loads(req.content)
            assert body == {
                "model": "MiniMax-Hailuo-2.3",
                "prompt": "雨夜空镜 [固定]",
                "duration": 6,
                "resolution": "768P",
                "prompt_optimizer": False,
            }
            raw["task_id"] = "task-mm"
        elif req.url.path == "/v1/query/video_generation":
            assert req.url.params["task_id"] == "task-mm"
            raw.update(status="Success", file_id="file-mm")
        else:
            assert req.url.path == "/v1/files/retrieve"
            assert req.url.params["file_id"] == "file-mm"
            raw["file"] = {"download_url": "https://cdn.example.invalid/video.mp4"}
        return httpx.Response(200, json=raw)

    provider.transport = httpx.MockTransport(handler)
    assert asyncio.run(provider.generate(request())).external_id == "task-mm"
    result = asyncio.run(provider.download_result("task-mm", tmp_path / "result.mp4"))
    assert result.read_bytes() == b"test-video"
    assert len(calls) == 4
    assert provider.estimate_cost(request()).amount is None


@pytest.mark.parametrize(
    "changes",
    [
        {"duration": 10, "resolution": "1080P"},
        {"duration": 5},
        {"resolution": "720p"},
        {"model": "MiniMax-H3"},
        {"aspect_ratio": "9:16"},
        {"generation_mode": "first_last_frame"},
        {"generation_mode": "image_to_video"},
        {"last_frame": "https://example.com/last.png"},
        {"reference_audios": ["https://example.com/audio.mp3"]},
        {"seed": 1},
        {"prompt": "a" * 2001},
        {"first_frame": "https://example.com/first.png"},
    ],
)
def test_invalid_specs_are_rejected_without_http(provider, changes):
    with pytest.raises(ProviderResponseError):
        provider.build_request(request(**changes))


@pytest.mark.parametrize(
    "duration,resolution", [(6, "768P"), (10, "768P"), (6, "1080P")]
)
def test_supported_specs(provider, duration, resolution):
    assert (
        provider.build_request(request(duration=duration, resolution=resolution))[
            "duration"
        ]
        == duration
    )


def test_confirmation_and_no_payg_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("MINIMAX_TOKEN_PLAN_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_TOKEN_PLAN_ENV_FILE", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "payg-must-not-be-used")
    provider = MiniMaxProvider(tmp_path)
    assert not provider.capabilities().configured
    assert "payg-must-not-be-used" not in provider.capabilities().model_dump_json()
    with pytest.raises(ProviderConfigurationError):
        provider.build_request(request(api_parameters={}))
    with pytest.raises(ProviderConfigurationError):
        asyncio.run(provider.generate(request()))


def test_explicit_legacy_subscription_file(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("MINIMAX_API_KEY=legacy-subscription\nUNRELATED_SECRET=private\n")
    monkeypatch.delenv("MINIMAX_TOKEN_PLAN_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_TOKEN_PLAN_ENV_FILE", str(source))
    provider = MiniMaxProvider(tmp_path)
    assert provider.capabilities().configured
    assert provider._key == "legacy-subscription"
    assert "private" not in provider.capabilities().model_dump_json()
    monkeypatch.setenv("MINIMAX_TOKEN_PLAN_KEY", "dedicated")
    assert MiniMaxProvider(tmp_path)._key == "dedicated"


def test_first_frame_encoding_and_path_guard(provider, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "frame.png").write_bytes(b"test")
    body = provider.build_request(
        request(generation_mode="image_to_video", first_frame="assets/frame.png")
    )
    assert body["first_frame_image"] == "data:image/png;base64,dGVzdA=="
    with pytest.raises(ProviderResponseError):
        provider.image_input("../private.png")


@pytest.mark.parametrize(
    "remote,local",
    [
        ("Preparing", "submitted"),
        ("Queueing", "queued"),
        ("Processing", "processing"),
        ("Fail", "failed"),
    ],
)
def test_status_mapping(provider, remote, local):
    provider.transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, json={"base_resp": {"status_code": 0}, "status": remote}
        )
    )
    assert asyncio.run(provider.get_status("task")).status == local


@pytest.mark.parametrize("failure", ["quota", "timeout", "malformed"])
def test_errors_never_retry_or_switch_key(provider, failure):
    calls = []

    def handler(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(
            200, json={"base_resp": {"status_code": 1008}} if failure == "quota" else []
        )

    provider.transport = httpx.MockTransport(handler)
    with pytest.raises(ProviderResponseError):
        asyncio.run(provider.generate(request()))
    assert len(calls) == 1


def test_studio_generation_round_trip(client, shot, provider):
    client.app.state.video_providers._providers["minimax"] = provider

    def handler(req):
        if req.method == "POST":
            return httpx.Response(
                200, json={"base_resp": {"status_code": 0}, "task_id": "integration-mm"}
            )
        return httpx.Response(
            200, json={"base_resp": {"status_code": 0}, "status": "Processing"}
        )

    provider.transport = httpx.MockTransport(handler)
    response = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "minimax",
            "model": "MiniMax-Hailuo-2.3",
            "duration": 6,
            "resolution": "768P",
            "first_frame": "https://example.invalid/frame.png",
            "api_parameters": {
                "token_plan_confirmed": True,
                "material_purpose": "test",
            },
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "submitted"
    assert data["provider"] == "minimax"
    assert data["api_parameters"]["material_purpose"] == "test"
    refreshed = client.post(f"/api/v1/generations/{data['id']}/refresh").json()
    assert refreshed["status"] == "processing"
    assert refreshed["external_id"] == "integration-mm"
