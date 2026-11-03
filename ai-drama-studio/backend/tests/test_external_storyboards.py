from copy import deepcopy
from uuid import uuid4


def payload(character_id):
    return {
        "request_token": str(uuid4()),
        "title": "Claude 原稿",
        "source_idea": "人物发现一封信。",
        "draft": {
            "screenplay": "她说：别打开。\n他没有回答。",
            "scenes": [
                {
                    "location": "旧办公室",
                    "shots": [
                        {
                            "order_index": 1,
                            "duration": 5,
                            "shot_type": "近景",
                            "characters": [{"character_id": character_id}],
                            "visual_description": "她的手停在信封上方。",
                            "dialogue": "她：别打开。",
                            "generation_prompt": "手指静止，然后缓慢收回。",
                        }
                    ],
                }
            ],
        },
    }


def test_import_preserves_text_and_retries_without_duplicate(
    client, project, character, monkeypatch
):
    async def forbidden(*args, **kwargs):
        raise AssertionError("External import must not call a model")

    monkeypatch.setattr("app.api.planning.generate_draft", forbidden)
    monkeypatch.setattr("app.api.planning.generate_storyboard", forbidden)
    body = payload(character["id"])
    url = f"/api/v1/projects/{project['id']}/external-storyboards"
    result = client.post(url, json=body)
    assert result.status_code == 200, result.text
    script = result.json()
    assert (
        script["structured_content"]["draft"]["screenplay"]
        == body["draft"]["screenplay"]
    )
    assert (
        script["structured_content"]["draft"]["scenes"][0]["shots"][0]["dialogue"]
        == "她：别打开。"
    )
    assert not script["structured_content"]["screenplay_approved"]
    assert script["character_ids"] == [character["id"]]
    assert client.post(url, json=body).json()["id"] == script["id"]
    assert len(client.get(f"/api/v1/projects/{project['id']}/scripts").json()) == 1
    assert client.get(f"/api/v1/projects/{project['id']}/episodes").json() == []
    body["title"] = "不同内容"
    assert client.post(url, json=body).status_code == 409


def test_import_validation_is_atomic(client, project, character):
    body = payload(character["id"])
    url = f"/api/v1/projects/{project['id']}/external-storyboards"
    unknown = deepcopy(body)
    unknown["draft"]["scenes"][0]["shots"][0]["characters"] = [
        {"character_id": "missing"}
    ]
    assert client.post(url, json=unknown).status_code == 422
    unordered = deepcopy(body)
    unordered["draft"]["scenes"][0]["shots"][0]["order_index"] = 2
    assert client.post(url, json=unordered).status_code == 422
    stale_ids = deepcopy(body)
    stale_ids["draft"]["scenes"][0]["id"] = "existing-scene"
    assert client.post(url, json=stale_ids).status_code == 422
    assert client.get(f"/api/v1/projects/{project['id']}/scripts").json() == []


def test_import_into_episode_then_publish_and_retry(client, project, character):
    episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes",
        json={
            "title": "第一集",
            "episode_number": 1,
            "duration_target": 5,
        },
    ).json()
    body = payload(character["id"])
    url = f"/api/v1/projects/{project['id']}/external-storyboards?episode_id={episode['id']}"
    created = client.post(url, json=body)
    assert created.status_code == 200, created.text
    script = created.json()
    endpoint = f"/api/v1/scripts/{script['id']}"
    approved = client.put(
        endpoint + "/review",
        json={
            "expected_version": script["version"],
            "draft": script["structured_content"]["draft"],
            "screenplay_approved": True,
        },
    ).json()
    published = client.post(
        endpoint + "/publish-storyboard", json={"expected_version": approved["version"]}
    )
    assert published.status_code == 200, published.text
    assert published.json()["episode_id"] == episode["id"]
    assert client.post(url, json=body).json()["id"] == script["id"]
    body["request_token"] = str(uuid4())
    assert client.post(url, json=body).status_code == 409
    board = client.get(f"/api/v1/episodes/{episode['id']}/storyboard").json()
    assert board["scenes"][0]["shots"][0]["dialogue"] == "她：别打开。"
