import os

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.local_play import LOCAL_ORIGIN, LOCAL_USER, attach_local_ui, create_local_app
from app.main import create_app


def test_loopback_play_protects_both_game_and_api(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("墨境本机", encoding="utf-8")
    app = create_app(Settings(db_path=tmp_path / "game.db", cors_origins=(), https_model_only=True))
    attach_local_ui(app, dist, "test-local-password")
    with TestClient(app, base_url=LOCAL_ORIGIN) as browser:
        assert browser.get("/").status_code == 401
        assert browser.get("/api/v1/worlds").status_code == 401

    with TestClient(app, base_url=LOCAL_ORIGIN) as browser:
        browser.auth = httpx.BasicAuth(LOCAL_USER, "test-local-password")
        page = browser.get("/")
        assert "墨境本机" in page.text
        assert page.headers["x-frame-options"] == "DENY"
        assert browser.get("/api/v1/worlds", headers={"Host": "other.example"}).status_code == 400
        for origin in ("null", "https://other.example"):
            assert browser.post("/api/v1/worlds", headers={"Origin": origin},
                                json={"name": "不应保存"}).status_code == 403
        assert browser.post("/api/v1/worlds", headers={"Origin": LOCAL_ORIGIN},
                            json={"name": "雾港"}).status_code == 201
        assert browser.get("/api/v1/worlds").json()["total"] == 1
        config = {"base_url": "http://model.example/v1", "model": "test", "api_key": "unused"}
        denied = browser.post("/api/v1/model-sessions", headers={"Origin": LOCAL_ORIGIN}, json=config)
        assert denied.status_code == 422
        assert denied.json()["error"]["code"] == "MODEL_HTTPS_REQUIRED"
        config["base_url"] = "https://model.example/v1"
        assert browser.post("/api/v1/model-sessions", headers={"Origin": LOCAL_ORIGIN}, json=config).status_code == 201


def test_optional_word_parser_does_not_block_play(client, monkeypatch):
    monkeypatch.setattr("app.parsers.extractor._load_docx_parser", lambda: None)
    capabilities = client.get("/api/v1/capabilities").json()
    assert ".docx" not in capabilities["formats"]
    assert ".pdf" in capabilities["formats"]
    world = client.post("/api/v1/worlds", json={"name": "旅程"}).json()
    result = client.post(f"/api/v1/worlds/{world['id']}/documents",
                         files={"file": ("设定.docx", b"example")})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "DOCX_UNAVAILABLE"


def test_native_profile_restores_after_restart_and_can_be_deleted(tmp_path):
    from app.model_gateway import ModelSessionStore

    class MemoryProfile:
        value = None

        def save(self, value):
            self.value = value

        def load(self):
            return self.value

        def clear(self):
            self.value = None

    profile = MemoryProfile()
    settings = Settings(db_path=tmp_path / "game.db", https_model_only=True)

    def make_app():
        return create_app(settings, model_profile=profile, narration_store=ModelSessionStore(ttl_seconds=None))

    with TestClient(make_app()) as client:
        assert client.post("/api/v1/model-profile/restore").json() == {"session": None}
        created = client.post("/api/v1/model-sessions", json={
            "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "secret-fixture",
        })
        assert created.status_code == 201
        assert created.json()["remembered"]
        assert created.json()["expires_at"] is None
        assert "secret-fixture" not in created.text

    with TestClient(make_app()) as client:
        restored = client.post("/api/v1/model-profile/restore").json()["session"]
        assert restored["model"] == "deepseek-flash"
        assert restored["session_id"] != created.json()["session_id"]
        assert client.post("/api/v1/model-profile/restore").json()["session"] == restored
        assert client.delete("/api/v1/model-profile").status_code == 204
        assert client.post(f"/api/v1/model-sessions/{restored['session_id']}/test").status_code == 409

    with TestClient(make_app()) as client:
        assert client.post("/api/v1/model-profile/restore").json() == {"session": None}


def test_separate_device_instances_never_share_worlds_saves_or_model_sessions(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("墨境本机", encoding="utf-8")
    server_db = str(tmp_path / "other-deployment.db")
    server_origin = "https://other.example"
    monkeypatch.setenv("MOJING_DB_PATH", server_db)
    monkeypatch.setenv("MOJING_PUBLIC_ORIGIN", server_origin)

    one = create_local_app(dist, tmp_path / "phone-one", "password-one")
    two = create_local_app(dist, tmp_path / "phone-two", "password-two")
    with TestClient(one, base_url=LOCAL_ORIGIN) as a, TestClient(two, base_url=LOCAL_ORIGIN) as b:
        a.auth = httpx.BasicAuth(LOCAL_USER, "password-one")
        b.auth = httpx.BasicAuth(LOCAL_USER, "password-two")
        a.headers["Origin"] = b.headers["Origin"] = LOCAL_ORIGIN

        def create_game(client):
            world = client.post("/api/v1/worlds", json={"name": "同名世界"}).json()
            run = client.post(f"/api/v1/worlds/{world['id']}/adventures",
                              json={"name": "各自的冒险", "mode": "fake"}).json()
            save = client.post(f"/api/v1/adventures/{run['id']}/checkpoints",
                               json={"name": "起点", "request_id": "save", "expected_revision": 0}).json()
            return world["id"], run["id"], save["id"]

        world_a, run_a, save_a = create_game(a)
        world_b, run_b, save_b = create_game(b)
        assert world_a != world_b
        for client, own, other in [(a, world_a, (world_b, run_b, save_b)),
                                   (b, world_b, (world_a, run_a, save_a))]:
            assert [row["id"] for row in client.get("/api/v1/worlds").json()["items"]] == [own]
            archive = client.get(f"/api/v1/worlds/{own}/export?include_saves=true").json()
            assert len(archive["saves"]["adventures"]) == len(archive["saves"]["checkpoints"]) == 1
            foreign_world, foreign_run, foreign_save = other
            assert client.get(f"/api/v1/worlds/{foreign_world}/export?include_saves=true").status_code == 404
            assert client.get(f"/api/v1/adventures/{foreign_run}").status_code == 404
            assert client.post(f"/api/v1/checkpoints/{foreign_save}/restore",
                               json={"name": "分支", "request_id": "restore"}).status_code == 404

        connection = {"base_url": "https://model.example/v1", "model": "same-model", "api_key": "same-test-key"}
        session_a = a.post("/api/v1/model-sessions", json=connection).json()
        session_b = b.post("/api/v1/model-sessions", json=connection).json()
        assert session_a["session_id"] != session_b["session_id"]
        assert b.post(f"/api/v1/model-sessions/{session_a['session_id']}/test").status_code == 409

    assert not (tmp_path / "other-deployment.db").exists()
    assert os.environ["MOJING_DB_PATH"] == server_db
    assert os.environ["MOJING_PUBLIC_ORIGIN"] == server_origin
