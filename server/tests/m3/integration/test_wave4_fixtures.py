"""M3 第四轮准备：两份普通导入资料、不同地点与人物，供组合验收复用。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import create_world, upload


FIXTURES = Path(__file__).parent / "fixtures"


def _import_world(tmp_path, markdown_name):
    app = create_app(Settings(db_path=tmp_path / "wave4.db"))
    with TestClient(app) as client:
        world = create_world(client)
        content = (FIXTURES / markdown_name).read_bytes()
        response = upload(client, world["id"], markdown_name, content)
        assert response.status_code == 201, response.text
        run = client.post(
            f"/api/v1/worlds/{world['id']}/adventures",
            json={"name": f"探索 {markdown_name}", "mode": "fake"},
        )
        assert run.status_code == 201, run.text
        return client, world, run.json()


def test_two_imported_worlds_have_distinct_places_and_people(tmp_path):
    client_a, world_a, run_a = _import_world(tmp_path, "world_a_白石镇.md")
    client_b, world_b, run_b = _import_world(tmp_path, "world_b_潮汐港.md")

    assert world_a["id"] != world_b["id"]

    search_a = client_a.get(
        "/api/v1/search", params={"world_id": world_a["id"], "q": "阿岚"}
    ).json()
    search_b = client_b.get(
        "/api/v1/search", params={"world_id": world_b["id"], "q": "沈九"}
    ).json()
    assert search_a["items"]
    assert search_b["items"]

    cross = client_a.get(
        "/api/v1/search", params={"world_id": world_a["id"], "q": "沈九"}
    ).json()
    assert cross["items"] == []

    docs_a = client_a.get(f"/api/v1/worlds/{world_a['id']}/documents").json()["items"]
    docs_b = client_b.get(f"/api/v1/worlds/{world_b['id']}/documents").json()["items"]
    assert {item["id"] for item in docs_a}.isdisjoint({item["id"] for item in docs_b})
    assert docs_a[0]["chunk_count"] >= 1
    assert docs_b[0]["chunk_count"] >= 1

    assert run_a["id"] != run_b["id"]
    assert run_a["world_id"] == world_a["id"]
    assert run_b["world_id"] == world_b["id"]


def test_fixture_texts_do_not_share_people(tmp_path):
    text_a = (FIXTURES / "world_a_白石镇.md").read_text(encoding="utf-8")
    text_b = (FIXTURES / "world_b_潮汐港.md").read_text(encoding="utf-8")
    assert "阿岚" in text_a and "霍五" in text_a
    assert "沈九" in text_b and "阿螺" in text_b
    assert "阿岚" not in text_b and "沈九" not in text_a