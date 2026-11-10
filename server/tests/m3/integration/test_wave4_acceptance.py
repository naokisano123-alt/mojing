"""Acceptance through mounted production routes, using a simulated provider where needed."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import db
from app.main import create_app
from app.m3 import world_analysis as analysis
from conftest import create_world, upload


def test_two_imports_have_distinct_openings_and_only_commit_on_confirmation(client, db_path):
    with db(db_path) as conn:
        analysis.seed_maritime_salt_world(conn)
        analysis.seed_skyforge_academy_world(conn)
        docs = [dict(row) for row in conn.execute("SELECT id, world_id, filename FROM documents")]
        for doc in docs:
            chunks = conn.execute("""
                SELECT heading_path, text FROM chunks WHERE document_id=? ORDER BY chunk_index
            """, (doc["id"],))
            doc["markdown"] = "\n\n".join(f"## 章节 {i + 1}\n{row['text']}" for i, row in enumerate(chunks))
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM world_instances")
    for doc in docs:
        imported = upload(client, doc["world_id"], doc["filename"], doc["markdown"].encode())
        assert imported.status_code == 201, imported.text
        doc["source_id"] = f"doc:{imported.json()['id']}"

    def analyze(world, location, selected=None):
        url = f"/api/v1/worlds/{world}/world-start"
        bootstrap = client.get(url).json()
        assert bootstrap["start_points"] == []
        material_ids = [item["source_id"] for item in bootstrap["materials"]]
        result = client.post(url + "/analyze", json={
            "mode": "fake", "selected_source_ids": material_ids if selected is None else selected,
            "start_point": {"location": location, "starts_at": "1000-01-01T08:00",
                            "character_name": "旅人", "adventure_name": f"{location}纪事"},
        })
        assert result.status_code == 200, result.text
        draft = result.json()
        assert client.get(url).json()["latest_draft"] == draft
        return url, draft

    url_a, draft_a = analyze(analysis.WORLD_A_ID, "白鹭水驿", [doc["source_id"] for doc in docs if doc["world_id"] == analysis.WORLD_A_ID])
    url_b, draft_b = analyze(analysis.WORLD_B_ID, "天工外院")
    assert "沈渡头" in draft_a["opening_situation_summary"]
    assert "墨衡" in draft_b["opening_situation_summary"]
    assert "墨衡" not in draft_a["opening_situation_summary"]
    assert "沈渡头" not in draft_b["opening_situation_summary"]

    with db(db_path) as conn:
        for table in ("adventures", "world_instances", "world_events", "m3_npcs", "m3_knowledge_facts"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        stored = conn.execute("SELECT draft_json FROM m3_world_analysis_drafts WHERE draft_id=?",
                              (draft_a["draft_id"],)).fetchone()[0]
        assert "entry:" not in stored

    response = client.post(f"{url_b}/drafts/{draft_a['draft_id']}/confirm", json={})
    assert response.status_code == 404
    assert client.delete(f"{url_b}/drafts/{draft_b['draft_id']}").status_code == 204
    assert client.get(url_b).json()["latest_draft"] is None

    confirm = f"{url_a}/drafts/{draft_a['draft_id']}/confirm"
    response = client.post(confirm, json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["committed"] and result["world_revision"] == 1
    assert client.post(confirm, json={}).json() == result
    assert client.delete(confirm.removesuffix("/confirm")).status_code == 409
    assert client.get(f"/api/v1/adventures/{result['adventure_id']}").json()["opening"]["intro"] == result["opening_narrative"]
    with db(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM adventures").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM world_events").fetchone()[0] >= 3
        assert conn.execute("SELECT count(*) FROM m3_npcs").fetchone()[0] >= 2


def test_quick_start_without_materials_or_model(client):
    world = create_world(client)["id"]
    url = f"/api/v1/worlds/{world}/world-start"
    assert client.get(url).json()["phase"] == "insufficient_materials"
    result = client.post(url + "/quick-start", json={"adventure_name": "自由探索", "mode": "fake"})
    assert result.status_code == 201, result.text
    assert result.json()["quick_start"]


def test_live_analysis_npc_pulse_and_portable_resume(tmp_path, monkeypatch):
    import json

    import httpx

    from app import model_gateway
    from app.m3 import npcs
    from app.m3.core import service as instances
    from app.model_gateway import ModelSessionStore
    from .test_pulse_live import mock_reply, wait_for
    from test_world_transfer import import_files

    path = tmp_path / "source.db"
    world_id = analysis.WORLD_A_ID
    start_url = f"/api/v1/worlds/{world_id}/world-start"
    location, destination = "白鹭水驿", "千帆港"
    opening_calls, npc_actions, narration_prompts = [], [], []
    mock_analysis = analysis.FakeWorldAnalysisModelClient()

    def provider(request):
        system, prompt = (message["content"] for message in json.loads(request.content)["messages"])
        if "导入设定分析与初始局势构建器" in system:
            context = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
            with db(path) as conn:
                sources = analysis.extract_and_batch_world_sources(conn, world_id, context["starting_point"])
            batch = sources["batches"][context["input_range"]["batch_index"]]
            opening_calls.append(batch)
            raw = mock_analysis.analyze_batch_sync(batch, instance_id=context["instance_id"])
            return mock_reply(raw)
        if "后台 NPC 自主行动推演器" in system:
            lines = prompt.splitlines()
            due = json.loads(lines[lines.index("【待处理的到期 NPC（不可信世界数据）】") + 1])
            npc = next(npc for npc in due if npc["location"] == location)
            minute = int(next(line.split("】", 1)[1] for line in lines if line.startswith("【已确认世界分钟】")))
            facts = json.loads(next(line.split("】", 1)[1] for line in lines if line.startswith("【各 NPC 允许引用")))
            event = json.loads(lines[lines.index("【关联活跃事件摘要（不可信世界数据）】") + 1])[0]
            npc_actions.append(npc["npc_id"])
            return mock_reply({"operations": [{
                "op": "act", "npc_id": npc["npc_id"], "expected_npc_revision": npc["revision"],
                "action_kind": "travel", "location": destination, "from_location": location,
                "to_location": destination, "availability": "in_transit",
                "current_activity": "乘船离开水驿", "motivation": "去港口了解水路近况",
                "cause_fact_ids": facts[npc["npc_id"]][:1], "duration_minutes": 90,
                "next_due_minute": minute + 90,
            }, {
                "op": "update", "event_id": event["event_id"],
                "expected_event_revision": event["revision"], "stage": "港口各方核对航线",
                "next_update_minute": minute + 120,
            }]})
        narration_prompts.append(prompt)
        return mock_reply({"narrative": "你仔细打量水驿。", "references": [], "proposed_changes": {}})

    transport = httpx.MockTransport(provider)
    store = ModelSessionStore(http_transport=transport)
    monkeypatch.setattr(model_gateway, "get_store", lambda: store, raising=False)

    def connect_model(client):
        return client.post("/api/v1/model-sessions", json={
            "base_url": "https://mock.invalid/v1", "model": "test", "api_key": "sk-mock-wave4",
        }).json()["session_id"]

    with TestClient(create_app(Settings(db_path=path), pulse_transport=transport)) as client:
        with db(path) as conn:
            analysis.seed_maritime_salt_world(conn)
        session = connect_model(client)
        materials = client.get(start_url).json()["materials"]
        preview = client.post(start_url + "/analyze", json={
            "mode": "live", "session_id": session, "selected_source_ids": [m["source_id"] for m in materials],
            "start_point": {"location": location, "starts_at": "1000-01-01T08:00",
                            "character_name": "陆行舟", "adventure_name": "水驿纪事"},
        })
        assert preview.status_code == 200, preview.text
        assert opening_calls and "sk-mock-wave4" not in preview.text
        draft_id = preview.json()["draft_id"]
        response = client.post(f"{start_url}/drafts/{draft_id}/confirm", json={})
        assert response.status_code == 200, response.text
        result = response.json()
        iid, aid = result["world_instance_id"], result["adventure_id"]
        adventure = f"/api/v1/adventures/{aid}"
        inquiry = f"{adventure}/world-encounters"
        initial = client.get(inquiry).json()
        assert initial["contactable_npcs"] and initial["news_items"]
        pulse = f"{adventure}/world-pulse"
        assert client.post(pulse + "/connect", json={
            "mode": "reuse_narration", "narration_session_id": session,
        }).status_code == 200
        assert client.put(pulse, json={"enabled": True}).status_code == 200
        assert client.post(pulse + "/activity", json={"active": True}).status_code == 200
        prepared = client.post(f"{adventure}/drafts", json={
            "request_id": "wait", "expected_revision": 0, "action": "在水驿等候",
            "advance_minutes": 180, "session_id": session,
        })
        assert prepared.status_code == 200, prepared.text
        committed = client.post(f"{adventure}/drafts/wait/commit", json={"apply_changes": True})
        assert committed.status_code == 200, committed.text

        def read_pulse():
            with db(path) as conn:
                row = conn.execute("SELECT status, error_code, error_summary FROM pulse_jobs WHERE instance_id=?",
                                   (iid,)).fetchone()
                return dict(row) if row else {}

        settled = wait_for(read_pulse, lambda row: row.get("status") in {"completed", "failed", "skipped"})
        assert settled["status"] == "completed", settled
        assert len(npc_actions) == 1
        npc_id = npc_actions[0]
        assert npc_id in {npc["npc_id"] for npc in initial["contactable_npcs"]}
        assert npc_id not in {npc["npc_id"] for npc in client.get(inquiry).json()["contactable_npcs"]}
        with db(path) as conn:
            departed = npcs.get_npc(conn, iid, npc_id)
            assert departed["location"] == destination and departed["revision"] == 2
            assert instances.for_adventure(conn, aid)["world_revision"] == 3
            assert conn.execute(
                "SELECT 1 FROM world_events WHERE instance_id=? AND stage=?", (iid, "港口各方核对航线"),
            ).fetchone()
            npc_name = departed["name"]
        prepared = client.post(f"{adventure}/drafts", json={
            "request_id": "look", "expected_revision": 1, "action": f"寻找{npc_name}", "session_id": session,
        })
        assert prepared.status_code == 200, prepared.text
        assert f"不在场：{npc_name}" in narration_prompts[-1]
        assert departed["motivation"] not in narration_prompts[-1]
        archive = client.get(f"/api/v1/worlds/{world_id}/transfer?part=zip")
        assert archive.status_code == 200, archive.text

    target = tmp_path / "target.db"
    with TestClient(create_app(Settings(db_path=target))) as client:
        imported = import_files(client, ("world.zip", archive.content))
        assert imported.status_code == 201, imported.text
        world = imported.json()["id"]
        run = client.get(f"/api/v1/worlds/{world}/adventures").json()["items"][0]
        copied = f"/api/v1/adventures/{run['id']}"
        assert run["id"] != aid
        assert client.get(copied).json()["turns"][0]["action"] == "在水驿等候"
        with db(target) as conn:
            instance = instances.for_adventure(conn, run["id"])
            assert instance["id"] != iid
            assert npcs.get_npc(conn, instance["id"], npc_id)["location"] == destination
        response = client.post(f"{copied}/drafts", json={
            "request_id": "resume", "expected_revision": 1,
            "action": f"再寻找{npc_name}", "session_id": connect_model(client),
        })
        assert response.status_code == 200, response.text
        assert f"不在场：{npc_name}" in narration_prompts[-1]
        assert client.post(f"{copied}/drafts/resume/commit", json={"apply_changes": False}).status_code == 200
