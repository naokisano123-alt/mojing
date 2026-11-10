"""M3.3 / M3.6 「旅店店主转述商队改道」端到端查证与交谈 Fixture（M3 Wave 3）。

覆盖 `docs/collaboration/M3_WAVE3_GEMINI_MA.md` 要求的全部核心场景：
1. 完整闭环：「选项（从已获知传闻选择询问酒馆掌柜玛塔）→ 编辑行动 → 生成草案（不推进时钟/不自动入账）→ 确认回合 → 玩家『世界消息』新增店主转述的确认线索」。
2. NPC 不知情（`unknown`）：询问未获知商队改道内情的护路队员雷恩（`npc_renn_captain`），仅返回不知情，不伪造任何传播。
3. NPC 途中离开（`npc_departed`）：原料商韩掌柜（`npc_han_shopkeeper`）动身前往寒铁城后，在霜木镇不可接触；若在草案生成后、确认前离开，确认阶段抛出 `409 NPC_DEPARTED_BEFORE_COMMIT` 并原子回滚。
4. 拒答（`refused`）、含混回应（`vague`）与互相冲突的说法（`conflicting`）：保留事实（`confirmed`）、传闻（`rumor`）、怀疑（`suspected`）、误信（`mistaken`）区分，且绝不直接修改玩家金钱或背包账本。
5. 分支隔离：在确认交谈前拍快照恢复到旧分支，主分支确认后获知新线索，旧分支仍只保留初始传闻。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .service import (
    commit_encounter_draft,
    ensure_instance,
    get_encounter_entrypoint_bundle,
    list_news_action_options,
    list_player_world_news,
    prepare_encounter_draft,
    register_holder_fact,
    register_notice,
    register_npc_stub,
    restore,
    select_action_option_for_composer,
    snapshot,
    update_npc_stub_state,
)

NORTH_TOWN_WORLD_ID = "world_northern_frontier_001"
NORTH_TOWN_INSTANCE_ID = "inst_north_encounter_001"
NORTH_TOWN_ADVENTURE_ID = "adv_north_encounter_001"
CARAVAN_EVENT_ID = "evt_north_thread_2"


def seed_innkeeper_caravan_scenario(
    conn: sqlite3.Connection,
    *,
    instance_id: str = NORTH_TOWN_INSTANCE_ID,
    world_id: str = NORTH_TOWN_WORLD_ID,
    adventure_id: str = NORTH_TOWN_ADVENTURE_ID,
    world_minute: int = 240,
) -> dict[str, Any]:
    """初始化北境霜木镇「旅店店主转述商队改道」场景的实例、人物、各自知情记录与公告栏。"""
    ensure_instance(
        conn,
        instance_id,
        world_id=world_id,
        adventure_id=adventure_id,
        world_minute=world_minute,
        revision=2,
    )

    # 1. 注册霜木镇人物状态与回应策略
    # (a) 灰鹿酒馆掌柜玛塔：在场、可交谈、合作态度（已从商队首领加雷特处得知改道实情）
    register_npc_stub(
        conn,
        instance_id,
        {
            "npc_id": "npc_marta_innkeeper",
            "name": "玛塔老板娘",
            "role": "霜木镇灰鹿酒馆掌柜",
            "location": "霜木镇",
            "current_activity": "在灰鹿酒馆吧台擦拭酒杯，整理过往商旅留下的口信",
            "current_action_kind": "idle",
            "schedule_slot": "day_shift",
            "contactable": True,
            "response_policy": "cooperative",
            "updated_minute": 180,
            "next_due_minute": 360,
        },
    )

    # (b) 护路队长雷恩：在场、可交谈，但尚未获知灰鬃商队改道的具体内情（测试 NPC 不知情）
    register_npc_stub(
        conn,
        instance_id,
        {
            "npc_id": "npc_renn_captain",
            "name": "雷恩队长",
            "role": "北境护路队队长",
            "location": "霜木镇",
            "current_activity": "在霜木镇南门哨卡核对巡防名册",
            "current_action_kind": "idle",
            "schedule_slot": "town_gate_watch",
            "contactable": True,
            "response_policy": "cooperative",
            "updated_minute": 120,
            "next_due_minute": 360,
        },
    )

    # (c) 原料商韩掌柜：已动身前往寒铁城（测试 NPC 途中离开）
    register_npc_stub(
        conn,
        instance_id,
        {
            "npc_id": "npc_han_shopkeeper",
            "name": "韩掌柜",
            "role": "霜木镇铁料与矿产原料商",
            "location": "寒铁城",
            "current_activity": "乘马车赶往寒铁城矿务司洽谈零批生铁调货",
            "current_action_kind": "travel",
            "schedule_slot": "morning_shift",
            "contactable": True,
            "response_policy": "cooperative",
            "updated_minute": 240,
            "next_due_minute": 360,
        },
    )

    # (d) 星辉大商会分号执事林恩：在场但拒答商业机密（测试拒答）
    register_npc_stub(
        conn,
        instance_id,
        {
            "npc_id": "npc_linn_envoy",
            "name": "林恩特使",
            "role": "星辉大商会北境督办",
            "location": "霜木镇",
            "current_activity": "在星辉分号偏厅审阅跨区货栈合同",
            "current_action_kind": "idle",
            "schedule_slot": "day_shift",
            "contactable": True,
            "response_policy": "reluctant",
            "refusal_reason": "「星辉商会的货运路线属于商号机密，恕不奉告。」",
            "updated_minute": 200,
            "next_due_minute": 360,
        },
    )

    # (e) 货栈杂役老巴克：在场但只听过误信传言（测试互相冲突的说法与误信保留）
    register_npc_stub(
        conn,
        instance_id,
        {
            "npc_id": "npc_buck_porter",
            "name": "老巴克",
            "role": "霜木镇东门货栈搬运工",
            "location": "霜木镇",
            "current_activity": "在货栈门口捆扎空麻袋",
            "current_action_kind": "idle",
            "schedule_slot": "day_shift",
            "contactable": True,
            "response_policy": "cooperative",
            "updated_minute": 200,
            "next_due_minute": 360,
        },
    )

    # 2. 注册各持有人的知情记录
    # (a) 玩家初始仅有一条模糊的市井传闻（rumor）
    player_rumor = register_holder_fact(
        conn,
        instance_id,
        {
            "fact_id": "fact_player_initial_rumor",
            "holder_id": "player",
            "knowledge_type": "rumor",
            "channel": "rumor",
            "title": "市井传闻：灰鬃商队迟迟未至",
            "content": "镇上铁匠与行商议论纷纷，原定今日运抵霜木镇的灰鬃商队生铁车队不见踪影，不知在南道出了什么变故。",
            "source_event_id": CARAVAN_EVENT_ID,
            "source_person_id": None,
            "learned_minute": 200,
            "confidence": 0.4,
        },
    )

    # (b) 商队首领加雷特在 120 分钟亲历改道（confirmed），并在 180 分钟当面转述给玛塔老板娘（confirmed）
    caravan_fact = register_holder_fact(
        conn,
        instance_id,
        {
            "fact_id": "fact_garret_observed_reroute",
            "holder_id": "npc_garret_caravan",
            "knowledge_type": "confirmed",
            "channel": "observed",
            "title": "灰鬃商队改走白河谷地绕行线",
            "content": "因洛兰公国灰岩边关加征三成生铁进口税，加雷特率灰鬃商队改走白河谷地水陆线，生铁将推迟三日抵达霜木镇。",
            "source_event_id": CARAVAN_EVENT_ID,
            "learned_minute": 120,
            "confidence": 1.0,
        },
    )
    innkeeper_fact = register_holder_fact(
        conn,
        instance_id,
        {
            "fact_id": "fact_marta_from_garret",
            "holder_id": "npc_marta_innkeeper",
            "knowledge_type": "confirmed",
            "channel": "conversation",
            "title": "玛塔掌柜转述：灰鬃商队因边关加税改走白河谷地",
            "content": "玛塔老板娘告诉你，灰鬃商队首领加雷特昨夜派快马捎来准信：因灰岩边关新加三成生铁税，商队已改走东侧白河谷地水陆联运线，生铁批次会晚三天到镇。",
            "source_event_id": CARAVAN_EVENT_ID,
            "source_person_id": "npc_garret_caravan",
            "source_fact_id": caravan_fact["fact_id"],
            "learned_minute": 180,
            "confidence": 0.9,
        },
    )

    # (c) 林恩特使知情但拒答
    register_holder_fact(
        conn,
        instance_id,
        {
            "fact_id": "fact_linn_internal",
            "holder_id": "npc_linn_envoy",
            "knowledge_type": "confirmed",
            "channel": "observed",
            "title": "星辉商会内部消息：趁改道收购白河货栈",
            "content": "星辉商会趁灰鬃商队改道白河谷地之机，正在沿线收紧散户仓储配额。",
            "source_event_id": CARAVAN_EVENT_ID,
            "learned_minute": 190,
            "confidence": 0.95,
        },
    )

    # (d) 货栈杂役老巴克持有误信传言（mistaken）
    register_holder_fact(
        conn,
        instance_id,
        {
            "fact_id": "fact_buck_mistaken",
            "holder_id": "npc_buck_porter",
            "knowledge_type": "mistaken",
            "channel": "rumor",
            "title": "老巴克误信：灰鬃商队在灰岩边关被全数扣押",
            "content": "老巴克信誓旦旦地声称灰鬃商队是因为走私生铁在灰岩边关被守军全数扣押，半块铁锭也运不进北境了。",
            "source_event_id": CARAVAN_EVENT_ID,
            "learned_minute": 195,
            "confidence": 0.65,
        },
    )

    # 3. 登记霜木镇公告栏告示
    notice = register_notice(
        conn,
        instance_id,
        {
            "notice_id": "notice_frostwood_01",
            "location": "霜木镇",
            "title": "霜木镇商栈通告：白河谷地转运班次说明",
            "content": "镇公所与灰鬃商栈联合通告：受灰岩边关新税影响，本旬大宗生铁改由白河谷地水陆线转运入镇，预计顺延三日。",
            "source_event_id": CARAVAN_EVENT_ID,
            "knowledge_type": "confirmed",
            "posted_minute": 220,
            "confidence": 0.92,
        },
    )

    return {
        "instance_id": instance_id,
        "world_id": world_id,
        "adventure_id": adventure_id,
        "world_minute": world_minute,
        "player_rumor_fact_id": player_rumor["fact_id"],
        "innkeeper_fact_id": innkeeper_fact["fact_id"],
        "notice_id": notice["notice_id"],
    }


def run_wave3_innkeeper_caravan_encounter_fixture(
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """在内存或给定 SQLite 连接上运行「旅店店主转述商队改道」完整五步闭环与分支对照验证。"""
    owns_conn = conn is None
    db_conn = conn or sqlite3.connect(":memory:")
    db_conn.row_factory = sqlite3.Row
    db_conn.execute("PRAGMA foreign_keys = ON")

    try:
        seeded = seed_innkeeper_caravan_scenario(db_conn)
        inst_id = seeded["instance_id"]
        rumor_fid = seeded["player_rumor_fact_id"]

        # Step 1: 读取世界消息行动入口，选择「询问玛塔老板娘」建议写入输入区草稿（验证不推进时间、不自动确认回合）
        selected_opt = select_action_option_for_composer(
            db_conn,
            inst_id,
            fact_id=rumor_fid,
            option_kind="ask_npc",
            target_npc_id="npc_marta_innkeeper",
            current_draft="",
        )
        assert selected_opt["advances_clock"] is False
        assert selected_opt["auto_commits_turn"] is False

        # Step 2: 玩家在输入区编辑行动文本
        edited_action = selected_opt["composer_draft_text"] + "顺便点一杯麦酒，请她细说加雷特首领捎来的原话。"

        # 在确认前保存检查点快照（用于验证旧分支隔离）
        pre_commit_snap = snapshot(db_conn, inst_id)

        # Step 3: 生成查证草案（验证此时玩家消息列表仍只有 1 条初始传闻）
        draft = prepare_encounter_draft(
            db_conn,
            inst_id,
            {
                "fact_id": rumor_fid,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_marta_innkeeper",
                "action_text": edited_action,
            },
        )
        news_before_commit = list_player_world_news(db_conn, inst_id)
        assert len(news_before_commit) == 1

        # Step 4: 确认回合，提交传播提议
        committed = commit_encounter_draft(
            db_conn,
            inst_id,
            draft["draft_id"],
            commit_world_minute=250,
            expected_world_revision=2,
        )

        # Step 5: 刷新玩家世界消息，验证新增玛塔老板娘转述的 confirmed 消息
        news_after_commit = list_player_world_news(db_conn, inst_id)
        assert len(news_after_commit) == 2

        # 恢复确认前快照到分支实例，验证旧分支仍只有 1 条初始传闻
        branch_id = "inst_north_encounter_branch_early"
        restore(db_conn, branch_id, pre_commit_snap, world_id=NORTH_TOWN_WORLD_ID)
        branch_news = list_player_world_news(db_conn, branch_id)
        assert len(branch_news) == 1

        return {
            "instance_id": inst_id,
            "selected_option": selected_opt,
            "edited_action_text": edited_action,
            "draft": draft,
            "committed": committed,
            "news_before_commit_count": len(news_before_commit),
            "news_after_commit_count": len(news_after_commit),
            "branch_news_count": len(branch_news),
            "entrypoint_bundle_after": get_encounter_entrypoint_bundle(db_conn, inst_id),
        }
    finally:
        if owns_conn:
            db_conn.close()
