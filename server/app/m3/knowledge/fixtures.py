"""M3.3 人物知识与传播独立测试及集成验收 Fake Fixtures（北境小镇多跳贸易场景 + Wave 2 脉动推演适配场景）。"""

from __future__ import annotations

import sqlite3
from typing import Any

from .schema import ensure_knowledge_schema
from .service import (
    apply_proposal,
    build_narration_knowledge_context,
    list_known,
    project_player_news,
    read_knowledge_snapshot,
    record_transmission,
    register_event_stub,
    register_holder,
    validate_proposal,
)


def create_instance_fixture(
    conn: sqlite3.Connection,
    instance_id: str = "inst_north_town",
    *,
    world_id: str = "world_north",
    adventure_id: str | None = "adv_north_01",
    world_minute: int = 60,
    revision: int = 1,
) -> dict[str, Any]:
    """初始化独立测试用的世界实例根记录（幂等）。"""
    ensure_knowledge_schema(conn)
    conn.execute(
        """
        INSERT INTO world_instances(id, world_id, adventure_id, world_minute, revision)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            world_id = excluded.world_id,
            adventure_id = excluded.adventure_id,
            world_minute = excluded.world_minute,
            revision = excluded.revision
        """,
        (instance_id, world_id, adventure_id, world_minute, revision),
    )
    return {
        "instance_id": instance_id,
        "world_id": world_id,
        "adventure_id": adventure_id,
        "world_minute": world_minute,
        "revision": revision,
    }


def seed_north_town_multi_hop_fixture(
    conn: sqlite3.Connection,
    instance_id: str = "inst_north_town",
    *,
    world_minute: int = 60,
    include_player_conversation: bool = False,
) -> dict[str, Any]:
    """构建《用户总纲》§十七北境小镇贸易与商队多跳传播 Fixture：

    1. 权威事件：
       - `evt_tariff_01`（第 10 分钟）：邻国提高北境生铁进口关税（真实标题对玩家隐藏）
       - `evt_caravan_02`（第 20 分钟）：灰羽商队改走东谷山道，预计延误三日
       - `evt_royal_secret_03`（第 30 分钟）：王都枢密院密议边防换防（对小镇 NPC 与玩家完全隐藏）
    2. 人物登记：
       - `npc:caravan_leader`（灰羽商队首领林策）
       - `npc:innkeeper`（北境酒馆掌柜老周）
       - `npc:gate_guard`（城门卫兵阿石）
       - `npc:blacksmith`（铁匠铺学徒小满）
       - `player`（工坊主玩家）
    3. 同事件不同认知 & 多跳链路：
       - 跳 1（observed，第 20 分钟）：商队首领林策亲历 `evt_caravan_02`（confirmed，可信度 1.0）
       - 分歧认知（rumor，第 25 分钟）：城门卫兵阿石对同一事件 `evt_caravan_02` 误信为“灰羽商队在雪原遭遇山贼全军覆没”（mistaken，可信度 0.7）
       - 跳 2（conversation，第 35 分钟）：商队首领林策向酒馆掌柜老周当面交谈告知商队改道详情（confirmed，可信度 0.85）
       - 跳 3（可选，conversation，第 50 分钟）：玩家与酒馆掌柜老周交谈后才获知商队改道消息；在此之前玩家完全看不到 `evt_caravan_02` 与 `evt_royal_secret_03`！
    """
    create_instance_fixture(conn, instance_id, world_minute=world_minute)

    register_event_stub(
        conn,
        instance_id,
        "evt_tariff_01",
        title="【隐藏权威标题】邻国霜冕公国颁布生铁重税令",
        description="霜冕公国自今日起对出境生铁加征三成关税。",
        created_minute=10,
        updated_minute=10,
        location="霜冕边境",
        scope="regional",
        next_update_minute=180,
    )
    register_event_stub(
        conn,
        instance_id,
        "evt_caravan_02",
        title="【隐藏权威标题】灰羽商队绕行东谷山道",
        description="灰羽商队为避开重税关卡改走东谷崎岖山道，生铁抵达北境小镇将推迟三日。",
        created_minute=20,
        updated_minute=20,
        location="东谷山道",
        scope="regional",
        participants=["npc:caravan_leader"],
        next_update_minute=240,
    )
    register_event_stub(
        conn,
        instance_id,
        "evt_royal_secret_03",
        title="【绝密隐藏标题】王都枢密院密议北境换防",
        description="远方王都正在秘密调动第三骑士团，小镇无人知晓。",
        created_minute=30,
        updated_minute=30,
        location="王都",
        scope="national",
        next_update_minute=720,
    )

    register_holder(conn, instance_id, "npc:caravan_leader", name="商队首领林策", role="merchant", location="东谷山道")
    register_holder(conn, instance_id, "npc:innkeeper", name="酒馆掌柜老周", role="innkeeper", location="北境酒馆")
    register_holder(conn, instance_id, "npc:gate_guard", name="城门卫兵阿石", role="guard", location="北境镇门")
    register_holder(conn, instance_id, "npc:blacksmith", name="铁匠学徒小满", role="apprentice", location="铁匠铺")
    register_holder(conn, instance_id, "player", name="工坊主（玩家）", role="player", location="北境工坊")

    initial_hops = [
        {
            "fact_id": "fact_caravan_observed",
            "holder_id": "npc:caravan_leader",
            "knowledge_type": "confirmed",
            "title": "商队改走东谷山道",
            "content": "因邻国关卡加征生铁关税，我带灰羽商队改走东谷山道，预计晚三日到北境小镇。",
            "source_event_id": "evt_caravan_02",
            "channel": "observed",
            "learned_minute": 20,
            "confidence": 1.0,
        },
        {
            "fact_id": "fact_guard_mistaken",
            "holder_id": "npc:gate_guard",
            "knowledge_type": "mistaken",
            "title": "商队疑似在雪原遇袭覆没",
            "content": "灰羽商队按期未过镇门，定是在北境雪原遭了山贼截杀，怕是全军覆没了。",
            "source_event_id": "evt_caravan_02",
            "channel": "rumor",
            "learned_minute": 25,
            "confidence": 0.7,
        },
        {
            "fact_id": "fact_innkeeper_from_caravan",
            "holder_id": "npc:innkeeper",
            "knowledge_type": "confirmed",
            "title": "灰羽商队捎信将晚三日抵镇",
            "content": "商队首领林策托先遣伙计带话，因邻国加税改走东谷山道，这批生铁要晚三天才能运到。",
            "source_event_id": "evt_caravan_02",
            "source_person_id": "npc:caravan_leader",
            "source_fact_id": "fact_caravan_observed",
            "channel": "conversation",
            "learned_minute": 35,
            "confidence": 0.85,
        },
    ]
    record_transmission(conn, instance_id, initial_hops)

    if include_player_conversation:
        advance_player_conversation_with_innkeeper(conn, instance_id, learned_minute=50)

    return {
        "instance_id": instance_id,
        "events": ["evt_tariff_01", "evt_caravan_02", "evt_royal_secret_03"],
        "player_news": project_player_news(conn, instance_id),
    }


def advance_player_conversation_with_innkeeper(
    conn: sqlite3.Connection,
    instance_id: str = "inst_north_town",
    *,
    learned_minute: int = 50,
) -> dict[str, Any]:
    """执行多跳传播的最后一跳：玩家与酒馆掌柜老周交谈，获知商队改道与生铁延期。"""
    res = record_transmission(
        conn,
        instance_id,
        {
            "fact_id": "fact_player_from_innkeeper",
            "holder_id": "player",
            "knowledge_type": "confirmed",
            "title": "掌柜告知灰羽商队改道东谷",
            "content": "酒馆掌柜老周告诉你，灰羽商队首领林策捎来消息：因邻国加征关税，商队改走东谷山道，生铁原料将推迟三天运抵小镇。",
            "source_event_id": "evt_caravan_02",
            "source_person_id": "npc:innkeeper",
            "source_fact_id": "fact_innkeeper_from_caravan",
            "channel": "conversation",
            "learned_minute": learned_minute,
            "confidence": 0.85,
        },
    )
    return res[0]


def run_wave2_multi_hop_pulse_fixture(
    conn: sqlite3.Connection,
    instance_id: str = "inst_north_wave2",
) -> dict[str, Any]:
    """M3 Wave 2 端到端多跳传播验收 Fixture（覆盖「商队首领先得知改道 → 店主转述 → 玩家才看见」）：

    通过 `read_knowledge_snapshot` -> `validate_proposal`（事务外纯校验）-> `apply_proposal`（短事务原子提交）
    完整跑通三阶段演进，并同步返回每一步的玩家消息 UI 投影与叙事上下文数据块，证明零越界泄漏。
    """
    create_instance_fixture(conn, instance_id, world_minute=25, revision=1)

    register_event_stub(
        conn,
        instance_id,
        "evt_tariff_01",
        title="【隐藏权威标题】邻国霜冕公国颁布生铁重税令",
        description="霜冕公国自今日起对出境生铁加征三成关税。",
        created_minute=10,
        updated_minute=10,
        location="霜冕边境",
        scope="regional",
        next_update_minute=180,
    )
    register_event_stub(
        conn,
        instance_id,
        "evt_caravan_02",
        title="【隐藏权威标题】灰羽商队绕行东谷山道",
        description="灰羽商队为避开重税关卡改走东谷崎岖山道，生铁抵达北境小镇将推迟三日。",
        created_minute=20,
        updated_minute=20,
        location="东谷山道",
        scope="regional",
        participants=["npc:caravan_leader"],
        next_update_minute=240,
    )
    register_event_stub(
        conn,
        instance_id,
        "evt_royal_secret_03",
        title="【绝密隐藏标题】王都枢密院密议北境换防",
        description="远方王都正在秘密调动第三骑士团，小镇无人知晓。",
        created_minute=22,
        updated_minute=22,
        location="王都",
        scope="national",
        next_update_minute=720,
    )

    register_holder(conn, instance_id, "npc:caravan_leader", name="商队首领林策", role="merchant", location="东谷山道")
    register_holder(conn, instance_id, "npc:innkeeper", name="酒馆掌柜老周", role="innkeeper", location="北境酒馆")
    register_holder(conn, instance_id, "npc:gate_guard", name="城门卫兵阿石", role="guard", location="北境镇门")
    register_holder(conn, instance_id, "player", name="工坊主（玩家）", role="player", location="北境工坊")

    # 阶段 1（第 25 分钟，版本 1）：商队首领亲历改道（confirmed），卫兵产生误信（mistaken）
    job_step1 = {
        "job_id": "job_pulse_step1",
        "instance_id": instance_id,
        "kind": "advance",
        "base_world_revision": 1,
        "target_world_minute": 25,
    }
    snap_step1 = read_knowledge_snapshot(
        conn, instance_id, job_step1, allowed_event_ids=["evt_caravan_02"]
    )
    validated_step1 = validate_proposal(
        {
            "instance_id": instance_id,
            "mode": "advance",
            "world_minute": 25,
            "world_revision": 1,
            "transmissions": [
                {
                    "fact_id": "fact_w2_leader_observed",
                    "holder_id": "npc:caravan_leader",
                    "knowledge_type": "confirmed",
                    "title": "商队改走东谷山道",
                    "content": "因邻国关卡加征生铁关税，我带灰羽商队改走东谷山道，预计晚三日到北境小镇。",
                    "source_event_id": "evt_caravan_02",
                    "channel": "observed",
                    "learned_minute": 20,
                    "confidence": 1.0,
                },
                {
                    "fact_id": "fact_w2_guard_mistaken",
                    "holder_id": "npc:gate_guard",
                    "knowledge_type": "mistaken",
                    "title": "商队疑似在雪原遇袭覆没",
                    "content": "灰羽商队按期未过镇门，定是在北境雪原遭了山贼截杀。",
                    "source_event_id": "evt_caravan_02",
                    "channel": "rumor",
                    "learned_minute": 25,
                    "confidence": 0.7,
                },
            ],
        },
        snap_step1,
    )
    apply_proposal(conn, instance_id, validated_step1, job_step1, allowed_event_ids=["evt_caravan_02"])
    step1_leader_known = list_known(conn, instance_id, "npc:caravan_leader")
    step1_guard_known = list_known(conn, instance_id, "npc:gate_guard")
    step1_innkeeper_known = list_known(conn, instance_id, "npc:innkeeper")
    step1_player_news = project_player_news(conn, instance_id)
    step1_narration_ctx = build_narration_knowledge_context(conn, instance_id, action="检查工坊生铁库存")

    # 阶段 2（第 35 分钟，版本 2）：商队首领向酒馆店主老周转述改道；玩家仍不可见
    conn.execute("UPDATE world_instances SET world_minute = 35, revision = 2 WHERE id = ?", (instance_id,))
    job_step2 = {
        "job_id": "job_pulse_step2",
        "instance_id": instance_id,
        "kind": "advance",
        "base_world_revision": 2,
        "target_world_minute": 35,
    }
    snap_step2 = read_knowledge_snapshot(
        conn, instance_id, job_step2, allowed_event_ids=["evt_caravan_02"]
    )
    validated_step2 = validate_proposal(
        {
            "instance_id": instance_id,
            "mode": "advance",
            "world_minute": 35,
            "world_revision": 2,
            "transmissions": [
                {
                    "fact_id": "fact_w2_innkeeper_relay",
                    "holder_id": "npc:innkeeper",
                    "knowledge_type": "confirmed",
                    "title": "灰羽商队捎信将晚三日抵镇",
                    "content": "商队首领林策托先遣伙计带话，因邻国加税改走东谷山道，生铁要晚三天运到。",
                    "source_event_id": "evt_caravan_02",
                    "source_person_id": "npc:caravan_leader",
                    "channel": "conversation",
                    "learned_minute": 35,
                    "confidence": 0.85,
                }
            ],
        },
        snap_step2,
    )
    apply_proposal(conn, instance_id, validated_step2, job_step2, allowed_event_ids=["evt_caravan_02"])
    step2_innkeeper_known = list_known(conn, instance_id, "npc:innkeeper")
    step2_player_news = project_player_news(conn, instance_id)
    step2_narration_ctx = build_narration_knowledge_context(conn, instance_id, action="在街上闲逛")

    # 阶段 3（第 50 分钟，版本 3）：酒馆店主老周向玩家转述，玩家此时才看见消息
    conn.execute("UPDATE world_instances SET world_minute = 50, revision = 3 WHERE id = ?", (instance_id,))
    job_step3 = {
        "job_id": "job_pulse_step3",
        "instance_id": instance_id,
        "kind": "advance",
        "base_world_revision": 3,
        "target_world_minute": 50,
    }
    snap_step3 = read_knowledge_snapshot(
        conn, instance_id, job_step3, allowed_event_ids=["evt_caravan_02"]
    )
    validated_step3 = validate_proposal(
        {
            "instance_id": instance_id,
            "mode": "advance",
            "world_minute": 50,
            "world_revision": 3,
            "transmissions": [
                {
                    "fact_id": "fact_w2_player_from_innkeeper",
                    "holder_id": "player",
                    "knowledge_type": "confirmed",
                    "title": "掌柜告知灰羽商队改道东谷",
                    "content": "酒馆掌柜老周告诉你，灰羽商队首领林策捎来消息：因邻国加征关税，商队改走东谷山道，生铁原料将推迟三天运抵小镇。",
                    "source_event_id": "evt_caravan_02",
                    "source_person_id": "npc:innkeeper",
                    "channel": "conversation",
                    "learned_minute": 50,
                    "confidence": 0.85,
                }
            ],
        },
        snap_step3,
    )
    apply_proposal(conn, instance_id, validated_step3, job_step3, allowed_event_ids=["evt_caravan_02"])
    step3_player_news = project_player_news(conn, instance_id)
    step3_narration_ctx = build_narration_knowledge_context(
        conn, instance_id, action="向酒馆掌柜打听生铁商队", location="北境酒馆"
    )

    return {
        "instance_id": instance_id,
        "step1": {
            "leader_known": step1_leader_known,
            "guard_known": step1_guard_known,
            "innkeeper_known": step1_innkeeper_known,
            "player_news": step1_player_news,
            "narration_context": step1_narration_ctx,
        },
        "step2": {
            "innkeeper_known": step2_innkeeper_known,
            "player_news": step2_player_news,
            "narration_context": step2_narration_ctx,
        },
        "step3": {
            "player_news": step3_player_news,
            "narration_context": step3_narration_ctx,
        },
    }
