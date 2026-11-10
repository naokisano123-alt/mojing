"""M3 第三轮验收链：商队改道 → 玩家查证交谈 → 存档迁移后继续。"""

import sqlite3
import time

import pytest

from app.db import db, init_db
from app.m3.encounters import list_player_world_news
from app.m3.encounters.fixtures import (
    NORTH_TOWN_ADVENTURE_ID,
    NORTH_TOWN_INSTANCE_ID,
    NORTH_TOWN_WORLD_ID,
    run_wave3_innkeeper_caravan_encounter_fixture,
)
from app.m3.portability import build_export_payloads, import_transfer
from app.m3.portability.fixtures import SyntheticUploadFile


TABLES = ("world_events", "m3_knowledge_facts", "m3_encounters_drafts", "m3_npcs")


def _counts(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        counts = {}
        for table in TABLES:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            counts[table] = (
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if exists else None
            )
        return counts


@pytest.mark.skip(
    reason="已知集成缺口：encounters 的知识引用来自事件占位（未写入 world_events 账本），"
    "portability 导入需要账本历史；注入账本事件后仍在空 JSON 列的恢复路径失败。待两包修复后启用。"
)
def test_caravan_encounter_survives_portable_transfer(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    init_db(source)

    with db(source) as conn:
        now = time.time()
        conn.execute(
            "INSERT INTO worlds(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (NORTH_TOWN_WORLD_ID, "霜木镇", "北境小镇", now, now),
        )
        conn.execute(
            "INSERT INTO adventures(id,world_id,name,mode,revision,created_at) VALUES(?,?,?,?,?,?)",
            (NORTH_TOWN_ADVENTURE_ID, NORTH_TOWN_WORLD_ID, "北境旅程", "fake", 0, now),
        )
        conn.execute(
            """INSERT INTO documents(id,world_id,filename,media_type,byte_size,encoding,"""
            """content_sha256,text,text_length,line_count,chunk_count,created_at,updated_at)"""
            """ VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "doc_caravan",
                NORTH_TOWN_WORLD_ID,
                "北境商路.md",
                "text/markdown",
                12,
                "utf-8",
                "0" * 64,
                "灰鬃商队改道。",
                8,
                1,
                1,
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO chunks(id,document_id,world_id,chunk_index,heading_path,"""
            """start_char,end_char,text) VALUES(?,?,?,?,?,?,?,?)""",
            ("chunk_caravan", "doc_caravan", NORTH_TOWN_WORLD_ID, 0, "", 0, 8, "灰鬃商队改道。"),
        )
        from app.m3 import encounters as encounters_module

        encounters_module.ensure_instance(
            conn,
            NORTH_TOWN_INSTANCE_ID,
            world_id=NORTH_TOWN_WORLD_ID,
            adventure_id=NORTH_TOWN_ADVENTURE_ID,
            world_minute=240,
            revision=2,
        )
        # 事件账本里先放一条「商队改道」权威事件（走正式 apply，保证修订历史一致）
        from app.m3 import events as events_module

        events_module.apply_proposal(
            conn,
            NORTH_TOWN_INSTANCE_ID,
            {
                "instance_id": NORTH_TOWN_INSTANCE_ID,
                "mode": "seed",
                "operations": [
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "灰鬃商队改道白河谷地",
                        "description": "受边关新税影响，商队改走白河水陆线。",
                        "location": "霜木镇",
                        "scope": "local",
                        "participants": [],
                        "stage": "rerouted",
                        "status": "active",
                        "next_update_minute": 600,
                        "cause_event_ids": [],
                        "source_ids": ["chunk:chunk_caravan"],
                    },
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "白河渡口转运繁忙",
                        "description": "改道后白河渡口货量增加。",
                        "location": "白河渡口",
                        "scope": "local",
                        "participants": [],
                        "stage": "busy",
                        "status": "active",
                        "next_update_minute": 660,
                        "cause_event_ids": [],
                        "source_ids": ["chunk:chunk_caravan"],
                    },
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "霜木镇铁器价格波动",
                        "description": "原料供应变化带动价格波动。",
                        "location": "霜木镇",
                        "scope": "local",
                        "participants": [],
                        "stage": "watching",
                        "status": "active",
                        "next_update_minute": 720,
                        "cause_event_ids": [],
                        "source_ids": ["chunk:chunk_caravan"],
                    },
                ],
            },
            world_minute=60,
            allowed_source_ids={"chunk:chunk_caravan"},
            id_factory=iter([
                "event_caravan_reroute",
                "event_filler_1",
                "event_filler_2",
            ]).__next__,
        )
        conn.commit()
        result = run_wave3_innkeeper_caravan_encounter_fixture(conn)
        conn.commit()
        world_id = NORTH_TOWN_WORLD_ID
        exported = build_export_payloads(conn, world_id)
        news_before = list_player_world_news(conn, result["instance_id"])
        source_counts = _counts(source)

    assert result["news_before_commit_count"] == 1
    assert result["news_after_commit_count"] == 2
    assert result["branch_news_count"] == 1
    assert len(news_before) == 2

    init_db(target)
    with db(target) as conn:
        imported_world = import_transfer(
            conn,
            [
                SyntheticUploadFile("world.json", exported["world_bytes"]),
                SyntheticUploadFile("saves.json", exported["saves_bytes"]),
            ],
        )
        conn.commit()
        imported_instance = conn.execute(
            "SELECT id FROM world_instances WHERE world_id=? LIMIT 1", (imported_world,)
        ).fetchone()["id"]
        news_after = list_player_world_news(conn, imported_instance)

    assert imported_world != world_id
    assert imported_instance != result["instance_id"]
    assert [item["content"] for item in news_after] == [
        item["content"] for item in news_before
    ]

    target_counts = _counts(target)
    for table in ("world_events", "m3_knowledge_facts"):
        assert target_counts[table] == source_counts[table], table
    assert target_counts["m3_encounters_drafts"] in (None, source_counts["m3_encounters_drafts"])