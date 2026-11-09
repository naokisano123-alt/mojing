"""M3.0 实例根：迁移、归属、原子推进与快照恢复。"""

import json
import sqlite3
import time

import pytest

from app.adventures import service as adventures
from app.db import db, init_db
from app.errors import ApiError
from app.m3.core import schema as m3_schema
from app.m3.core import service as core


def seed_world(conn, world_id="w1"):
    now = time.time()
    conn.execute(
        "INSERT INTO worlds(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
        (world_id, "测试世界", "", now, now),
    )


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "m3.db"
    init_db(path)
    return path


def test_migration_is_idempotent(db_path):
    with db(db_path) as conn:
        seed_world(conn)
        conn.execute(
            "INSERT INTO adventures(id,world_id,name,mode,revision,created_at) VALUES('a1','w1','旧旅程','fake',3,?)",
            (time.time(),),
        )
    for _ in range(3):
        init_db(db_path)
    with db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM world_instances").fetchone()[0]
        row = conn.execute("SELECT * FROM world_instances WHERE adventure_id='a1'").fetchone()
    assert count == 1
    assert row["revision"] == 3
    assert row["world_minute"] == 0


def test_ensure_schema_backfills_legacy_adventure(db_path):
    with db(db_path) as conn:
        seed_world(conn)
        conn.execute(
            "INSERT INTO adventures(id,world_id,name,mode,revision,created_at) VALUES('a2','w1','旧','fake',5,?)",
            (time.time(),),
        )
        m3_schema.ensure_schema(conn)
        instance = core.for_adventure(conn, "a2")
        assert instance["world_revision"] == 5
        assert instance["world_minute"] == 0


def test_new_adventure_gets_instance(db_path):
    with db(db_path) as conn:
        seed_world(conn)
        run = adventures.create_adventure(conn, "w1", "新旅程", "fake")
        instance = core.for_adventure(conn, run["id"])
        assert instance["world_minute"] == 0
        assert instance["world_revision"] == 0
        assert instance["world_id"] == "w1"


def test_commit_advances_instance_minute_and_revision(db_path):
    with db(db_path) as conn:
        seed_world(conn)
        run = adventures.create_adventure(conn, "w1", "确认测试", "fake")
        result = {
            "state_before": run["character"],
            "memory_cursor": 0,
            "proposed_changes": {"advance_minutes": 15},
            "projected_state": {**run["character"], "elapsed_minutes": 15},
        }
        conn.execute(
            "INSERT INTO adventure_drafts(id,adventure_id,action,base_revision,status,created_at,result,advance_minutes) "
            "VALUES('d1',?, '等待', 0, 'ready', ?, ?, 45)",
            (run["id"], time.time(), json.dumps(result)),
        )
        conn.commit()
        adventures.commit(conn, run["id"], "d1", True)
        instance = core.for_adventure(conn, run["id"])
        assert instance["world_minute"] == 15
        assert instance["world_revision"] == 1


def test_advance_rejects_stale_revision(db_path):
    with db(db_path) as conn:
        seed_world(conn)
        run = adventures.create_adventure(conn, "w1", "冲突", "fake")
        instance = core.for_adventure(conn, run["id"])
        updated = core.advance(conn, instance["id"], expected_revision=0, minutes=5)
        assert updated["world_revision"] == 1
        with pytest.raises(ApiError) as exc:
            core.advance(conn, instance["id"], expected_revision=0, minutes=5)
        assert exc.value.code == "INSTANCE_CONFLICT"


def test_snapshot_restore_creates_new_instance(db_path):
    with db(db_path) as conn:
        seed_world(conn)
        run = adventures.create_adventure(conn, "w1", "快照", "fake")
        instance = core.for_adventure(conn, run["id"])
        core.advance(conn, instance["id"], expected_revision=0, minutes=40)
        snap = core.snapshot(conn, instance["id"])

        other = adventures.create_adventure(conn, "w1", "恢复分支", "fake")
        restored = core.restore(conn, snap, adventure_id=other["id"], world_id="w1")
        assert restored["id"] != instance["id"]
        assert restored["world_minute"] == 40
        assert restored["world_revision"] == 1


def test_restore_rejects_cross_world_snapshot(db_path):
    with db(db_path) as conn:
        seed_world(conn, "w1")
        seed_world(conn, "w2")
        run = adventures.create_adventure(conn, "w1", "甲", "fake")
        instance = core.for_adventure(conn, run["id"])
        snap = core.snapshot(conn, instance["id"])
        with pytest.raises(ApiError) as exc:
            core.restore(conn, snap, adventure_id="x", world_id="w2")
        assert exc.value.code == "INSTANCE_WORLD_MISMATCH"