import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import test_shot_images as image_fixtures
from sqlalchemy import select
from test_shot_images import blank_shot

from app.models import Generation, Shot
from app.models.entities import ProductionBatchItem
from app.providers.video.base import ProviderResponseError, VideoGenerationStatus
from app.services.production_batches import (
    claim_next_item,
    process_item,
    stop_worker,
    worker,
)


@pytest.fixture
def image_api(client, monkeypatch):
    calls, state = image_fixtures.image_api.__wrapped__(client, monkeypatch)
    provider = client.app.state.video_providers.get("kling")
    transport = provider.transport

    def handler(request):
        response = transport.handle_request(request)
        if (
            state.get("missing_first")
            and request.url.host == "h1.inkwai.com"
            and request.url.path == "/keyframe.png"
        ):
            return httpx.Response(503)
        if (
            state.get("two_images")
            and request.method == "GET"
            and "images/" in request.url.path
        ):
            data = response.json()
            data["data"]["task_result"]["images"].append(
                {"url": "https://h1.inkwai.com/second.png"}
            )
            return httpx.Response(200, json=data)
        if (
            request.method == "POST"
            and "images/" not in request.url.path
            and response.status_code == 200
        ):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"id": "video-task", "status": "submitted"}},
            )
        return response

    provider.transport = httpx.MockTransport(handler)
    return calls, state


def endpoint(client, shot):
    with client.app.state.database.session_factory() as db:
        episode_id = db.get(Shot, shot["id"]).scene.episode_id
    return f"/api/v1/episodes/{episode_id}/production-batches"


def plan(client, shot, kind="keyframes", **changes):
    body = {
        "kind": kind,
        "shot_ids": [shot["id"]],
        "request_token": str(uuid4()),
        **changes,
    }
    response = client.post(endpoint(client, shot) + "/preview", json=body)
    assert response.status_code == 200, response.text
    return {
        **body,
        "confirmed": True,
        "preview_hash": response.json()["preview_hash"],
    }, response.json()


def create(client, shot, kind="keyframes", **changes):
    body, preview = plan(client, shot, kind, **changes)
    result = client.post(endpoint(client, shot), json=body)
    assert result.status_code == 200, (result.text, preview)
    return result.json(), body


def tick(client, batch):
    with client.app.state.database.session_factory() as db:
        item = db.scalar(
            select(ProductionBatchItem).where(
                ProductionBatchItem.batch_id == batch["id"]
            )
        )
        asyncio.run(process_item(db, item, client.app.state.video_providers))
    return client.get(
        f"/api/v1/episodes/{batch['episode_id']}/production-batches"
    ).json()[0]


def workflow_tick(client, batch):
    for row in batch["items"]:
        with client.app.state.database.session_factory() as db:
            item = db.get(ProductionBatchItem, row["id"])
            asyncio.run(process_item(db, item, client.app.state.video_providers))
    return next(
        b
        for b in client.get(
            f"/api/v1/episodes/{batch['episode_id']}/production-batches"
        ).json()
        if b["id"] == batch["id"]
    )


@pytest.mark.parametrize("kind", ["videos", "workflow"])
def test_review_batches_reject_more_than_three_before_any_provider_call(client, shot, image_api, kind):
    body = {
        "kind": kind,
        "shot_ids": [shot["id"], "other-1", "other-2", "other-3"],
        "request_token": str(uuid4()),
        "confirmed": True,
        "preview_hash": "stale-preview",
    }
    for suffix in ("/preview", ""):
        response = client.post(endpoint(client, shot) + suffix, json=body)
        assert response.status_code == 422
        assert "最多 3 个" in response.json()["detail"]
    assert not image_api[0]
    assert client.get(endpoint(client, shot)).json() == []


def test_workflow_waits_for_first_frame_then_submits_native_video(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    narration = "【画外旁白｜沈青｜普通话】“她只想结束这项服务。”沈青不张嘴。"
    shot = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_prompt": narration},
    ).json()
    batch, body = create(client, shot, "workflow")
    assert [i["status"] for i in batch["items"]] == ["queued", "waiting_keyframe"]
    assert not image_api[0]
    assert client.post(endpoint(client, shot), json=body).json()["id"] == batch["id"]
    result = workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["waiting", "waiting_keyframe"]
    assert client.get("/api/v1/generations").json() == []
    result = workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["succeeded", "queued"]
    result = workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["succeeded", "submitted"]
    record = client.get("/api/v1/generations").json()[0]
    assert record["resolution"] == "720p"
    assert record["prompt"] == narration
    assert (
        record["request_snapshot"]["metadata"]["selected_keyframe"]["candidate_index"]
        == 0
    )
    video_post = next(
        r
        for r in image_api[0]
        if r.method == "POST" and "/image-to-video/" in r.url.path
    )
    assert json.loads(video_post.content)["settings"]["audio"] == "native"
    workflow_tick(client, batch)
    assert sum(r.method == "POST" for r in image_api[0]) == 2


def test_workflow_reuses_existing_frame_or_explicitly_regenerates(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    images, _ = create(client, shot)
    tick(client, images)
    tick(client, images)
    _, preview = plan(client, shot, "workflow", reuse_selected=False)
    assert preview["rows"][0]["needs_keyframe"] is True
    batch, _ = create(client, shot, "workflow")
    assert len(batch["items"]) == 1 and batch["items"][0]["stage"] == "videos"
    assert workflow_tick(client, batch)["items"][0]["status"] == "submitted"
    assert (
        sum(r.method == "POST" and "images/" in r.url.path for r in image_api[0]) == 1
    )


def test_workflow_validates_video_before_paying_for_images(client, shot, image_api):
    shot = blank_shot(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "duration": 16},
    )
    body, preview = plan(client, shot, "workflow")
    assert "时长" in preview["rows"][0]["error"]
    assert client.post(endpoint(client, shot), json=body).status_code == 422
    assert not image_api[0]


@pytest.mark.parametrize("failure", ["download_failure", "timeout", "reject"])
def test_workflow_failed_or_unknown_image_never_submits_video(
    client, shot, image_api, failure
):
    shot = blank_shot(client, shot)
    image_api[1][failure] = True
    batch, _ = create(client, shot, "workflow")
    workflow_tick(client, batch)
    result = workflow_tick(client, batch)
    assert result["items"][1]["status"] == "skipped"
    assert not result["active"] and client.get("/api/v1/generations").json() == []
    assert sum(r.method == "POST" for r in image_api[0]) == 1


def test_stop_workflow_during_image_generation_blocks_future_video(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot, "workflow")
    workflow_tick(client, batch)
    response = client.post(f"/api/v1/production-batches/{batch['id']}/stop")
    assert [i["status"] for i in response.json()["items"]] == ["waiting", "skipped"]
    result = workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["succeeded", "skipped"]
    assert client.get("/api/v1/generations").json() == []


def test_workflow_rejects_overlaps_and_stale_video_after_adoption(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot, "workflow")
    for kind in ("workflow", "keyframes", "videos"):
        _, preview = plan(client, shot, kind)
        assert "进行中" in preview["rows"][0]["error"]
    workflow_tick(client, batch)
    workflow_tick(client, batch)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"] + 1, "dialogue": "已改台词"},
    )
    assert workflow_tick(client, batch)["items"][1]["status"] == "failed"
    assert client.get("/api/v1/generations").json() == []


def test_workflow_text_mode_does_not_generate_unused_images(client, shot, image_api):
    shot = blank_shot(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_mode": "text_to_video"},
    )
    batch, _ = create(client, shot, "workflow")
    assert len(batch["items"]) == 1
    assert workflow_tick(client, batch)["items"][0]["status"] == "submitted"
    assert not any("images/" in r.url.path for r in image_api[0])


def test_video_batch_switches_to_seedance_only_after_definite_kling_quota_rejection(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    shot = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_mode": "text_to_video", "duration": 3},
    ).json()
    other = client.post(
        f"/api/v1/scenes/{shot['scene_id']}/shots",
        json={"order_index": 2, "shot_type": "wide", "duration": 3,
              "visual_description": "第二个镜头", "generation_mode": "text_to_video"},
    ).json()
    kling = client.app.state.video_providers.get("kling")
    original_kling = kling.transport
    kling_posts = []

    def rejected_kling(request):
        if request.method == "POST" and "/text-to-video/" in request.url.path:
            kling_posts.append(request)
            return httpx.Response(402, json={"code": 1102, "message": "resource package exhausted"})
        return original_kling.handle_request(request)

    kling.transport = httpx.MockTransport(rejected_kling)
    seedance = client.app.state.video_providers.get("seedance")
    original_seedance = seedance.transport
    seedance_posts = []

    def captured_seedance(request):
        if request.method == "POST":
            seedance_posts.append(request)
        return original_seedance.handle_request(request)

    seedance.transport = httpx.MockTransport(captured_seedance)
    batch, body = create(client, shot, "videos", shot_ids=[shot["id"], other["id"]],
                         fallback_provider="seedance")

    def process(index):
        with client.app.state.database.session_factory() as db:
            item = db.get(ProductionBatchItem, batch["items"][index]["id"])
            asyncio.run(process_item(db, item, client.app.state.video_providers))

    process(0)
    current = client.get(endpoint(client, shot)).json()[0]
    assert current["fallback_active"] is True
    assert current["items"][0]["status"] == "queued"
    assert client.post(endpoint(client, shot), json=body).json()["id"] == batch["id"]
    process(1)
    process(0)
    current = client.get(endpoint(client, shot)).json()[0]
    assert [item["status"] for item in current["items"]] == ["submitted", "submitted"]
    assert [item["provider"] for item in current["items"]] == ["seedance", "seedance"]
    assert len(kling_posts) == 1 and len(seedance_posts) == 2
    assert all(json.loads(request.content)["duration"] == 4 for request in seedance_posts)
    records = client.get("/api/v1/generations").json()
    assert sum(record["provider"] == "kling" and record["status"] == "failed" for record in records) == 1
    assert sum(record["provider"] == "seedance" for record in records) == 2


def test_video_batch_uses_kling_source_prompt_without_requiring_character_element(
    client, shot, image_api
):
    shot = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_mode": "text_to_video",
              "generation_prompt": "Seedance 动作提示词"},
    ).json()
    root = client.app.state.video_providers.get("kling").data_root
    source = root / "assets" / "projects" / "PRJ_REVENGE_001" / "storyboard_details.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"shots": {shot["id"]: {
        "source_shot_id": "S01", "fields": {"可灵提示词": "可灵镜头提示词"}
    }}}))
    _, preview = plan(client, shot, "videos", fallback_provider="seedance")
    row = preview["rows"][0]
    assert row["error"] is None
    assert row["label"].startswith("S01")
    assert row["video"]["prompt"].startswith("可灵镜头提示词")
    assert row["video"]["use_character_bindings"] is False


@pytest.mark.parametrize("status,code,expected", [(429, 1302, "queued"), (503, None, "unknown")])
def test_video_batch_does_not_switch_on_rate_limit_or_ambiguous_failure(
    client, shot, image_api, status, code, expected
):
    shot = blank_shot(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_mode": "text_to_video"},
    )
    kling = client.app.state.video_providers.get("kling")

    def rejected(request):
        return httpx.Response(status, json={"code": code, "message": "not enough capacity"})

    kling.transport = httpx.MockTransport(rejected)
    batch, _ = create(client, shot, "videos", fallback_provider="seedance")
    with client.app.state.database.session_factory() as db:
        item = db.get(ProductionBatchItem, batch["items"][0]["id"])
        asyncio.run(process_item(db, item, client.app.state.video_providers))
    current = client.get(endpoint(client, shot)).json()[0]
    assert current["fallback_active"] is False
    assert current["items"][0]["status"] == expected
    assert all(record["provider"] == "kling" for record in client.get("/api/v1/generations").json())


def test_workflow_recovers_adoption_commit_without_selecting_twice(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot, "workflow")
    workflow_tick(client, batch)
    tick(client, batch)  # Image selected; dependent video has not yet advanced.
    with client.app.state.database.session_factory() as db:
        db.get(ProductionBatchItem, batch["items"][0]["id"]).status = "waiting"
        db.commit()
    result = workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["succeeded", "queued"]
    assert workflow_tick(client, batch)["items"][1]["status"] == "submitted"
    studio = client.get(f"/api/v1/shots/{shot['id']}/keyframe-studio").json()
    assert studio["shot_version"] == shot["version"] + 1


def test_workflow_failure_does_not_block_other_shots(client, shot, image_api):
    shot = blank_shot(client, shot)
    other = client.post(
        f"/api/v1/scenes/{shot['scene_id']}/shots",
        json={
            "order_index": 2,
            "shot_type": "wide",
            "duration": 5,
            "visual_description": "另一镜头",
            "generation_mode": "text_to_video",
        },
    ).json()
    batch, _ = create(client, shot, "workflow", shot_ids=[shot["id"], other["id"]])
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "dialogue": "修改"},
    )
    result = workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["failed", "skipped", "submitted"]
    records = client.get("/api/v1/generations").json()
    assert len(records) == 1 and records[0]["shot_id"] == other["id"]


def test_worker_resumes_persisted_workflow_dependency(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot, "workflow")
    workflow_tick(client, batch)

    async def restart():
        task = asyncio.create_task(
            worker(
                client.app.state.database,
                client.app.state.video_providers,
                poll_seconds=0.01,
            )
        )
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                with client.app.state.database.session_factory() as db:
                    if db.get(ProductionBatchItem, batch["items"][1]["id"]).status in {
                        "queued",
                        "submitted",
                    }:
                        return
            raise AssertionError("Waiting dependency did not resume")
        finally:
            await stop_worker(task)

    asyncio.run(restart())
    result = workflow_tick(client, batch)
    assert result["items"][1]["status"] == "submitted"
    assert sum(r.method == "POST" for r in image_api[0]) == 2


def test_batch_keyframes_autoselect_first_and_video_native(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, body = create(client, shot)
    assert not image_api[0]  # Preview/enqueue never calls the paid API.
    duplicate = client.post(endpoint(client, shot), json=body)
    assert duplicate.status_code == 200 and duplicate.json()["id"] == batch["id"]
    assert tick(client, batch)["items"][0]["status"] == "waiting"
    assert tick(client, batch)["items"][0]["status"] == "succeeded"
    selected = client.get(f"/api/v1/shots/{shot['id']}/keyframe-studio").json()[
        "selected"
    ]
    assert selected["candidate_index"] == 0 and selected["usable"]
    _, preview = plan(client, shot)
    assert preview["rows"][0]["skip"] is True
    video, _ = create(client, shot, "videos")
    assert tick(client, video)["items"][0]["status"] == "submitted"
    record = client.get("/api/v1/generations").json()[0]
    assert record["api_parameters"]["audio"] == "native"
    assert (
        record["request_snapshot"]["metadata"]["selected_keyframe"]["selection_token"]
        == selected["selection_token"]
    )
    assert record["duration"] == shot["duration"]
    with client.app.state.database.session_factory() as db:
        item = db.get(ProductionBatchItem, video["items"][0]["id"])
        generation = db.get(Generation, item.result_id)
        assert item.payload["video"]["request_token"] == generation.request_token


def test_worker_claim_lease_prevents_concurrent_pickup(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    now = datetime.now(UTC)

    with client.app.state.database.session_factory() as first:
        assert claim_next_item(first, "worker-one", now) == batch["items"][0]["id"]
    with client.app.state.database.session_factory() as second:
        assert claim_next_item(second, "worker-two", now) is None
        item = second.get(ProductionBatchItem, batch["items"][0]["id"])
        assert item.lease_owner == "worker-one"


def test_submitted_batch_video_is_polled_and_persisted(
    client, shot, image_api, monkeypatch
):
    shot = blank_shot(client, shot)
    keyframes, _ = create(client, shot)
    assert tick(client, keyframes)["items"][0]["status"] == "waiting"
    assert tick(client, keyframes)["items"][0]["status"] == "succeeded"
    batch, _ = create(client, shot, "videos")
    submitted = tick(client, batch)
    assert submitted["active"]
    assert submitted["items"][0]["status"] == "submitted"

    provider = client.app.state.video_providers.get("kling")

    async def completed(external_id):
        return VideoGenerationStatus(
            external_id=external_id,
            status="succeeded",
            output_url="https://cdn.example.invalid/result.mp4",
        )

    async def download(_external_id, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"durable-video")
        return destination

    monkeypatch.setattr(provider, "get_status", completed)
    monkeypatch.setattr(provider, "download_result", download)
    ready = tick(client, batch)
    assert not ready["active"]
    assert ready["items"][0]["status"] == "ready"
    generation = client.get("/api/v1/generations").json()[0]
    assert generation["status"] == "ready"
    assert generation["output_path"] == (
        f"assets/generations/{generation['id']}/result.mp4"
    )
    assert generation["output_bytes"] == len(b"durable-video")
    assert generation["output_sha256"] == hashlib.sha256(b"durable-video").hexdigest()
    assert (
        client.get(
            "/media/" + generation["output_path"].removeprefix("assets/")
        ).content
        == b"durable-video"
    )


def test_download_failure_is_terminal_without_video_resubmission(
    client, shot, image_api, monkeypatch
):
    shot = blank_shot(client, shot)
    keyframes, _ = create(client, shot)
    tick(client, keyframes)
    tick(client, keyframes)
    batch, _ = create(client, shot, "videos")
    tick(client, batch)
    provider = client.app.state.video_providers.get("kling")

    async def completed(external_id):
        return VideoGenerationStatus(external_id=external_id, status="succeeded")

    async def download(_external_id, _destination):
        raise ProviderResponseError("cdn unavailable")

    monkeypatch.setattr(provider, "get_status", completed)
    monkeypatch.setattr(provider, "download_result", download)
    failed = tick(client, batch)
    assert failed["items"][0]["status"] == "failed"
    generation = client.get("/api/v1/generations").json()[0]
    assert generation["status"] == "download_failed"
    post_count = sum(
        request.method == "POST" and "images/" not in request.url.path
        for request in image_api[0]
    )
    tick(client, batch)
    assert (
        sum(
            request.method == "POST" and "images/" not in request.url.path
            for request in image_api[0]
        )
        == post_count
    )


def test_worker_only_marks_expired_submission_unknown(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    now = datetime.now(UTC)
    with client.app.state.database.session_factory() as db:
        item = db.get(ProductionBatchItem, batch["items"][0]["id"])
        item.status = "submitting"
        item.lease_owner = "active-worker"
        item.lease_until = now + timedelta(minutes=5)
        db.commit()

    with client.app.state.database.session_factory() as db:
        assert claim_next_item(db, "other-worker", now) is None
        item = db.get(ProductionBatchItem, batch["items"][0]["id"])
        assert item.status == "submitting"
        assert item.lease_owner == "active-worker"


def test_missing_reference_and_missing_frame_are_preflight_errors(
    client, shot, image_api
):
    _, preview = plan(client, shot)
    assert preview["rows"][0]["error"]
    shot = blank_shot(client, shot)
    _, preview = plan(client, shot, "videos")
    assert "首帧" in preview["rows"][0]["error"]
    assert not image_api[0]


def test_confirmation_and_changed_preview_do_not_charge(client, shot, image_api):
    shot = blank_shot(client, shot)
    body, _ = plan(client, shot)
    assert (
        client.post(
            endpoint(client, shot), json={**body, "confirmed": False}
        ).status_code
        == 422
    )
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "visual_description": "新画面"},
    )
    assert client.post(endpoint(client, shot), json=body).status_code == 409
    assert not image_api[0]


def test_changed_queued_shot_fails_without_charge(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "dialogue": "已修改"},
    )
    assert tick(client, batch)["items"][0]["status"] == "failed"
    assert not image_api[0]


def test_stop_queued_and_overlapping_batch(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    _, preview = plan(client, shot)
    assert "进行中" in preview["rows"][0]["error"]
    result = client.post(f"/api/v1/production-batches/{batch['id']}/stop")
    assert result.json()["items"][0]["status"] == "skipped"
    assert tick(client, batch)["active"] is False and not image_api[0]


def test_unknown_submission_is_never_retried(client, shot, image_api):
    shot = blank_shot(client, shot)
    image_api[1]["timeout"] = True
    batch, _ = create(client, shot)
    tick(client, batch)
    final = tick(client, batch)
    assert final["items"][0]["status"] == "unknown"
    posts = sum(r.method == "POST" for r in image_api[0])
    tick(client, batch)
    assert sum(r.method == "POST" for r in image_api[0]) == posts == 1


def test_first_candidate_failure_does_not_fall_back_to_second(client, shot, image_api):
    shot = blank_shot(client, shot)
    image_api[1].update(two_images=True, missing_first=True)
    batch, _ = create(client, shot, image_count=2)
    tick(client, batch)
    result = tick(client, batch)
    assert result["items"][0]["status"] == "failed"
    assert "第一张" in result["items"][0]["error"]
    studio = client.get(f"/api/v1/shots/{shot['id']}/keyframe-studio").json()
    assert studio["selected"] is None
    assert studio["jobs"][0]["candidates"][1]["path"]


def test_stop_keeps_submitted_keyframe_running(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    tick(client, batch)
    response = client.post(f"/api/v1/production-batches/{batch['id']}/stop")
    assert response.json()["items"][0]["status"] == "waiting"
    assert tick(client, batch)["items"][0]["status"] == "succeeded"


def test_edit_during_generation_preserves_candidates_without_adopting(
    client, shot, image_api
):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    tick(client, batch)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "dialogue": "新版对白"},
    )
    assert tick(client, batch)["items"][0]["status"] == "failed"
    studio = client.get(f"/api/v1/shots/{shot['id']}/keyframe-studio").json()
    assert studio["selected"] is None and studio["jobs"][0]["candidates"][0]["path"]


def test_video_timeout_is_unknown_and_never_retried(client, shot, image_api):
    shot = blank_shot(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_mode": "text_to_video"},
    )
    image_api[1]["timeout"] = True
    batch, _ = create(client, shot, "videos")
    assert tick(client, batch)["items"][0]["status"] == "unknown"
    tick(client, batch)
    assert sum(r.method == "POST" for r in image_api[0]) == 1


def test_mixed_batch_uses_each_shots_own_prompt_and_duration(client, shot, image_api):
    shot = blank_shot(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={
            "expected_version": shot["version"],
            "generation_mode": "text_to_video",
            "generation_prompt": "第一个镜头对白",
        },
    )
    second = client.post(
        f"/api/v1/scenes/{shot['scene_id']}/shots",
        json={
            "order_index": 2,
            "shot_type": "wide",
            "duration": 8,
            "visual_description": "另一个空间",
            "generation_prompt": "第二个镜头对白",
            "generation_mode": "text_to_video",
        },
    ).json()
    invalid = client.post(
        f"/api/v1/scenes/{shot['scene_id']}/shots",
        json={
            "order_index": 3,
            "shot_type": "wide",
            "duration": 16,
            "visual_description": "时长不合法",
            "generation_mode": "text_to_video",
        },
    ).json()
    batch, _ = create(
        client, shot, "videos", shot_ids=[shot["id"], second["id"], invalid["id"]]
    )
    assert [i["status"] for i in batch["items"]] == ["queued", "queued", "failed"]
    with client.app.state.database.session_factory() as db:
        for item in db.scalars(
            select(ProductionBatchItem).where(
                ProductionBatchItem.batch_id == batch["id"]
            )
        ).all():
            asyncio.run(process_item(db, item, client.app.state.video_providers))
    records = {r["shot_id"]: r for r in client.get("/api/v1/generations").json()}
    assert records[shot["id"]]["prompt"] == "第一个镜头对白"
    assert records[second["id"]]["prompt"] == "第二个镜头对白"
    assert records[second["id"]]["duration"] == 8
    assert len(records) == 2 and all(
        r["api_parameters"]["audio"] == "native" for r in records.values()
    )


def test_worker_restart_does_not_replay_submitting_item(client, shot, image_api):
    shot = blank_shot(client, shot)
    batch, _ = create(client, shot)
    with client.app.state.database.session_factory() as db:
        item = db.get(ProductionBatchItem, batch["items"][0]["id"])
        item.status = "submitting"
        db.commit()

    async def restart():
        task = asyncio.create_task(
            worker(
                client.app.state.database,
                client.app.state.video_providers,
                poll_seconds=0.01,
            )
        )
        await asyncio.sleep(0.01)
        await stop_worker(task)

    asyncio.run(restart())
    result = tick(client, batch)
    assert result["items"][0]["status"] == "unknown" and not image_api[0]


def test_revision_lock_and_invalid_selection(client, shot, image_api):
    shot = blank_shot(client, shot)
    with client.app.state.database.session_factory() as db:
        db.get(Shot, shot["id"]).scene.episode.status = "revising"
        db.commit()
    _, preview = plan(client, shot)
    assert "修订" in preview["rows"][0]["error"]
    body = {
        "kind": "keyframes",
        "shot_ids": [shot["id"], shot["id"]],
        "request_token": str(uuid4()),
    }
    assert (
        client.post(endpoint(client, shot) + "/preview", json=body).status_code == 422
    )
    body["shot_ids"] = ["foreign-shot"]
    assert (
        client.post(endpoint(client, shot) + "/preview", json=body).status_code == 422
    )


@pytest.mark.parametrize("mode", ["first_last_frame", "reference_to_video"])
def test_unsupported_modes_do_not_submit(client, shot, image_api, mode):
    shot = blank_shot(client, shot)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_mode": mode},
    )
    _, preview = plan(client, shot, "videos")
    assert "单独提交" in preview["rows"][0]["error"]
    assert not image_api[0]


def test_three_retries_then_seedance_preserves_audio_and_order(client, shot, image_api):
    shot = blank_shot(client, shot)
    shot = client.patch(f"/api/v1/shots/{shot['id']}", json={"expected_version": shot["version"], "generation_mode": "text_to_video", "duration": 2}).json()
    second = client.post(f"/api/v1/scenes/{shot['scene_id']}/shots", json={"order_index": 2, "shot_type": "wide", "duration": 5, "visual_description": "second", "generation_mode": "text_to_video"}).json()
    registry = client.app.state.video_providers
    attempts = []
    def rejected(request):
        attempts.append(request)
        return httpx.Response(429, json={"code": 1302, "message": "rate limit"})
    registry.get("kling").transport = httpx.MockTransport(rejected)
    batch, _ = create(client, shot, "videos", fallback_provider="seedance", kling_retries=3, shot_ids=[shot["id"], second["id"]])
    item_id = batch["items"][0]["id"]
    for index in range(4):
        with client.app.state.database.session_factory() as db:
            item = db.get(ProductionBatchItem, item_id)
            item.lease_until = None
            db.commit()
            assert claim_next_item(db, "test") == item_id
            asyncio.run(process_item(db, db.get(ProductionBatchItem, item_id), registry, "test"))
            assert claim_next_item(db, "second-worker") != batch["items"][1]["id"]
    result = client.get(endpoint(client, shot)).json()[0]
    assert len(attempts) == 4
    assert result["fallback_active"] is True
    assert result["items"][0]["status"] == "queued"
    with client.app.state.database.session_factory() as db:
        from app.models.entities import ProductionBatch
        from app.services.production_batches import _seedance_video_payload
        item = db.get(ProductionBatchItem, item_id)
        video = _seedance_video_payload(db.get(ProductionBatch, batch["id"]), item, db.get(Shot, shot["id"]))
        assert video["duration"] == 4
        assert video["api_parameters"]["generate_audio"] is True
        assert len(item.payload["kling_failures"]) == 4


def test_recovery_does_not_replay_seedance_rejected_portrait(client, shot, image_api):
    from app.models.entities import ProductionBatch
    from app.services.production_batches import _seedance_video_payload
    shot = blank_shot(client, shot)
    client.patch(f"/api/v1/shots/{shot['id']}", json={"expected_version": shot["version"], "generation_mode": "text_to_video"})
    batch, _ = create(client, shot, "videos", fallback_provider="seedance")
    with client.app.state.database.session_factory() as db:
        b = db.get(ProductionBatch, batch["id"])
        item = db.get(ProductionBatchItem, batch["items"][0]["id"])
        source = db.get(Shot, shot["id"])
        original = _seedance_video_payload(b, item, source)["request_token"]
        item.payload = {**item.payload, "recovery_attempt": 1, "seedance_input_blocked": True}
        recovered = _seedance_video_payload(b, item, source)["request_token"]
        assert original != recovered
        assert recovered == _seedance_video_payload(b, item, source)["request_token"]
        b.options = {**b.options, "fallback_active": True}
        db.commit()
        asyncio.run(process_item(db, item, client.app.state.video_providers))
        assert item.status == "failed"
        assert "人像" in item.error
    assert not image_api[0]
    assert client.get("/api/v1/generations").json() == []
