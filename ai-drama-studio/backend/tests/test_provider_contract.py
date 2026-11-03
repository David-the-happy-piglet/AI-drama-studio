import asyncio
import json

import httpx
import pytest

from app.providers.video.base import (
    ProviderConfigurationError,
    ProviderRequestRejectedError,
    ProviderResponseError,
    VideoGenerationRequest,
)
from app.providers.video.seedance import BASE_URL, DEFAULT_MODEL, SeedanceProvider


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-secret")
    monkeypatch.setenv("ARK_BASE_URL", BASE_URL)
    monkeypatch.setenv("ARK_VIDEO_MODEL", DEFAULT_MODEL)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "media.example":
            return httpx.Response(200, content=b"video-result")
        assert request.headers["authorization"] == "Bearer test-secret"
        if request.method == "POST":
            return httpx.Response(200, json={"id": "cgt-test"})
        if request.url.path.endswith("/cgt-test"):
            return httpx.Response(
                200,
                json={
                    "id": "cgt-test",
                    "status": "succeeded",
                    "model": DEFAULT_MODEL,
                    "content": {"video_url": "https://media.example/result.mp4"},
                },
            )
        return httpx.Response(200, json={"items": [], "total": 0})

    return SeedanceProvider(
        tmp_path, tmp_path, transport=httpx.MockTransport(handler)
    ), calls


def request(**kwargs):
    return VideoGenerationRequest(
        generation_id="test",
        prompt="果茶广告",
        generation_mode=kwargs.pop("generation_mode", "text_to_video"),
        **kwargs,
    )


def test_submit_and_query_use_ark_protocol(provider):
    adapter, calls = provider
    result = asyncio.run(adapter.generate(request(duration=11, aspect_ratio="16:9")))
    assert result.external_id == "cgt-test"
    assert result.model == DEFAULT_MODEL
    assert result.status == "submitted"
    assert str(calls[0].url) == BASE_URL + "/contents/generations/tasks"
    assert json.loads(calls[0].content) == {
        "model": DEFAULT_MODEL,
        "content": [{"type": "text", "text": "果茶广告"}],
        "generate_audio": True,
        "watermark": False,
        "resolution": "720p",
        "ratio": "16:9",
        "duration": 11,
    }
    status = asyncio.run(adapter.get_status(result.external_id))
    assert status.status == "succeeded"
    assert status.output_url == "https://media.example/result.mp4"
    assert "test-secret" not in result.model_dump_json()
    assert "test-secret" not in adapter.capabilities().model_dump_json()


def test_multimodal_example_preserves_order_and_roles(provider):
    adapter, _ = provider
    body = adapter.build_request(
        request(
            generation_mode="reference_to_video",
            duration=11,
            reference_images=["https://example.com/1.jpg", "https://example.com/2.jpg"],
            reference_videos=["https://example.com/1.mp4"],
            reference_audios=["https://example.com/1.mp3"],
            api_parameters={"generate_audio": True, "watermark": False},
        )
    )
    assert [item["role"] for item in body["content"][1:]] == [
        "reference_image",
        "reference_image",
        "reference_video",
        "reference_audio",
    ]
    assert body["content"][3]["video_url"]["url"] == "https://example.com/1.mp4"


def test_local_frames_audio_and_asset_ids(provider, tmp_path):
    adapter, _ = provider
    (tmp_path / "first.png").write_bytes(b"image")
    (tmp_path / "voice.mp3").write_bytes(b"audio")
    frames = adapter.build_request(
        request(
            generation_mode="first_last_frame",
            first_frame="first.png",
            last_frame="asset://last",
        )
    )
    assert frames["content"][1]["image_url"]["url"] == "data:image/png;base64,aW1hZ2U="
    assert frames["content"][1]["role"] == "first_frame"
    assert frames["content"][2]["role"] == "last_frame"
    refs = adapter.build_request(
        request(
            generation_mode="reference_to_video",
            reference_images=["first.png"],
            reference_audios=["voice.mp3"],
        )
    )
    assert refs["content"][2]["audio_url"]["url"] == "data:audio/mp3;base64,YXVkaW8="


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration": 16},
        {"duration": 3},
        {"duration": 5.5},
        {"api_parameters": {"model": "unreviewed-model"}},
        {"api_parameters": {"poll": 30}},
        {"api_parameters": {"generate_audio": "true"}},
        {"resolution": "360p"},
        {"generation_mode": "image_to_video"},
        {
            "generation_mode": "first_last_frame",
            "first_frame": "https://example.com/1.png",
        },
        {
            "generation_mode": "reference_to_video",
            "reference_audios": ["https://example.com/1.mp3"],
        },
        {
            "generation_mode": "reference_to_video",
            "reference_images": ["asset://image"] * 10,
        },
        {"generation_mode": "reference_to_video", "reference_videos": ["local.mp4"]},
        {"generation_mode": "image_to_video", "first_frame": "../private.png"},
        {"reference_images": ["asset://image"]},
    ],
)
def test_invalid_requests_never_submit(provider, kwargs):
    adapter, calls = provider
    with pytest.raises(ProviderRequestRejectedError):
        asyncio.run(adapter.generate(request(**kwargs)))
    assert calls == []


def test_missing_key_blocks_network(tmp_path, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    adapter = SeedanceProvider(tmp_path, tmp_path)
    assert not adapter.capabilities().configured
    with pytest.raises(ProviderConfigurationError, match="ARK_API_KEY"):
        asyncio.run(adapter.generate(request()))


def test_only_official_endpoint_receives_key(provider):
    adapter, calls = provider
    adapter.base_url = "https://another.example/api/v3"
    with pytest.raises(ProviderConfigurationError, match="官方地址"):
        asyncio.run(adapter.generate(request()))
    assert not calls


@pytest.mark.parametrize(
    "code, expected",
    [
        (401, ProviderRequestRejectedError),
        (429, ProviderRequestRejectedError),
        (408, ProviderResponseError),
        (500, ProviderResponseError),
    ],
)
def test_http_error_classification_and_secret_redaction(provider, code, expected):
    adapter, _ = provider
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(
            code, json={"error": {"code": "AccessDenied", "message": "test-secret"}}
        )
    )
    with pytest.raises(expected) as error:
        asyncio.run(adapter.generate(request()))
    assert type(error.value) is expected
    assert "test-secret" not in str(error.value)


def test_timeout_is_ambiguous_and_never_retried(provider):
    adapter, _ = provider
    calls = []

    def timeout(req):
        calls.append(req)
        raise httpx.ReadTimeout("test-secret", request=req)

    adapter.transport = httpx.MockTransport(timeout)
    with pytest.raises(ProviderResponseError) as error:
        asyncio.run(adapter.generate(request()))
    assert not isinstance(error.value, ProviderRequestRejectedError)
    assert len(calls) == 1
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize(
    "remote, local",
    [
        ("queued", "submitted"),
        ("running", "processing"),
        ("failed", "failed"),
        ("expired", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_status_mapping_including_failure_details(provider, remote, local):
    adapter, _ = provider
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "id": "cgt-test",
                "status": remote,
                "error": {"code": "test", "message": "failure reason"}
                if remote == "failed"
                else None,
            },
        )
    )
    status = asyncio.run(adapter.get_status("cgt-test"))
    assert status.status == local
    if remote == "failed":
        assert status.error_message == "failure reason"


def test_account_probe_does_not_generate_or_claim_balance(provider):
    adapter, calls = provider
    info = asyncio.run(adapter.get_account_info())
    assert info["quota_verified"] is False
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.params["page_size"] == "1"


def test_download_never_sends_key_to_media_host(provider, tmp_path):
    adapter, calls = provider
    path = asyncio.run(adapter.download_result("cgt-test", tmp_path / "output.mp4"))
    assert path.read_bytes() == b"video-result"
    assert "authorization" not in calls[-1].headers
    assert not (tmp_path / "output.mp4.part").exists()
