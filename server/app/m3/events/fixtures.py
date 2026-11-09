"""M3.1 / M3.5 北境贸易（Northern Trade）可复现因果链 Fixture 与集成测试数据包。

契约要求（`docs/collaboration/M3_WAVE1_GEMINI_MARCIN.md` §3 & §4、`docs/M3_DESIGN_DRAFT.md` §17）：
1. 交付一个北境贸易 fixture：一座北境小镇（霜木镇）、两座邻近城市（寒铁城、白河城）、
   两个国家（北境王国、洛兰公国）、一个大型商会（星辉大商会）、十名具有基本身份的 NPC。
2. 初始生成 5 条主要事件线（本地商业竞争、区域贸易路线变化、邻近城市资源争夺、国家外交问题、跨国商会扩张计划）。
3. 完整演进因果链：
   邻国提高进口税率 -> 商队调整运输路线 -> 本地原材料供应减少 & 商人调整采购计划 -> 本地铁料挂牌价受影响。
   每个后果都绑定明确的 `source_event_id` 与 `source_event_revision`，且不触碰玩家个人账本。
4. 提供可直接供 DeepSeekHarness、Gemini Li、Gemini Ma 集成测试消费的输入和期望输出。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .service import (
    apply_proposal,
    ensure_instance_exists,
    list_consequences,
    list_events,
    snapshot,
    trace_causality,
)
from .types import CanonSourceItem

NORTHERN_TRADE_WORLD_ID = "world_northern_frontier_001"
NORTHERN_TRADE_INSTANCE_ID = "inst_northern_frontier_001"

NORTHERN_TRADE_NPCS: tuple[dict[str, str], ...] = (
    {"npc_id": "npc_han_shopkeeper", "name": "韩掌柜", "role": "霜木镇铁料与矿产原料商", "location": "霜木镇"},
    {"npc_id": "npc_hawk_blacksmith", "name": "霍克铁匠", "role": "霜木镇老铁匠坊主", "location": "霜木镇"},
    {"npc_id": "npc_marta_innkeeper", "name": "玛塔老板娘", "role": "霜木镇灰鹿酒馆掌柜", "location": "霜木镇"},
    {"npc_id": "npc_eilin_mayor", "name": "艾琳镇长", "role": "霜木镇执政官", "location": "霜木镇"},
    {"npc_id": "npc_renn_captain", "name": "雷恩队长", "role": "北境护路队队长", "location": "霜木镇"},
    {"npc_id": "npc_garret_caravan", "name": "加雷特首领", "role": "灰鬃商队总领队", "location": "北境主商道"},
    {"npc_id": "npc_linn_envoy", "name": "林恩特使", "role": "星辉大商会北境督办", "location": "寒铁城"},
    {"npc_id": "npc_victor_warden", "name": "维克多矿监", "role": "寒铁城矿务司主管", "location": "寒铁城"},
    {"npc_id": "npc_serena_councillor", "name": "瑟蕾娜议员", "role": "白河城贸易市政官", "location": "白河城"},
    {"npc_id": "npc_claude_commissioner", "name": "克劳德专员", "role": "洛兰公国灰岩边关税务官", "location": "灰岩边关"},
)

NORTHERN_TRADE_CANON_SOURCES: tuple[CanonSourceItem, ...] = (
    CanonSourceItem(
        source_id="chunk:north_geography_01",
        kind="chunk",
        title="北境地理与霜木镇概况",
        text=(
            "北境王国南端与洛兰公国接壤。霜木镇位于北境山麓要冲，镇内工坊依赖从寒铁城和洛兰边境运入的生铁与焦炭。"
            "邻近两座大城分别为盛产矿石的寒铁城与水路发达的白河城。"
        ),
    ),
    CanonSourceItem(
        source_id="chunk:north_trade_routes_02",
        kind="chunk",
        title="北境主商道与灰鬃商队",
        text=(
            "灰鬃商队常年往返灰岩边关、寒铁城与霜木镇。若灰岩边关赋税加重或主商道受阻，"
            "商队可改走东侧白河谷地水陆联运线，但绕行将导致霜木镇大宗铁料与精煤交货延迟。"
        ),
    ),
    CanonSourceItem(
        source_id="entry:faction_starlight_guild_01",
        kind="entry",
        title="星辉大商会",
        text=(
            "横跨北境王国与洛兰公国的巨型商业辛迪加，在寒铁城设有北境总栈，并在霜木镇开设分号，"
            "试图通过集中仓储与长期包销协议排挤本地独立手工业工坊与零散原料商。"
        ),
    ),
    CanonSourceItem(
        source_id="entry:diplomacy_loran_tariff_02",
        kind="entry",
        title="北境与洛兰边贸协定",
        text=(
            "北境王国与洛兰公国的五年边贸减税协定即将到期。洛兰公国财政吃紧，灰岩边关税务官克劳德多次提议"
            "对过境生铁、焦炭与成品铁器加征三成进口关税。"
        ),
    ),
    CanonSourceItem(
        source_id="entry:conflict_iron_vein_03",
        kind="entry",
        title="寒铁山脉新矿脉归属争端",
        text=(
            "寒铁城矿务司与白河城市政厅近期在交界山脊发现富铁矿脉，双方均声称拥有采矿特许权，矿石外运配额因此陷入僵局。"
        ),
    ),
)

NORTHERN_TRADE_ALLOWED_SOURCE_IDS: frozenset[str] = frozenset(
    s.source_id for s in NORTHERN_TRADE_CANON_SOURCES
)


def build_northern_trade_seed_proposal(
    *,
    instance_id: str = NORTHERN_TRADE_INSTANCE_ID,
    world_minute: int = 0,
) -> dict[str, Any]:
    """生成北境贸易场景的 5 条初始主要事件线提议（对应 M3 总纲第 17 节）。"""
    return {
        "instance_id": instance_id,
        "mode": "seed",
        "summary": "北境小镇（霜木镇）开局初始五条主要事件线。",
        "operations": [
            {
                "op": "create",
                "kind": "thread",
                "title": "霜木镇本地商业竞争",
                "description": "星辉大商会霜木镇分号向本地工坊压低铁器收购价，韩掌柜与霍克铁匠商议联合稳价。",
                "location": "霜木镇",
                "scope": "local",
                "participants": ["韩掌柜", "霍克铁匠", "林恩特使"],
                "stage": "local_bidding",
                "status": "active",
                "next_update_minute": world_minute + 180,
                "cause_event_ids": [],
                "source_ids": ["chunk:north_geography_01", "entry:faction_starlight_guild_01"],
                "reason": "开局初始化：本地工坊与商会分号的商业竞争主线。",
            },
            {
                "op": "create",
                "kind": "thread",
                "title": "北境区域贸易路线变化",
                "description": "灰鬃商队正在评估北境主商道与白河谷地绕行线的运输成本与通关风险。",
                "location": "北境主商道",
                "scope": "regional",
                "participants": ["加雷特首领", "韩掌柜", "玛塔老板娘"],
                "stage": "route_evaluation",
                "status": "active",
                "next_update_minute": world_minute + 120,
                "cause_event_ids": [],
                "source_ids": ["chunk:north_trade_routes_02"],
                "reason": "开局初始化：区域贸易路线变化主线。",
            },
            {
                "op": "create",
                "kind": "thread",
                "title": "寒铁城与白河城矿脉资源争夺",
                "description": "两座邻近城市围绕交界富铁矿脉的采掘特许权僵持不下，矿石外运审批放缓。",
                "location": "寒铁山脉交界",
                "scope": "regional",
                "participants": ["维克多矿监", "瑟蕾娜议员"],
                "stage": "mining_standoff",
                "status": "active",
                "next_update_minute": world_minute + 240,
                "cause_event_ids": [],
                "source_ids": ["entry:conflict_iron_vein_03"],
                "reason": "开局初始化：邻近城市资源争夺主线。",
            },
            {
                "op": "create",
                "kind": "thread",
                "title": "北境王国与洛兰公国边贸外交问题",
                "description": "洛兰公国灰岩边关税务署酝酿在新季度大幅上调进口商品与过境生铁税率。",
                "location": "灰岩边关",
                "scope": "international",
                "participants": ["克劳德专员", "艾琳镇长"],
                "stage": "tariff_review",
                "status": "active",
                "next_update_minute": world_minute + 120,
                "cause_event_ids": [],
                "source_ids": ["entry:diplomacy_loran_tariff_02"],
                "reason": "开局初始化：两国边贸关税外交主线。",
            },
            {
                "op": "create",
                "kind": "thread",
                "title": "星辉大商会跨国仓储扩张计划",
                "description": "星辉大商会筹划收购白河城至霜木镇沿线货栈，建立跨国集中配货网络。",
                "location": "寒铁城·星辉总栈",
                "scope": "national",
                "participants": ["林恩特使", "瑟蕾娜议员"],
                "stage": "depot_expansion",
                "status": "active",
                "next_update_minute": world_minute + 300,
                "cause_event_ids": [],
                "source_ids": ["entry:faction_starlight_guild_01"],
                "reason": "开局初始化：跨国商会扩张计划主线。",
            },
        ],
    }


def run_northern_trade_causal_chain(
    conn: sqlite3.Connection,
    instance_id: str = NORTHERN_TRADE_INSTANCE_ID,
) -> dict[str, Any]:
    """在 SQLite 连接上完整执行北境贸易三步因果演进链，返回每一步的输入与实际产出快照。

    演进步骤：
    - Step 0 (`world_minute=0`)：种下 5 条初始主线（`evt_north_thread_1` .. `evt_north_thread_5`）。
    - Step 1 (`world_minute=120`)：邻国洛兰公国正式提高进口商品税率（推进外交主线 `evt_north_thread_4` 至 rev.2）。
    - Step 2 (`world_minute=240`)：受关税上调（`evt_north_thread_4`）影响，灰鬃商队改变运输路线（推进贸易路线主线 `evt_north_thread_2` 至 rev.2，并衍生临时事件 `evt_north_inc_1`「灰鬃商队改走白河绕行线」）。
    - Step 3 (`world_minute=360`)：商队改道临时事件 `evt_north_inc_1` 完结（`status='completed'`），直接导致本地原材料供应减少与韩掌柜调整采购计划（衍生临时事件 `evt_north_inc_2`「霜木镇原材料短缺与商人采购调整」，记录本地供应与市场价格影响）。
    """
    ensure_instance_exists(
        conn,
        instance_id,
        world_id=NORTHERN_TRADE_WORLD_ID,
        adventure_id="adv_northern_frontier_001",
        world_minute=0,
        revision=0,
    )

    id_seq = iter(
        [
            "evt_north_thread_1",
            "evt_north_thread_2",
            "evt_north_thread_3",
            "evt_north_thread_4",
            "evt_north_thread_5",
            "evt_north_inc_1",
            "evt_north_inc_2",
        ]
    )
    csq_seq = iter(
        [
            "csq_north_01_tariff",
            "csq_north_02_route",
            "csq_north_03_caravan",
            "csq_north_04_supply",
            "csq_north_05_merchant",
            "csq_north_06_price",
        ]
    )

    # Step 0: Seed 5 initial major threads at world_minute = 0
    seed_input = build_northern_trade_seed_proposal(instance_id=instance_id, world_minute=0)
    seed_result = apply_proposal(
        conn,
        instance_id,
        seed_input,
        world_minute=0,
        allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        id_factory=lambda: next(id_seq),
        consequence_id_factory=lambda: next(csq_seq),
    )

    # Step 1 (world_minute = 120): 邻国提高进口商品税率
    conn.execute("UPDATE world_instances SET world_minute = 120, revision = 1 WHERE id = ?", (instance_id,))
    step1_input: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": "advance",
        "summary": "洛兰公国正式实施灰岩边关新关税令，提高北境进口商品与生铁过境税率。",
        "operations": [
            {
                "op": "update",
                "event_id": "evt_north_thread_4",
                "expected_event_revision": 1,
                "stage": "tariff_enforced",
                "status": "active",
                "description": "洛兰公国灰岩边关正式将生铁、焦炭与器具进口税率上调三成，过境商队通关成本陡增。",
                "next_update_minute": 300,
                "consequences": [
                    {
                        "domain": "politics",
                        "target": "灰岩边关·进口关税",
                        "summary": "洛兰公国对北境生铁与大宗原料加征 30% 进口关税。",
                        "impact_type": "direct",
                    }
                ],
                "reason": "邻国提高进口商品税率生效。",
            }
        ],
    }
    step1_result = apply_proposal(
        conn,
        instance_id,
        step1_input,
        world_minute=120,
        allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        id_factory=lambda: next(id_seq),
        consequence_id_factory=lambda: next(csq_seq),
    )

    # Step 2 (world_minute = 240): 商队改变运输路线（引用 evt_north_thread_4 为起因，并衍生临时事件 evt_north_inc_1）
    conn.execute("UPDATE world_instances SET world_minute = 240, revision = 2 WHERE id = ?", (instance_id,))
    step2_input: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": "advance",
        "summary": "受灰岩边关加税影响，灰鬃商队避开主商道改走白河谷地绕行线。",
        "operations": [
            {
                "op": "update",
                "event_id": "evt_north_thread_2",
                "expected_event_revision": 1,
                "stage": "caravan_rerouted",
                "status": "active",
                "description": "为规避灰岩边关高额新税，灰鬃商队宣布暂停直达霜木镇的主商道班次，改道白河谷地。",
                "next_update_minute": 420,
                "cause_event_ids": ["evt_north_thread_4"],
                "consequences": [
                    {
                        "domain": "trade_route",
                        "target": "北境主商道",
                        "summary": "北境主商道直达货运班次削减，主力商队改走白河谷地水陆线。",
                        "impact_type": "direct",
                    }
                ],
                "reason": "因邻国上调关税，商队改变运输路线。",
            },
            {
                "op": "create",
                "kind": "incident",
                "title": "灰鬃商队改走白河绕行线",
                "description": "加雷特首领率领的本月灰鬃生铁船队改停白河城码头转运，原本发往霜木镇的生铁与精煤批次延误。",
                "location": "白河谷地商道",
                "scope": "regional",
                "participants": ["加雷特首领", "韩掌柜"],
                "stage": "reroute_in_transit",
                "status": "active",
                "next_update_minute": 360,
                "cause_event_ids": ["evt_north_thread_4", "evt_north_thread_2"],
                "source_ids": ["chunk:north_trade_routes_02"],
                "consequences": [
                    {
                        "domain": "trade_route",
                        "target": "霜木镇·月度生铁班车",
                        "summary": "发往霜木镇的月度生铁与焦炭车队因改道推迟抵达。",
                        "impact_type": "direct",
                    }
                ],
                "reason": "由商队改道主线衍生出的短期运输调整临时事件。",
            },
        ],
    }
    step2_result = apply_proposal(
        conn,
        instance_id,
        step2_input,
        world_minute=240,
        allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        id_factory=lambda: next(id_seq),
        consequence_id_factory=lambda: next(csq_seq),
    )

    # Step 3 (world_minute = 360): 商队改道事件完成 -> 本地原材料供应减少 & 商人调整采购计划 -> 本地铁料挂牌价上涨
    conn.execute("UPDATE world_instances SET world_minute = 360, revision = 3 WHERE id = ?", (instance_id,))
    step3_input: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": "advance",
        "summary": "改道商队在白河城被截留分销，霜木镇本地原材料供应减少，原料商韩掌柜调整采购计划并上调铁料挂牌价。",
        "operations": [
            {
                "op": "update",
                "event_id": "evt_north_inc_1",
                "expected_event_revision": 1,
                "stage": "reroute_completed",
                "status": "completed",
                "description": "灰鬃商队在白河城完成转运卸货，仅三成生铁余量继续运往霜木镇。",
                "next_update_minute": None,
                "consequences": [
                    {
                        "domain": "regional_supply",
                        "target": "霜木镇·生铁与焦炭供应",
                        "summary": "霜木镇本旬到港生铁与精煤供应量锐减七成。",
                        "impact_type": "direct",
                    }
                ],
                "reason": "商队改道运输结算完成，触发本地供应缩减。",
            },
            {
                "op": "create",
                "kind": "incident",
                "title": "霜木镇原材料短缺与商人采购调整",
                "description": "因商队改道导致本地生铁供应减少，原料商韩掌柜缩减零散工坊配额、转向寒铁城紧急询价，并上调本地铁料挂牌价。",
                "location": "霜木镇",
                "scope": "local",
                "participants": ["韩掌柜", "霍克铁匠"],
                "stage": "quota_tightened",
                "status": "active",
                "next_update_minute": 540,
                "cause_event_ids": ["evt_north_inc_1", "evt_north_thread_2"],
                "source_ids": ["chunk:north_geography_01"],
                "consequences": [
                    {
                        "domain": "npc_inventory",
                        "target": "韩掌柜铁料铺·采购与配额计划",
                        "summary": "韩掌柜暂停大宗赊销，调整采购计划改向寒铁城零批调货。",
                        "impact_type": "direct",
                    },
                    {
                        "domain": "market_price",
                        "target": "霜木镇·生铁与精煤挂牌价",
                        "summary": "受供应短缺影响，霜木镇生铁与精煤市场参考挂牌价上浮。",
                        "impact_type": "direct",
                    },
                ],
                "reason": "商队改道事件合法完成后，衍生本地原材料短缺与商人采购计划调整临时事件。",
            },
        ],
    }
    step3_result = apply_proposal(
        conn,
        instance_id,
        step3_input,
        world_minute=360,
        allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        id_factory=lambda: next(id_seq),
        consequence_id_factory=lambda: next(csq_seq),
    )

    causal_trace = trace_causality(conn, instance_id, "evt_north_inc_2")
    final_snapshot = snapshot(conn, instance_id)

    return {
        "world_id": NORTHERN_TRADE_WORLD_ID,
        "instance_id": instance_id,
        "npcs": list(NORTHERN_TRADE_NPCS),
        "canon_sources": [
            {
                "source_id": s.source_id,
                "kind": s.kind,
                "title": s.title,
                "text": s.text,
            }
            for s in NORTHERN_TRADE_CANON_SOURCES
        ],
        "steps": [
            {"step": 0, "world_minute": 0, "input": seed_input, "output": seed_result},
            {"step": 1, "world_minute": 120, "input": step1_input, "output": step1_result},
            {"step": 2, "world_minute": 240, "input": step2_input, "output": step2_result},
            {"step": 3, "world_minute": 360, "input": step3_input, "output": step3_result},
        ],
        "causal_trace_for_local_shortage": causal_trace,
        "final_consequences": list_consequences(conn, instance_id),
        "final_events": list_events(conn, instance_id)["items"],
        "final_snapshot": final_snapshot,
    }


def get_northern_trade_integration_bundle() -> dict[str, Any]:
    """在内存 SQLite 中运行北境贸易完整链路并导出可直接用于集成测试的输入与期望输出包。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        return run_northern_trade_causal_chain(conn, NORTHERN_TRADE_INSTANCE_ID)
    finally:
        conn.close()
