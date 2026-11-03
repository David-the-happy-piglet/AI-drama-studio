import asyncio
import base64
import hashlib
import json

import httpx
import pytest
import test_character_images
from test_character_elements import upload
from test_character_images import endpoint, payload

from app.models.entities import CharacterImageJob
from app.providers.image.kling import KlingImageProvider
from app.providers.video.base import ProviderResponseError
from app.providers.video.kling import rejection_detail


@pytest.fixture
def images(client, monkeypatch):
    return test_character_images.images.__wrapped__(client, monkeypatch)


def reference_request(client, project, character):
    path = upload(client, project, color="green")
    character = client.put(
        endpoint(character) + "/references",
        json={
            "expected_version": character["version"],
            "master_reference_path": path,
            "additional_reference_paths": [],
        },
    ).json()
    studio = client.get(endpoint(character) + "/portrait-studio").json()
    return character, payload(
        character,
        mode="reference",
        prompt="生成45度半侧面肖像",
        reference_path=path,
        reference_sha256=studio["reference"]["sha256"],
        reference_confirmed=True,
    )


def omni_transport(images, timeout=False):
    provider, calls, _state = images
    original = provider.transport

    def handler(request):
        if request.url.host == "h1.inkwai.com":
            return original.handler(request)
        calls.append(request)
        assert request.url.path.startswith("/v1/images/omni-image")
        if timeout and request.method == "POST":
            raise httpx.ReadTimeout("uncertain", request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "omni-task",
                    "task_status": "submitted"
                    if request.method == "POST"
                    else "succeed",
                    "task_result": {
                        "result_type": "single",
                        "images": [
                            {"url": f"https://h1.inkwai.com/test/{i}.png"}
                            for i in range(2)
                        ],
                    },
                },
            },
        )

    provider.transport = httpx.MockTransport(handler)


def test_exact_approved_master_bytes_are_sent_and_recorded(
    client, project, character, images
):
    saved, body = reference_request(client, project, character)
    original = (
        client.app.state.settings.data_root / body["reference_path"]
    ).read_bytes()
    omni_transport(images)
    base = endpoint(saved) + "/portrait-jobs"
    response = client.post(base, json=body)
    assert response.status_code == 200, response.text
    job = response.json()
    request = json.loads(images[1][0].content)
    assert request["model_name"] == "kling-v3-omni"
    assert (
        base64.b64decode(request["image_list"][0]["image"], validate=True) == original
    )
    assert len(request["image_list"]) == 1
    assert request["prompt"] == job["effective_prompt"]
    assert "<<<image_1>>>" in request["prompt"] and "45度" in request["prompt"]
    assert "避免：" in request["prompt"] and "negative_prompt" not in request
    assert request["result_type"] == "single" and request["n"] == 2
    assert (
        job["mode"] == "reference"
        and job["reference_sha256"] == hashlib.sha256(original).hexdigest()
    )
    snapshot = client.app.state.settings.data_root / job["reference_snapshot_path"]
    assert snapshot.read_bytes() == original
    assert request["image_list"][0]["image"] not in response.text
    assert "test-image-key" not in response.text
    assert client.post(base, json=body).json()["id"] == job["id"]
    result = client.post(f"{base}/{job['id']}/refresh").json()
    assert result["status"] == "succeeded"
    assert len(result["candidates"]) == 2
    assert (
        client.get(endpoint(saved)).json()["master_reference_path"]
        == body["reference_path"]
    )
    # Input evidence remains stable even if the original upload is changed later.
    (client.app.state.settings.data_root / body["reference_path"]).write_bytes(
        b"changed"
    )
    history = client.get(endpoint(saved) + "/portrait-studio").json()
    assert (
        history["reference"]["error"]
        and history["jobs"][0]["reference_sha256"] == job["reference_sha256"]
    )
    assert snapshot.read_bytes() == original
    assert sum(r.method == "POST" for r in images[1]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"reference_confirmed": False},
        {"reference_path": None},
        {"reference_sha256": None},
        {"reference_sha256": "invalid"},
        {"prompt": "x" * 2500},
        {"mode": "text"},
    ],
)
def test_reference_validation_blocks_upload(client, project, character, images, change):
    saved, body = reference_request(client, project, character)
    result = client.post(endpoint(saved) + "/portrait-jobs", json={**body, **change})
    assert result.status_code == 422, result.text
    assert not images[1]


@pytest.mark.parametrize(
    "kind",
    ["missing", "changed_hash", "changed_file", "foreign", "url", "small", "symlink"],
)
def test_invalid_reference_cannot_fall_back_to_text(
    client, project, character, images, tmp_path, kind
):
    saved, body = reference_request(client, project, character)
    if kind == "changed_hash":
        body["reference_sha256"] = "0" * 64
    elif kind == "changed_file":
        (client.app.state.settings.data_root / body["reference_path"]).write_bytes(
            b"corrupt"
        )
    elif kind == "missing":
        (client.app.state.settings.data_root / body["reference_path"]).unlink()
    elif kind == "symlink":
        path = client.app.state.settings.data_root / body["reference_path"]
        path.unlink()
        path.symlink_to(tmp_path / "outside.png")
    else:
        if kind == "foreign":
            other = client.post("/api/v1/projects", json={"name": "other"}).json()
            path = upload(client, other)
        elif kind == "url":
            path = "https://example.invalid/private.jpg"
        else:
            path = upload(client, project, size=(299, 400))
        with client.app.state.database.session_factory() as db:
            from app.models import Character

            db.get(Character, saved["id"]).master_reference_path = path
            db.commit()
        body["reference_path"] = path
    result = client.post(endpoint(saved) + "/portrait-jobs", json=body)
    assert result.status_code in {409, 422}, result.text
    assert not images[1]


def test_missing_master_and_old_client_fail_closed(client, project, character, images):
    body = payload(
        character,
        mode="reference",
        reference_confirmed=True,
        reference_path="assets/absent.png",
        reference_sha256="0" * 64,
    )
    assert (
        client.post(endpoint(character) + "/portrait-jobs", json=body).status_code
        == 409
    )
    saved, _ = reference_request(client, project, character)
    body = payload(saved)
    del body["mode"]
    assert client.post(endpoint(saved) + "/portrait-jobs", json=body).status_code == 409
    assert not images[1]


def test_reference_timeout_queries_omni_external_id_without_reupload(
    client, project, character, images
):
    saved, body = reference_request(client, project, character)
    omni_transport(images, timeout=True)
    base = endpoint(saved) + "/portrait-jobs"
    job = client.post(base, json=body).json()
    assert job["status"] == "unknown"
    assert client.post(base, json=body).json()["id"] == job["id"]
    assert client.post(f"{base}/{job['id']}/refresh").json()["status"] == "succeeded"
    assert images[1][1].url.path == "/v1/images/omni-image/" + job["id"]
    assert sum(r.method == "POST" for r in images[1]) == 1


def test_token_cannot_switch_mode_or_reference(client, project, character, images):
    saved, body = reference_request(client, project, character)
    omni_transport(images)
    base = endpoint(saved) + "/portrait-jobs"
    job = client.post(base, json=body).json()
    assert (
        client.post(base, json={**body, "reference_sha256": "0" * 64}).status_code
        == 409
    )
    text = payload(saved, request_token=body["request_token"], prompt=body["prompt"])
    assert client.post(base, json=text).status_code == 409
    assert len(images[1]) == 1
    # Missing snapshot can never be sent as a text-only request.
    with client.app.state.database.session_factory() as db:
        row = db.get(CharacterImageJob, job["id"])
        row.input_snapshot = None
        with pytest.raises(ProviderResponseError):
            asyncio.run(KlingImageProvider(images[0]).create(row))


def test_legacy_text_history_remains_explicitly_text_only(client, character, images):
    base = endpoint(character) + "/portrait-jobs"
    job = client.post(base, json=payload(character)).json()
    with client.app.state.database.session_factory() as db:
        row = db.get(CharacterImageJob, job["id"])
        db.delete(row.input_snapshot)
        db.commit()
    legacy = client.get(endpoint(character) + "/portrait-studio").json()["jobs"][0]
    assert legacy["mode"] == "text" and legacy["reference_snapshot_path"] is None
    client.post(f"{base}/{job['id']}/refresh")
    assert images[1][1].url.path.startswith("/v1/images/generations/")


def test_detailed_rejection_redacts_credentials_and_images(monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "secret-key-for-test")
    message = rejection_detail(
        httpx.Response(
            429,
            json={
                "code": 1102,
                "message": "not enough secret-key-for-test " + "a" * 200,
                "request_id": "req-123",
            },
        )
    )
    assert "1102" in message and "资源包" in message and "req-123" in message
    assert "secret-key-for-test" not in message and "a" * 100 not in message
