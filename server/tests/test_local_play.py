import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.local_play import LOCAL_ORIGIN, LOCAL_USER, attach_local_ui
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


def test_mobile_pulse_connects_using_frontend_transport_routes(tmp_path):
    """Use the shipped frontend paths against the same loopback app as Android."""
    import re
    from pathlib import Path

    from app.model_gateway import ModelSessionStore

    source = (Path(__file__).resolve().parents[2] / "src/features/world-pulse/api.ts").read_text()
    transport = source.split("export const worldPulseApi:", 1)[1].split("/**", 1)[0]
    routes = {}
    for method, endpoint in re.findall(r"['\"](GET|POST|PUT)['\"],\s*`([^`]+)`", transport):
        routes[endpoint.rsplit("/", 1)[-1]] = (method, endpoint)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("mobile test")
    store = ModelSessionStore()
    app = create_app(Settings(db_path=tmp_path / "pulse.db"), narration_store=store)
    attach_local_ui(app, dist, "test-local-password")
    with TestClient(app, base_url=LOCAL_ORIGIN, headers={"Origin": LOCAL_ORIGIN}) as browser:
        browser.auth = httpx.BasicAuth(LOCAL_USER, "test-local-password")
        world = browser.post("/api/v1/worlds", json={"name": "雾港"}).json()
        run = browser.post(f"/api/v1/worlds/{world['id']}/adventures",
                           json={"name": "手机版验收", "mode": "live"}).json()
        config = {"base_url": "https://mock.invalid/v1", "model": "test", "api_key": "test-key"}
        sid = browser.post("/api/v1/model-sessions", json=config).json()["session_id"]

        def call(route, payload=None):
            method, endpoint = routes[route]
            endpoint = endpoint.replace("${encodeURIComponent(adventureId)}", run["id"])
            return browser.request(method, "/api/v1" + endpoint, json=payload)

        # Both UI connection choices must reach a real route, including when
        # unknown POST paths would otherwise fall through to StaticFiles (405).
        route = next(key for key in routes if key not in {"world-pulse", "activity"})
        for config in ({**config, "mode": "independent"},
                       {"mode": "reuse_narration", "narration_session_id": sid}):
            connected = call(route, config)
            assert connected.status_code == 200, connected.text
            assert connected.json()["mode"] == config["mode"]
            assert not connected.json()["enabled"]
        assert call("world-pulse", {"enabled": False}).status_code == 200
        assert call("activity", {"active": False}).status_code == 200
