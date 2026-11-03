import hashlib
from uuid import uuid4

from test_character_elements import upload
from test_shot_images import base, blank_shot


def batch(client, shot, project):
    context = client.get(base(shot) + "/keyframe-studio").json()
    path = upload(client, project, color="blue")
    sha = hashlib.sha256(
        (client.app.state.settings.data_root / path).read_bytes()
    ).hexdigest()
    return {
        "request_token": str(uuid4()),
        "confirmed": True,
        "items": [
            {
                "shot_id": shot["id"],
                "expected_version": context["shot_version"],
                "context_hash": context["context_hash"],
                "path": path,
                "sha256": sha,
            }
        ],
    }


def test_import_retry_refresh_and_video_handoff(client, project, shot):
    shot = blank_shot(client, shot)
    payload = batch(client, shot, project)
    url = "/api/v1/episodes/EP001/external-keyframes"
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    selected = response.json()["items"][0]["selected"]
    assert selected["usable"]
    assert client.get("/api/v1/generations").json() == []
    retry = client.post(url, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["items"][0]["already_imported"]
    assert (
        retry.json()["items"][0]["selected"]["selection_token"]
        == selected["selection_token"]
    )
    refreshed = client.post(base(shot) + f"/keyframe-jobs/{selected['job_id']}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["model"] == "codex-imagegen"
    video = client.post(
        base(shot) + "/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
            "keyframe_selection_token": selected["selection_token"],
            "use_character_bindings": False,
        },
    )
    assert video.status_code == 201, video.text
    payload["items"][0]["prompt"] = "changed"
    assert client.post(url, json=payload).status_code == 409


def test_invalid_batch_never_partially_selects(client, project, shot):
    shot = blank_shot(client, shot)
    other = client.post(
        f"/api/v1/scenes/{shot['scene_id']}/shots",
        json={
            "order_index": 2,
            "duration": 3,
            "shot_type": "wide",
            "visual_description": "Empty room",
        },
    ).json()
    payload = batch(client, shot, project)
    second = batch(client, other, project)["items"][0]
    second["path"] = "/etc/passwd"
    payload["items"].append(second)
    response = client.post("/api/v1/episodes/EP001/external-keyframes", json=payload)
    assert response.status_code == 422, response.text
    studio = client.get(base(shot) + "/keyframe-studio").json()
    assert studio["selected"] is None and studio["jobs"] == []
    assert studio["shot_version"] == shot["version"]


def test_stale_context_and_modified_file_are_rejected(client, project, shot):
    shot = blank_shot(client, shot)
    payload = batch(client, shot, project)
    url = "/api/v1/episodes/EP001/external-keyframes"
    payload["items"][0]["sha256"] = "a" * 64
    assert client.post(url, json=payload).status_code == 422
    payload = batch(client, shot, project)
    payload["items"][0]["context_hash"] = "b" * 64
    assert client.post(url, json=payload).status_code == 409


def test_missing_character_master_blocks_import(client, project, shot):
    payload = batch(client, shot, project)
    response = client.post("/api/v1/episodes/EP001/external-keyframes", json=payload)
    assert response.status_code == 422, response.text
    assert client.get(base(shot) + "/keyframe-studio").json()["selected"] is None
