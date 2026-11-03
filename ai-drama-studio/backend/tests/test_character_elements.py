import asyncio
import base64
import io
import json
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.models import Character
from app.models.entities import CharacterProviderBinding
from app.schemas import KlingElementCreate
from app.services.character_elements import BindingConflict, create_element


def upload(
    client, project, color="red", size=(400, 500), content=None, filename="face.png"
):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    result = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"asset_kind": "character_reference"},
        files={
            "file": (
                filename,
                content if content is not None else buffer.getvalue(),
                "image/png",
            )
        },
    )
    assert result.status_code == 200
    return result.json()["path"]


def refs(client, project, character):
    paths = [upload(client, project, "red"), upload(client, project, "blue")]
    response = client.put(
        f"/api/v1/characters/{character['id']}/references",
        json={
            "expected_version": character["version"],
            "master_reference_path": paths[0],
            "additional_reference_paths": paths[1:],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def creation(character):
    return {
        "expected_version": character["version"],
        "element_name": "Alice",
        "element_description": "黑色齐肩发，左眼下小痣",
        "confirmed": True,
    }


@pytest.fixture
def kling(client, monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "test-element-key-not-real")
    monkeypatch.setenv("KLING_BASE_URL", "https://api-singapore.klingai.com")
    provider = client.app.state.video_providers.get("kling")
    calls = []
    state = {"status": "succeed"}

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-element-key-not-real"
        if (
            request.method == "POST"
            and request.url.path == "/v1/general/advanced-custom-elements"
        ):
            body = json.loads(request.content)
            assert body["reference_type"] == "image_refer"
            assert base64.b64decode(
                body["element_image_list"]["frontal_image"]
            ).startswith(b"\x89PNG")
            assert len(body["element_image_list"]["refer_images"]) == 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"task_id": "element-task", "task_status": "submitted"},
                },
            )
        if request.method == "GET":
            assert (
                request.url.path == "/v1/general/advanced-custom-elements/element-task"
            )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "element-task",
                        "task_status": state["status"],
                        "task_status_msg": "reference rejected",
                        "task_result": {
                            "elements": [
                                {"element_id": 1234567890123456789, "status": "succeed"}
                            ]
                        },
                    },
                },
            )
        return httpx.Response(
            200, json={"code": 0, "data": {"id": "video-task", "status": "submitted"}}
        )

    provider.transport = httpx.MockTransport(handler)
    return provider, calls, state


def ready_binding(client, project, character, kling):
    saved = refs(client, project, character)
    endpoint = f"/api/v1/characters/{character['id']}/kling-element"
    result = client.post(endpoint, json=creation(saved))
    assert result.status_code == 200, result.text
    assert result.json()["binding"]["status"] == "submitted"
    result = client.post(endpoint + "/refresh")
    assert result.json()["usable"] is True
    assert result.json()["binding"]["element_id"] == "1234567890123456789"
    return saved, endpoint


def test_local_save_is_offline_and_requires_confirmation(
    client, project, character, kling
):
    saved = refs(client, project, character)
    assert kling[1] == []
    assert saved["master_reference_path"]
    endpoint = f"/api/v1/characters/{character['id']}/kling-element"
    payload = creation(saved)
    payload["confirmed"] = False
    assert client.post(endpoint, json=payload).status_code == 422
    assert kling[1] == []
    response = client.post(endpoint, json=creation(saved))
    assert response.status_code == 200
    assert client.post(endpoint, json=creation(saved)).status_code == 409
    assert len(kling[1]) == 1
    assert (
        "account_scope" not in response.text and "test-element-key" not in response.text
    )
    assert "frontal_image" not in response.text


def test_binding_reuse_snapshot_and_no_upload_on_video(
    client, project, character, shot, kling
):
    _, endpoint = ready_binding(client, project, character, kling)
    preview = client.get(
        f"/api/v1/shots/{shot['id']}/character-bindings/preview"
    ).json()
    assert preview["errors"] == []
    response = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "kling",
            "first_frame": "https://example.invalid/frame.png",
            "duration": 5,
        },
    )
    assert response.status_code == 201, response.text
    generation = response.json()
    assert generation["status"] == "submitted"
    alias = preview["elements"][0]["id"]
    assert "@" + alias in generation["prompt"]
    assert (
        generation["api_parameters"]["elements"][0]["element_id"]
        == "1234567890123456789"
    )
    assert (
        generation["request_snapshot"]["metadata"]["character_bindings"][0][
            "character_id"
        ]
        == character["id"]
    )
    body = json.loads(kling[1][-1].content)
    assert any(
        item.get("element_id") == "1234567890123456789" for item in body["contents"]
    )
    assert (
        len([r for r in kling[1] if r.url.path.endswith("advanced-custom-elements")])
        == 1
    )
    # Separate read session sees durable mapping; no remote query on GET.
    assert client.get(endpoint).json()["usable"]


def test_stale_assets_and_account_change_block_auto_binding(
    client, project, character, shot, kling, monkeypatch
):
    saved, endpoint = ready_binding(client, project, character, kling)
    monkeypatch.setenv("KLING_API_KEY", "rotated-key")
    assert not client.get(endpoint).json()["usable"]
    assert client.post(endpoint + "/refresh").status_code == 409
    monkeypatch.setenv("KLING_API_KEY", "test-element-key-not-real")
    assert (
        client.patch(
            f"/api/v1/characters/{character['id']}",
            json={"expected_version": saved["version"], "face_description": "new face"},
        ).status_code
        == 200
    )
    assert not client.get(endpoint).json()["usable"]
    count = len(kling[1])
    response = client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "kling",
        },
    )
    assert response.status_code == 422
    assert "重新审阅" in response.text and len(kling[1]) == count


def test_missing_binding_and_unsupported_mode_require_explicit_optout(
    client, shot, kling
):
    endpoint = f"/api/v1/shots/{shot['id']}/generations"
    assert (
        client.post(
            endpoint,
            json={
                "request_token": str(uuid4()),
                "confirmed": True,
                "provider": "kling",
            },
        ).status_code
        == 422
    )
    assert not kling[1]
    response = client.post(
        endpoint,
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "kling",
            "use_character_bindings": False,
            "first_frame": "https://example.invalid/frame.png",
            "duration": 5,
        },
    )
    assert response.status_code == 201 and response.json()["status"] == "submitted"
    preview = client.get(
        f"/api/v1/shots/{shot['id']}/character-bindings/preview?model=kling-3.0-turbo"
    ).json()
    assert not preview["supported"]
    assert any("不支持主体" in error for error in preview["errors"])


@pytest.mark.parametrize(
    "kind",
    [
        "small",
        "wide",
        "fake",
        "webp",
        "duplicate",
        "outside",
        "other_project",
        "symlink",
    ],
)
def test_reject_invalid_or_foreign_assets_without_remote_calls(
    client, project, character, kling, tmp_path, kind
):
    main = upload(client, project)
    extra = upload(client, project, "blue")
    if kind == "small":
        extra = upload(client, project, size=(299, 400))
    if kind == "wide":
        extra = upload(client, project, size=(1000, 300))
    if kind == "fake":
        extra = upload(client, project, content=b"not an image")
    if kind == "webp":
        extra = upload(client, project, filename="fake.webp")
    if kind == "duplicate":
        extra = main
    if kind == "outside":
        extra = str(tmp_path / "outside.png")
    if kind == "other_project":
        other = client.post("/api/v1/projects", json={"name": "Other"}).json()
        extra = upload(client, other)
    if kind == "symlink":
        data_root = client.app.state.settings.data_root
        link = data_root / main
        link.unlink()
        link.symlink_to(tmp_path / "outside.png")
    result = client.put(
        f"/api/v1/characters/{character['id']}/references",
        json={
            "expected_version": character["version"],
            "master_reference_path": main,
            "additional_reference_paths": [extra],
        },
    )
    assert result.status_code == 422
    assert kling[1] == []


def test_unknown_submission_recovers_by_external_task_id_without_reposting(
    client, project, character, kling
):
    saved = refs(client, project, character)
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            raise httpx.ReadTimeout("uncertain", request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "recovered",
                    "task_status": "succeed",
                    "task_result": {"elements": [{"element_id": "remote-id"}]},
                },
            },
        )

    kling[0].transport = httpx.MockTransport(handler)
    endpoint = f"/api/v1/characters/{character['id']}/kling-element"
    result = client.post(endpoint, json=creation(saved)).json()
    assert result["binding"]["status"] == "unknown"
    assert client.post(endpoint, json=creation(saved)).status_code == 409
    assert client.post(endpoint + "/refresh").json()["usable"]
    assert calls[1].url.path.endswith(result["binding"]["external_task_id"])
    assert len(calls) == 2


def test_failed_query_does_not_allow_duplicate_post(client, project, character, kling):
    saved = refs(client, project, character)
    endpoint = f"/api/v1/characters/{character['id']}/kling-element"
    client.post(endpoint, json=creation(saved))
    kling[0].transport = httpx.MockTransport(lambda request: httpx.Response(503))
    assert client.post(endpoint + "/refresh").json()["binding"]["status"] == "submitted"
    assert client.post(endpoint, json=creation(saved)).status_code == 409


def test_definitive_rejection_and_remote_failed_task_can_retry(
    client, project, character, kling
):
    saved = refs(client, project, character)
    endpoint = f"/api/v1/characters/{character['id']}/kling-element"
    original = kling[0].transport
    kling[0].transport = httpx.MockTransport(lambda r: httpx.Response(400))
    assert (
        client.post(endpoint, json=creation(saved)).json()["binding"]["status"]
        == "failed"
    )
    kling[0].transport = original
    assert (
        client.post(endpoint, json=creation(saved)).json()["binding"]["status"]
        == "submitted"
    )
    kling[2]["status"] = "failed"
    assert client.post(endpoint + "/refresh").json()["binding"]["status"] == "failed"
    assert (
        client.post(endpoint, json=creation(saved)).json()["binding"]["status"]
        == "submitted"
    )


def test_duplicate_concurrent_creates_only_submit_once(
    client, project, character, kling
):
    saved = refs(client, project, character)
    calls = []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"task_id": "one", "task_status": "submitted"},
                },
            )

        kling[0].transport = httpx.MockTransport(handler)
        factory = client.app.state.database.session_factory
        with factory() as a, factory() as b:
            first = asyncio.create_task(
                create_element(
                    a,
                    a.get(Character, saved["id"]),
                    KlingElementCreate(**creation(saved)),
                    kling[0],
                )
            )
            await entered.wait()
            with pytest.raises(BindingConflict):
                await create_element(
                    b,
                    b.get(Character, saved["id"]),
                    KlingElementCreate(**creation(saved)),
                    kling[0],
                )
            release.set()
            await first

    asyncio.run(run())
    assert len(calls) == 1
    with client.app.state.database.session_factory() as db:
        assert (
            db.get(CharacterProviderBinding, (character["id"], "kling")).task_id
            == "one"
        )
