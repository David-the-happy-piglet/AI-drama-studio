import asyncio
import json

import httpx
import pytest

from app.providers.video.base import ProviderResponseError, VideoGenerationRequest
from app.providers.video.kling import KlingProvider


def test_kling_new_api_submission_and_task_query(tmp_path, monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "test-not-real")
    monkeypatch.setenv("KLING_BASE_URL", "https://api-singapore.klingai.com")

    def handler(request):
        assert request.headers["authorization"] == "Bearer test-not-real"
        if request.method == "POST":
            assert request.url.path == "/text-to-video/kling-3.0"
            body = json.loads(request.content)
            assert body["settings"] == {
                "duration": 5,
                "resolution": "1080p",
                "audio": "native",
                "multi_shot": False,
                "aspect_ratio": "9:16",
            }
            assert body["options"]["external_task_id"] == "GEN_TEST"
            return httpx.Response(
                200, json={"code": 0, "data": {"id": "task-1", "status": "submitted"}}
            )
        assert request.url.path == "/tasks"
        assert request.url.params["task_ids"] == "task-1"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "id": "task-1",
                        "status": "succeeded",
                        "outputs": [
                            {"type": "video", "url": "https://example.invalid/clip.mp4"}
                        ],
                    }
                ],
            },
        )

    provider = KlingProvider(tmp_path, transport=httpx.MockTransport(handler))
    request = VideoGenerationRequest(
        generation_id="GEN_TEST",
        prompt="一封信",
        generation_mode="text_to_video",
        duration=5,
        resolution="1080p",
        aspect_ratio="9:16",
        api_parameters={"audio": "native"},
    )
    assert asyncio.run(provider.generate(request)).external_id == "task-1"
    assert asyncio.run(provider.get_status("task-1")).output_url.endswith("clip.mp4")


def test_kling_frames_are_encoded_and_invalid_modes_rejected(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "first.png").write_bytes(b"fixture-image")
    provider = KlingProvider(tmp_path, data_root=tmp_path)
    request = VideoGenerationRequest(
        generation_id="G",
        prompt="@主角转头",
        first_frame="assets/first.png",
        duration=5,
        api_parameters={"elements": [{"element_id": "el-1", "id": "主角"}]},
    )
    path, body = provider.build_request(request)
    assert path == "/image-to-video/kling-3.0"
    assert body["contents"][1]["url"] == "Zml4dHVyZS1pbWFnZQ=="
    assert body["contents"][2]["element_id"] == "el-1"
    with pytest.raises(ProviderResponseError):
        provider.build_request(request.model_copy(update={"duration": 30}))
    with pytest.raises(ProviderResponseError):
        provider.build_request(
            request.model_copy(update={"generation_mode": "reference_to_video"})
        )


@pytest.mark.parametrize("mode", ["text_to_video", "image_to_video"])
def test_turbo_protocol_and_restrictions(tmp_path, mode):
    provider = KlingProvider(tmp_path)
    request = VideoGenerationRequest(
        generation_id="TURBO_TEST",
        model="kling-3.0-turbo",
        prompt="风吹树叶",
        generation_mode=mode,
        first_frame="https://example.com/first.png",
        duration=5,
        resolution="720p",
        api_parameters={"audio": "native"},
    )
    path, body = provider.build_request(request)
    assert (
        path
        == f"/{'text-to-video' if mode == 'text_to_video' else 'image-to-video'}/kling-3.0-turbo"
    )
    assert "audio" not in body["settings"]
    assert "multi_shot" not in body["settings"]
    for changes in [
        {"last_frame": "https://example.com/last.png"},
        {"resolution": "4k"},
        {"api_parameters": {"audio": "off"}},
        {"generation_mode": "first_last_frame"},
    ]:
        with pytest.raises(ProviderResponseError):
            provider.build_request(request.model_copy(update=changes))


@pytest.mark.parametrize(
    "mode",
    ["text_to_video", "image_to_video", "first_last_frame", "reference_to_video"],
)
def test_omni_input_routing(tmp_path, mode):
    provider = KlingProvider(tmp_path)
    request = VideoGenerationRequest(
        generation_id="OMNI_TEST",
        model="kling-3.0-omni",
        prompt="@image_1 的角色转头",
        generation_mode=mode,
        first_frame="https://example.com/first.png",
        last_frame="https://example.com/last.png",
        reference_images=["https://example.com/ref.png"],
        duration=5,
    )
    path, body = provider.build_request(request)
    assert path == "/omni-video/kling-3.0-omni"
    types = [item["type"] for item in body["contents"]]
    assert (
        types
        == {
            "text_to_video": ["prompt"],
            "image_to_video": ["prompt", "first_frame", "last_frame"],
            "first_last_frame": ["prompt", "first_frame", "last_frame"],
            "reference_to_video": ["prompt", "refer_image"],
        }[mode]
    )
    if mode == "reference_to_video":
        assert body["contents"][1]["id"] == "image_1"
    with pytest.raises(ProviderResponseError):
        provider.build_request(
            request.model_copy(
                update={"reference_videos": ["https://example.com/video.mp4"]}
            )
        )


def test_omni_reference_budget(tmp_path):
    provider = KlingProvider(tmp_path)
    request = VideoGenerationRequest(
        generation_id="LIMIT",
        model="kling-3.0-omni",
        prompt="人物走路",
        generation_mode="reference_to_video",
        reference_images=["https://example.com/image.png"] * 5,
    )
    with pytest.raises(ProviderResponseError, match="最多 4"):
        provider.build_request(request)
