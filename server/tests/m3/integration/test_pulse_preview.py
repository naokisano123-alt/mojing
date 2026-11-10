import json
import time
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import db, init_db
from app.main import create_app
from app.m3.core import service as instances
from conftest import create_world, upload
from test_scenario_clock import OPENING, commit, draft
from test_world_transfer import import_files


def wait_for_news(client, path, count):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"{path}/world-news")
        assert result.status_code == 200, result.text
        items = result.json()["items"]
        if len(items) >= count:
            return items
        time.sleep(0.05)
    raise AssertionError(f"Expected {count} world messages, received {items}")


def test_preview_clock_messages_checkpoint_and_cross_device_transfer(tmp_path):
    path = tmp_path / "source.db"
    with TestClient(create_app(Settings(db_path=path))) as client:
        world_id = create_world(client)["id"]
        base = f"/api/v1/worlds/{world_id}"
        opening = client.post(f"{base}/opening-settings", json={
            **OPENING, "events": [{**OPENING["events"][1], "at_minute": 45}],
        }).json()
        run = client.post(f"{base}/adventures", json={
            "name": "演示", "mode": "fake", "opening_id": opening["id"],
        }).json()
        adventure = f"/api/v1/adventures/{run['id']}"
        switch = f"{adventure}/world-pulse"
        # 集成修正：状态字段可加性扩展（mode/state/model），断言改为子集
        initial_status = client.get(switch).json()
        assert initial_status["available"] is True
        assert initial_status["enabled"] is False
        assert client.put(switch, json={"enabled": True}).json()["error"]["code"] == "PULSE_SOURCE_REQUIRED"
        assert upload(client, world_id, "驿路.txt", "北境小镇设有公告栏。".encode()).status_code == 201
        assert client.put(switch, json={"enabled": True}).json()["enabled"]
        first = wait_for_news(client, adventure, 1)[0]
        assert first["knowledge_type"] == "rumor"
        assert "source_event_id" not in first
        assert client.put(switch, json={"enabled": True}).json()["enabled"]
        assert client.get(f"{adventure}/world-news").json()["items"] == [first]
        saved = client.post(f"{adventure}/checkpoints", json={
            "request_id": "before", "expected_revision": 0, "name": "公告初现",
        }).json()
        assert "instance_snapshot" not in saved

        prepared = draft(client, run, "one", "等待两小时", advance_minutes=120)
        assert prepared.json()["result"]["clock"]["elapsed_minutes"] == 45
        progressed = commit(client, run, "one")
        assert progressed.status_code == 200, progressed.text
        assert progressed.json()["character"]["elapsed_minutes"] == 45
        later_news = wait_for_news(client, adventure, 2)
        assert later_news[0]["learned_minute"] == 45
        with db(path) as conn:
            original = instances.for_adventure(conn, run["id"])
            assert (original["world_minute"], original["world_revision"]) == (45, 3)
            assert conn.execute(
                "SELECT DISTINCT potentially_billed FROM pulse_usage_ledger"
            ).fetchall()[0][0] == 0

        assert client.put(switch, json={"enabled": False}).json()["enabled"] is False
        assert draft(client, progressed.json(), "two", "继续等待", advance_minutes=120).status_code == 200
        assert commit(client, progressed.json(), "two").status_code == 200
        assert client.put(switch, json={"enabled": True}).json()["enabled"]
        assert client.get(f"{adventure}/world-news").json()["items"] == later_news
        with db(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM pulse_jobs").fetchone()[0] == 2
        assert "instance_snapshot" not in client.get(f"{adventure}/checkpoints").text

        archive = client.get(f"{base}/transfer?part=zip")
        assert archive.status_code == 200, archive.text
        with ZipFile(BytesIO(archive.content)) as zipped:
            package = json.loads(zipped.read("saves.json"))
            assert package["version"] == 4
            assert "pulse_jobs" not in package and "pulse_instance_configs" not in package

    target_path = tmp_path / "target.db"
    with TestClient(create_app(Settings(db_path=target_path))) as phone:
        result = import_files(phone, ("world.zip", archive.content))
        assert result.status_code == 201, result.text
        world_id = result.json()["id"]
        copied = phone.get(f"/api/v1/worlds/{world_id}/adventures").json()["items"][0]
        copied_path = f"/api/v1/adventures/{copied['id']}"
        assert copied["id"] != run["id"]
        assert phone.get(f"{copied_path}/world-pulse").json()["enabled"] is False
        assert phone.get(f"{copied_path}/world-news").json()["items"] == later_news
        point = phone.get(f"{copied_path}/checkpoints").json()["items"][0]
        branched = phone.post(f"/api/v1/checkpoints/{point['id']}/restore", json={
            "request_id": "restored", "name": "导入后分支",
        })
        assert branched.status_code == 201, branched.text
        branch_path = f"/api/v1/adventures/{branched.json()['id']}"
        assert phone.get(f"{branch_path}/world-news").json()["items"] == [first]
        with db(target_path) as conn:
            restored = instances.for_adventure(conn, copied["id"])
            branch = instances.for_adventure(conn, branched.json()["id"])
            assert len({restored["id"], original["id"], branch["id"]}) == 3
            assert (restored["world_minute"], branch["world_minute"]) == (165, 0)
            assert conn.execute("SELECT COUNT(*) FROM pulse_jobs").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM world_instances").fetchone()[0] == 2


def test_legacy_instance_column_and_clock_migration(tmp_path):
    path = tmp_path / "old.db"
    with TestClient(create_app(Settings(db_path=path))) as client:
        world_id = create_world(client)["id"]
        runs = [
            client.post(f"/api/v1/worlds/{world_id}/adventures", json={
                "name": f"旅程{i}", "character": {"elapsed_minutes": 85},
            }).json()
            for i in range(2)
        ]
    with db(path) as conn:
        conn.execute("ALTER TABLE world_instances RENAME COLUMN revision TO world_revision")
        conn.execute("UPDATE world_instances SET world_revision=8 WHERE adventure_id=?", (runs[0]["id"],))
        conn.execute("DELETE FROM world_instances WHERE adventure_id=?", (runs[1]["id"],))
        conn.execute("UPDATE adventures SET revision=4 WHERE id=?", (runs[1]["id"],))
    for _ in range(2):
        init_db(path)
    with db(path) as conn:
        old, backfilled = [instances.for_adventure(conn, run["id"]) for run in runs]
        assert old["world_revision"] == 8 and old["world_minute"] == 85
        assert backfilled["world_revision"] == 4 and backfilled["world_minute"] == 85
        assert "world_revision" not in {row[1] for row in conn.execute("PRAGMA table_info(world_instances)")}
