"""M3 第四轮公共插槽：分析草案路由的未就绪语义与生命周期。"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.m3.core import world_analysis as seam
from conftest import create_world


class FakeProvider:
    name = "fake-analysis"

    def __init__(self):
        self.drafts = {}
        self.confirmed = []

    def analyze(self, conn, world_id, request):
        draft_id = f"draft-{len(self.drafts) + 1}"
        draft = {
            "draft_id": draft_id,
            "world_id": world_id,
            "status": "ready",
            "summary": {"people": ["阿岚"], "places": ["白石镇"]},
            "source_ids": request.get("source_ids", []),
        }
        self.drafts[draft_id] = draft
        return draft

    def get_draft(self, conn, draft_id):
        return self.drafts.get(draft_id)

    def confirm(self, conn, draft_id, request):
        draft = self.drafts[draft_id]
        self.confirmed.append(draft_id)
        return {"adventure_id": "adv-1", "instance_id": "inst-1", "draft_id": draft_id}


@pytest.fixture()
def client(tmp_path):
    seam.clear_providers()
    app = create_app(Settings(db_path=tmp_path / "m4.db"))
    with TestClient(app) as test_client:
        yield test_client
    seam.clear_providers()


def test_analysis_requires_provider(client):
    world = create_world(client)
    response = client.post(f"/api/v1/worlds/{world['id']}/analysis-drafts", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "M4_NOT_READY"


def test_analysis_draft_lifecycle(client):
    fake = FakeProvider()
    seam.register_provider(fake)
    world = create_world(client)

    created = client.post(
        f"/api/v1/worlds/{world['id']}/analysis-drafts",
        json={"source_ids": ["chunk:a"]},
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == "ready"
    assert draft["source_ids"] == ["chunk:a"]

    fetched = client.get(f"/api/v1/analysis-drafts/{draft['draft_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["summary"]["people"] == ["阿岚"]

    missing = client.get("/api/v1/analysis-drafts/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DRAFT_NOT_FOUND"

    confirmed = client.post(
        f"/api/v1/analysis-drafts/{draft['draft_id']}/confirm", json={"name": "白石镇"}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["instance_id"] == "inst-1"
    assert fake.confirmed == [draft["draft_id"]]


def test_unknown_world_is_rejected(client):
    fake = FakeProvider()
    seam.register_provider(fake)
    response = client.post("/api/v1/worlds/nope/analysis-drafts", json={})
    assert response.status_code == 404
