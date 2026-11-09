from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import create_world
from test_world_transfer import import_files


OPENING = {
    "name": "细器铺试工",
    "intro": "黎斯清早到了南窗细器铺。",
    "starts_at": "1200-01-01T08:00",
    "character": {"name": "黎斯", "location": "南窗细器铺", "coins": 10, "elapsed_minutes": 0},
    "shortcut": "接下来三天照常做工。",
    "events": [
        {"title": "开张", "description": "店铺开始营业。", "at_minute": 60, "every_minutes": 1440, "interrupt": False},
        {"title": "急单", "description": "塞文问黎斯愿不愿意接一笔急单。", "at_minute": 1680, "every_minutes": None, "interrupt": True},
        {"title": "验收", "description": "第三天中午验收。", "at_minute": 3120, "every_minutes": None, "interrupt": True},
    ],
}


def draft(client, run, request_id, action, **fields):
    return client.post(f"/api/v1/adventures/{run['id']}/drafts", json={
        "request_id": request_id, "expected_revision": run["revision"], "action": action, **fields,
    })


def commit(client, run, request_id, apply=True):
    return client.post(f"/api/v1/adventures/{run['id']}/drafts/{request_id}/commit", json={"apply_changes": apply})


def test_openings_hide_future_events_and_keep_clock_after_fork_and_import(client, tmp_path: Path):
    world = create_world(client, "赫赭")["id"]
    base = f"/api/v1/worlds/{world}"
    author = f"{base}/opening-settings"
    opening = client.post(author, json=OPENING).json()
    alternate = client.post(author, json={**OPENING, "name": "初到刻希弗", "events": []})
    assert alternate.status_code == 201
    assert [item["name"] for item in client.get(f"{base}/openings").json()["items"]] == ["细器铺试工", "初到刻希弗"]
    assert "急单" not in client.get(f"{base}/openings").text
    assert "急单" in client.get(author).text

    run = client.post(f"{base}/adventures", json={
        "name": "黎斯的试工", "opening_id": opening["id"], "character": {"location": "错误地点"},
    }).json()
    assert run["character"]["location"] == "南窗细器铺"
    detail = f"/api/v1/adventures/{run['id']}"
    assert "验收" not in client.get(detail).text
    checkpoint = client.post(f"{detail}/checkpoints", json={
        "request_id": "before", "expected_revision": 0, "name": "试工当天",
    }).json()

    # Editing a published opening applies only to new adventures.
    assert client.put(f"{author}/{opening['id']}", json={**OPENING, "events": []}).status_code == 200
    prepared = draft(client, run, "three", "接下来三天照常做工。")
    assert prepared.status_code == 200, prepared.text
    assert "验收" not in prepared.text
    clock = prepared.json()["result"]["clock"]
    assert (clock["requested_minutes"], clock["elapsed_minutes"], clock["interrupted"]) == (4320, 1680, True)
    assert [(event["title"], event["count"]) for event in clock["events"]] == [("开张", 2), ("急单", 1)]
    assert clock["to"] == "1200-01-02T12:00"
    assert client.get(detail).json()["character"]["elapsed_minutes"] == 0
    assert commit(client, run, "three", False).json()["error"]["code"] == "CLOCK_CONFIRM_REQUIRED"
    assert draft(client, run, "three", "接下来三天照常做工。", advance_minutes=60).status_code == 409
    progressed = commit(client, run, "three").json()
    assert progressed["character"]["elapsed_minutes"] == 1680
    assert commit(client, run, "three").json()["revision"] == 1
    follow = draft(client, progressed, "continue", "接单后继续工作", advance_minutes=60).json()["result"]
    assert follow["clock"]["events"] == []

    branch_path = f"/api/v1/checkpoints/{checkpoint['id']}/restore"
    branch = client.post(branch_path, json={"request_id": "branch", "name": "另一种决定"}).json()
    assert draft(client, branch, "again", "接下来三天照常做工。").json()["result"]["clock"] == clock

    archive = client.get(f"{base}/transfer?part=zip")
    assert archive.status_code == 200, archive.text
    with TestClient(create_app(Settings(db_path=tmp_path / "second.db"))) as target:
        imported = import_files(target, ("world.zip", archive.content))
        assert imported.status_code == 201, imported.text
        dest = f"/api/v1/worlds/{imported.json()['id']}"
        assert len(target.get(f"{dest}/openings").json()["items"]) == 2
        copied = next(item for item in target.get(f"{dest}/adventures").json()["items"] if item["name"] == "黎斯的试工")
        assert copied["character"]["elapsed_minutes"] == 1680
        original_turn = target.get(f"/api/v1/adventures/{copied['id']}").json()["turns"][0]
        assert original_turn["result"]["clock"] == clock
        copied_save = target.get(f"/api/v1/adventures/{copied['id']}/checkpoints").json()["items"][0]
        restored = target.post(f"/api/v1/checkpoints/{copied_save['id']}/restore",
                               json={"request_id": "restored", "name": "迁移后的分支"}).json()
        assert draft(target, restored, "again", "接下来三天照常做工。").json()["result"]["clock"] == clock


def test_an_ordinary_action_reaches_nearby_event_without_skipping_it(client):
    world = create_world(client)["id"]
    base = f"/api/v1/worlds/{world}"
    events = [{**OPENING["events"][1], "at_minute": 3}]
    opening = client.post(f"{base}/opening-settings", json={**OPENING, "events": events}).json()
    run = client.post(f"{base}/adventures", json={"name": "日常", "opening_id": opening["id"]}).json()
    result = draft(client, run, "small", "找塞文说话").json()["result"]
    assert result["clock"]["elapsed_minutes"] == 3
    assert result["clock"]["events"][0]["title"] == "急单"
    assert commit(client, run, "small").json()["character"]["elapsed_minutes"] == 3
