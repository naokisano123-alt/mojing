from io import BytesIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import upload
from test_checkpoints import advance, make_run, restore, save
from test_world_export import write_note


def import_files(client, *files):
    return client.post("/api/v1/world-transfers", files=[("files", file) for file in files])


def test_transfer_to_another_database_keeps_playable_history_and_branches(client, tmp_path):
    world_id, original = make_run(client)
    document = upload(client, world_id, "地点.txt", "👋 路口有两条小路。".encode()).json()
    assert write_note(client, original["id"], "干粮", "来自旧街的小店。").status_code == 200
    original = advance(client, original, "one", "看看路口")
    checkpoint = save(client, original, "save-one")
    branch = restore(client, checkpoint).json()
    branch = advance(client, branch, "two", "往小路走")
    original = advance(client, original, "three", "往大路走")
    save(client, original, "save-two")
    url = f"/api/v1/worlds/{world_id}/transfer"
    world_file = client.get(url, params={"part": "world"})
    save_file = client.get(url, params={"part": "saves"})
    bundle = client.get(url, params={"part": "zip"})
    assert world_file.status_code == save_file.status_code == bundle.status_code == 200
    with ZipFile(BytesIO(bundle.content)) as zipped:
        assert zipped.read("world.json") == world_file.content
        assert zipped.read("saves.json") == save_file.content

    with TestClient(create_app(Settings(db_path=tmp_path / "second-phone.db"))) as phone:
        only_saves = ("存档.json", save_file.content)
        assert import_files(phone, only_saves).json()["error"]["code"] == "WORLD_TRANSFER_SETTINGS_REQUIRED"
        world_only = import_files(phone, ("世界观.json", world_file.content))
        assert world_only.status_code == 201, world_only.text
        empty_world = world_only.json()["id"]
        assert phone.get(f"/api/v1/worlds/{empty_world}/adventures").json()["items"] == []
        migrated = import_files(phone, only_saves)
        assert migrated.status_code == 201, migrated.text
        migrated_world = migrated.json()["id"]
        assert migrated_world not in {world_id, empty_world}
        runs = phone.get(f"/api/v1/worlds/{migrated_world}/adventures").json()["items"]
        assert len(runs) == 2
        for before, action in ((original, "往大路走"), (branch, "往小路走")):
            target = next(item for item in runs if item["name"] == before["name"])
            assert target["id"] != before["id"]
            old = client.get(f"/api/v1/adventures/{before['id']}").json()
            detail = phone.get(f"/api/v1/adventures/{target['id']}").json()
            assert detail["character"] == old["character"]
            assert detail["item_descriptions"] == {"干粮": "来自旧街的小店。"}
            assert [turn["action"] for turn in detail["turns"]] == ["看看路口", action]
            assert [turn["result"]["narrative"] for turn in detail["turns"]] == [
                turn["result"]["narrative"] for turn in old["turns"]
            ]
            source = detail["turns"][0]["result"]["sources"][0]
            assert source["document_id"] != document["id"]
            assert phone.get(f"/api/v1/documents/{source['document_id']}/raw").json()["text"] == "👋 路口有两条小路。"
            advance(phone, target, "continue", "继续旅程")

        parent = next(item for item in runs if item["name"] == original["name"])
        child = next(item for item in runs if item["name"] == branch["name"])
        saved = phone.get(f"/api/v1/adventures/{parent['id']}/checkpoints").json()["items"]
        assert len(saved) == 2
        origin = next(point for point in saved if point["revision"] == 1)
        assert child["origin_checkpoint_id"] == origin["id"] != checkpoint["id"]
        fork = restore(phone, origin, "restored")
        assert fork.status_code == 201, fork.text
        assert fork.json()["revision"] == 1

        for files in ((("archive.zip", bundle.content),), (("world.json", world_file.content), only_saves)):
            copied = import_files(phone, *files)
            assert copied.status_code == 201, copied.text
            assert copied.json()["id"] != migrated_world
            assert len(phone.get(f"/api/v1/worlds/{copied.json()['id']}/adventures").json()["items"]) == 2


def test_rejects_mismatches_bad_zip_and_broken_history_without_partial_world(client):
    world_id, run = make_run(client)
    advance(client, run, "one")
    url = f"/api/v1/worlds/{world_id}/transfer"
    before = client.get(url, params={"part": "world"}).content
    assert client.patch(f"/api/v1/worlds/{world_id}", json={"description": "更新版本"}).status_code == 200
    save_data = client.get(url, params={"part": "saves"}).json()
    current = client.get(url, params={"part": "world"}).content
    result = import_files(client, ("world.json", before), ("saves.json", json.dumps(save_data).encode()))
    assert result.json()["error"]["code"] == "WORLD_TRANSFER_MISMATCH"
    save_data["adventure_turns"] = []
    result = import_files(client, ("world.json", current), ("saves.json", json.dumps(save_data).encode()))
    assert result.json()["error"]["code"] == "WORLD_TRANSFER_INVALID"
    raw = BytesIO()
    with ZipFile(raw, "w") as zipped:
        zipped.writestr("../world.json", current)
        zipped.writestr("saves.json", json.dumps(save_data))
    result = import_files(client, ("archive.zip", raw.getvalue()))
    assert result.json()["error"]["code"] == "WORLD_TRANSFER_INVALID"
    assert client.get("/api/v1/worlds").json()["total"] == 1
