import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.models import Character, Script
from app.models.entities import CharacterImageJob, CharacterProviderBinding
from app.providers.image.kling import KlingImageProvider
from app.providers.video.base import ProviderResponseError
from app.services.character_elements import account_scope
from app.services.character_images import PortraitCreate, create_job, refresh_job


def endpoint(character):
    return f"/api/v1/characters/{character['id']}"


def payload(character, **changes):
    return {
        "expected_version": character["version"],
        "request_token": str(uuid4()),
        "prompt": "单人正面母版肖像，灰背景",
        "count": 2,
        "confirmed": True,
        "mode": "text",
        **changes,
    }


@pytest.fixture
def images(client, monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "test-image-key-not-real")
    monkeypatch.setenv("KLING_BASE_URL", "https://api-singapore.klingai.com")
    provider = client.app.state.video_providers.get("kling")
    buffer = io.BytesIO()
    Image.new("RGB", (400, 500), "blue").save(buffer, format="PNG")
    state = {"post": "submitted", "get": "succeed", "bad_download": False}
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "h1.inkwai.com":
            assert "authorization" not in request.headers
            if state["bad_download"] and request.url.path.endswith("1.png"):
                return httpx.Response(503)
            return httpx.Response(200, content=buffer.getvalue())
        assert request.headers["authorization"] == "Bearer test-image-key-not-real"
        assert request.url.path.startswith("/v1/images/generations")
        status = state["post"] if request.method == "POST" else state["get"]
        if status == "timeout":
            raise httpx.ReadTimeout("uncertain", request=request)
        if status == "reject":
            return httpx.Response(400)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "image-task",
                    "task_status": status,
                    "task_result": {
                        "images": []
                        if state.get("empty")
                        else [
                            {"index": i, "url": f"https://h1.inkwai.com/test/{i}.png"}
                            for i in range(2)
                        ]
                    },
                },
            },
        )

    provider.transport = httpx.MockTransport(handler)
    return provider, calls, state


def generate(client, character):
    base = endpoint(character) + "/portrait-jobs"
    response = client.post(base, json=payload(character))
    assert response.status_code == 200, response.text
    return client.post(f"{base}/{response.json()['id']}/refresh").json()


def test_prompt_source_prefers_episode_script_and_preserves_original(
    client, project, character, shot, images
):
    factory = client.app.state.database.session_factory
    with factory() as db:
        current = db.get(Script, "SCRIPT_EP01_001")
        current.structured_content = {
            "preproduction": {
                "character_prompts": [
                    {"character_id": character["id"], "portrait": "正式母版 prompt"}
                ]
            }
        }
        db.add(
            Script(
                id="NEW_UNUSED",
                project_id=project["id"],
                title="未采用稿",
                source_idea="test",
                structured_content={
                    "preproduction": {
                        "character_prompts": [
                            {"character_id": character["id"], "portrait": "未采用母版"}
                        ]
                    }
                },
                updated_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    context = client.get(endpoint(character) + "/portrait-studio").json()
    assert context["prompt"] == "正式母版 prompt"
    assert context["configured"] and not context["jobs"] and not images[1]
    client.post(
        endpoint(character) + "/portrait-jobs", json=payload(character, prompt="edited")
    )
    assert (
        client.get(endpoint(character) + "/portrait-studio").json()["prompt"]
        == "正式母版 prompt"
    )


def test_fallback_prompt_and_configuration(client, character, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    result = client.get(endpoint(character) + "/portrait-studio").json()
    assert "Alice" in result["prompt"] and not result["configured"]
    assert (
        client.post(
            endpoint(character) + "/portrait-jobs", json=payload(character)
        ).status_code
        == 503
    )


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed": False},
        {"count": 5},
        {"count": 0},
        {"prompt": " "},
        {"prompt": "a" * 2501},
        {"resolution": "4k"},
        {"request_token": "bad"},
    ],
)
def test_validation_never_submits(client, character, images, change):
    assert (
        client.post(
            endpoint(character) + "/portrait-jobs", json=payload(character, **change)
        ).status_code
        == 422
    )
    assert not images[1]


def test_contract_persistence_selection_and_safe_serialization(
    client, character, images
):
    base = endpoint(character)
    job = generate(client, character)
    assert job["status"] == "succeeded" and len(job["candidates"]) == 2
    body = json.loads(images[1][0].content)
    assert body["model_name"] == "kling-v3" and body["n"] == 2
    assert body["aspect_ratio"] == "3:4" and body["resolution"] == "1k"
    assert body["external_task_id"] == job["id"] and "image" not in body
    assert client.get(base).json()["master_reference_path"] is None
    assert "inkwai" not in json.dumps(job) and "account_scope" not in job
    count = len(images[1])
    selected = client.post(
        f"{base}/portrait-jobs/{job['id']}/select",
        json={
            "expected_version": character["version"],
            "candidate_index": 0,
            "confirmed": True,
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["version"] == character["version"] + 1
    assert selected.json()["master_reference_path"] == job["candidates"][0]["path"]
    assert len(images[1]) == count
    for candidate in job["candidates"]:
        assert (client.app.state.settings.data_root / candidate["path"]).is_file()
        assert (
            client.get(
                "/media/" + candidate["path"].removeprefix("assets/")
            ).status_code
            == 200
        )
    with client.app.state.database.session_factory() as db:
        assert len(db.get(CharacterImageJob, job["id"]).selection_history) == 1


def test_idempotency_and_active_guard(client, character, images):
    base = endpoint(character) + "/portrait-jobs"
    body = payload(character)
    job = client.post(base, json=body).json()
    assert client.post(base, json=body).json()["id"] == job["id"]
    assert client.post(base, json={**body, "prompt": "different"}).status_code == 409
    assert client.post(base, json=payload(character)).status_code == 409
    assert len(images[1]) == 1


def test_replacement_preserves_files_and_expires_binding(
    client, project, character, images
):
    from test_character_elements import refs

    saved = refs(client, project, character)
    original_paths = [
        saved["master_reference_path"],
        *saved["reference_assets"]["additional_reference_paths"],
    ]
    with client.app.state.database.session_factory() as db:
        db.add(
            CharacterProviderBinding(
                character_id=character["id"],
                provider="kling",
                account_scope=account_scope(images[0]),
                source_version=saved["version"],
                external_task_id="old",
                status="succeeded",
                element_id="old-element",
            )
        )
        db.commit()
    job = generate(client, saved)
    base = endpoint(saved)
    calls = len(images[1])
    result = client.post(
        f"{base}/portrait-jobs/{job['id']}/select",
        json={
            "expected_version": saved["version"],
            "candidate_index": 0,
            "confirmed": True,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["reference_assets"]["additional_reference_paths"] == []
    assert not client.get(base + "/kling-element").json()["usable"]
    assert len(images[1]) == calls
    for path in original_paths:
        assert (client.app.state.settings.data_root / path).is_file()
    with client.app.state.database.session_factory() as db:
        history = db.get(CharacterImageJob, job["id"]).selection_history[0]
        assert history["previous_master"] == original_paths[0]
        assert history["previous_additional"] == original_paths[1:]


def test_uncertain_submission_recovers_without_reposting(client, character, images):
    images[2]["post"] = "timeout"
    base = endpoint(character) + "/portrait-jobs"
    job = client.post(base, json=payload(character)).json()
    assert job["status"] == "unknown"
    assert client.post(base, json=payload(character)).status_code == 409
    assert client.post(f"{base}/{job['id']}/refresh").json()["status"] == "succeeded"
    assert images[1][1].url.path.endswith(job["id"])
    assert sum(r.method == "POST" for r in images[1]) == 1


def test_failed_query_and_empty_success_keep_active_guard(client, character, images):
    base = endpoint(character) + "/portrait-jobs"
    job = client.post(base, json=payload(character)).json()
    images[2]["get"] = "timeout"
    assert client.post(f"{base}/{job['id']}/refresh").json()["status"] == "submitted"
    images[2].update(get="succeed", empty=True)
    assert client.post(f"{base}/{job['id']}/refresh").json()["status"] == "unknown"
    assert client.post(base, json=payload(character)).status_code == 409


def test_definitive_failure_allows_new_confirmed_batch(client, character, images):
    base = endpoint(character) + "/portrait-jobs"
    images[2]["post"] = "reject"
    assert client.post(base, json=payload(character)).json()["status"] == "failed"
    images[2]["post"] = "submitted"
    assert client.post(base, json=payload(character)).json()["status"] == "submitted"


def test_partial_download_retries_only_missing_file(client, character, images):
    images[2]["bad_download"] = True
    job = generate(client, character)
    assert job["status"] == "partial"
    assert job["candidates"][0]["path"] and not job["candidates"][1]["path"]
    count = len(images[1])
    images[2]["bad_download"] = False
    result = client.post(
        f"{endpoint(character)}/portrait-jobs/{job['id']}/refresh"
    ).json()
    assert result["status"] == "succeeded"
    assert len(images[1]) == count + 1 and images[1][-1].url.path.endswith("1.png")


def test_selection_requires_current_version_and_explicit_stale_acceptance(
    client, character, images
):
    base = endpoint(character)
    job = generate(client, character)
    changed = client.patch(
        base,
        json={"expected_version": character["version"], "face_description": "new face"},
    ).json()
    selection = {
        "expected_version": character["version"],
        "candidate_index": 0,
        "confirmed": True,
    }
    url = f"{base}/portrait-jobs/{job['id']}/select"
    assert client.post(url, json=selection).status_code == 409
    selection["expected_version"] = changed["version"]
    assert client.post(url, json=selection).status_code == 409
    assert client.post(url, json={**selection, "accept_stale": True}).status_code == 200
    other = client.post(
        f"/api/v1/projects/{character['project_id']}/characters", json={"name": "Other"}
    ).json()
    assert (
        client.post(f"{endpoint(other)}/portrait-jobs/{job['id']}/refresh").status_code
        == 404
    )


def test_account_change_and_poll_lease_do_not_query(
    client, character, images, monkeypatch
):
    base = endpoint(character) + "/portrait-jobs"
    job = client.post(base, json=payload(character)).json()
    with client.app.state.database.session_factory() as db:
        row = db.get(CharacterImageJob, job["id"])
        row.refresh_until = datetime.now(UTC) + timedelta(minutes=5)
        db.commit()
    assert client.post(f"{base}/{job['id']}/refresh").json()["status"] == "submitted"
    monkeypatch.setenv("KLING_API_KEY", "different-account")
    assert client.post(f"{base}/{job['id']}/refresh").status_code == 409
    assert len(images[1]) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://h1.inkwai.com/a.png",
        "https://127.0.0.1/a.png",
        "https://h1.inkwai.com.evil.test/a.png",
        "https://user:pass@h1.inkwai.com/a.png",
        "https://h1.inkwai.com:8443/a.png",
    ],
)
def test_download_rejects_untrusted_addresses(client, images, url):
    with pytest.raises(ProviderResponseError):
        asyncio.run(KlingImageProvider(images[0]).download(url, "PROJECT", "JOB", 0))
    assert not images[1]


@pytest.mark.parametrize("kind", ["redirect", "corrupt", "oversize", "small"])
def test_download_rejects_unsafe_content(client, images, kind):
    buffer = io.BytesIO()
    Image.new("RGB", (299, 400), "red").save(buffer, format="PNG")
    response = {
        "redirect": httpx.Response(302, headers={"location": "https://127.0.0.1/"}),
        "corrupt": httpx.Response(200, content=b"broken"),
        "oversize": httpx.Response(200, content=b"x" * (10 * 1024 * 1024 + 1)),
        "small": httpx.Response(200, content=buffer.getvalue()),
    }[kind]
    images[0].transport = httpx.MockTransport(lambda r: response)
    with pytest.raises(ProviderResponseError):
        asyncio.run(
            KlingImageProvider(images[0]).download(
                "https://h1.inkwai.com/a.png", "PROJECT", "JOB", 0
            )
        )


def test_concurrent_create_and_refresh_submit_once(client, character, images):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            images[1].append(request)
            entered.set()
            await release.wait()
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"task_id": "one", "task_status": "submitted"},
                },
            )

        images[0].transport = httpx.MockTransport(handler)
        body = PortraitCreate(**payload(character))
        factory = client.app.state.database.session_factory
        with factory() as a, factory() as b:
            first = asyncio.create_task(
                create_job(a, a.get(Character, character["id"]), body, images[0])
            )
            await entered.wait()
            duplicate = await create_job(
                b, b.get(Character, character["id"]), body, images[0]
            )
            assert duplicate["status"] == "submitting"
            await refresh_job(
                b,
                b.get(Character, character["id"]),
                b.get(CharacterImageJob, duplicate["id"]),
                images[0],
            )
            release.set()
            await first

    asyncio.run(run())
    assert len(images[1]) == 1
