from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.providers.video.base import (
    ProviderConfigurationError,
    ProviderRequestRejectedError,
    ProviderResponseError,
)


def test_health_and_provider_configuration_is_visible(client: TestClient):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/livez").json() == {"status": "ok"}
    assert client.get("/api/v1/readyz").json() == {"status": "ready"}

    providers = {
        item["provider"]: item for item in client.get("/api/v1/providers/video").json()
    }
    assert providers["mock"]["configured"] is True
    assert providers["seedance"]["configured"] is True
    assert providers["kling"]["configured"] is False
    assert providers["seedance"]["metadata"]["transport"] == "http"
    assert providers["seedance"]["metadata"]["model"] == "doubao-seedance-2-0-260128"

    account = client.get("/api/v1/providers/video/seedance/account")
    assert account.status_code == 200
    assert account.json()["authenticated"] is True
    assert account.json()["account"]["quota_verified"] is False


def test_character_identity_and_outfit_are_separate(
    client: TestClient, project: dict, character: dict
):
    outfit = client.post(
        f"/api/v1/characters/{character['id']}/outfits",
        json={
            "id": "OUTFIT_EVENING_001",
            "name": "Evening",
            "components": ["black dress"],
        },
    )
    assert outfit.status_code == 201
    assert outfit.json()["character_id"] == character["id"]
    assert "components" not in character
    assert len(client.get(f"/api/v1/characters/{character['id']}/outfits").json()) == 1


def test_project_asset_upload_is_private_to_media_root(
    client: TestClient, project: dict
):
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"asset_kind": "shot_keyframe"},
        files={"file": ("approved frame.png", b"fake-png", "image/png")},
    )
    assert response.status_code == 200
    asset = response.json()
    assert asset["path"].startswith("assets/projects/PRJ_REVENGE_001/shot_keyframe/")
    assert asset["url"].startswith("/media/projects/PRJ_REVENGE_001/shot_keyframe/")
    assert client.get(asset["url"]).content == b"fake-png"


def test_storyboard_and_mock_generation_history(client: TestClient, shot: dict):
    storyboard = client.get("/api/v1/episodes/EP001/storyboard")
    assert storyboard.status_code == 200
    assert storyboard.json()["scenes"][0]["shots"][0]["id"] == shot["id"]

    created = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
            "references": ["characters/ALICE_001/face/front.png"],
            "resolution": "1080p",
            "aspect_ratio": "9:16",
            "api_parameters": {"test_marker": "contract-only"},
        },
    )
    assert created.status_code == 201
    generation = created.json()
    assert generation["status"] == "submitted"
    assert generation["model"] == "mock-video-v1"
    assert generation["provider_response"]["task_id"] == generation["external_id"]
    assert generation["project_id"] == "PRJ_REVENGE_001"
    assert generation["episode_id"] == "EP001"
    assert generation["scene_id"] == "EP001_SC001"
    assert generation["shot_id"] == "EP001_SC001_SH001"
    assert generation["request_snapshot"]["metadata"]["shot_version"] == 1
    assert (
        generation["request_snapshot"]["metadata"]["provider_configuration"]["model"]
        == "mock-video-v1"
    )

    refreshed = client.post(f"/api/v1/generations/{generation['id']}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "succeeded"
    assert refreshed.json()["output_path"].startswith("mock://")

    history = client.get(
        "/api/v1/generations", params={"shot_id": shot["id"], "provider": "mock"}
    )
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [generation["id"]]


def test_generation_submission_is_confirmed_and_idempotent(
    client: TestClient, shot: dict, monkeypatch
):
    endpoint = f"/api/v1/shots/{shot['id']}/generations"
    provider = client.app.state.video_providers.get("mock")
    original = provider.generate
    calls = []

    async def recording(request):
        calls.append(request)
        return await original(request)

    monkeypatch.setattr(provider, "generate", recording)
    token = str(uuid4())
    payload = {
        "request_token": token,
        "confirmed": True,
        "provider": "mock",
        "prompt": "同一次确认只提交一次",
    }
    first = client.post(endpoint, json=payload)
    replay = client.post(endpoint, json=payload)
    conflict = client.post(endpoint, json={**payload, "prompt": "不同参数"})

    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert first.json()["request_token"] == token
    assert len(calls) == 1
    assert conflict.status_code == 409
    assert len(calls) == 1


def test_generation_requires_explicit_confirmation(client: TestClient, shot: dict):
    endpoint = f"/api/v1/shots/{shot['id']}/generations"
    token = str(uuid4())

    assert client.post(endpoint, json={"provider": "mock"}).status_code == 422
    assert (
        client.post(
            endpoint,
            json={
                "request_token": token,
                "confirmed": False,
                "provider": "mock",
            },
        ).status_code
        == 422
    )
    assert client.get("/api/v1/generations").json() == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderConfigurationError("missing key"), "configuration_required"),
        (ProviderRequestRejectedError("invalid input"), "failed"),
        (ProviderResponseError("timeout after submit"), "unknown"),
    ],
)
def test_generation_records_definitive_and_ambiguous_provider_errors(
    client: TestClient, shot: dict, monkeypatch, error, expected
):
    provider = client.app.state.video_providers.get("mock")

    async def fail(_request):
        raise error

    monkeypatch.setattr(provider, "generate", fail)
    response = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == expected


def test_seedance_ark_submission_and_refresh(
    client: TestClient, shot: dict, tmp_path: Path
):
    first_frame = client.app.state.settings.data_root / "assets" / "approved-keyframe.png"
    first_frame.write_bytes(b"test image")
    created = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "seedance",
            "first_frame": str(first_frame),
            "resolution": "720p",
        },
    )
    assert created.status_code == 201
    generation = created.json()
    assert generation["status"] == "submitted"
    assert generation["external_id"] == "fake-seedance-submit"
    assert generation["model"] == "doubao-seedance-2-0-260128"
    assert generation["provider_response"] == {"id": "fake-seedance-submit"}

    refreshed = client.post(f"/api/v1/generations/{generation['id']}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "succeeded"
    assert refreshed.json()["output_path"] == "https://example.invalid/generated.mp4"


def test_human_edits_use_optimistic_versioning(client: TestClient, shot: dict):
    updated = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={
            "expected_version": 1,
            "duration": 4,
            "camera_movement": "static",
            "status": "approved_for_generation",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["duration"] == 4

    stale_edit = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": 1, "duration": 8},
    )
    assert stale_edit.status_code == 409
    assert stale_edit.json()["detail"]["current_version"] == 2


def test_outfit_cannot_be_attached_to_another_character(
    client: TestClient, project: dict, character: dict
):
    second = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"id": "CHAR_BOB_001", "name": "Bob"},
    ).json()
    outfit = client.post(
        f"/api/v1/characters/{character['id']}/outfits",
        json={"id": "OUTFIT_ALICE_001", "name": "Alice only"},
    ).json()
    episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes",
        json={"id": "EP_BAD", "title": "Validation"},
    ).json()
    scene = client.post(
        f"/api/v1/episodes/{episode['id']}/scenes",
        json={"id": "SC_BAD", "order_index": 1, "character_ids": [second["id"]]},
    ).json()
    response = client.post(
        f"/api/v1/scenes/{scene['id']}/shots",
        json={
            "order_index": 1,
            "duration": 3,
            "characters": [{"character_id": second["id"], "outfit_id": outfit["id"]}],
            "shot_type": "close_up",
            "visual_description": "Bob looks at camera.",
        },
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]
