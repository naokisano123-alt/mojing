"""M2-A 叙事实验路由的注入接口测试（不联网、不写库）。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.narration.core import ContextSource


class StubContext:
    def __init__(self, sources, metadata):
        self.sources = sources
        self.metadata = metadata


def make_context_builder():
    calls = {"count": 0}

    def build(conn, world_id, action):
        calls["count"] += 1
        if world_id == "missing":
            error = RuntimeError("world missing")
            error.code = "WORLD_NOT_FOUND"
            error.status = 404
            error.message = "世界不存在"
            raise error
        return StubContext(
            sources=(ContextSource("chunk:c1", "chunk", "渡云港位于山脚。"),),
            metadata=(
                {
                    "source_id": "chunk:c1",
                    "kind": "chunk",
                    "title": "云山纪",
                    "document_id": "d1",
                    "chunk_id": "c1",
                    "chunk_index": 0,
                },
            ),
        )

    return build, calls


class StubStore:
    def __init__(self):
        self.sessions = {}
        self.deleted = []

    def create(self, base_url, model, api_key):
        sid = "s" + str(len(self.sessions) + 1)
        self.sessions[sid] = {"base_url": base_url, "model": model, "api_key": api_key}
        return {"session_id": sid, "model": model, "expires_at": "2026-11-08T23:00:00Z"}

    def delete(self, session_id):
        self.deleted.append(session_id)
        self.sessions.pop(session_id, None)

    def test(self, session_id):
        if session_id not in self.sessions:
            error = RuntimeError("expired")
            error.code = "MODEL_SESSION_EXPIRED"
            error.status = 409
            error.message = "会话已过期"
            raise error
        return {"ok": True, "model": self.sessions[session_id]["model"]}

    def get_client(self, session_id):
        if session_id not in self.sessions:
            error = RuntimeError("expired")
            error.code = "MODEL_SESSION_EXPIRED"
            error.status = 409
            error.message = "会话已过期"
            raise error

        class Client:
            async def generate(self, request):
                return {
                    "narrative": "渡云港的风把盐味吹进屋檐。",
                    "references": [request.sources[0].source_id],
                }

        return Client()


@pytest.fixture()
def stub_app(tmp_path):
    builder, calls = make_context_builder()
    store = StubStore()
    app = create_app(
        Settings(db_path=tmp_path / "m2.db"),
        narration_store=store,
        context_builder=builder,
    )
    return TestClient(app), store, calls


def test_fake_preview_returns_draft_with_sources(stub_app):
    client, _, calls = stub_app
    response = client.post(
        "/api/v1/worlds/w1/narration-preview",
        json={"action": "我问店主去哪里找活干", "mode": "fake"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "fake"
    assert body["request_id"]
    assert body["narrative"]
    assert body["references"] == ["chunk:c1"]
    assert body["sources"][0]["chunk_id"] == "c1"
    assert calls["count"] == 1


def test_preview_rejects_blank_action(stub_app):
    client, _, _ = stub_app
    response = client.post("/api/v1/worlds/w1/narration-preview", json={"action": "   ", "mode": "fake"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ACTION_INVALID"


def test_preview_maps_context_error(stub_app):
    client, _, _ = stub_app
    response = client.post("/api/v1/worlds/missing/narration-preview", json={"action": "看看", "mode": "fake"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORLD_NOT_FOUND"


def test_live_without_session_is_expired(stub_app):
    client, _, _ = stub_app
    response = client.post("/api/v1/worlds/w1/narration-preview", json={"action": "看看", "mode": "live"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_SESSION_EXPIRED"


def test_live_with_session_uses_client(stub_app):
    client, store, _ = stub_app
    created = client.post(
        "/api/v1/model-sessions",
        json={"base_url": "https://example.invalid/v1", "model": "test-model", "api_key": "sk-test"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert "api_key" not in created.json()

    response = client.post(
        "/api/v1/worlds/w1/narration-preview",
        json={"action": "看看码头", "mode": "live", "session_id": session_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "live"


def test_session_delete_is_idempotent_and_empty(stub_app):
    client, store, _ = stub_app
    created = client.post(
        "/api/v1/model-sessions",
        json={"base_url": "https://example.invalid/v1", "model": "m", "api_key": "k"},
    ).json()
    sid = created["session_id"]
    first = client.delete(f"/api/v1/model-sessions/{sid}")
    assert first.status_code == 204 and first.content == b""
    second = client.delete(f"/api/v1/model-sessions/{sid}")
    assert second.status_code == 204


def test_session_test_reports_expired(stub_app):
    client, _, _ = stub_app
    response = client.post("/api/v1/model-sessions/nope/test")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_SESSION_EXPIRED"


def test_preview_does_not_write_database(stub_app, tmp_path):
    client, _, _ = stub_app
    import sqlite3

    db_path = tmp_path / "m2.db"
    with sqlite3.connect(db_path) as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("worlds", "documents", "chunks")}
    client.post("/api/v1/worlds/w1/narration-preview", json={"action": "看看", "mode": "fake"})
    with sqlite3.connect(db_path) as conn:
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("worlds", "documents", "chunks")}
    assert before == after


def test_missing_modules_return_not_ready(tmp_path, monkeypatch):
    """没有 model_gateway / narration_context 时必须明确未就绪，而不是 500 或假装成功。"""
    import app.main as main_module

    monkeypatch.setattr(main_module, "_load_attr", lambda *args, **kwargs: None)
    client = TestClient(create_app(Settings(db_path=tmp_path / "m2b.db")))

    session = client.post(
        "/api/v1/model-sessions",
        json={"base_url": "https://example.invalid/v1", "model": "m", "api_key": "k"},
    )
    assert session.status_code == 503
    assert session.json()["error"]["code"] == "M2_NOT_READY"

    preview = client.post(
        "/api/v1/worlds/w1/narration-preview", json={"action": "看看", "mode": "fake"}
    )
    assert preview.status_code == 503
    assert preview.json()["error"]["code"] == "M2_NOT_READY"


# --------------------------------------------------------------------------
# R7：客户端断开必须取消上游任务（真实 ASGI 消息，不直接 cancel 服务函数）
# --------------------------------------------------------------------------


def test_client_disconnect_cancels_generation(tmp_path):
    import asyncio
    import json as jsonlib

    from app.narration.core import ContextSource

    class SlowClient:
        def __init__(self):
            self.cancelled = False

        async def generate(self, request):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return {"narrative": "不应到达", "references": []}

    slow = SlowClient()

    class Store:
        def get_client(self, session_id):
            return slow

        def create(self, *args):  # pragma: no cover - 不走这里
            raise AssertionError

        def delete(self, *args):  # pragma: no cover
            pass

        def test(self, *args):  # pragma: no cover
            pass

    class Bundle:
        sources = (ContextSource("chunk:c1", "chunk", "资料"),)
        metadata = ({"source_id": "chunk:c1", "kind": "chunk", "title": "资料"},)

    app = create_app(
        Settings(db_path=tmp_path / "disc.db"),
        narration_store=Store(),
        context_builder=lambda conn, world_id, action: Bundle(),
    )

    body = jsonlib.dumps({"action": "慢慢想", "mode": "live", "session_id": "s1"}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/worlds/w1/narration-preview",
        "raw_path": b"/api/v1/worlds/w1/narration-preview",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    async def scenario():
        state = {"body_sent": False, "disconnected": False}
        sent = []

        async def mark_disconnected():
            await asyncio.sleep(0.3)
            state["disconnected"] = True

        async def receive():
            # Starlette 的 is_disconnected 只读取“立即可用”的消息：
            # 这里不要 await，先用 request 消息占位，断开后用 disconnect 消息。
            if not state["body_sent"]:
                state["body_sent"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            if state["disconnected"]:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": True}

        async def send(message):
            sent.append(message)

        flip = asyncio.ensure_future(mark_disconnected())
        try:
            await asyncio.wait_for(app(scope, receive, send), timeout=5)
        finally:
            flip.cancel()
        return sent

    sent = asyncio.run(scenario())
    assert slow.cancelled is True
    assert not any(
        m.get("type") == "http.response.body" and b"narrative" in m.get("body", b"")
        for m in sent
    )