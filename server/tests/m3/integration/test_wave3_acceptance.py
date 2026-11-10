"""Authoritative caravan event, NPC action, player inquiry and portable continuation."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import db
from app.main import create_app
from app.m3 import encounters, events, npcs
from app.m3.core import service as instances
from app.m3.encounters.fixtures import CARAVAN_EVENT_ID, seed_innkeeper_caravan_scenario
from conftest import create_world, upload
from test_scenario_clock import OPENING, commit, draft
from test_world_transfer import import_files


def test_caravan_encounter_survives_portable_transfer(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    with TestClient(create_app(Settings(db_path=source))) as client:
        world_id = create_world(client, "霜木镇")["id"]
        base = f"/api/v1/worlds/{world_id}"
        response = upload(client, world_id, "北境商路.md", "# 北境商路\n灰鬃商队改道。".encode())
        assert response.status_code == 201, response.text
        doc_id = response.json()["id"]
        assert upload(client, world_id, "镇志.txt", "霜木镇有一座酒馆。".encode()).status_code == 201
        opening = client.post(f"{base}/opening-settings", json={
            **OPENING, "character": {**OPENING["character"], "location": "霜木镇"}, "events": [],
        })
        assert opening.status_code == 201, opening.text
        run = client.post(f"{base}/adventures", json={
            "name": "北境旅程", "opening_id": opening.json()["id"],
        }).json()
        assert draft(client, run, "arrive", "在镇上等候四小时。", advance_minutes=240).status_code == 200
        run = commit(client, run, "arrive").json()
        adventure = f"/api/v1/adventures/{run['id']}"
        inquiry = f"{adventure}/world-encounters"
        with db(source) as conn:
            iid = instances.for_adventure(conn, run["id"])["id"]
            canon = "chunk:" + conn.execute("SELECT id FROM chunks WHERE document_id=?", (doc_id,)).fetchone()[0]
            events.apply_proposal(conn, iid, {
                "mode": "seed",
                "operations": [{
                    "op": "create", "kind": "thread", "title": title,
                    "description": "边关加税导致运输变化。", "location": "霜木镇",
                    "scope": "local", "stage": "rerouted", "status": "active",
                    "next_update_minute": 600, "source_ids": [canon],
                } for title in ("灰鬃商队改道", "渡口转运", "霜木镇铁器供应")],
            }, world_minute=60, id_factory=iter((CARAVAN_EVENT_ID, "event_ferry", "event_supply")).__next__)
            seed_innkeeper_caravan_scenario(conn, instance_id=iid, world_id=world_id, adventure_id=run["id"])
            seed = npcs.build_northern_trade_npc_seed_proposal(instance_id=iid, world_minute=240)
            op = next(item for item in seed["operations"] if item["npc_id"] == "npc_han_shopkeeper")
            npcs.apply_proposal(conn, iid, {**seed, "operations": [{**op, "source_ids": [canon]}]})
            npc_id = op["npc_id"]

        initial = client.get(inquiry, params={"location": "伪造地点"})
        assert initial.status_code == 200, initial.text
        assert initial.json()["player_location"] == "霜木镇"
        assert next(npc for npc in initial.json()["contactable_npcs"] if npc["npc_id"] == npc_id)["contactable"]
        action = "在霜木镇等商队的消息。"
        assert draft(client, run, "wait", action, advance_minutes=60).status_code == 200
        assert commit(client, run, "wait").status_code == 200
        with db(source) as conn:
            inst = instances.for_adventure(conn, run["id"])
            assert inst["world_minute"] == 300
            action = npcs.build_wave3_mock_npc_responses(instance_id=iid)["advance_minute_60"]["operations"][0]
            npcs.apply_proposal(conn, iid, {
                "mode": "advance", "world_minute": inst["world_minute"],
                "base_world_revision": inst["world_revision"],
                "operations": [{
                    **action, "next_due_minute": inst["world_minute"] + 90,
                    "source_ids": [canon], "cause_event_ids": [CARAVAN_EVENT_ID], "cause_fact_ids": [],
                }],
            })

        # Checkpoint before the player learns what the innkeeper knows.
        point = client.post(f"{adventure}/checkpoints", json={"name": "询问之前", "expected_revision": 2, "request_id": "pre"})
        assert point.status_code == 201, point.text
        bundle = client.get(inquiry).json()
        assert all(npc["npc_id"] != npc_id for npc in bundle["contactable_npcs"])
        rumor = bundle["news_items"][0]
        option = next(item for item in bundle["options_by_fact_id"][rumor["fact_id"]]
                      if item["target_npc_id"] == "npc_marta_innkeeper")
        assert option["contactable"]
        response = client.post(f"{inquiry}/drafts", json={
            "fact_id": rumor["fact_id"], "option_kind": option["kind"],
            "target_npc_id": option["target_npc_id"], "action_text": option["suggested_action_text"] + "请她详细说说。",
            "player_location": "伪造地点",
        })
        assert response.status_code == 201, response.text
        assert "source_event_id" not in response.json()["proposed_transmissions"][0]
        assert len(client.get(f"{adventure}/world-news").json()["items"]) == 1
        draft_id = response.json()["draft_id"]
        commit_path = f"{inquiry}/drafts/{draft_id}/commit"
        assert client.post(commit_path, json={"commit_world_minute": 1000}).status_code == 422
        result = client.post(commit_path, json={})
        assert result.status_code == 200, result.text
        assert client.post(commit_path, json={}).json()["idempotent_replay"]
        news = client.get(f"{adventure}/world-news").json()["items"]
        assert len(news) == 2 and any("晚三天" in item["content"] for item in news)

        assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
        archive = client.get(f"{base}/transfer?part=zip")
        assert archive.status_code == 200, archive.text

    with TestClient(create_app(Settings(db_path=target))) as client:
        response = import_files(client, ("world.zip", archive.content))
        assert response.status_code == 201, response.text
        target_world = response.json()["id"]
        imported = client.get(f"/api/v1/worlds/{target_world}/adventures").json()["items"][0]
        adventure = f"/api/v1/adventures/{imported['id']}"
        assert imported["id"] != run["id"]
        assert client.get(f"{adventure}/world-news").json()["items"] == news
        with db(target) as conn:
            inst = instances.for_adventure(conn, imported["id"])
            npc = npcs.get_npc(conn, inst["id"], npc_id)
            event = events.get_event(conn, inst["id"], CARAVAN_EVENT_ID)
            assert npc["location"] == "白河城"
            assert npc["source_ids"] == event["source_ids"] and canon not in npc["source_ids"]
            assert encounters.snapshot(conn, inst["id"])["history"][0]["draft_id"] == draft_id
        point_id = client.get(f"{adventure}/checkpoints").json()["items"][0]["id"]
        branch = client.post(f"/api/v1/checkpoints/{point_id}/restore", json={
            "name": "回到询问之前", "request_id": "fork",
        })
        assert branch.status_code == 201, branch.text
        assert len(client.get(f"/api/v1/adventures/{branch.json()['id']}/world-news").json()["items"]) == 1
        assert draft(client, imported, "resume", "回到酒馆休息。").status_code == 200
        assert commit(client, imported, "resume").status_code == 200
