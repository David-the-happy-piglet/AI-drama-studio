import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

# Importing app.main constructs the ASGI app. Keep developer credentials out of tests.
os.environ["APP_ENV"] = "test"

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("ARK_API_KEY", "test-ark-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128")

    def ark_response(request):
        assert request.headers["authorization"] == "Bearer test-ark-key"
        if request.method == "POST":
            return httpx.Response(200, json={"id": "fake-seedance-submit"})
        if request.url.path.endswith("/fake-seedance-submit"):
            return httpx.Response(
                200,
                json={
                    "id": "fake-seedance-submit",
                    "status": "succeeded",
                    "model": "doubao-seedance-2-0-260128",
                    "content": {"video_url": "https://example.invalid/generated.mp4"},
                },
            )
        return httpx.Response(200, json={"items": [], "total": 0})

    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_root=tmp_path / "data",
        provider_config_root=project_root / "config" / "providers",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        app.state.video_providers.get("seedance").transport = httpx.MockTransport(
            ark_response
        )
        yield test_client


@pytest.fixture
def project(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"id": "PRJ_REVENGE_001", "name": "都市复仇短剧"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def character(client: TestClient, project: dict) -> dict:
    response = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={
            "id": "CHAR_ALICE_001",
            "name": "Alice",
            "gender": "female",
            "apparent_age": "26",
            "ethnicity": "Chinese",
            "face_description": "oval face, cool fair skin",
            "hairstyle": "shoulder-length straight bob",
            "hair_color": "black",
            "distinctive_features": ["small mole below left eye"],
            "negative_constraints": ["do not remove the mole"],
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def shot(client: TestClient, project: dict, character: dict) -> dict:
    outfit_response = client.post(
        f"/api/v1/characters/{character['id']}/outfits",
        json={
            "id": "OUTFIT_OFFICE_001",
            "name": "Office",
            "components": ["charcoal blazer", "white shirt"],
        },
    )
    assert outfit_response.status_code == 201

    script_response = client.post(
        f"/api/v1/projects/{project['id']}/scripts",
        json={
            "id": "SCRIPT_EP01_001",
            "title": "她回来了",
            "source_idea": "Alice returns to expose a corporate conspiracy.",
            "genre": "urban revenge",
            "character_ids": [character["id"]],
        },
    )
    assert script_response.status_code == 201

    episode_response = client.post(
        f"/api/v1/projects/{project['id']}/episodes",
        json={
            "id": "EP001",
            "script_id": script_response.json()["id"],
            "episode_number": 1,
            "title": "她回来了",
            "duration_target": 90,
        },
    )
    assert episode_response.status_code == 201

    scene_response = client.post(
        f"/api/v1/episodes/{episode_response.json()['id']}/scenes",
        json={
            "id": "EP001_SC001",
            "order_index": 1,
            "location_id": "LOC_OFFICE_001",
            "time_of_day": "night",
            "character_ids": [character["id"]],
            "summary": "Alice confronts the empty boardroom.",
        },
    )
    assert scene_response.status_code == 201

    shot_response = client.post(
        f"/api/v1/scenes/{scene_response.json()['id']}/shots",
        json={
            "id": "EP001_SC001_SH001",
            "order_index": 1,
            "duration": 6,
            "characters": [
                {
                    "character_id": character["id"],
                    "outfit_id": outfit_response.json()["id"],
                }
            ],
            "location_id": "LOC_OFFICE_001",
            "shot_type": "medium_close_up",
            "camera_angle": "eye_level",
            "camera_movement": "slow_push_in",
            "action": "Alice places an evidence folder on the table.",
            "emotion": "controlled anger",
            "visual_description": "Alice stands at the end of a dim modern boardroom.",
            "manual_provider_override": "mock",
            "generation_mode": "image_to_video",
        },
    )
    assert shot_response.status_code == 201
    return shot_response.json()
