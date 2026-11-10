"""M3.4 北境贸易（Northern Trade）NPC 自主行动、模拟深度与多步 Mock 推演 Fixture（M3 Wave 3）。

契约要求（`docs/collaboration/M3_WAVE3_GEMINI_MARCIN.md`）：
1. 用北境贸易背景覆盖：
   - 两个 NPC 对同一局势（灰鬃商队改道 / 生铁延误 `evt_caravan_reroute_02`）采取不同安排：
     `npc_han_shopkeeper`（韩掌柜）采取 `travel`（前往白河城洽谈水路生铁），
     `npc_hawk_blacksmith`（霍克铁匠）采取 `shift_change`（留守霜木镇切换为单炉节炭班）与 `task_progress`（废铁重熔任务）；
   - 近景（`foreground`）、关联（`associated`）、背景（`background`）三档模拟深度动态升降档与可测量预算；
   - 四种动作类型全覆盖（`travel`、`conversation_plan`、`shift_change`、`task_progress`）；
   - 无到期不变更（第 90 分钟无到期 NPC 时跳过）；
   - 支持 Fake 与 `httpx.MockTransport` 端到端重放。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any

import httpx

from .ai import OpenAINpcModelClient
from .service import (
    apply_proposal,
    check_npc_accessibility,
    ensure_instance_exists,
    list_contactable_npcs,
    list_npc_action_log,
    list_npc_tasks,
    list_npcs,
    read_snapshot,
    register_event_stub,
    register_knowledge_stub,
    snapshot,
)
from .types import DepthBudget
from .validator import validate_proposal

NORTHERN_TRADE_WORLD_ID = "world_northern_frontier_001"
NORTHERN_TRADE_INSTANCE_ID = "inst_northern_frontier_001"

NORTHERN_TRADE_ALLOWED_SOURCE_IDS: tuple[str, ...] = (
    "chunk:north_geography_01",
    "chunk:north_trade_routes_02",
    "entry:faction_starlight_guild_01",
    "entry:diplomacy_loran_tariff_02",
    "entry:conflict_iron_vein_03",
)

NORTHERN_TRADE_INITIAL_NPCS: tuple[dict[str, Any], ...] = (
    {
        "op": "seed_npc",
        "npc_id": "npc_han_shopkeeper",
        "name": "韩掌柜",
        "role": "霜木镇铁料与矿产原料商",
        "faction": "霜木镇独立商户联席",
        "location": "霜木镇",
        "home_location": "霜木镇",
        "action_kind": "task_progress",
        "task_id": "task_han_iron_inventory",
        "task_title": "盘点镇内生铁与精煤库存",
        "task_stage": "核对星辉商会报价与库房余量",
        "task_progress_percent": 30,
        "task_status": "active",
        "current_activity": "在霜木镇铁料铺盘点生铁库存并核对边关来信",
        "motivation": "边贸减税协定临近到期，需提前备足霜木镇工坊所需生铁",
        "goal": "保住独立原料铺货源，防止被星辉大商会吞并",
        "depth_tier": "foreground",
        "shift_name": "日间挂牌营业班",
        "availability": "available",
        "duration_minutes": 60,
        "next_due_minute": 60,
        "source_ids": ["chunk:north_geography_01", "entry:faction_starlight_guild_01"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_hawk_blacksmith",
        "name": "霍克铁匠",
        "role": "霜木镇老铁匠坊主",
        "faction": "霜木镇工匠行会",
        "location": "霜木镇",
        "home_location": "霜木镇",
        "action_kind": "shift_change",
        "from_shift": "晨间备料班",
        "to_shift": "常规双炉锻造班",
        "shift_name": "常规双炉锻造班",
        "current_activity": "开启双锻炉赶制北境护路队与镇民预定的铁器",
        "motivation": "护路队巡山装备交期将近，需趁焦炭充足全速锻打",
        "goal": "按期交付护路队锁甲与农具，维持铁匠坊声誉",
        "depth_tier": "foreground",
        "availability": "available",
        "duration_minutes": 60,
        "next_due_minute": 60,
        "source_ids": ["chunk:north_geography_01"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_marta_innkeeper",
        "name": "玛塔老板娘",
        "role": "霜木镇灰鹿酒馆掌柜",
        "faction": "霜木镇商户",
        "location": "霜木镇",
        "home_location": "霜木镇",
        "action_kind": "shift_change",
        "from_shift": "晨间清扫班",
        "to_shift": "午间迎客与消息打听班",
        "shift_name": "午间迎客与消息打听班",
        "current_activity": "在灰鹿酒馆大堂招待歇脚旅人并留意商道传闻",
        "motivation": "灰鬃商队今日应抵镇投宿，需提前备好热汤与客房",
        "goal": "经营灰鹿酒馆，收集北境商道第一手风声",
        "depth_tier": "foreground",
        "availability": "available",
        "duration_minutes": 60,
        "next_due_minute": 60,
        "source_ids": ["chunk:north_trade_routes_02"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_eilin_mayor",
        "name": "艾琳镇长",
        "role": "霜木镇执政官",
        "faction": "北境王国地方公署",
        "location": "霜木镇",
        "home_location": "霜木镇",
        "action_kind": "task_progress",
        "task_id": "task_mayor_winter_reserve",
        "task_title": "霜木镇冬储物资核算",
        "task_stage": "汇总各工坊燃料缺口",
        "task_progress_percent": 20,
        "task_status": "active",
        "current_activity": "在镇公署核算入冬煤炭与铁料储备表",
        "motivation": "确保霜木镇在边贸税率变动下不断绝冬储物资",
        "goal": "维护霜木镇民生与工商业稳定",
        "depth_tier": "foreground",
        "shift_name": "公署日间值守",
        "availability": "available",
        "duration_minutes": 180,
        "next_due_minute": 180,
        "source_ids": ["chunk:north_geography_01"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_renn_captain",
        "name": "雷恩队长",
        "role": "北境护路队队长",
        "faction": "北境护路队",
        "location": "霜木镇",
        "home_location": "霜木镇",
        "action_kind": "shift_change",
        "from_shift": "清晨操演班",
        "to_shift": "镇门与南口巡防班",
        "shift_name": "镇门与南口巡防班",
        "current_activity": "率护路队员在霜木镇南门核查过往货车",
        "motivation": "防范边关加税后走私客与山贼扰乱商道治安",
        "goal": "保障霜木镇至寒铁城及白河谷地的道路安全",
        "depth_tier": "foreground",
        "availability": "available",
        "duration_minutes": 180,
        "next_due_minute": 180,
        "source_ids": ["chunk:north_trade_routes_02"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_garret_caravan",
        "name": "加雷特首领",
        "role": "灰鬃商队总领队",
        "faction": "灰鬃商队",
        "location": "北境主商道",
        "home_location": "寒铁城",
        "action_kind": "task_progress",
        "task_id": "task_reroute_eastern_valley",
        "task_title": "灰鬃商队改走东侧白河谷地转运生铁",
        "task_stage": "先遣伙计探路与重编驮队",
        "task_progress_percent": 25,
        "task_status": "active",
        "current_activity": "在北境主商道分岔口指挥车队转向东侧白河谷地",
        "motivation": "灰岩边关加征三成生铁重税，改走白河谷地水陆联运以规避重税",
        "goal": "将寒铁城与边境生铁安全运抵霜木镇",
        "depth_tier": "associated",
        "shift_name": "行商押运班",
        "availability": "in_transit",
        "duration_minutes": 60,
        "next_due_minute": 60,
        "source_ids": ["chunk:north_trade_routes_02", "entry:diplomacy_loran_tariff_02"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_linn_envoy",
        "name": "林恩特使",
        "role": "星辉大商会北境督办",
        "faction": "星辉大商会",
        "location": "寒铁城",
        "home_location": "寒铁城",
        "action_kind": "task_progress",
        "task_id": "task_starlight_contract_push",
        "task_title": "推行北境工坊排他包销契约",
        "task_stage": "调拨寒铁城总栈生铁准备压价",
        "task_progress_percent": 40,
        "task_status": "active",
        "current_activity": "在寒铁城总栈审阅霜木镇各工坊的原料依赖度报告",
        "motivation": "趁边关加税与商队延误，迫使霜木镇独立铁匠签署长期包销协议",
        "goal": "垄断北境生铁与成品铁器分销渠道",
        "depth_tier": "associated",
        "shift_name": "总栈督办值守",
        "availability": "available",
        "duration_minutes": 180,
        "next_due_minute": 180,
        "source_ids": ["entry:faction_starlight_guild_01"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_victor_warden",
        "name": "维克多矿监",
        "role": "寒铁城矿务司主管",
        "faction": "寒铁城矿务司",
        "location": "寒铁城",
        "home_location": "寒铁城",
        "action_kind": "shift_change",
        "from_shift": "矿坑早检",
        "to_shift": "矿石配额审批班",
        "shift_name": "矿石配额审批班",
        "current_activity": "在寒铁城矿务司核查新矿脉勘探文书",
        "motivation": "与白河城争夺交界山脊富铁矿脉特许权",
        "goal": "维护寒铁城对北境矿产的优先管辖权",
        "depth_tier": "background",
        "availability": "available",
        "duration_minutes": 240,
        "next_due_minute": 240,
        "source_ids": ["entry:conflict_iron_vein_03"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_serena_councillor",
        "name": "瑟蕾娜议员",
        "role": "白河城贸易市政官",
        "faction": "白河城市政厅",
        "location": "白河城",
        "home_location": "白河城",
        "action_kind": "task_progress",
        "task_id": "task_river_barge_dispatch",
        "task_title": "白河水路铁矿驳船扩编",
        "task_stage": "征调东谷渡口平底驳船",
        "task_progress_percent": 35,
        "task_status": "active",
        "current_activity": "在白河城港务厅调度东谷水陆联运驳船",
        "motivation": "吸引避开灰岩边关的商队改走白河城水路以增加市政关税",
        "goal": "扩大白河城在北境水陆贸易中的枢纽地位",
        "depth_tier": "background",
        "shift_name": "市政厅日班",
        "availability": "available",
        "duration_minutes": 240,
        "next_due_minute": 240,
        "source_ids": ["chunk:north_trade_routes_02", "entry:conflict_iron_vein_03"],
    },
    {
        "op": "seed_npc",
        "npc_id": "npc_claude_commissioner",
        "name": "克劳德专员",
        "role": "洛兰公国灰岩边关税务官",
        "faction": "洛兰公国财政署",
        "location": "灰岩边关",
        "home_location": "灰岩边关",
        "action_kind": "shift_change",
        "from_shift": "边关宿值",
        "to_shift": "重税稽查班",
        "shift_name": "重税稽查班",
        "current_activity": "在灰岩边关关隘督导税吏按新税率清点过境生铁车队",
        "motivation": "执行洛兰公国加征三成生铁关税令，充实公国边境财政",
        "goal": "严格执行灰岩边关新关税标准",
        "depth_tier": "background",
        "availability": "available",
        "duration_minutes": 240,
        "next_due_minute": 240,
        "source_ids": ["entry:diplomacy_loran_tariff_02"],
    },
)


def build_northern_trade_npc_seed_proposal(
    *,
    instance_id: str = NORTHERN_TRADE_INSTANCE_ID,
    world_minute: int = 0,
) -> dict[str, Any]:
    """构建北境贸易 10 名初始 NPC 的结构化初始化提议。"""
    ops = []
    for item in NORTHERN_TRADE_INITIAL_NPCS:
        copied = dict(item)
        copied["next_due_minute"] = world_minute + int(copied["duration_minutes"])
        ops.append(copied)
    return {
        "instance_id": instance_id,
        "mode": "seed",
        "world_minute": world_minute,
        "summary": "初始化北境贸易场景十名核心、关联与背景 NPC 的身份、地点、动机与首轮到期时间。",
        "operations": ops,
    }


def seed_northern_trade_npcs(
    conn: sqlite3.Connection,
    *,
    instance_id: str = NORTHERN_TRADE_INSTANCE_ID,
    world_id: str = NORTHERN_TRADE_WORLD_ID,
    adventure_id: str = "adv_northern_frontier_001",
    world_minute: int = 0,
) -> dict[str, Any]:
    """在 SQLite 连接中初始化北境贸易世界实例、关联事件桩、NPC 已知事实桩及 10 名初始 NPC。"""
    inst = ensure_instance_exists(
        conn,
        instance_id,
        world_id=world_id,
        adventure_id=adventure_id,
        world_minute=world_minute,
        revision=0,
    )

    # 登记北境贸易核心事件桩（使独立跑测无需强耦合 m3/events 也能验证完整事件因果）
    register_event_stub(
        conn,
        instance_id,
        "evt_loran_tariff_01",
        title="洛兰公国灰岩边关加征三成生铁进口税",
        description="灰岩边关税务官克劳德正式对过境生铁与焦炭加征三成重税。",
        location="灰岩边关",
        scope="international",
        participants=["克劳德专员", "加雷特首领"],
        stage="tariff_enforced",
        status="active",
        created_minute=0,
        updated_minute=30,
        next_update_minute=180,
    )
    register_event_stub(
        conn,
        instance_id,
        "evt_caravan_reroute_02",
        title="灰鬃商队改走白河谷地导致霜木镇生铁延期",
        description="为避开灰岩边关重税，加雷特首领率灰鬃商队绕行白河谷地，霜木镇生铁与精煤交货延迟三日。",
        location="北境主商道",
        scope="regional",
        participants=["韩掌柜", "霍克铁匠", "玛塔老板娘", "加雷特首领"],
        stage="rerouting_eastern_valley",
        status="active",
        created_minute=20,
        updated_minute=60,
        next_update_minute=60,
    )
    register_event_stub(
        conn,
        instance_id,
        "evt_local_competition_03",
        title="霜木镇本地工坊与星辉大商会分号的价格博弈",
        description="星辉大商会试图趁原料短缺压低收购价并吸纳独立工坊订单。",
        location="霜木镇",
        scope="local",
        participants=["韩掌柜", "霍克铁匠", "林恩特使"],
        stage="supply_squeeze",
        status="active",
        created_minute=0,
        updated_minute=60,
        next_update_minute=120,
    )

    # 登记各 NPC 自身的已知事实桩（供验证因果动机引用）
    register_knowledge_stub(
        conn,
        instance_id,
        "fact_han_caravan_delay",
        holder_id="npc_han_shopkeeper",
        title="灰鬃商队绕行白河谷地延误生铁交货",
        content="韩掌柜收到加雷特首领快信，得知生铁车队改走白河谷地水陆联运，至少延误三日。",
        source_event_id="evt_caravan_reroute_02",
        learned_minute=40,
    )
    register_knowledge_stub(
        conn,
        instance_id,
        "fact_hawk_coal_shortage",
        holder_id="npc_hawk_blacksmith",
        title="铁匠坊精煤与生铁补给推迟",
        content="霍克铁匠核对库房后确认，因灰鬃商队改道，本批生铁与精煤无法按期入库。",
        source_event_id="evt_caravan_reroute_02",
        learned_minute=45,
    )
    register_knowledge_stub(
        conn,
        instance_id,
        "fact_marta_road_rumor",
        holder_id="npc_marta_innkeeper",
        title="南来旅人盛传边关加税与商队改道",
        content="灰鹿酒馆多名南来行商谈及灰岩边关加税及灰鬃商队改走东谷之事。",
        source_event_id="evt_caravan_reroute_02",
        learned_minute=50,
    )
    register_knowledge_stub(
        conn,
        instance_id,
        "fact_garret_tariff_notice",
        holder_id="npc_garret_caravan",
        title="亲历灰岩边关加征三成生铁重税",
        content="加雷特首领在边关收到新税令，决定率队改道东侧白河谷地。",
        source_event_id="evt_loran_tariff_01",
        learned_minute=20,
    )

    seed_proposal = build_northern_trade_npc_seed_proposal(
        instance_id=instance_id,
        world_minute=world_minute,
    )
    applied = apply_proposal(
        conn,
        instance_id,
        seed_proposal,
        allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
    )
    return {
        "instance": inst,
        "seed_result": applied,
    }


def build_wave3_mock_npc_responses(
    *,
    instance_id: str = NORTHERN_TRADE_INSTANCE_ID,
) -> dict[str, dict[str, Any]]:
    """构建北境贸易多步 HTTP Mock 响应字典（按 `world_minute` 索引）。"""
    return {
        "advance_minute_60": {
            "instance_id": instance_id,
            "mode": "advance",
            "world_minute": 60,
            "base_world_revision": 1,
            "summary": (
                "第 60 分钟：面对灰鬃商队改道与生铁延期同一局势，"
                "韩掌柜决定立即动身前往白河城洽谈水路生铁（travel），"
                "霍克铁匠决定留守霜木镇切换为单炉节炭班（shift_change），"
                "玛塔老板娘约见雷恩队长核实商道治安（conversation_plan），"
                "加雷特首领在主商道推进东谷转运任务（task_progress）。"
            ),
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "location": "白河城",
                    "availability": "in_transit",
                    "current_activity": "携原料账册乘轻便马车赶往白河城水运码头洽谈替代生铁船期",
                    "motivation": "灰鬃商队因边关重税改道延误，需赶在星辉大商会垄断前赴白河城锁定水路生铁",
                    "duration_minutes": 90,
                    "next_due_minute": 150,
                    "cause_event_ids": ["evt_caravan_reroute_02", "evt_local_competition_03"],
                    "cause_fact_ids": ["fact_han_caravan_delay"],
                    "source_ids": ["chunk:north_trade_routes_02"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_hawk_blacksmith",
                    "expected_npc_revision": 1,
                    "action_kind": "shift_change",
                    "location": "霜木镇",
                    "from_shift": "常规双炉锻造班",
                    "to_shift": "单炉节炭与废铁分拣班",
                    "shift_name": "单炉节炭与废铁分拣班",
                    "availability": "busy",
                    "current_activity": "关停副锻炉并带领学徒切换至单炉节炭与废铁分拣班，优先保障护路队急单",
                    "motivation": "同样面对灰鬃商队改道导致的生铁与精煤延期，选择留守工坊换班节流以不断火",
                    "duration_minutes": 60,
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                    "cause_fact_ids": ["fact_hawk_coal_shortage"],
                    "source_ids": ["chunk:north_geography_01"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_marta_innkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "conversation_plan",
                    "location": "霜木镇",
                    "target_npc_id": "npc_renn_captain",
                    "meeting_location": "霜木镇",
                    "conversation_topic": "核实灰鬃商队改走白河谷地的延误天数与南门治安巡防",
                    "availability": "available",
                    "current_activity": "在灰鹿酒馆与护路队雷恩队长商议延误商队接应与住客安顿",
                    "motivation": "旅人纷纷议论商队改道，需向护路队队长核实道路实情并调整客房预留",
                    "duration_minutes": 60,
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                    "cause_fact_ids": ["fact_marta_road_rumor"],
                    "source_ids": ["chunk:north_trade_routes_02"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_garret_caravan",
                    "expected_npc_revision": 1,
                    "action_kind": "task_progress",
                    "location": "北境主商道",
                    "task_id": "task_reroute_eastern_valley",
                    "task_title": "灰鬃商队改走东侧白河谷地转运生铁",
                    "task_stage": "抵达白河谷地西口并分批装运生铁过渡口",
                    "task_progress_percent": 65,
                    "task_status": "active",
                    "availability": "in_transit",
                    "current_activity": "在白河谷地渡口督导灰鬃商队将大宗生铁转装驳船与轻车",
                    "motivation": "绕开灰岩边关三成重税后全速推进东谷水陆联运，力争减少霜木镇断供损失",
                    "duration_minutes": 60,
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_loran_tariff_01", "evt_caravan_reroute_02"],
                    "cause_fact_ids": ["fact_garret_tariff_notice"],
                    "source_ids": ["chunk:north_trade_routes_02"],
                },
            ],
        },
        "advance_minute_120": {
            "instance_id": instance_id,
            "mode": "advance",
            "world_minute": 120,
            "base_world_revision": 2,
            "summary": (
                "第 120 分钟：韩掌柜已离开霜木镇降档为关联（associated）且尚未到期；"
                "霍克铁匠在霜木镇推进废铁回炉任务（task_progress），"
                "玛塔老板娘切换至晚间迎客班（shift_change），"
                "加雷特首领完成白河谷地转运阶段任务（task_progress 100%）。"
            ),
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_hawk_blacksmith",
                    "expected_npc_revision": 2,
                    "action_kind": "task_progress",
                    "location": "霜木镇",
                    "task_id": "task_hawk_scrap_smelting",
                    "task_title": "旧铁器与边角料重熔提纯",
                    "task_stage": "完成首炉回收熟铁锭锻打",
                    "task_progress_percent": 60,
                    "task_status": "active",
                    "availability": "available",
                    "current_activity": "在铁匠坊将回收的旧农具与铁渣重熔提纯为可用熟铁锭",
                    "motivation": "在商队新一批生铁运抵前，靠废铁重熔补足护路队修械原料缺口",
                    "duration_minutes": 60,
                    "next_due_minute": 180,
                    "cause_event_ids": ["evt_caravan_reroute_02", "evt_local_competition_03"],
                    "cause_fact_ids": ["fact_hawk_coal_shortage"],
                    "source_ids": ["chunk:north_geography_01"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_marta_innkeeper",
                    "expected_npc_revision": 2,
                    "action_kind": "shift_change",
                    "location": "霜木镇",
                    "from_shift": "午间迎客与消息打听班",
                    "to_shift": "晚间酒馆食宿接待班",
                    "shift_name": "晚间酒馆食宿接待班",
                    "availability": "available",
                    "current_activity": "在灰鹿酒馆点亮风灯，接待护路队换防队员与滞留行商",
                    "motivation": "与雷恩队长核实商队晚到详情后，将预留大通铺改为接待滞留散客",
                    "duration_minutes": 60,
                    "next_due_minute": 180,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                    "cause_fact_ids": ["fact_marta_road_rumor"],
                    "source_ids": ["chunk:north_trade_routes_02"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_garret_caravan",
                    "expected_npc_revision": 2,
                    "action_kind": "task_progress",
                    "location": "北境主商道",
                    "task_id": "task_reroute_eastern_valley",
                    "task_title": "灰鬃商队改走东侧白河谷地转运生铁",
                    "task_stage": "全队通过白河谷地水陆交接点驶向霜木镇东口",
                    "task_progress_percent": 100,
                    "task_status": "completed",
                    "availability": "in_transit",
                    "current_activity": "完成白河谷地水陆转运，率领灰鬃商队主力向霜木镇东口进发",
                    "motivation": "尽快将绕行生铁送达霜木镇以兑现契约",
                    "duration_minutes": 60,
                    "next_due_minute": 180,
                    "cause_event_ids": ["evt_loran_tariff_01", "evt_caravan_reroute_02"],
                    "cause_fact_ids": ["fact_garret_tariff_notice"],
                    "source_ids": ["chunk:north_trade_routes_02"],
                },
            ],
        },
    }


def run_northern_trade_npc_simulation(
    conn: sqlite3.Connection,
    *,
    instance_id: str = NORTHERN_TRADE_INSTANCE_ID,
    world_id: str = NORTHERN_TRADE_WORLD_ID,
) -> dict[str, Any]:
    """使用 `httpx.MockTransport` 端到端运行北境贸易 NPC 自主行动四步链：
    1. 第 0 分钟：Seed 初始化 10 名 NPC；
    2. 第 60 分钟：Advance 1（韩掌柜与霍克铁匠对同一事件 `evt_caravan_reroute_02` 采取不同安排 + 玛塔交谈安排 + 加雷特任务推进）；
    3. 第 90 分钟：Advance 2（无到期 NPC，跳过模型请求且状态零变更）；
    4. 第 120 分钟：Advance 3（韩掌柜因离开霜木镇由 foreground 降档为 associated，霍克、玛塔、加雷特继续演进）。
    """
    seed_info = seed_northern_trade_npcs(
        conn,
        instance_id=instance_id,
        world_id=world_id,
        world_minute=0,
    )
    mock_payloads = build_wave3_mock_npc_responses(instance_id=instance_id)
    http_calls: list[dict[str, Any]] = []

    def _mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        user_msg = body["messages"][1]["content"]
        if "【已确认世界分钟】60" in user_msg:
            reply_obj = mock_payloads["advance_minute_60"]
            http_calls.append({"world_minute": 60, "model": body.get("model")})
        elif "【已确认世界分钟】120" in user_msg:
            reply_obj = mock_payloads["advance_minute_120"]
            http_calls.append({"world_minute": 120, "model": body.get("model")})
        else:
            raise AssertionError(f"Unexpected mock NPC request at minute: {user_msg}")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(reply_obj, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 520, "completion_tokens": 260, "total_tokens": 780},
            },
        )

    client = OpenAINpcModelClient(
        base_url="https://mock.npc.invalid/v1",
        api_key="sk-npc-mock-secret",
        model="npc-sim-v3",
        transport=httpx.MockTransport(_mock_handler),
    )

    # Step 2: 推进世界时钟到第 60 分钟
    conn.execute("UPDATE world_instances SET world_minute = 60 WHERE id = ?", (instance_id,))
    snap_60 = read_snapshot(
        conn,
        instance_id,
        {"kind": "advance", "target_world_minute": 60, "player_location": "霜木镇"},
        budget=DepthBudget(max_foreground_npcs=3, max_associated_npcs=2, max_background_npcs=0, max_total_npcs=5),
    )
    gen_60 = asyncio.run(client.generate_proposal(snap_60, validate=True))
    applied_60 = apply_proposal(
        conn,
        instance_id,
        gen_60["validated"],
        {"job_id": "job_npc_adv_60", "kind": "advance", "target_world_minute": 60, "base_world_revision": 1},
    )

    # 核查第 60 分钟后韩掌柜（途中离开）与霍克铁匠（留在霜木镇）的可接触性差异
    han_access_60 = check_npc_accessibility(
        conn, instance_id, "npc_han_shopkeeper", player_location="霜木镇", world_minute=60
    )
    hawk_access_60 = check_npc_accessibility(
        conn, instance_id, "npc_hawk_blacksmith", player_location="霜木镇", world_minute=60
    )

    # Step 3: 推进世界时钟到第 90 分钟（最早到期时间为 120，无到期 NPC）
    conn.execute("UPDATE world_instances SET world_minute = 90 WHERE id = ?", (instance_id,))
    snap_90 = read_snapshot(
        conn,
        instance_id,
        {"kind": "advance", "target_world_minute": 90, "player_location": "霜木镇"},
    )
    gen_90 = asyncio.run(client.generate_proposal(snap_90, validate=True))

    # Step 4: 推进世界时钟到第 120 分钟
    conn.execute("UPDATE world_instances SET world_minute = 120 WHERE id = ?", (instance_id,))
    snap_120 = read_snapshot(
        conn,
        instance_id,
        {"kind": "advance", "target_world_minute": 120, "player_location": "霜木镇"},
        budget=DepthBudget(max_foreground_npcs=3, max_associated_npcs=2, max_background_npcs=0, max_total_npcs=5),
    )
    gen_120 = asyncio.run(client.generate_proposal(snap_120, validate=True))
    applied_120 = apply_proposal(
        conn,
        instance_id,
        gen_120["validated"],
        {"job_id": "job_npc_adv_120", "kind": "advance", "target_world_minute": 120, "base_world_revision": 2},
    )

    final_snapshot = snapshot(conn, instance_id)
    contactable_120 = list_contactable_npcs(
        conn,
        instance_id,
        player_location="霜木镇",
        world_minute=120,
        include_unavailable_at_location=True,
    )

    return {
        "instance_id": instance_id,
        "world_id": world_id,
        "seed": seed_info["seed_result"],
        "step_60": {
            "snapshot_budget": snap_60["budget"],
            "selected_npc_ids": snap_60["selected_npc_ids"],
            "applied": applied_60,
            "han_accessibility": han_access_60,
            "hawk_accessibility": hawk_access_60,
        },
        "step_90_no_due": {
            "status": snap_90["status"],
            "skip": snap_90["skip"],
            "reason_code": snap_90["reason_code"],
            "earliest_next_due_minute": snap_90["earliest_next_due_minute"],
            "model_skipped": gen_90["skipped"],
        },
        "step_120": {
            "snapshot_budget": snap_120["budget"],
            "selected_npc_ids": snap_120["selected_npc_ids"],
            "han_tier_evaluation": next(
                e for e in snap_120["tier_evaluations"] if e["npc_id"] == "npc_han_shopkeeper"
            ),
            "applied": applied_120,
            "contactable_at_frostwood": contactable_120,
        },
        "http_calls": http_calls,
        "final_npcs": list_npcs(conn, instance_id)["items"],
        "final_tasks": list_npc_tasks(conn, instance_id),
        "action_log_count": len(list_npc_action_log(conn, instance_id)),
        "final_snapshot": final_snapshot,
    }


def get_wave3_northern_trade_npc_bundle() -> dict[str, Any]:
    """读取 `wave3_northern_trade_npc_fixture.json` 可重放测试数据包。"""
    fixture_path = Path(__file__).with_name("wave3_northern_trade_npc_fixture.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))
