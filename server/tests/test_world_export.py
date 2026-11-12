
def world(client, name):
    response = client.post("/api/v1/worlds", json={"name": name, "description": "世界简介"})
    assert response.status_code == 201
    return response.json()["id"]


def run(client, world_id, mode="fake"):
    response = client.post(f"/api/v1/worlds/{world_id}/adventures", json={
        "name": "出发", "mode": mode,
        "character": {"inventory": [{"name": "旧罗盘", "quantity": 1}]},
    })
    assert response.status_code == 201
    return response.json()["id"]


def write_note(client, adventure, name, description):
    return client.patch(f"/api/v1/adventures/{adventure}/item-description",
                        json={"name": name, "description": description})


def test_world_export_separates_settings_from_confirmed_saves(client):
    home = world(client, "北域领")
    other = world(client, "别的世界")
    home_run = run(client, home)
    other_run = run(client, other)
    assert write_note(client, home_run, "旧罗盘", "只有穿过霜河时才会转动。").status_code == 200
    assert write_note(client, other_run, "旧罗盘", "其他世界的资料").status_code == 200

    for filename, text in [("北岸.txt", "北岸是旧城区。"), ("已删除.txt", "不应导出")]:
        upload = client.post(f"/api/v1/worlds/{home}/documents",
                             files={"file": (filename, text.encode(), "text/plain")})
        assert upload.status_code == 201
        if filename == "已删除.txt":
            assert client.delete(f"/api/v1/documents/{upload.json()['id']}").status_code == 204

    entry = client.post(f"/api/v1/worlds/{home}/lore-entries", json={
        "type": "location", "title": "珈姆玛桥", "summary": "跨越霜河。",
    })
    assert entry.status_code == 201

    path = f"/api/v1/adventures/{home_run}"
    for request_id, action, revision in [("step-1", "走上旧桥", 0), ("step-2", "未确认行动", 1)]:
        assert client.post(f"{path}/drafts", json={
            "request_id": request_id, "expected_revision": revision, "action": action,
        }).status_code == 200
        if revision == 0:
            assert client.post(f"{path}/drafts/{request_id}/commit", json={}).status_code == 200
    saved = client.post(f"{path}/checkpoints", json={
        "name": "过桥前", "request_id": "save-1", "expected_revision": 1,
    })
    assert saved.status_code == 201
    branch = client.post(f"/api/v1/checkpoints/{saved.json()['id']}/restore", json={
        "name": "另一条路", "request_id": "fork-1",
    })
    assert branch.status_code == 201

    export = f"/api/v1/worlds/{home}/export"
    settings = client.get(export)
    assert settings.status_code == 200
    assert settings.headers["content-disposition"].startswith("attachment;")
    payload = settings.json()
    assert payload["format"] == "mojing-world"
    assert payload["version"] == 1
    assert payload["world"]["id"] == home
    assert [doc["text"] for doc in payload["documents"]] == ["北岸是旧城区。"]
    assert [item["title"] for item in payload["lore_entries"]] == ["珈姆玛桥"]
    assert payload["item_descriptions"] == [{"name": "旧罗盘", "description": "只有穿过霜河时才会转动。"}]
    assert "saves" not in payload

    with_saves = client.get(export, params={"include_saves": True})
    saves = with_saves.json()["saves"]
    assert {row["id"] for row in saves["adventures"]} == {home_run, branch.json()["id"]}
    assert {row["action"] for row in saves["turns"]} == {"走上旧桥"}
    assert {row["name"] for row in saves["checkpoints"]} == {"自动存档", "过桥前"}
    assert "未确认行动" not in with_saves.text
    assert "其他世界的资料" not in with_saves.text
    assert client.get("/api/v1/worlds/missing/export").status_code == 404


def test_item_lore_follows_world_but_does_not_change_adventure_state(client):
    home = world(client, "本世界")
    a = run(client, home)
    b = run(client, home)
    before = client.get(f"/api/v1/adventures/{a}").json()
    assert write_note(client, a, "未持有", "无效").status_code == 404
    assert write_note(client, a, "旧罗盘", "字" * 2001).status_code == 422
    saved = write_note(client, a, " 旧罗盘 ", " 刻着三道浅痕。 ")
    assert saved.json() == {"name": "旧罗盘", "description": "刻着三道浅痕。"}
    other = client.get(f"/api/v1/adventures/{b}").json()
    assert other["item_descriptions"] == {"旧罗盘": "刻着三道浅痕。"}
    after = client.get(f"/api/v1/adventures/{a}").json()
    assert after["revision"] == before["revision"]
    assert after["character"] == before["character"]
    assert write_note(client, b, "旧罗盘", " ").status_code == 200
    assert client.get(f"/api/v1/adventures/{a}").json()["item_descriptions"] == {}
