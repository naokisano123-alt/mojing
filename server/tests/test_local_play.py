import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.local_play import LOCAL_ORIGIN, LOCAL_USER, attach_local_ui
from app.main import create_app


def test_loopback_play_protects_both_game_and_api(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("墨境本机", encoding="utf-8")
    app = create_app(Settings(db_path=tmp_path / "game.db", cors_origins=()))
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
