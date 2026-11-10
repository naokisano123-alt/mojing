"""M3.2 脉动控制：默认关闭、连接会话、显式启停与活动租约。"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import create_world, upload


@pytest.fixture()
def client(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "pulse.db"))
    with TestClient(app) as test_client:
        yield test_client


SAMPLE = "# 霜木镇\n\n镇口的公告栏贴着商会的新告示。\n"


def make_live_run(client):
    world = create_world(client)
    upload(client, world["id"], "霜木镇.md", SAMPLE.encode("utf-8"))
    response = client.post(
        f"/api/v1/worlds/{world['id']}/adventures",
        json={"name": "真实脉动", "mode": "live"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_pulse_defaults_off_and_requires_connection(client):
    run = make_live_run(client)
    status = client.get(f"/api/v1/adventures/{run['id']}/world-pulse").json()
    assert status["enabled"] is False
    assert status["mode"] == "unbound"
    assert status["state"] == "off"

    enable = client.put(f"/api/v1/adventures/{run['id']}/world-pulse", json={"enabled": True})
    assert enable.status_code == 409
    assert enable.json()["error"]["code"] == "PULSE_NOT_CONNECTED"


def test_connect_independent_then_enable_and_disable(client):
    run = make_live_run(client)
    connect = client.post(
        f"/api/v1/adventures/{run['id']}/world-pulse/connect",
        json={
            "mode": "independent",
            "base_url": "https://api.example.invalid/v1",
            "model": "mock-model",
            "api_key": "sk-test",
        },
    )
    assert connect.status_code == 200, connect.text
    assert connect.json()["mode"] == "independent"

    status = client.get(f"/api/v1/adventures/{run['id']}/world-pulse").json()
    assert status["mode"] == "independent"
    assert status["available"] is True

    assert client.put(f"/api/v1/adventures/{run['id']}/world-pulse", json={"enabled": True}).status_code == 200
    status = client.get(f"/api/v1/adventures/{run['id']}/world-pulse").json()
    assert status["enabled"] is True
    assert status["state"] == "ready"

    assert client.post(f"/api/v1/adventures/{run['id']}/world-pulse/activity", json={"active": False}).status_code == 200
    assert client.put(f"/api/v1/adventures/{run['id']}/world-pulse", json={"enabled": False}).status_code == 200


def test_fake_journey_rejects_model_connection(client):
    world = create_world(client)
    run = client.post(
        f"/api/v1/worlds/{world['id']}/adventures", json={"name": "模拟", "mode": "fake"}
    ).json()
    response = client.post(
        f"/api/v1/adventures/{run['id']}/world-pulse/connect",
        json={"mode": "independent", "base_url": "https://api.example.invalid/v1", "model": "m", "api_key": "k"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PULSE_LIVE_ONLY"


def test_validation_errors_are_explicit(client):
    run = make_live_run(client)
    bad_mode = client.post(
        f"/api/v1/adventures/{run['id']}/world-pulse/connect", json={"mode": "nope"}
    )
    assert bad_mode.status_code == 422
    bad_enabled = client.put(f"/api/v1/adventures/{run['id']}/world-pulse", json={"enabled": "yes"})
    assert bad_enabled.status_code == 422
    bad_activity = client.post(
        f"/api/v1/adventures/{run['id']}/world-pulse/activity", json={"active": "yes"}
    )
    assert bad_activity.status_code == 422