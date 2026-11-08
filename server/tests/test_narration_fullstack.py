"""R7-07：真实 ModelSessionStore + httpx.MockTransport + 真实上下文 + generate_draft 的全栈闭环。"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from conftest import create_world, upload

SAMPLE = "# 云山纪\n\n## 渡云港\n\n渡云港位于山脚第一重云海旁，是商贾与山民交汇之所。\n"


def completion(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": finish_reason,
                }
            ]
        },
    )


def make_client(tmp_path, handler):
    store = ModelSessionStore(http_transport=httpx.MockTransport(handler))
    app = create_app(Settings(db_path=tmp_path / "fullstack.db"), narration_store=store)
    return TestClient(app), store


def new_session(client):
    response = client.post(
        "/api/v1/model-sessions",
        json={"base_url": "https://api.example.invalid/v1", "model": "mock-model", "api_key": "sk-mock"},
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def test_live_preview_full_stack_success(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"].startswith("Bearer ")
        return completion(json.dumps({"narrative": "渡云港的风里有盐味。", "references": []}))

    client, _ = make_client(tmp_path, handler)
    world = create_world(client)
    upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8"))
    session_id = new_session(client)

    response = client.post(
        f"/api/v1/worlds/{world['id']}/narration-preview",
        json={"action": "我去渡云港看看", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "live"
    assert body["narrative"] == "渡云港的风里有盐味。"
    assert body["references"] == []
    assert body["sources"], "真实上下文应提供来源"
    assert "sk-mock" not in json.dumps(body)


def test_live_preview_rejects_invented_reference(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return completion(json.dumps({"narrative": "编造引用", "references": ["chunk:not-provided"]}))

    client, _ = make_client(tmp_path, handler)
    world = create_world(client)
    upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8"))
    session_id = new_session(client)

    response = client.post(
        f"/api/v1/worlds/{world['id']}/narration-preview",
        json={"action": "随便看看", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_REFERENCE_INVALID"


def test_live_preview_maps_auth_failure(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client, _ = make_client(tmp_path, handler)
    world = create_world(client)
    upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8"))
    session_id = new_session(client)

    response = client.post(
        f"/api/v1/worlds/{world['id']}/narration-preview",
        json={"action": "看看", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 401
    assert "sk-mock" not in response.text


def test_live_preview_maps_rate_limit(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    client, _ = make_client(tmp_path, handler)
    world = create_world(client)
    upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8"))
    session_id = new_session(client)

    response = client.post(
        f"/api/v1/worlds/{world['id']}/narration-preview",
        json={"action": "看看", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 429


def test_live_preview_maps_upstream_timeout(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream slow", request=request)

    client, _ = make_client(tmp_path, handler)
    world = create_world(client)
    upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8"))
    session_id = new_session(client)

    response = client.post(
        f"/api/v1/worlds/{world['id']}/narration-preview",
        json={"action": "看看", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "MODEL_TIMEOUT"


def test_live_preview_does_not_write_world(tmp_path):
    import sqlite3

    def handler(request: httpx.Request) -> httpx.Response:
        return completion(json.dumps({"narrative": "只读预览", "references": []}))

    client, _ = make_client(tmp_path, handler)
    world = create_world(client)
    upload(client, world["id"], "云山纪.md", SAMPLE.encode("utf-8"))
    session_id = new_session(client)

    db_path = tmp_path / "fullstack.db"
    tables = ("worlds", "documents", "chunks", "lore_entries", "lore_entry_sources")

    def snapshot():
        with sqlite3.connect(db_path) as conn:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

    before = snapshot()
    response = client.post(
        f"/api/v1/worlds/{world['id']}/narration-preview",
        json={"action": "看看", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 200, response.text
    assert snapshot() == before
