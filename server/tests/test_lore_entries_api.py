"""M1-D 条目 HTTP 层集成测试（DSH 接线验证）。

覆盖：创建/列表/详情/更新/删除、revision 冲突、来源跨世界拒绝、来源摘要服务端生成。
"""

from conftest import create_world, upload

SAMPLE = "# 云山纪\n\n## 渡云港\n\n位于山脚第一重云海旁，是凡间商贾与山民唯一的交汇之所。\n"


def _world_with_chunk(client):
    world = create_world(client)
    doc = upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8")).json()
    chunks = client.get(f"/api/v1/documents/{doc['id']}/chunks").json()["items"]
    return world, doc, chunks[0]


def test_create_entry_with_source(client):
    world, doc, chunk = _world_with_chunk(client)
    payload = {
        "type": "location",
        "title": "渡云港",
        "summary": "山脚的港口。",
        "status": "draft",
        "source_links": [
            {
                "document_id": doc["id"],
                "chunk_id": chunk["id"],
                "start_char": chunk["start_char"],
                "end_char": chunk["end_char"],
            }
        ],
    }
    response = client.post(f"/api/v1/worlds/{world['id']}/lore-entries", json=payload)
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["revision"] == 1
    assert entry["origin"] == "manual"
    assert entry["source_links"][0]["availability"] == "active"
    assert entry["source_links"][0]["excerpt"]
    assert entry["needs_source_review"] is False


def test_manual_entry_without_source(client):
    world = create_world(client)
    response = client.post(
        f"/api/v1/worlds/{world['id']}/lore-entries",
        json={"type": "other", "title": "无来源条目", "summary": "", "status": "draft"},
    )
    assert response.status_code == 201
    assert response.json()["source_links"] == []


def test_revision_conflict_on_update(client):
    world = create_world(client)
    entry = client.post(
        f"/api/v1/worlds/{world['id']}/lore-entries",
        json={"type": "rule", "title": "第一条", "summary": "", "status": "draft"},
    ).json()

    ok = client.patch(f"/api/v1/lore-entries/{entry['id']}", json={"title": "改一次", "expected_revision": 1})
    assert ok.status_code == 200
    assert ok.json()["revision"] == 2

    stale = client.patch(f"/api/v1/lore-entries/{entry['id']}", json={"title": "再改", "expected_revision": 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "LORE_ENTRY_CONFLICT"
    assert stale.json()["error"]["details"]["current_revision"] == 2


def test_delete_entry_requires_revision(client):
    world = create_world(client)
    entry = client.post(
        f"/api/v1/worlds/{world['id']}/lore-entries",
        json={"type": "character", "title": "甲", "summary": "", "status": "draft"},
    ).json()

    assert client.delete(f"/api/v1/lore-entries/{entry['id']}", params={"expected_revision": 9}).status_code == 409
    assert client.delete(f"/api/v1/lore-entries/{entry['id']}", params={"expected_revision": 1}).status_code == 204
    assert client.get(f"/api/v1/lore-entries/{entry['id']}").status_code == 404


def test_source_from_other_world_is_rejected(client):
    world_a, doc_a, chunk_a = _world_with_chunk(client)
    world_b = create_world(client, name="世界乙")
    response = client.post(
        f"/api/v1/worlds/{world_b['id']}/lore-entries",
        json={
            "type": "location",
            "title": "跨世界来源",
            "summary": "",
            "status": "draft",
            "source_links": [
                {
                    "document_id": doc_a["id"],
                    "chunk_id": chunk_a["id"],
                    "start_char": chunk_a["start_char"],
                    "end_char": chunk_a["end_char"],
                }
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"SOURCE_CROSS_WORLD", "SOURCE_DELETED", "SOURCE_CHUNK_MISMATCH"}


def test_list_and_filter_entries(client):
    world = create_world(client)
    for title, status in [("甲", "draft"), ("乙", "confirmed"), ("丙", "draft")]:
        client.post(
            f"/api/v1/worlds/{world['id']}/lore-entries",
            json={"type": "other", "title": title, "summary": "", "status": status},
        )
    listing = client.get(f"/api/v1/worlds/{world['id']}/lore-entries").json()
    assert listing["total"] == 3
    drafts = client.get(f"/api/v1/worlds/{world['id']}/lore-entries", params={"status": "draft"}).json()
    assert drafts["total"] == 2


def test_patch_rejects_non_integer_revision(client):
    world = create_world(client)
    entry = client.post(
        f"/api/v1/worlds/{world['id']}/lore-entries",
        json={"type": "rule", "title": "严格版本", "summary": "", "status": "draft"},
    ).json()
    for bad in (True, 1.5, "1"):
        response = client.patch(
            f"/api/v1/lore-entries/{entry['id']}",
            json={"title": "改", "expected_revision": bad},
        )
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_delete_rejects_coerced_revision(client):
    world = create_world(client)
    entry = client.post(
        f"/api/v1/worlds/{world['id']}/lore-entries",
        json={"type": "rule", "title": "严格删除", "summary": "", "status": "draft"},
    ).json()
    for bad in ("true", "1.5", "1abc", "0", "-1"):
        response = client.delete(
            f"/api/v1/lore-entries/{entry['id']}", params={"expected_revision": bad}
        )
        assert response.status_code == 422, bad
    assert client.get(f"/api/v1/lore-entries/{entry['id']}").status_code == 200
