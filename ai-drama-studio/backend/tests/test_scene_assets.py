import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
import test_production_batches as batches
import test_shot_images as frames
import test_storyboard_revision as revisions
from test_character_elements import upload

from app.models import Shot


@pytest.fixture
def image_api(client, monkeypatch):
    return batches.image_api.__wrapped__(client, monkeypatch)


def make_asset(client, project, extras=False):
    body = {
        "name": "沈青家 · 夜晚",
        "description": "书桌靠窗，空椅子在桌边，暖色台灯。",
        "master_reference_path": upload(client, project, "gray", size=(800, 450)),
        "additional_reference_paths": [
            upload(client, project, "yellow", size=(800, 450))
        ]
        if extras
        else [],
    }
    response = client.post(f"/api/v1/projects/{project['id']}/scene-assets", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def bind(client, scene_id, asset_id):
    endpoint = f"/api/v1/scenes/{scene_id}/scene-master"
    current = client.get(endpoint).json()
    response = client.put(
        endpoint,
        json={"expected_version": current["scene_version"], "asset_id": asset_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def edit_body(asset, **changes):
    return {
        k: asset[k]
        for k in (
            "name",
            "description",
            "master_reference_path",
            "additional_reference_paths",
        )
    } | {"expected_version": asset["version"], **changes}


def test_local_scene_save_and_binding_never_calls_provider(
    client, project, shot, image_api
):
    asset = make_asset(client, project, extras=True)
    result = bind(client, shot["scene_id"], asset["id"])
    assert result["asset_id"] == asset["id"]
    listing = client.get(f"/api/v1/projects/{project['id']}/scene-assets").json()
    assert listing[0]["bound_scene_ids"] == [shot["scene_id"]]
    assert not image_api[0]
    with client.app.state.database.session_factory() as db:
        current = db.get(Shot, shot["id"])
        assert current.version == shot["version"] + 1
        assert (
            current.dialogue == shot["dialogue"]
            and current.characters == shot["characters"]
        )


def test_scene_and_character_bytes_sent_with_correct_reference_indexes(
    client, project, character, shot, image_api
):
    frames.add_master(client, project, character)
    asset = make_asset(client, project, extras=True)
    bind(client, shot["scene_id"], asset["id"])
    context = client.get(frames.base(shot) + "/keyframe-studio").json()
    assert context["model"] == "kling-v3-omni" and len(context["references"]) == 3
    assert "身份对应 <<<image_1>>>" in context["reference_prefix"]
    assert "场景环境参考 <<<image_2>>>" in context["reference_prefix"]
    assert "场景环境参考 <<<image_3>>>" in context["reference_prefix"]
    assert asset["description"] in context["reference_prefix"]
    job = frames.complete(client, shot)
    post = next(r for r in image_api[0] if r.method == "POST")
    assert post.url.path.endswith("/images/omni-image")
    body = json.loads(post.content)
    root = client.app.state.settings.data_root
    for sent, ref in zip(body["image_list"], context["references"], strict=True):
        assert base64.b64decode(sent["image"]) == (root / ref["path"]).read_bytes()
    assert job["references_snapshot"][1]["scene_asset_id"] == asset["id"]
    assert "不复制环境参考图中的人物" in body["prompt"]


def test_environment_only_uses_omni_not_text_and_workflow_keeps_reference(
    client, project, shot, image_api
):
    shot = frames.blank_shot(client, shot)
    asset = make_asset(client, project)
    bind(client, shot["scene_id"], asset["id"])
    batch, _ = batches.create(client, shot, "workflow")
    batches.workflow_tick(client, batch)
    batches.workflow_tick(client, batch)
    result = batches.workflow_tick(client, batch)
    assert [i["status"] for i in result["items"]] == ["succeeded", "submitted"]
    post = next(r for r in image_api[0] if r.method == "POST")
    assert json.loads(post.content)["model_name"] == "kling-v3-omni"
    assert len(json.loads(post.content)["image_list"]) == 1


def test_shared_asset_edit_invalidates_bound_frames_and_pending_workflow(
    client, project, shot, image_api
):
    shot = frames.blank_shot(client, shot)
    asset = make_asset(client, project)
    bind(client, shot["scene_id"], asset["id"])
    scene2 = client.post(
        "/api/v1/episodes/EP001/scenes",
        json={"order_index": 2, "summary": "同一地点另一场"},
    ).json()
    bind(client, scene2["id"], asset["id"])
    other = client.post(
        f"/api/v1/scenes/{scene2['id']}/shots",
        json={
            "order_index": 1,
            "duration": 5,
            "shot_type": "wide",
            "visual_description": "夜间房间",
        },
    ).json()
    batch, _ = batches.create(client, shot, "workflow")
    batches.workflow_tick(client, batch)
    batches.workflow_tick(client, batch)
    studio = client.get(frames.base(shot) + "/keyframe-studio").json()
    assert studio["selected"]["usable"]
    updated = client.put(
        f"/api/v1/scene-assets/{asset['id']}",
        json=edit_body(asset, description="台灯位于书桌左侧，保持相对位置"),
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert len(updated.json()["bound_scene_ids"]) == 2
    result = batches.workflow_tick(client, batch)
    assert result["items"][1]["status"] == "failed"
    studio = client.get(frames.base(shot) + "/keyframe-studio").json()
    assert (
        not studio["selected"]["usable"] and studio["jobs"][0]["candidates"][0]["path"]
    )
    with client.app.state.database.session_factory() as db:
        assert db.get(Shot, other["id"]).version == other["version"] + 1
    assert client.get("/api/v1/generations").json() == []


def test_cross_project_assets_invalid_images_and_version_conflicts(
    client, project, shot
):
    asset = make_asset(client, project)
    other = client.post("/api/v1/projects", json={"name": "另一项目"}).json()
    copied = client.post(
        f"/api/v1/projects/{other['id']}/scene-assets", json=edit_body(asset)
    )
    assert copied.status_code == 422
    foreign = make_asset(client, other)
    binding = client.get(f"/api/v1/scenes/{shot['scene_id']}/scene-master").json()
    assert (
        client.put(
            f"/api/v1/scenes/{shot['scene_id']}/scene-master",
            json={
                "expected_version": binding["scene_version"],
                "asset_id": foreign["id"],
            },
        ).status_code
        == 422
    )
    duplicate = edit_body(
        asset, additional_reference_paths=[asset["master_reference_path"]]
    )
    assert (
        client.put(f"/api/v1/scene-assets/{asset['id']}", json=duplicate).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/v1/scene-assets/{asset['id']}",
            json=edit_body(asset, expected_version=99),
        ).status_code
        == 409
    )
    assert (
        client.put(f"/api/v1/scene-assets/{asset['id']}", json=edit_body(asset)).json()[
            "version"
        ]
        == 1
    )


def test_unbind_and_revision_guard(client, project, shot):
    asset = make_asset(client, project)
    bind(client, shot["scene_id"], asset["id"])
    assert bind(client, shot["scene_id"], None)["asset_id"] is None
    with client.app.state.database.session_factory() as db:
        current = db.get(Shot, shot["id"])
        current.scene.episode.status = "revising"
        version = current.scene.version
        assert current.version == shot["version"] + 2
        db.commit()
    response = client.put(
        f"/api/v1/scenes/{shot['scene_id']}/scene-master",
        json={"expected_version": version, "asset_id": asset["id"]},
    )
    assert response.status_code == 409


def test_missing_scene_file_blocks_generation_without_text_fallback(
    client, project, shot, image_api
):
    shot = frames.blank_shot(client, shot)
    asset = make_asset(client, project)
    bind(client, shot["scene_id"], asset["id"])
    path = client.app.state.settings.data_root / asset["master_reference_path"]
    path.rename(Path(str(path) + ".moved"))
    studio = client.get(frames.base(shot) + "/keyframe-studio").json()
    assert studio["errors"] and studio["model"] == "kling-v3-omni"
    response = client.post(
        frames.base(shot) + "/keyframe-jobs", json=frames.body(client, shot)
    )
    assert response.status_code == 422 and not image_api[0]


def test_binding_survives_unchanged_storyboard_republish(client, project):
    endpoint, board_url, board = revisions.published(client, project)
    scene_id = board["scenes"][0]["id"]
    asset = make_asset(client, project)
    bind(client, scene_id, asset["id"])
    opened = revisions.reopen(client, endpoint)
    revisions.save_and_publish(
        client, endpoint, opened, opened["structured_content"]["draft"]
    )
    assert client.get(board_url).json()["scenes"][0]["id"] == scene_id
    assert (
        client.get(f"/api/v1/scenes/{scene_id}/scene-master").json()["asset_id"]
        == asset["id"]
    )


def test_scene_upload_and_asset_edit_warns_old_video(client, project, shot, image_api):
    shot = frames.blank_shot(client, shot)
    asset = make_asset(client, project)
    root = client.app.state.settings.data_root
    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"asset_kind": "scene_reference"},
        files={
            "file": (
                "room.png",
                (root / asset["master_reference_path"]).read_bytes(),
                "image/png",
            )
        },
    )
    assert (
        uploaded.status_code == 200 and "/scene_reference/" in uploaded.json()["path"]
    )
    bind(client, shot["scene_id"], asset["id"])
    job = frames.complete(client, shot)
    studio = client.get(frames.base(shot) + "/keyframe-studio").json()
    adopted = frames.adopt(client, shot, job, version=studio["shot_version"])
    assert adopted.status_code == 200
    video = client.post(
        frames.base(shot) + "/generations",
        json={
            "request_token": str(uuid4()),
            "confirmed": True,
            "provider": "mock",
            "keyframe_selection_token": adopted.json()["selected"]["selection_token"],
        },
    )
    assert video.status_code == 201
    updated = client.put(
        f"/api/v1/scene-assets/{asset['id']}",
        json=edit_body(asset, description="更新场景照明"),
    )
    assert updated.status_code == 200
    assert client.get("/api/v1/generations").json()[0]["review_warning"]
