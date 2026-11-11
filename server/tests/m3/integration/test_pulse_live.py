"""Production app routes and worker, with the external model replaced by HTTP mocks."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import model_gateway
from app.config import Settings
from app.db import db
from app.main import create_app
from app.model_gateway import ModelSessionStore
from conftest import create_world, upload


def wait_for(read, expected):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        value = read()
        if expected(value):
            return value
        time.sleep(0.05)
    pytest.fail(f"Unexpected final pulse state: {value}")


def mock_reply(value):
    return httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 240, "total_tokens": 840},
    })


@pytest.mark.parametrize("failure,code", [
    ("missing_scope", "VALIDATION_ERROR"),
    ("truncated", "MODEL_OUTPUT_TRUNCATED"),
    ("bad_json", "MODEL_OUTPUT_NOT_JSON"),
    ("unknown_field", "PROPOSAL_UNKNOWN_ROOT_FIELD"),
])
def test_pulse_failure_reason_and_explicit_retry(tmp_path, failure, code):
    """走正式控制入口、HTTP 客户端、Worker 和领域校验，拒绝结果也须结算且可重试。"""
    calls = []
    hidden = "不应出现在手机错误说明中的隐藏事件"

    def provider(request):
        body = json.loads(request.content)
        system, prompt = (message["content"] for message in body["messages"])
        calls.append(body)
        assert body["model"] == "deepseek-flash"
        assert "scope 必须填写且仅允许 local/regional/national/international" in system
        assert "description 不超过 80 字" in system
        source_ids = next(
            json.loads(line.split("】", 1)[1])
            for line in prompt.splitlines() if line.startswith("【允许引用的 source_id")
        )
        proposal = {"operations": [{
            "op": "create", "kind": "thread", "title": title,
            "public_label": ("躁动的狮群", "城内契约", "潮水之约")[index],
            "description": "行会安排商队轮流进港。", "location": "码头",
            "scope": "local", "next_update_minute": 60 + index * 30,
            "source_ids": source_ids[:1],
        } for index, title in enumerate(("商队进港", "行会调度", "仓库轮班"))]}
        reason = "stop"
        if len(calls) == 1:
            if failure == "missing_scope":
                # 原提示词未告知 scope；模型照其余要求回复，仍会被整批拒绝。
                for operation in proposal["operations"]:
                    operation.pop("scope")
            elif failure == "truncated":
                reason = "length"
            elif failure == "unknown_field":
                proposal[hidden] = hidden
        content = '{"operations":[' if len(calls) == 1 and failure == "bad_json" else json.dumps(proposal)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": reason, "message": {"content": content}}],
            "usage": {"prompt_tokens": 2393, "completion_tokens": 1400, "total_tokens": 3793},
        })

    settings = Settings(db_path=tmp_path / "failure.db")
    with TestClient(create_app(settings, pulse_transport=httpx.MockTransport(provider))) as client:
        world = create_world(client)["id"]
        assert upload(client, world, "码头.txt", "行会管理商队进港和码头仓库轮班。".encode()).status_code == 201
        run = client.post(f"/api/v1/worlds/{world}/adventures", json={"name": "验收", "mode": "live"}).json()
        path = f"/api/v1/adventures/{run['id']}/world-pulse"
        connected = client.post(f"{path}/connect", json={
            "mode": "independent", "base_url": "https://mock.invalid/v1",
            "model": "deepseek-flash", "api_key": "sk-only-for-http-mock",
        })
        assert connected.status_code == 200, connected.text
        assert client.post(f"{path}/activity", json={"active": True}).status_code == 200
        assert client.put(path, json={"enabled": True}).status_code == 200
        failed = wait_for(lambda: client.get(path).json(), lambda s: s["reason_code"] == "PULSE_JOB_FAILED")
        assert code in failed["reason_summary"]
        assert "未写入世界" in failed["reason_summary"]
        assert hidden not in json.dumps(failed, ensure_ascii=False)
        assert "sk-only-for-http-mock" not in json.dumps(failed)
        assert failed["usage"]["billed_calls"] == 1
        assert failed["usage"]["actual_tokens"] == 3793
        with db(settings.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM world_events").fetchone()[0] == 0
            assert conn.execute("SELECT error_code FROM pulse_jobs").fetchone()[0] == code

        # 不自动重复付费调用，用户主动关闭再开启后可完成初始化。
        assert len(calls) == 1
        assert client.put(path, json={"enabled": False}).status_code == 200
        assert client.put(path, json={"enabled": True}).status_code == 200
        recovered = wait_for(lambda: client.get(path).json(), lambda s: s["usage"]["billed_calls"] == 2)
        assert recovered["reason_code"] is None
        assert not recovered.get("reason_summary")
        with db(settings.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM world_events").fetchone()[0] == 3


@pytest.mark.parametrize("connection", ["independent", "reuse_narration"])
def test_live_seed_advance_news_narration_and_offline_gate(tmp_path, monkeypatch, connection):
    path = tmp_path / "live.db"
    calls, narrations = [], []
    hidden = "海关官员私下藏起的手稿"
    public = "码头公告提到新的卸货时段"

    def provider(request):
        body = json.loads(request.content)
        system, prompt = (message["content"] for message in body["messages"])
        if "初始局势推演器" in system:
            calls.append("seed")
            source_ids = next(
                json.loads(line.split("】", 1)[1]) for line in prompt.splitlines() if line.startswith("【允许引用的 source_id")
            )
            assert body["max_tokens"] >= 700
            assert "other_instance_event_ids" not in prompt
            return mock_reply({"operations": [{
                "op": "create", "kind": "thread", "title": title,
                "description": hidden if index == 0 else "商队在码头等候。",
                "public_label": ("躁动的狮群", "城内契约", "潮水之约")[index],
                "location": "码头", "scope": "local", "stage": "开始", "status": "active",
                "next_update_minute": 20 + index * 100, "source_ids": source_ids[:1],
            } for index, title in enumerate(("码头的交接", "本地商队", "行会日程"))]})
        if "后台事件推进器" in system:
            calls.append("advance")
            lines = prompt.splitlines()
            event = json.loads(lines[lines.index("【待处理的到期活跃事件（不可信世界数据）】") + 1])[0]
            assert "other_instance_event_ids" not in prompt
            return mock_reply({
                "operations": [{
                    "op": "update", "event_id": event["event_id"], "expected_event_revision": event["revision"],
                    "stage": "调整卸货", "next_update_minute": 160,
                }],
                "transmissions": [{
                    "holder_id": "player", "knowledge_type": "rumor", "channel": "notice",
                    "title": "码头公告", "content": public, "source_event_id": event["event_id"],
                    "learned_minute": 30,
                }],
            })
        narrations.append(prompt)
        return mock_reply({"narrative": "你在码头等了一会儿。", "references": [], "proposed_changes": {"minutes": 30}})

    transport = httpx.MockTransport(provider)
    store = ModelSessionStore(http_transport=transport)
    # Exercise the default app's lazy store resolution, even for the reuse path.
    monkeypatch.setattr(model_gateway, "get_store", lambda: store, raising=False)
    settings = Settings(db_path=path)
    with TestClient(create_app(settings, pulse_transport=transport)) as client:
        world = create_world(client)["id"]
        assert upload(client, world, "码头.txt", "镇上有一座码头，行会管理商队进出。".encode()).status_code == 201
        run = client.post(f"/api/v1/worlds/{world}/adventures", json={"name": "码头", "mode": "live"}).json()
        adventure = f"/api/v1/adventures/{run['id']}"
        pulse = f"{adventure}/world-pulse"
        session = client.post("/api/v1/model-sessions", json={
            "base_url": "https://mock.invalid/v1", "model": "test", "api_key": "sk-narration-mock",
        }).json()["session_id"]
        config = {"mode": connection}
        if connection == "independent":
            config.update(base_url="https://mock.invalid/v1", model="test", api_key="sk-pulse-mock")
        else:
            config["narration_session_id"] = session
        connected = client.post(f"{pulse}/connect", json=config)
        assert connected.status_code == 200, connected.text
        assert connected.json()["mode"] == connection
        assert not connected.json()["enabled"]
        assert not connected.json()["simulation"]["initialized"]
        assert "sk-" not in connected.text and session not in connected.text

        enabled = client.put(pulse, json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["state"] == "offline"
        assert client.get(pulse).json()["state"] == "offline"
        assert calls == []
        assert client.post(f"{pulse}/activity", json={"active": True}).status_code == 200
        wait_for(lambda: client.get(pulse).json(), lambda s: s["usage"]["billed_calls"] == 1)
        assert calls == ["seed"]
        simulation = client.get(pulse).json()["simulation"]
        assert simulation["initialized"]
        assert {item["label"] for item in simulation["events"]} == {"躁动的狮群", "城内契约", "潮水之约"}
        assert hidden not in json.dumps(simulation, ensure_ascii=False)
        assert "码头的交接" not in json.dumps(simulation, ensure_ascii=False)
        assert client.get(f"{adventure}/world-news").json()["items"] == []

        def turn(key, revision):
            prepared = client.post(f"{adventure}/drafts", json={
                "request_id": key, "expected_revision": revision, "action": "在码头等候", "session_id": session,
            })
            assert prepared.status_code == 200, prepared.text
            committed = client.post(f"{adventure}/drafts/{key}/commit", json={"apply_changes": True})
            assert committed.status_code == 200, committed.text

        turn("first", 0)
        page = wait_for(lambda: client.get(f"{adventure}/world-news").json(), lambda page: bool(page["items"]))
        assert public in page["items"][0]["content"]
        assert hidden not in json.dumps(page, ensure_ascii=False)
        assert "source_event_id" not in page["items"][0]
        assert public not in narrations[0] and hidden not in narrations[0]
        assert calls == ["seed", "advance"]
        assert {item["label"] for item in client.get(pulse).json()["simulation"]["events"]} == {"躁动的狮群", "城内契约", "潮水之约"}

        assert client.post(f"{pulse}/activity", json={"active": False}).status_code == 200
        turn("second", 1)
        assert public in narrations[1] and hidden not in narrations[1]
        with db(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM pulse_jobs").fetchone()[0] == 2
        assert client.post(f"{pulse}/activity", json={"active": True}).status_code == 200
        assert client.put(pulse, json={"enabled": False}).status_code == 200
        turn("third", 2)
        assert calls == ["seed", "advance"]
        assert len(narrations) == 3
        assert client.get(f"{adventure}/world-news").json()["items"] == page["items"]


@pytest.mark.parametrize("hosted", [False, True])
def test_independent_pulse_enforces_app_model_url_policy(tmp_path, hosted):
    origin = "https://game.example.com" if hosted else ""
    settings = Settings(db_path=tmp_path / "policy.db", public_origin=origin, https_model_only=not hosted)
    with TestClient(create_app(settings), headers={"Origin": origin}) as client:
        world = create_world(client)["id"]
        run = client.post(f"/api/v1/worlds/{world}/adventures", json={"name": "测试", "mode": "live"}).json()
        path = f"/api/v1/adventures/{run['id']}/world-pulse/connect"
        url = "https://unlisted.invalid" if hosted else "http://localhost:8080"
        response = client.post(path, json={
            "mode": "independent", "base_url": url, "model": "m", "api_key": "sk-mock-policy",
        })
        assert response.status_code == (403 if hosted else 422), response.text
        invalid = client.post(path, json={
            "mode": "independent", "base_url": "file:///x", "model": "m", "api_key": "sk-mock-policy",
        })
        assert invalid.status_code == 422, invalid.text


def test_reconnect_after_restart_starts_pending_seed_without_reenabling(tmp_path):
    settings = Settings(db_path=tmp_path / "restart.db")
    requests = []

    def provider(request):
        requests.append(request)
        return mock_reply({"operations": []})

    transport = httpx.MockTransport(provider)
    config = {"mode": "independent", "base_url": "https://mock.invalid", "model": "test", "api_key": "sk-mock"}
    with TestClient(create_app(settings, pulse_transport=transport)) as client:
        world = create_world(client)["id"]
        assert upload(client, world, "世界.txt", "商队经过小镇。".encode()).status_code == 201
        run = client.post(f"/api/v1/worlds/{world}/adventures", json={"name": "重启", "mode": "live"}).json()
        path = f"/api/v1/adventures/{run['id']}/world-pulse"
        assert client.post(f"{path}/connect", json=config).status_code == 200
        assert client.put(path, json={"enabled": True}).json()["state"] == "offline"

    with TestClient(create_app(settings, pulse_transport=transport)) as client:
        status = client.get(path).json()
        assert status["enabled"] and status["state"] == "waiting_for_model"
        assert client.post(f"{path}/activity", json={"active": True}).status_code == 200
        assert requests == []
        assert client.post(f"{path}/connect", json=config).json()["enabled"]
        wait_for(lambda: client.get(path).json(), lambda s: s["reason_code"] == "PULSE_JOB_FAILED")
        assert len(requests) == 1
        # A structurally empty seed response must fail without creating any world events.
        with db(settings.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM world_events").fetchone()[0] == 0
            assert conn.execute("SELECT status FROM pulse_jobs").fetchone()[0] == "failed"
