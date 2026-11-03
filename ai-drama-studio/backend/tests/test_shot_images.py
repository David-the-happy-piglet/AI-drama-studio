import base64
import io
import json
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from test_character_elements import upload


@pytest.fixture
def image_api(client, monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "test-keyframe-key")
    monkeypatch.setenv("KLING_BASE_URL", "https://api-singapore.klingai.com")
    calls, state = [], {"timeout": False, "reject": False, "download_failure": False}
    output = io.BytesIO()
    Image.new("RGB", (800, 450), "blue").save(output, format="PNG")

    def handler(request):
        calls.append(request)
        if request.url.host == "h1.inkwai.com":
            assert "authorization" not in request.headers
            return httpx.Response(
                503 if state["download_failure"] else 200, content=output.getvalue()
            )
        assert request.headers["authorization"] == "Bearer test-keyframe-key"
        if request.method == "POST" and state["timeout"]:
            raise httpx.ReadTimeout("uncertain", request=request)
        if request.method == "POST" and state["reject"]:
            return httpx.Response(429, json={"code": 1303, "message": "quota"})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "keyframe-task",
                    "task_status": "submitted"
                    if request.method == "POST"
                    else "succeed",
                    "task_result": {
                        "images": [{"url": "https://h1.inkwai.com/keyframe.png"}]
                    },
                },
            },
        )

    client.app.state.video_providers.get("kling").transport = httpx.MockTransport(
        handler
    )
    return calls, state


def base(shot):
    return f"/api/v1/shots/{shot['id']}"


def body(client, shot, **changes):
    context = client.get(base(shot) + "/keyframe-studio").json()
    return {
        "expected_version": context["shot_version"],
        "context_hash": context["context_hash"],
        "prompt": "夜晚书桌前的中景，单幅首帧",
        "negative_prompt": "拼图",
        "request_token": str(uuid4()),
        "confirmed": True,
        "references_confirmed": True,
        **changes,
    }


def blank_shot(client, shot):
    return client.patch(
        base(shot), json={"expected_version": shot["version"], "characters": []}
    ).json()


def add_master(client, project, character, color="green"):
    path = upload(client, project, color=color)
    response = client.put(
        f"/api/v1/characters/{character['id']}/references",
        json={
            "expected_version": character["version"],
            "master_reference_path": path,
            "additional_reference_paths": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def complete(client, shot):
    response = client.post(base(shot) + "/keyframe-jobs", json=body(client, shot))
    assert response.status_code == 200, response.text
    job = response.json()
    result = client.post(base(shot) + f"/keyframe-jobs/{job['id']}/refresh")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded", result.text
    return result.json()


def adopt(client, shot, job, version=None):
    return client.post(
        base(shot) + f"/keyframe-jobs/{job['id']}/select",
        json={
            "expected_version": version or shot["version"],
            "candidate_index": 0,
            "confirmed": True,
        },
    )


def test_text_keyframe_select_and_video_handoff(client, shot, image_api):
    shot = blank_shot(client, shot)
    context = client.get(base(shot) + "/keyframe-studio").json()
    assert context["references"] == [] and context["model"] == "kling-v3"
    assert shot["visual_description"] in context["prompt"]
    job = complete(client, shot)
    request = json.loads(image_api[0][0].content)
    assert "image_list" not in request and request["aspect_ratio"] == "16:9"
    assert request["model_name"] == "kling-v3"
    assert client.get(base(shot) + "/keyframe-studio").json()["selected"] is None
    assert "/shot_keyframes/" in job["candidates"][0]["path"]
    result = adopt(client, shot, job)
    assert result.status_code == 200, result.text
    selected = result.json()["selected"]
    assert (
        selected["usable"] and result.json()["shot"]["version"] == shot["version"] + 1
    )
    for field in ("characters", "dialogue", "generation_prompt", "generation_mode"):
        assert result.json()["shot"][field] == shot[field]
    assert client.get("/api/v1/generations").json() == []
    saved = client.get(base(shot) + "/keyframe-studio").json()["selected"]
    assert saved["path"] == selected["path"] and saved["usable"]
    response = client.post(
        base(shot) + "/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
            "keyframe_selection_token": selected["selection_token"],
        },
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()["request_snapshot"]
    assert snapshot["first_frame"] == selected["path"]
    assert snapshot["metadata"]["selected_keyframe"]["sha256"] == selected["sha256"]


def test_revision_blocks_paid_keyframe_and_selection_but_keeps_candidates(
    client, shot, image_api
):
    from app.models import Shot

    shot = blank_shot(client, shot)
    job = complete(client, shot)
    before_calls = len(image_api[0])
    with client.app.state.database.session_factory() as db:
        db.get(Shot, shot["id"]).scene.episode.status = "revising"
        db.commit()
    response = client.post(base(shot) + "/keyframe-jobs", json=body(client, shot))
    assert response.status_code == 422 and "修订" in response.text
    assert adopt(client, shot, job).status_code == 422
    assert len(image_api[0]) == before_calls
    context = client.get(base(shot) + "/keyframe-studio").json()
    assert context["selected"] is None
    assert (
        client.post(base(shot) + f"/keyframe-jobs/{job['id']}/refresh").status_code
        == 200
    )


def test_two_masters_sent_exactly_and_no_character_asset_mutation(
    client, project, character, shot, image_api
):
    first = add_master(client, project, character)
    second = client.post(
        f"/api/v1/projects/{project['id']}/characters", json={"name": "Second"}
    ).json()
    second = add_master(client, project, second, "red")
    shot = client.patch(
        base(shot),
        json={
            "expected_version": shot["version"],
            "characters": [*shot["characters"], {"character_id": second["id"]}],
        },
    ).json()
    before = client.get(f"/api/v1/characters/{first['id']}").json()
    job = complete(client, shot)
    request = json.loads(image_api[0][0].content)
    assert image_api[0][0].url.path == "/v1/images/omni-image"
    assert (
        request["model_name"] == "kling-v3-omni" and request["result_type"] == "single"
    )
    assert "negative_prompt" not in request and "避免：拼图" in request["prompt"]
    assert "<<<image_1>>>" in request["prompt"] and "<<<image_2>>>" in request["prompt"]
    assert len(request["image_list"]) == 2
    for index, ref in enumerate(job["references_snapshot"]):
        assert (
            base64.b64decode(request["image_list"][index]["image"])
            == (client.app.state.settings.data_root / ref["snapshot_path"]).read_bytes()
        )
    assert client.get(f"/api/v1/characters/{first['id']}").json() == before
    assert "test-keyframe-key" not in json.dumps(job)
    assert "https://h1.inkwai.com" not in json.dumps(job)


def test_missing_master_never_silently_falls_back(client, shot, image_api):
    studio = client.get(base(shot) + "/keyframe-studio").json()
    assert studio["errors"]
    response = client.post(base(shot) + "/keyframe-jobs", json=body(client, shot))
    assert response.status_code == 422
    assert not image_api[0]


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed": False},
        {"references_confirmed": False},
        {"prompt": " "},
        {"count": 5},
        {"prompt": "字" * 2500},
    ],
)
def test_rejects_unconfirmed_or_invalid_inputs(
    client, project, character, shot, image_api, change
):
    add_master(client, project, character)
    response = client.post(
        base(shot) + "/keyframe-jobs", json=body(client, shot, **change)
    )
    assert response.status_code == 422, response.text
    assert not image_api[0]


def test_changed_master_and_stale_version_block_submission(
    client, project, character, shot, image_api
):
    saved = add_master(client, project, character)
    payload = body(client, shot)
    add_master(client, project, saved, "red")
    assert client.post(base(shot) + "/keyframe-jobs", json=payload).status_code == 409
    payload = body(client, shot)
    client.patch(
        base(shot),
        json={"expected_version": shot["version"], "visual_description": "changed"},
    )
    assert client.post(base(shot) + "/keyframe-jobs", json=payload).status_code == 409
    assert not image_api[0]


def test_timeout_and_idempotency_and_active_slot(client, shot, image_api):
    shot = blank_shot(client, shot)
    payload = body(client, shot)
    image_api[1]["timeout"] = True
    endpoint = base(shot) + "/keyframe-jobs"
    job = client.post(endpoint, json=payload).json()
    assert job["status"] == "unknown"
    assert client.post(endpoint, json=payload).json()["id"] == job["id"]
    assert (
        client.post(endpoint, json={**payload, "prompt": "different"}).status_code
        == 409
    )
    assert (
        client.post(
            endpoint, json={**payload, "request_token": str(uuid4())}
        ).status_code
        == 409
    )
    assert sum(r.method == "POST" for r in image_api[0]) == 1
    image_api[1]["timeout"] = False
    response = client.post(endpoint + f"/{job['id']}/refresh")
    assert response.json()["status"] == "succeeded"
    assert sum(r.method == "POST" for r in image_api[0]) == 1


def test_failed_download_retries_download_only(client, shot, image_api):
    shot = blank_shot(client, shot)
    job = client.post(base(shot) + "/keyframe-jobs", json=body(client, shot)).json()
    image_api[1]["download_failure"] = True
    endpoint = base(shot) + f"/keyframe-jobs/{job['id']}/refresh"
    assert client.post(endpoint).json()["status"] == "download_failed"
    assert adopt(client, shot, job).status_code == 422
    image_api[1]["download_failure"] = False
    assert client.post(endpoint).json()["status"] == "succeeded"
    assert sum(r.method == "POST" for r in image_api[0]) == 1


def test_stale_selection_and_video_refused_after_edit(client, shot, image_api):
    shot = blank_shot(client, shot)
    job = complete(client, shot)
    result = adopt(client, shot, job).json()
    assert adopt(client, shot, job).status_code == 409
    client.patch(
        base(shot),
        json={
            "expected_version": result["shot"]["version"],
            "visual_description": "different",
        },
    )
    assert not client.get(base(shot) + "/keyframe-studio").json()["selected"]["usable"]
    assert adopt(client, shot, job, result["shot"]["version"] + 1).status_code == 409
    response = client.post(
        base(shot) + "/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
            "keyframe_selection_token": result["selected"]["selection_token"],
        },
    )
    assert response.status_code == 422
    assert client.get("/api/v1/generations").json() == []


def test_selected_token_cannot_be_reused_for_other_shot(client, shot, image_api):
    shot = blank_shot(client, shot)
    job = complete(client, shot)
    selected = adopt(client, shot, job).json()["selected"]
    other = client.post(
        f"/api/v1/scenes/{shot['scene_id']}/shots",
        json={
            "order_index": 2,
            "duration": 5,
            "shot_type": "wide",
            "visual_description": "other",
        },
    ).json()
    assert (
        client.post(base(other) + f"/keyframe-jobs/{job['id']}/refresh").status_code
        == 404
    )
    assert adopt(client, other, job).status_code == 422
    assert (
        client.post(
            base(other) + "/generations",
            json={
                "request_token": str(uuid4()),
                "confirmed": True,
                "provider": "mock",
                "keyframe_selection_token": selected["selection_token"],
            },
        ).status_code
        == 422
    )


def test_conflicting_first_frame_inputs_rejected(client, shot, image_api):
    shot = blank_shot(client, shot)
    selected = adopt(client, shot, complete(client, shot)).json()["selected"]
    response = client.post(
        base(shot) + "/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
            "keyframe_selection_token": selected["selection_token"],
            "first_frame": selected["path"],
        },
    )
    assert response.status_code == 422


def test_mutated_selected_file_rejected(client, shot, image_api):
    shot = blank_shot(client, shot)
    selected = adopt(client, shot, complete(client, shot)).json()["selected"]
    (client.app.state.settings.data_root / selected["path"]).write_bytes(b"broken")
    assert not client.get(base(shot) + "/keyframe-studio").json()["selected"]["usable"]
    assert (
        client.post(
            base(shot) + "/generations",
            json={
                "request_token": str(uuid4()),
                "confirmed": True,
                "provider": "mock",
                "keyframe_selection_token": selected["selection_token"],
            },
        ).status_code
        == 422
    )
