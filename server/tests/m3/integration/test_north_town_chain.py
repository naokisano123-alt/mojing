"""北境小镇验收链：事件 → 知识 → 玩家消息 → 检查点分支（真实模块，Fake 模型）。"""

import pytest
from fastapi.testclient import TestClient

from app.adventures import checkpoints, service as adventures
from app.config import Settings
from app.db import db
from app.main import create_app
from app.m3 import events, knowledge
from app.m3.core import service as core
from app.m3.events.fixtures import build_northern_trade_seed_proposal
from conftest import create_world


def _source_ids(proposal):
    ids = set()
    for op in proposal.get("operations", []):
        ids.update(op.get("source_ids") or [])
    return ids


def test_north_town_chain_end_to_end(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "chain.db"))
    with TestClient(app) as client:
        world = create_world(client)
        run = client.post(
            f"/api/v1/worlds/{world['id']}/adventures",
            json={"name": "北境小镇", "mode": "fake"},
        ).json()

        with db(tmp_path / "chain.db") as conn:
            instance = core.for_adventure(conn, run["id"])
            proposal = build_northern_trade_seed_proposal(
                instance_id=instance["id"], world_minute=0
            )
            applied = events.apply_proposal(
                conn,
                instance["id"],
                proposal,
                allowed_source_ids=_source_ids(proposal),
            )
            listed = events.list_events(conn, instance["id"], limit=10)
            event_ids = [item["event_id"] for item in listed["items"]]
            assert len(event_ids) >= 3, applied

            knowledge.record_transmission(
                conn,
                instance["id"],
                {
                    "holder_id": "player",
                    "knowledge_type": "confirmed",
                    "content": "霜木镇的商会正在压低铁器收购价。",
                    "source_event_id": event_ids[0],
                    "channel": "notice",
                    "learned_minute": 0,
                },
            )

        response = client.get(f"/api/v1/adventures/{run['id']}/world-news")
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["items"], "玩家应看到自己有渠道得知的消息"
        assert all(item.get("knowledge_type") for item in page["items"])

        with db(tmp_path / "chain.db") as conn:
            saved = checkpoints.create_checkpoint(conn, run["id"], "北境分支点", "req-1", run["revision"])
            conn.commit()
            branch = checkpoints.restore(conn, saved["id"], "另一条北境线", "req-2")
            conn.commit()
            new_instance = core.for_adventure(conn, branch["id"])
            branch_events = events.list_events(conn, new_instance["id"], limit=10)["items"]
            branch_news = knowledge.list_known(conn, new_instance["id"], "player", limit=10)["items"]

        assert branch["id"] != run["id"]
        assert new_instance["id"] != instance["id"]
        assert len(branch_events) == len(event_ids)
        assert branch_news and branch_news[0]["content"].startswith("霜木镇")
        assert branch["character"] == run["character"]
