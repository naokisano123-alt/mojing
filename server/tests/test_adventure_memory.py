import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from conftest import create_world
from test_checkpoints import advance, make_run, restore, save
from test_world_transfer import import_files


def test_deposit_and_secret_survive_twenty_turns_fork_and_device_transfer(tmp_path):
    prompts = []
    deposit = {"op": "record", "kind": "deposit", "title": "工具押金", "person": "塞文", "amount": 15}
    secret = {"op": "record", "kind": "secret", "title": "黎斯会金属塑形", "knowers": ["黎斯"]}

    def upstream(request):
        prompt = json.loads(request.content)["messages"][1]["content"]
        context, _, action = prompt.partition("【玩家行动】\n")
        prompts.append(context)
        facts = json.loads(context.split("【分支日志与当前事实】", 1)[1].splitlines()[1])
        result = {"narrative": "新的叙事", "references": [], "proposed_changes": {"minutes": 10}}
        if action.startswith("交付"):
            result["memory_updates"] = [deposit, secret]
            result["narrative"] = "很早以前交付了十五枚钱。"
        if action.startswith("向塞文坦白"):
            secret_id = next(item["id"] for item in facts if item["kind"] == "secret")
            result["memory_updates"] = [{"op": "learn", "id": secret_id, "knowers": ["塞文"], "reason": "黎斯亲口告诉塞文"}]
        if action.startswith("收回"):
            item_id = next(item["id"] for item in facts if item["kind"] == "deposit")
            result["memory_updates"] = [{"op": "resolve", "id": item_id, "outcome": "refund"}]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]})

    store = ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    session = store.create("https://model.invalid", "test", "secret")["session_id"]
    app = create_app(Settings(db_path=tmp_path / "first.db"), narration_store=store)
    with TestClient(app) as client:
        world = create_world(client, "赫赭")["id"]
        run = client.post(f"/api/v1/worlds/{world}/adventures", json={
            "name": "黎斯的旅程", "mode": "live", "character": {"coins": 20, "name": "黎斯"},
        }).json()
        run = advance(client, run, "first", "交付押金并悄悄使用金属塑形", session)
        assert run["character"]["coins"] == 5
        checkpoint = save(client, run)
        for index in range(20):
            run = advance(client, run, f"wait-{index}", "等待", session)
        assert "很早以前交付了十五枚钱" not in prompts[-1]
        run = advance(client, run, "reveal", "向塞文坦白", session)
        assert "【按日志找回的旧回合摘录】" in prompts[-1]
        assert "很早以前交付了十五枚钱" in prompts[-1]
        assert "工具押金" in prompts[-1] and "塞文" in prompts[-1]
        assert "其他 NPC 只能依据亲眼所见" in prompts[-1]
        run = advance(client, run, "refund", "收回塞文的押金", session)
        assert run["character"]["coins"] == 20
        again = client.post(f"/api/v1/adventures/{run['id']}/drafts", json={
            "request_id": "refund-again", "action": "收回塞文的押金", "session_id": session,
            "expected_revision": run["revision"],
        })
        assert again.status_code == 502, again.text
        assert again.json()["error"]["code"] == "MODEL_STATE_INVALID"
        detail = client.get(f"/api/v1/adventures/{run['id']}").json()
        facts = {item["kind"]: item for item in detail["memory"]["items"]}
        assert facts["deposit"]["status"] == "refund" and facts["secret"]["knowers"] == ["黎斯", "塞文"]
        assert [event["turn"] for event in detail["memory"]["events"]] == [23, 22, 1, 1]

        branch = restore(client, checkpoint).json()
        branch_detail = client.get(f"/api/v1/adventures/{branch['id']}").json()
        assert branch_detail["character"]["coins"] == 5
        facts = {item["kind"]: item for item in branch_detail["memory"]["items"]}
        assert facts["deposit"]["status"] == "active" and facts["secret"]["knowers"] == ["黎斯"]
        advance(client, branch, "after-fork", "去见塞文", session)
        assert '"knowers": ["黎斯"]' in prompts[-1]
        assert "黎斯亲口告诉塞文" not in prompts[-1]

        exported = client.get(f"/api/v1/worlds/{world}/transfer?part=zip")
        assert exported.status_code == 200
        with TestClient(create_app(Settings(db_path=tmp_path / "second.db"))) as phone:
            imported = import_files(phone, ("world.zip", exported.content))
            assert imported.status_code == 201, imported.text
            runs = phone.get(f"/api/v1/worlds/{imported.json()['id']}/adventures").json()["items"]
            migrated = next(item for item in runs if item["name"] == "黎斯的旅程")
            migrated_detail = phone.get(f"/api/v1/adventures/{migrated['id']}").json()
            assert migrated_detail["memory"] == detail["memory"]
            saves = phone.get(f"/api/v1/adventures/{migrated['id']}/checkpoints").json()["items"]
            copied_branch = restore(phone, saves[0]).json()
            copied_facts = phone.get(f"/api/v1/adventures/{copied_branch['id']}").json()["memory"]["items"]
            assert copied_facts == branch_detail["memory"]["items"]


def test_corrections_are_audited_and_checkpoint_and_draft_keep_their_cursor(client):
    world, run = make_run(client)
    route = f"/api/v1/adventures/{run['id']}"
    checkpoint = save(client, run, "before-correction")
    draft = client.post(f"{route}/drafts", json={
        "request_id": "before-correction", "expected_revision": 0, "action": "看看",
    })
    assert draft.status_code == 200
    result = client.patch(f"{route}/memory", json={
        "expected_cursor": 0, "reason": "补记曾经说过的秘密",
        "item": {"kind": "secret", "title": "会塑形", "detail": "开局事实", "knowers": ["黎斯"]},
    })
    assert result.status_code == 200, result.text
    assert client.post(f"{route}/drafts/before-correction/commit").json()["error"]["code"] == "MEMORY_CONFLICT"
    item = result.json()["items"][0]
    corrected = client.patch(f"{route}/memory", json={
        "expected_cursor": 1, "reason": "修正知情名单", "id": item["id"],
        "item": {**item, "knowers": ["黎斯", "同伴"]},
    })
    assert corrected.status_code == 200
    assert [event["reason"] for event in corrected.json()["events"]] == ["修正知情名单", "补记曾经说过的秘密"]
    branch = restore(client, checkpoint).json()
    assert client.get(f"/api/v1/adventures/{branch['id']}").json()["memory"]["items"] == []
    assert client.patch(f"{route}/memory", json={"expected_cursor": 1}).status_code == 409
    assert client.get(f"{route}").json()["turns"] == []


def test_older_save_files_can_still_be_imported(client):
    world, run = make_run(client)
    save(client, run)
    route = f"/api/v1/worlds/{world}/transfer"
    settings = client.get(f"{route}?part=world").content
    old = client.get(f"{route}?part=saves").json()
    old["version"] = 1
    old.pop("adventure_memory_events")
    old["adventure_checkpoints"][0].pop("memory_cursor")
    copied = import_files(client, ("world.json", settings), ("saves.json", json.dumps(old).encode()))
    assert copied.status_code == 201, copied.text
    runs = client.get(f"/api/v1/worlds/{copied.json()['id']}/adventures").json()["items"]
    assert client.get(f"/api/v1/adventures/{runs[0]['id']}").json()["memory"]["items"] == []
