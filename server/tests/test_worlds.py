from conftest import create_world


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["ok"] is True and body["db"] == "ok"


def test_create_and_list_world(client):
    world = create_world(client)
    assert world["document_count"] == 0
    assert world["name"] == "潮汐档案馆"

    listing = client.get("/api/v1/worlds").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == world["id"]


def test_create_world_rejects_blank_name(client):
    response = client.post("/api/v1/worlds", json={"name": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WORLD_NAME_INVALID"


def test_create_world_rejects_long_name(client):
    response = client.post("/api/v1/worlds", json={"name": "雾" * 41})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WORLD_NAME_INVALID"


def test_patch_world(client):
    world = create_world(client)
    response = client.patch(f"/api/v1/worlds/{world['id']}", json={"name": "潮汐档案馆 · 修订"})
    assert response.status_code == 200
    assert response.json()["name"] == "潮汐档案馆 · 修订"
    assert response.json()["description"] == world["description"]


def test_patch_empty_body_is_noop(client):
    world = create_world(client)
    response = client.patch(f"/api/v1/worlds/{world['id']}", json={})
    assert response.status_code == 200
    assert response.json()["name"] == world["name"]


def test_world_not_found(client):
    response = client.get("/api/v1/worlds/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORLD_NOT_FOUND"


def test_delete_world_is_soft_and_idempotent(client):
    world = create_world(client)
    assert client.delete(f"/api/v1/worlds/{world['id']}").status_code == 204
    assert client.get(f"/api/v1/worlds/{world['id']}").status_code == 404
    assert client.get("/api/v1/worlds").json()["total"] == 0
    assert client.delete(f"/api/v1/worlds/{world['id']}").status_code == 204


def test_worlds_are_isolated(client):
    first = create_world(client, name="世界甲")
    second = create_world(client, name="世界乙")
    assert first["id"] != second["id"]
    assert client.get("/api/v1/worlds").json()["total"] == 2
