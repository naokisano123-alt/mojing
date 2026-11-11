from fastapi.testclient import TestClient

from app.config import Settings
from app.db import db
from app.main import create_app
from conftest import create_world, upload
from test_world_transfer import import_files


def test_document_edit_rechunks_and_retains_cited_history(client, db_path, tmp_path):
    world = create_world(client)["id"]
    old_text = "# 海港\n\n旧灯塔在北岸。"
    doc = upload(client, world, "设定.md", old_text.encode()).json()
    old_chunk = client.get(f"/api/v1/documents/{doc['id']}/chunks").json()["items"][0]
    entry = client.post(f"/api/v1/worlds/{world}/lore-entries", json={
        "type": "location", "title": "灯塔", "summary": "手工记录的灯塔。", "status": "confirmed",
        "source_links": [{"document_id": doc["id"], "chunk_id": old_chunk["id"],
                          "start_char": old_chunk["start_char"], "end_char": old_chunk["end_char"]}],
    }).json()
    text = "# 港口\n\n新灯塔在南岸🌊。\n\n## 码头\n商船每晨抵达。"
    payload = {"text": text, "expected_content_sha256": doc["content_sha256"]}
    path = f"/api/v1/documents/{doc['id']}"
    response = client.put(path, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == doc["id"]
    assert response.json()["content_sha256"] != doc["content_sha256"]
    assert client.put(path, json=payload).json() == response.json()
    assert client.get(path + "/raw").json()["text"] == text
    assert client.get(f"/api/v1/worlds/{world}").json()["document_count"] == 1
    matches = client.get("/api/v1/search", params={"world_id": world, "q": "南岸"}).json()
    assert matches["total"] >= 1
    assert all(item["chunk_id"] != old_chunk["id"] for item in matches["items"])
    assert client.get("/api/v1/search", params={"world_id": world, "q": "北岸"}).json()["total"] == 0
    narration = client.post(f"/api/v1/worlds/{world}/narration-preview",
                            json={"mode": "fake", "action": "查看南岸"}).json()
    assert any(source["source_id"].startswith("chunk:") for source in narration["sources"])
    assert all(source["source_id"] != f"chunk:{old_chunk['id']}" for source in narration["sources"])
    link = client.get(f"/api/v1/lore-entries/{entry['id']}").json()["source_links"][0]
    assert link["availability"] == "deleted" and "北岸" in link["excerpt"]
    with db(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM documents WHERE deleted_at IS NOT NULL").fetchone()[0] == 1
        row = conn.execute("SELECT c.text,d.deleted_at FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?",
                           (old_chunk["id"],)).fetchone()
        assert "北岸" in row["text"] and row["deleted_at"]

    archive = client.get(f"/api/v1/worlds/{world}/transfer?part=zip")
    with TestClient(create_app(Settings(db_path=tmp_path / "second.db"))) as phone:
        result = import_files(phone, ("world.zip", archive.content))
        assert result.status_code == 201, result.text
        copied = phone.get(f"/api/v1/worlds/{result.json()['id']}").json()["documents"][0]
        assert phone.get(f"/api/v1/documents/{copied['id']}/raw").json()["text"] == text


def test_stale_or_invalid_document_edit_never_replaces_current_text(client):
    world = create_world(client)["id"]
    doc = upload(client, world, "规则.txt", "原文".encode()).json()
    path = f"/api/v1/documents/{doc['id']}"
    old = doc["content_sha256"]
    assert client.put(path, json={"text": "第一次修改", "expected_content_sha256": old}).status_code == 200
    stale = client.put(path, json={"text": "过期的修改", "expected_content_sha256": old})
    assert stale.status_code == 409
    assert client.put(path, json={"text": "", "expected_content_sha256": old}).status_code == 422
    assert client.get(path + "/raw").json()["text"] == "第一次修改"


def test_manual_settings_search_is_world_scoped(client):
    world = create_world(client)["id"]
    other = create_world(client, "另一个世界")["id"]
    for wid, title in [(world, "海港宵禁"), (world, "山城集市"), (other, "海港秘密")]:
        assert client.post(f"/api/v1/worlds/{wid}/lore-entries", json={
            "type": "rule", "title": title, "summary": "规则说明", "status": "confirmed",
        }).status_code == 201
    result = client.get(f"/api/v1/worlds/{world}/lore-entries", params={"q": "海港"}).json()
    assert result["total"] == 1 and result["items"][0]["title"] == "海港宵禁"
