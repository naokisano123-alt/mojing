"""M3 接线：同分钟多实例、失败回滚、Worker 开关、消息路由与 Fake 验收链。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.adventures import checkpoints as checkpoint_service
from app.adventures import service as adventures
from app.config import Settings
from app.db import db, init_db
from app.errors import ApiError
from app.main import create_app
from app.m3.core import adapters, service as core, worker
from conftest import create_world


def seed_world(conn, world_id="w1"):
    now = time.time()
    conn.execute(
        "INSERT INTO worlds(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
        (world_id, "测试世界", "", now, now),
    )


@pytest.fixture()
def app(tmp_path):
    return create_app(Settings(db_path=tmp_path / "m3w.db"))


@pytest.fixture()
def client(app):
    return TestClient(app)


class FakeKnowledge:
    """最小的知识投影 Fake：只记录快照与恢复调用，玩家只看得到自己的消息。"""

    name = "knowledge"

    def __init__(self):
        self.store = {}
        self.restored = []

    def snapshot(self, conn, instance_id):
        return {"items": list(self.store.get(instance_id, []))}

    def restore(self, conn, snapshot, *, new_instance_id, world_id):
        self.store.setdefault(new_instance_id, []).extend(snapshot.get("items", []))
        self.restored.append((new_instance_id, world_id))

    def list_known(self, conn, instance_id, holder_id, *, before=None, limit=30):
        items = [item for item in self.store.get(instance_id, []) if item.get("holder") == holder_id]
        items = items[:limit]
        return {"items": items, "next_before": None}


def test_same_minute_instances_are_independent(client, tmp_path):
    world = create_world(client)
    with db(tmp_path / "m3w.db") as conn:
        first = adventures.create_adventure(conn, world["id"], "甲", "fake")
        second = adventures.create_adventure(conn, world["id"], "乙", "fake")
        a = core.for_adventure(conn, first["id"])
        b = core.for_adventure(conn, second["id"])
        core.advance(conn, a["id"], expected_revision=0, minutes=0)
        core.advance(conn, a["id"], expected_revision=1, minutes=0)
        a2 = core.get(conn, a["id"])
        b2 = core.get(conn, b["id"])
    assert a2["world_minute"] == b2["world_minute"] == 0
    assert a2["world_revision"] == 2
    assert b2["world_revision"] == 0


def test_commit_rolls_back_everything_on_failure(client, tmp_path, monkeypatch):
    world = create_world(client)
    path = tmp_path / "m3w.db"
    with db(path) as conn:
        run = adventures.create_adventure(conn, world["id"], "回滚", "fake")
        conn.commit()
        with db(path) as conn:
            conn.execute(
                "INSERT INTO adventure_drafts(id,adventure_id,action,base_revision,status,created_at,result,advance_minutes) "
                "VALUES('d1',?, '等待', 0, 'ready', ?, ?, 10)",
                (
                    run["id"],
                    time.time(),
                    json.dumps({"state_before": run["character"], "memory_cursor": 0}),
                ),
            )
            conn.commit()

        def boom(*args, **kwargs):
            raise ApiError(409, "INSTANCE_CONFLICT", "注入的并发冲突")

        monkeypatch.setattr(adventures.instance_service, "advance", boom)

        def failing_commit():
            with db(path) as conn:
                adventures.commit(conn, run["id"], "d1", False)

        # 异常必须穿出 db() 上下文才会回滚
        with pytest.raises(ApiError):
            failing_commit()

        with db(path) as conn:
            after = adventures.get_adventure(conn, run["id"])
            turns = conn.execute("SELECT COUNT(*) FROM adventure_turns WHERE adventure_id=?", (run["id"],)).fetchone()[0]
            instance = core.for_adventure(conn, run["id"])
            draft = conn.execute("SELECT status FROM adventure_drafts WHERE adventure_id=? AND id='d1'", (run["id"],)).fetchone()[0]
    assert after["revision"] == run["revision"]
    assert turns == 0
    assert instance["world_revision"] == 0
    assert instance["world_minute"] == 0
    assert draft == "ready"


def test_worker_pause_switch_and_usage(client, tmp_path):
    create_world(client)
    with db(tmp_path / "m3w.db") as conn:
        assert worker.is_enabled(conn) is False
        with pytest.raises(ApiError) as exc:
            worker.ensure_running(conn)
        assert exc.value.code == "PULSE_DISABLED"
        worker.set_enabled(conn, True, "手动开启")
        assert worker.is_enabled(conn) is True
        worker.record_usage(conn, purpose="pulse", calls=2, tokens=120, game_day="day-1")
        worker.record_usage(conn, purpose="pulse", calls=1, tokens=80, game_day="day-1")
        assert worker.usage_summary(conn, "day-1") == {"calls": 3, "tokens": 200}


def test_world_news_route_uses_adapter(client, tmp_path):
    adapters.clear_adapters()
    fake = FakeKnowledge()
    adapters.register_adapter(fake)
    try:
        world = create_world(client)
        run = client.post(f"/api/v1/worlds/{world['id']}/adventures", json={"name": "看消息", "mode": "fake"}).json()
        with db(tmp_path / "m3w.db") as conn:
            instance = core.for_adventure(conn, run["id"])
            fake.store[instance["id"]] = [
                {"message_id": "m1", "holder": "player", "text": "镇长在找你"},
                {"message_id": "m2", "holder": "npc:smith", "text": "铁匠的秘密"},
            ]
        response = client.get(f"/api/v1/adventures/{run['id']}/world-news")
        assert response.status_code == 200
        body = response.json()
        assert [item["message_id"] for item in body["items"]] == ["m1"]
        assert body["next_before"] is None
    finally:
        adapters.clear_adapters()


def test_world_news_route_reports_not_ready(client):
    adapters.clear_adapters()
    world = create_world(client)
    run = client.post(f"/api/v1/worlds/{world['id']}/adventures", json={"name": "空", "mode": "fake"}).json()
    response = client.get(f"/api/v1/adventures/{run['id']}/world-news")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "M3_NOT_READY"


def test_fake_acceptance_chain_checkpoint_branch(client, tmp_path):
    adapters.clear_adapters()
    fake = FakeKnowledge()
    adapters.register_adapter(fake)
    try:
        world = create_world(client)
        with db(tmp_path / "m3w.db") as conn:
            run = adventures.create_adventure(conn, world["id"], "北境小镇", "fake")
            instance = core.for_adventure(conn, run["id"])
            core.advance(conn, instance["id"], expected_revision=0, minutes=30)
            fake.store[instance["id"]] = [{"message_id": "m1", "holder": "player", "text": "北境传来消息"}]
            conn.commit()
            saved = checkpoint_service.create_checkpoint(conn, run["id"], "分支点", "req-1", run["revision"])
            conn.commit()
            branch = checkpoint_service.restore(conn, saved["id"], "另一个分支", "req-2")
            branch_instance = core.for_adventure(conn, branch["id"])
            source_instance = core.get(conn, instance["id"])
    finally:
        adapters.clear_adapters()

    assert branch["id"] != run["id"]
    assert branch_instance["id"] != source_instance["id"]
    assert branch_instance["world_minute"] == 30
    assert fake.restored and fake.restored[0][0] == branch_instance["id"]
    assert branch["character"] == run["character"]