"""M3.4 北境贸易（Northern Trade）与第四轮（Wave 4）NPC 自主行动、模拟深度与脉动全生命周期 Mock 推演 Fixture。

契约要求（`docs/collaboration/M3_WAVE3_GEMINI_MARCIN.md` & `docs/collaboration/M3_WAVE4_GEMINI_LI.md`）：
1. 完整保留第三轮北境贸易 Fixture：
   - 两个 NPC 对同一局势采取不同安排、三档模拟深度升降档与可测量预算、无到期跳过及 `httpx.MockTransport` 四步重放。
2. 新增第四轮（Wave 4）初始化输入与脉动全生命周期 Mock 验证套件：
   - 从初始化输入（`seed_npcs_from_initialization`）接收人物状态（覆盖 `travel`、`stay`、`wait`、`no_change`、`plan_change`、`conversation_plan` 及 `canon`/`inferred` 区分）；
   - 通过 `PulseWorker` + `build_npc_pulse_callbacks` + `httpx.MockTransport` 端到端验证：
     - 两名人物对同一局势反应不同；
     - 组合提交只推进一次 `world_instances.revision`（解决双重更新实例版本冲突）；
     - 出行离开后不可接触，稍后返回原处「回头再遇见」并供查证与下一回合叙事使用同一份状态；
     - 任务失败重试与重复执行幂等；
     - 先离线推进时间（不积压历史、不发请求）后恢复以当前确认时间为锚继续推演；
     - 存档迁移（`snapshot` / `restore` 及跨库导出导入）后续玩状态一致。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterator

import httpx

from .ai import NpcPulseModelClientAdapter, OpenAINpcModelClient
from .service import (
    apply_proposal,
    build_adventure_narration_npc_context,
    build_npc_pulse_callbacks,
    check_npc_accessibility,
    ensure_instance_exists,
    get_npc,
    list_contactable_npcs,
    list_npc_action_log,
    list_npc_tasks,
    list_npcs,
    read_snapshot,
    register_event_stub,
    register_knowledge_stub,
    restore,
    seed_npcs_from_initialization,
    snapshot,
)
from .types import DepthBudget

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
    """使用 `httpx.MockTransport` 端到端运行北境贸易 NPC 自主行动四步链。"""
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

    han_access_60 = check_npc_accessibility(
        conn, instance_id, "npc_han_shopkeeper", player_location="霜木镇", world_minute=60
    )
    hawk_access_60 = check_npc_accessibility(
        conn, instance_id, "npc_hawk_blacksmith", player_location="霜木镇", world_minute=60
    )

    conn.execute("UPDATE world_instances SET world_minute = 90 WHERE id = ?", (instance_id,))
    snap_90 = read_snapshot(
        conn,
        instance_id,
        {"kind": "advance", "target_world_minute": 90, "player_location": "霜木镇"},
    )
    gen_90 = asyncio.run(client.generate_proposal(snap_90, validate=True))

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


# ==================== M3 Wave 4 新增：自定义世界初始化与脉动全生命周期 Fixture ====================


def build_wave4_custom_world_init_payload(
    *,
    world_kind: str = "harbor",
    instance_id: str = "inst_wave4_harbor_001",
    allowed_source_ids: list[str] | tuple[str, ...] = ("chunk:harbor_tide_01", "entry:guild_charter_02"),
) -> dict[str, Any]:
    """构建第四轮 AI 世界分析初始化草案样本（支持不同世界题材，验证不依赖预置人物并覆盖出行、留在原处、改变计划、交谈安排及推演补写）。"""
    srcs = list(allowed_source_ids)
    if world_kind == "academy":
        return {
            "instance_id": instance_id,
            "summary": "云栖书院开局初始化：藏书阁执事留守原处、外门学子动身下山、讲席长老改变讲学计划、戒律司约见核对名册。",
            "initial_events": [
                {
                    "event_id": "evt_academy_seal_01",
                    "title": "后山碑林古印松动",
                    "description": "雨夜过后，云栖书院后山碑林传出古印松动异响，山门加强出入核查。",
                    "location": "云栖书院山门",
                    "scope": "local",
                    "participants": ["沈执事", "陆青", "温长老", "周司仪"],
                    "stage": "initial_alert",
                    "status": "active",
                    "next_update_minute": 60,
                }
            ],
            "initial_facts": [
                {
                    "fact_id": "fact_shen_rubbing_missing",
                    "holder_id": "npc_shen_curator",
                    "title": "藏书阁拓片借阅异常",
                    "content": "沈执事发现三卷碑林旧拓片昨日未按期归还。",
                    "source_event_id": "evt_academy_seal_01",
                    "learned_minute": 0,
                },
                {
                    "fact_id": "fact_lu_herb_order",
                    "holder_id": "npc_lu_disciple",
                    "title": "丹房急需山麓灵草",
                    "content": "陆青奉命需在山门封禁前赶赴青溪集采买止血药材。",
                    "source_event_id": "evt_academy_seal_01",
                    "learned_minute": 0,
                },
            ],
            "initial_npcs": [
                {
                    "npc_id": "npc_shen_curator",
                    "name": "沈执事",
                    "role": "云栖书院藏书阁执事",
                    "faction": "云栖书院内院",
                    "location": "云栖书院山门",
                    "arrangement": "留在原处",
                    "current_activity": "在山门值房清点出入借阅玉牌",
                    "motivation": "碑林异动后留守山门核查古籍拓片去向",
                    "goal": "追回未归还的三卷碑林拓片",
                    "depth_tier": "foreground",
                    "duration_minutes": 60,
                    "source_ids": srcs[:1],
                    "relationships": [{"target_npc_id": "npc_wen_elder", "relation": "同门协作"}],
                },
                {
                    "npc_id": "npc_lu_disciple",
                    "name": "陆青",
                    "role": "外门采办弟子",
                    "faction": "云栖书院外门",
                    "location": "云栖书院山门",
                    "to_location": "青溪集",
                    "arrangement": "出行",
                    "current_activity": "背负药篓由云栖书院山门下山赶往青溪集",
                    "motivation": "趁山门尚未完全戒严先行下山采购丹房急需药材",
                    "goal": "当日往返青溪集带回药材",
                    "depth_tier": "foreground",
                    "duration_minutes": 60,
                    "source_ids": srcs[:1],
                },
                {
                    "npc_id": "npc_wen_elder",
                    "name": "温长老",
                    "role": "碑林讲席长老",
                    "faction": "云栖书院讲席堂",
                    "location": "云栖书院山门",
                    "arrangement": "改变计划",
                    "previous_plan": "晨间公开讲授古碑考据课",
                    "new_plan": "暂停公开授课，改为闭门加固碑林阵纹",
                    "current_activity": "取消晨间讲席，改在山门议事厅调配护山阵旗",
                    "motivation": "古印松动事关书院根基，优先加固阵纹",
                    "depth_tier": "foreground",
                    "duration_minutes": 60,
                    "source_ids": srcs[-1:],
                },
                {
                    "npc_id": "npc_zhou_marshal",
                    "name": "周司仪",
                    "role": "书院戒律司巡守",
                    "faction": "云栖书院戒律司",
                    "location": "云栖书院山门",
                    "arrangement": "交谈安排",
                    "target_npc_id": "npc_shen_curator",
                    "conversation_topic": "核对昨夜出入碑林与藏书阁的弟子名册",
                    "current_activity": "在山门约见沈执事核对弟子出入名录",
                    "motivation": "资料未载明巡守细节，AI 按书院规制补写巡守司仪协助排查",
                    "depth_tier": "associated",
                    "grounding": "inferred",
                    "duration_minutes": 90,
                },
            ],
        }

    return {
        "instance_id": instance_id,
        "summary": "星澜港海贸局势初始化：航运主事动身赴灯塔屿、船坞匠师留在原处、贸易会长改变计划、港务巡官安排交谈。",
        "initial_events": [
            {
                "event_id": "evt_harbor_fog_01",
                "title": "外海浓雾与灯塔信标异常导致远洋香料船队改泊",
                "description": "外海突起浓雾且北航道灯塔信标失灵，远洋船队暂泊灯塔屿外锚地，星澜港内香料与造船桐油现货紧缺。",
                "location": "星澜港码头",
                "scope": "regional",
                "participants": ["林船长", "周匠师", "苏会长", "陈巡官"],
                "stage": "fog_blockade",
                "status": "active",
                "next_update_minute": 60,
            }
        ],
        "initial_facts": [
            {
                "fact_id": "fact_lin_beacon_signal",
                "holder_id": "npc_lin_navigator",
                "title": "收到灯塔屿引航快艇旗语",
                "content": "林船长观测到灯塔屿引航快艇发来旗语，得知主力商船完好但需引航入港。",
                "source_event_id": "evt_harbor_fog_01",
                "learned_minute": 0,
            },
            {
                "fact_id": "fact_zhou_tung_oil_low",
                "holder_id": "npc_zhou_shipwright",
                "title": "船坞防渗桐油仅够三艘快船修缮",
                "content": "周匠师清点船坞库房确认防渗桐油余量不足，需立即调整修船排期。",
                "source_event_id": "evt_harbor_fog_01",
                "learned_minute": 0,
            },
            {
                "fact_id": "fact_su_auction_notice",
                "holder_id": "npc_su_guildmaster",
                "title": "潮音商行准备调整香料竞拍规则",
                "content": "苏会长得知船队晚点一日，决定暂缓大宗现货抛售。",
                "source_event_id": "evt_harbor_fog_01",
                "learned_minute": 0,
            },
        ],
        "initial_npcs": [
            {
                "npc_id": "npc_lin_navigator",
                "name": "林船长",
                "role": "星澜港引航公会大副",
                "faction": "星澜港引航公会",
                "location": "星澜港码头",
                "action_kind": "stay",
                "current_activity": "在星澜港码头引航栈桥观测海雾与潮汐水文",
                "motivation": "等候潮位平缓以便随时出港接应暂泊灯塔屿的船队",
                "goal": "引导受困外海的香料船队安全进港",
                "depth_tier": "foreground",
                "duration_minutes": 60,
                "next_due_minute": 60,
                "source_ids": srcs[:1],
                "relationships": [
                    {"target_npc_id": "npc_zhou_shipwright", "relation": "长期合作修船伙伴"},
                    {"target_npc_id": "npc_chen_inspector", "relation": "港务通关联络"},
                ],
            },
            {
                "npc_id": "npc_zhou_shipwright",
                "name": "周匠师",
                "role": "星澜船坞总匠师",
                "faction": "星澜造船行会",
                "location": "星澜港码头",
                "action_kind": "stay",
                "current_activity": "在星澜港码头一号船台检修引航快船龙骨",
                "motivation": "引航公会快船随时需要出港，留在原处保障船况",
                "goal": "确保港内应急引航船队正常运转",
                "depth_tier": "foreground",
                "duration_minutes": 60,
                "next_due_minute": 60,
                "source_ids": srcs[:1],
            },
            {
                "npc_id": "npc_su_guildmaster",
                "name": "苏会长",
                "role": "潮音海贸商会会长",
                "faction": "潮音商会",
                "location": "星澜港码头",
                "action_kind": "plan_change",
                "previous_plan": "今日正午在码头交易厅举行远洋香料公开竞拍",
                "new_plan": "将公开竞拍改为优先保供契约工坊的定量配售",
                "current_activity": "在星澜港码头商会分堂拟定定量配售新规",
                "motivation": "浓雾导致远洋船队推迟进港，改变原定竞拍计划以平抑港内哄抬物价",
                "goal": "稳住星澜港海贸物价与商会信誉",
                "depth_tier": "foreground",
                "duration_minutes": 60,
                "next_due_minute": 60,
                "source_ids": srcs[-1:],
            },
            {
                "npc_id": "npc_chen_inspector",
                "name": "陈巡官",
                "role": "星澜港港务司巡检官",
                "faction": "星澜港务司",
                "location": "星澜港码头",
                "action_kind": "conversation_plan",
                "target_npc_id": "npc_lin_navigator",
                "conversation_topic": "核对灯塔屿应急引航出港许可与防雾灯号",
                "current_activity": "在码头栈桥与林船长核对引航出港排班",
                "motivation": "资料未详述港务巡检官姓名，AI 按港口规制补写巡官协调引航",
                "goal": "维护浓雾期间星澜港航道秩序",
                "depth_tier": "associated",
                "grounding": "inferred",
                "duration_minutes": 120,
                "next_due_minute": 120,
            },
        ],
    }


def run_wave4_pulse_lifecycle_simulation(
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """端到端运行 M3 Wave 4 NPC 自主行动与世界脉动全生命周期 Mock 推演。"""
    from app.m3.pulse.budget import configure_instance_pulse
    from app.m3.pulse.control import set_pulse_activity
    from app.m3.pulse.queue import enqueue
    from app.m3.pulse.schema import ensure_pulse_schema
    from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
    from app.m3.pulse.types import PulseTrigger
    from app.m3.pulse.worker import PulseWorker

    temp_dir = None
    if db_path is None:
        temp_dir = tempfile.TemporaryDirectory()
        resolved_db_path = Path(temp_dir.name) / "wave4_npc_pulse.db"
    else:
        resolved_db_path = Path(db_path)

    @contextmanager
    def _db_factory() -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(resolved_db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    try:
        instance_id = "inst_wave4_harbor_001"
        world_id = "world_wave4_harbor_001"
        adventure_id = "adv_wave4_harbor_001"
        allowed_sources = ("chunk:harbor_tide_01", "entry:guild_charter_02")

        with _db_factory() as conn:
            ensure_pulse_schema(conn, ensure_instance_table=True)
            ensure_instance_exists(
                conn,
                instance_id,
                world_id=world_id,
                adventure_id=adventure_id,
                world_minute=0,
                revision=0,
            )
            init_payload = build_wave4_custom_world_init_payload(
                world_kind="harbor",
                instance_id=instance_id,
                allowed_source_ids=allowed_sources,
            )
            seed_res = seed_npcs_from_initialization(
                conn,
                instance_id,
                init_payload,
                world_minute=0,
                allowed_source_ids=allowed_sources,
                player_location="星澜港码头",
                advance_world_revision=True,
                idempotency_key="init_harbor_seed_001",
            )
            configure_instance_pulse(
                conn,
                instance_id,
                enabled=True,
                paused=False,
                min_game_minute_interval=15,
                cooldown_seconds=0,
                now=1000.0,
            )

        http_attempts_by_minute: dict[int, int] = {}
        http_log: list[dict[str, Any]] = []

        def _mock_transport_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            user_prompt = body["messages"][1]["content"]
            minute = 0
            for line in user_prompt.splitlines():
                if line.startswith("【已确认世界分钟】"):
                    minute = int(line.split("】", 1)[1].strip())
                    break
            rev = 0
            for line in user_prompt.splitlines():
                if line.startswith("【基准世界版本】"):
                    rev = int(line.split("】", 1)[1].strip())
                    break

            attempt_no = http_attempts_by_minute.get(minute, 0) + 1
            http_attempts_by_minute[minute] = attempt_no
            http_log.append({"world_minute": minute, "attempt": attempt_no, "base_world_revision": rev})

            if minute == 60:
                reply = {
                    "instance_id": instance_id,
                    "mode": "advance",
                    "world_minute": 60,
                    "base_world_revision": rev,
                    "summary": "第 60 分钟：林船长动身前往灯塔屿引航，周匠师留在星澜港码头改变修船计划，苏会长原地等待后续船讯。",
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_lin_navigator",
                            "expected_npc_revision": 1,
                            "action_kind": "travel",
                            "from_location": "星澜港码头",
                            "to_location": "灯塔屿",
                            "location": "灯塔屿",
                            "availability": "in_transit",
                            "current_activity": "驾驶引航快艇驶向灯塔屿外锚地接应受困船队",
                            "motivation": "已收到灯塔屿旗语信号，主动出海引导香料船队避礁",
                            "duration_minutes": 60,
                            "next_due_minute": 120,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_lin_beacon_signal"],
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_zhou_shipwright",
                            "expected_npc_revision": 1,
                            "action_kind": "plan_change",
                            "location": "星澜港码头",
                            "previous_plan": "确保港内应急引航船队正常运转",
                            "new_plan": "暂停民船外包大修，集中剩余桐油优先抢修港务引航船与灯塔补给艇",
                            "goal": "暂停民船外包大修，集中剩余桐油优先抢修港务引航船与灯塔补给艇",
                            "availability": "available",
                            "current_activity": "在星澜港码头船台重新调配木工班组，优先封涂灯塔补给艇",
                            "motivation": "防渗桐油库存吃紧，改变原定修船排期以保住引航船队",
                            "duration_minutes": 60,
                            "next_due_minute": 120,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_zhou_tung_oil_low"],
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_su_guildmaster",
                            "expected_npc_revision": 1,
                            "action_kind": "wait",
                            "location": "星澜港码头",
                            "availability": "available",
                            "current_activity": "在星澜港码头商会分堂静候林船长引航传回的靠泊时辰",
                            "motivation": "在引航结果明朗前原地等待，暂不调整第二批香料配额",
                            "duration_minutes": 150,
                            "next_due_minute": 210,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_su_auction_notice"],
                        },
                    ],
                }
            elif minute == 120:
                if attempt_no == 1:
                    return httpx.Response(502, json={"error": {"message": "Simulated upstream gateway 502"}})
                reply = {
                    "instance_id": instance_id,
                    "mode": "advance",
                    "world_minute": 120,
                    "base_world_revision": rev,
                    "summary": "第 120 分钟（重试成功）：林船长在灯塔屿会合船队并启程返航星澜港码头，周匠师与陈巡官在码头完成补给艇交接。",
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_lin_navigator",
                            "expected_npc_revision": 2,
                            "action_kind": "travel",
                            "from_location": "灯塔屿",
                            "to_location": "星澜港码头",
                            "location": "星澜港码头",
                            "availability": "in_transit",
                            "current_activity": "引领香料船队首舰由灯塔屿沿安全水道返航星澜港码头",
                            "motivation": "已完成灯塔屿编队会合，趁潮平雾散领航返回星澜港码头",
                            "duration_minutes": 90,
                            "next_due_minute": 210,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_lin_beacon_signal"],
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_zhou_shipwright",
                            "expected_npc_revision": 2,
                            "action_kind": "task_progress",
                            "location": "星澜港码头",
                            "task_id": "task_zhou_beacon_boat",
                            "task_title": "灯塔补给艇防渗加固",
                            "task_stage": "完成两艘灯塔补给艇桐油封涂交付",
                            "task_progress_percent": 100,
                            "task_status": "completed",
                            "availability": "available",
                            "current_activity": "在星澜港码头向港务司交付加固完毕的灯塔补给艇",
                            "motivation": "按新计划落实应急修船任务",
                            "duration_minutes": 90,
                            "next_due_minute": 210,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_zhou_tung_oil_low"],
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_chen_inspector",
                            "expected_npc_revision": 1,
                            "action_kind": "stay",
                            "location": "星澜港码头",
                            "availability": "available",
                            "current_activity": "在星澜港码头栈桥指挥灯号手点亮进港引航信号灯",
                            "motivation": "留在码头栈桥值守，准备接应返航船队靠泊",
                            "duration_minutes": 90,
                            "next_due_minute": 210,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                        },
                    ],
                }
            elif minute == 210:
                reply = {
                    "instance_id": instance_id,
                    "mode": "advance",
                    "world_minute": 210,
                    "base_world_revision": rev,
                    "summary": "第 210 分钟（回头再遇见）：林船长率船队顺利抵达星澜港码头靠泊待命，苏会长与林船长安排当面交谈清点货单。",
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_lin_navigator",
                            "expected_npc_revision": 3,
                            "action_kind": "stay",
                            "location": "星澜港码头",
                            "availability": "available",
                            "current_activity": "已返抵星澜港码头栈桥系缆靠泊，整理本航次水文日志并接待问询",
                            "motivation": "船队已安全入港，留在码头栈桥完成引航交接",
                            "duration_minutes": 60,
                            "next_due_minute": 270,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_lin_beacon_signal"],
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_su_guildmaster",
                            "expected_npc_revision": 2,
                            "action_kind": "conversation_plan",
                            "location": "星澜港码头",
                            "target_npc_id": "npc_lin_navigator",
                            "meeting_location": "星澜港码头",
                            "conversation_topic": "核对首舰到港香料与桐油舱单并启动定量配售",
                            "availability": "available",
                            "current_activity": "在星澜港码头栈桥与归来的林船长核对首舰到港舱单",
                            "motivation": "林船长已率船队返港，立即上前交接舱单兑现配售新规",
                            "duration_minutes": 60,
                            "next_due_minute": 270,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                            "cause_fact_ids": ["fact_su_auction_notice"],
                        },
                    ],
                }
            else:
                raise AssertionError(f"Unexpected mock pulse request at minute {minute}")

            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(reply, ensure_ascii=False)},
                        }
                    ],
                    "usage": {"prompt_tokens": 480, "completion_tokens": 220, "total_tokens": 700},
                },
            )

        now_val = 1000.0

        def _clock() -> float:
            return now_val

        npc_http_client = OpenAINpcModelClient(
            base_url="https://mock.npc.wave4.invalid/v1",
            api_key="sk-wave4-npc-secret",
            model="npc-wave4-sim",
            transport=httpx.MockTransport(_mock_transport_handler),
        )
        pulse_client_adapter = NpcPulseModelClientAdapter(npc_http_client)

        router = PulseSessionRouter()
        router.bind_fake(instance_id, pulse_client_adapter)  # type: ignore[arg-type]
        activity_reg = ActivityLeaseRegistry(clock=_clock)
        activity_reg.touch(instance_id, now=now_val)

        read_cb, val_cb, app_cb = build_npc_pulse_callbacks(
            player_location_resolver=lambda _c, _iid: "星澜港码头",
        )
        worker = PulseWorker(
            _db_factory,
            session_router=router,
            activity_registry=activity_reg,
            snapshot_reader=read_cb,
            proposal_validator=val_cb,
            proposal_applier=app_cb,
            clock=_clock,
        )

        # Step 1: 推进已确认时间到第 60 分钟并运行 PulseWorker
        with _db_factory() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 60 WHERE id = ?", (instance_id,))
            inst_row = conn.execute("SELECT revision FROM world_instances WHERE id = ?", (instance_id,)).fetchone()
            rev_before_60 = int(inst_row[0])
            enqueue(
                conn,
                PulseTrigger(
                    instance_id=instance_id,
                    kind="advance",
                    trigger_adventure_id=adventure_id,
                    trigger_revision=1,
                    base_world_revision=rev_before_60,
                    target_world_minute=60,
                    idempotency_key="turn_commit_60",
                    now=now_val,
                ),
            )

        out_60 = asyncio.run(worker.run_once(instance_id=instance_id))
        with _db_factory() as conn:
            rev_after_60 = int(
                conn.execute("SELECT revision FROM world_instances WHERE id = ?", (instance_id,)).fetchone()[0]
            )
            lin_access_60 = check_npc_accessibility(
                conn, instance_id, "npc_lin_navigator", player_location="星澜港码头", world_minute=60
            )
            zhou_access_60 = check_npc_accessibility(
                conn, instance_id, "npc_zhou_shipwright", player_location="星澜港码头", world_minute=60
            )
            su_npc_60 = get_npc(conn, instance_id, "npc_su_guildmaster")

        # Step 2: 推进已确认时间到第 120 分钟，测试首次 502 失败退避重试与二次成功
        now_val = 1060.0
        activity_reg.touch(instance_id, now=now_val)
        with _db_factory() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 120 WHERE id = ?", (instance_id,))
            enqueue(
                conn,
                PulseTrigger(
                    instance_id=instance_id,
                    kind="advance",
                    trigger_adventure_id=adventure_id,
                    trigger_revision=2,
                    base_world_revision=rev_after_60,
                    target_world_minute=120,
                    idempotency_key="turn_commit_120",
                    now=now_val,
                ),
            )

        out_120_fail = asyncio.run(worker.run_once(instance_id=instance_id))
        # 推进时钟跨过指数退避窗口后再次执行重试
        now_val = 1100.0
        activity_reg.touch(instance_id, now=now_val)
        out_120_retry_ok = asyncio.run(worker.run_once(instance_id=instance_id))

        # Step 3: 先离线（150-180 分钟推进时间不发请求、不积压历史）后恢复在线
        now_val = 1120.0
        with _db_factory() as conn:
            set_pulse_activity(
                conn,
                instance_id,
                False,
                session_router=router,
                activity_registry=activity_reg,
                now=now_val,
            )
            conn.execute("UPDATE world_instances SET world_minute = 180 WHERE id = ?", (instance_id,))
            from app.m3.pulse.worker import schedule_on_turn_commit

            scheduled_while_offline = schedule_on_turn_commit(
                conn,
                instance_id=instance_id,
                adventure_id=adventure_id,
                adventure_revision=3,
                world_minute=180,
                session_router=router,
                activity_registry=activity_reg,
                now=now_val,
            )
            now_val = 1150.0
            resumed_status = set_pulse_activity(
                conn,
                instance_id,
                True,
                session_router=router,
                activity_registry=activity_reg,
                now=now_val,
            )
            anchor_row = conn.execute(
                "SELECT resume_anchor_minute FROM pulse_instance_configs WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
            resume_anchor_minute = int(anchor_row[0])

        # Step 4: 第 210 分钟推进时间，林船长返回星澜港码头（回头再遇见）
        now_val = 1200.0
        activity_reg.touch(instance_id, now=now_val)
        with _db_factory() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 210 WHERE id = ?", (instance_id,))
            rev_before_210 = int(
                conn.execute("SELECT revision FROM world_instances WHERE id = ?", (instance_id,)).fetchone()[0]
            )
            enqueue(
                conn,
                PulseTrigger(
                    instance_id=instance_id,
                    kind="advance",
                    trigger_adventure_id=adventure_id,
                    trigger_revision=4,
                    base_world_revision=rev_before_210,
                    target_world_minute=210,
                    idempotency_key="turn_commit_210",
                    now=now_val,
                ),
            )

        out_210 = asyncio.run(worker.run_once(instance_id=instance_id))
        with _db_factory() as conn:
            lin_access_210 = check_npc_accessibility(
                conn, instance_id, "npc_lin_navigator", player_location="星澜港码头", world_minute=210
            )
            narration_ctx_210 = build_adventure_narration_npc_context(
                conn, instance_id, player_location="星澜港码头", world_minute=210
            )
            exported_snap = snapshot(conn, instance_id)
            ensure_instance_exists(
                conn,
                "inst_wave4_harbor_migrated",
                world_id=world_id,
                adventure_id="adv_wave4_harbor_migrated",
                world_minute=210,
                revision=int(exported_snap["world_revision"]),
            )
            restore(conn, "inst_wave4_harbor_migrated", exported_snap, world_id=world_id)
            migrated_lin = get_npc(conn, "inst_wave4_harbor_migrated", "npc_lin_navigator")
            migrated_zhou = get_npc(conn, "inst_wave4_harbor_migrated", "npc_zhou_shipwright")
            migrated_logs = list_npc_action_log(conn, "inst_wave4_harbor_migrated")

        return {
            "seed": seed_res,
            "step_60": {
                "worker_outcome": out_60["outcome"] if out_60 else None,
                "rev_before": rev_before_60,
                "rev_after": rev_after_60,
                "lin_accessibility": lin_access_60,
                "zhou_accessibility": zhou_access_60,
                "su_action_kind": su_npc_60["current_action_kind"],
            },
            "step_120_retry": {
                "first_attempt_outcome": out_120_fail["outcome"] if out_120_fail else None,
                "first_attempt_status": out_120_fail["job"]["status"] if out_120_fail and out_120_fail.get("job") else None,
                "second_attempt_outcome": out_120_retry_ok["outcome"] if out_120_retry_ok else None,
                "second_attempt_world_revision": out_120_retry_ok.get("new_world_revision") if out_120_retry_ok else None,
            },
            "step_180_offline_resume": {
                "scheduled_while_offline": scheduled_while_offline,
                "resumed_state": resumed_status["state"],
                "resume_anchor_minute": resume_anchor_minute,
            },
            "step_210_re_encounter": {
                "worker_outcome": out_210["outcome"] if out_210 else None,
                "lin_accessibility": lin_access_210,
                "narration_context": narration_ctx_210,
            },
            "migrated_continuation": {
                "lin": migrated_lin,
                "zhou": migrated_zhou,
                "action_log_count": len(migrated_logs),
            },
            "http_log": http_log,
        }
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
