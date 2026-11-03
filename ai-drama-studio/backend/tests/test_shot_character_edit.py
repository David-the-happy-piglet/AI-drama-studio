def test_edit_characters_preserves_shot_and_assets(client, project, character, shot):
    other = client.post(
        f"/api/v1/projects/{project['id']}/characters", json={"name": "Second"}
    ).json()
    character_before = client.get(f"/api/v1/characters/{character['id']}").json()
    endpoint = f"/api/v1/shots/{shot['id']}"
    characters = [*shot["characters"], {"character_id": other["id"]}]
    response = client.patch(endpoint, json={
        "expected_version": shot["version"], "characters": characters,
    })
    assert response.status_code == 200
    updated = response.json()
    assert updated["version"] == shot["version"] + 1
    assert updated["characters"][0] == shot["characters"][0]
    assert updated["characters"][1]["character_id"] == other["id"]
    for field in ("generation_prompt", "dialogue", "duration", "generation_mode", "status"):
        assert updated[field] == shot[field]
    assert client.get(f"/api/v1/characters/{character['id']}").json() == character_before
    assert client.patch(endpoint, json={
        "expected_version": shot["version"], "characters": [],
    }).status_code == 409
    cleared = client.patch(endpoint, json={
        "expected_version": updated["version"], "characters": [],
    })
    assert cleared.status_code == 200
    assert cleared.json()["characters"] == []
    assert client.get("/api/v1/generations", params={"shot_id": shot["id"]}).json() == []


def test_edit_rejects_other_project_character(client, shot):
    project = client.post("/api/v1/projects", json={"name": "Other"}).json()
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters", json={"name": "Unrelated"}
    ).json()
    result = client.patch(f"/api/v1/shots/{shot['id']}", json={
        "expected_version": shot["version"],
        "characters": [{"character_id": character["id"]}],
    })
    assert result.status_code == 422
