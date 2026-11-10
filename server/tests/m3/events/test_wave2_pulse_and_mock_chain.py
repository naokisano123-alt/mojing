"""M3 Wave 2 真实模型事件推演、PulseWorker 回调、Canon 预算挑选、安全校验与 Mock 多步因果链测试。

覆盖 `docs/collaboration/M3_WAVE2_GEMINI_MARCIN.md` 与 `docs/contracts/M3_WAVE2_API.md` 全部验收要求：
1. `PulseWorker` 三回调（`read_snapshot`, `validate_proposal`, `apply_proposal`）接入与 `mode`/`kind`、`revision`/`world_revision` 映射；
2. Seed 按稳定顺序从当前世界有效 Canon 挑选资料（设定条目与分块各有上限与总字符预算），生成 3～6 条可追溯活跃主线，无授权资料时给出可展示阻塞原因（`PULSE_SOURCE_REQUIRED`），且绝不把静态示例标题套进用户的世界；
3. Advance 仅传到期活跃事件及最小因果邻域、允许的 Canon ID 与节选；无到期事件或到期时间超过已确认分钟时返回跳过；保留事件寿命、临时事件上限（含快照外存量计数）与重大历史变化前置条件；
4. 严格输出结构（`PULSE_EVENT_OUTPUT_SCHEMA`）与提示词构建（`build_pulse_event_prompts`），拦截伪造 Canon、跨实例 ID、错误旧版本（事件版本与世界实例版本）、因果环、玩家账本字段与伪造时间，并在应用时重新核对实际数据库状态；
5. 使用 `httpx.MockTransport` 回包跑通「邻国加税 → 商队改道 → 本地供给变化」多步链，断言来源、版本、时间与因果，并验证并发冲突下 SAVEPOINT 完整回滚、绝无半笔状态。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator
import unittest

from app.m3.events import (
    ArbitraryPathForbiddenError,
    CausalCycleError,
    CrossInstanceReferenceError,
    EventRevisionConflictError,
    EventValidationError,
    FakeEventModelClient,
    NORTHERN_TRADE_INSTANCE_ID,
    NORTHERN_TRADE_WORLD_ID,
    PROMPT_EXAMPLES,
    PULSE_EVENT_OUTPUT_SCHEMA,
    PlayerLedgerProtectedError,
    PulseSourceRequiredError,
    SourceFabricatedError,
    WorldRevisionConflictError,
    apply_proposal,
    build_fake_pulse_generator,
    build_pulse_event_prompts,
    build_seed_request_from_snapshot,
    ensure_events_schema,
    ensure_instance_exists,
    generate_initial_situation,
    get_event,
    get_wave2_mock_causal_chain_bundle,
    list_consequences,
    list_event_revisions,
    list_events,
    read_snapshot,
    run_wave2_mock_causal_chain,
    seed_northern_trade_world_canon,
    select_canon_sources,
    validate_proposal,
)
from app.m3.pulse.budget import configure_instance_pulse
from app.m3.pulse.client import FakePulseModelClient
from app.m3.pulse.queue import enqueue
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.worker import PulseWorker


class TestWave2PulseAndMockChain(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_events_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    async def test_seed_canon_selection_budget_blocking_and_no_static_titles(self) -> None:
        """验证 Seed Canon 稳定排序、条数上限、字符预算、无资料阻塞原因，以及用户世界不套用静态示例标题。"""
        # 1. 创建空白世界实例（仅有未确认的 draft 设定条目和已删除文档） -> 触发 PULSE_SOURCE_REQUIRED 阻塞
        seed_northern_trade_world_canon(
            self.conn,
            world_id="world_empty_canon",
            instance_id="inst_empty_canon",
            adventure_id="adv_empty_canon",
        )
        self.conn.execute("DELETE FROM chunks WHERE world_id = 'world_empty_canon'")
        self.conn.execute("UPDATE lore_entries SET status = 'draft' WHERE world_id = 'world_empty_canon'")

        blocked_snap = read_snapshot(
            self.conn,
            "inst_empty_canon",
            {"kind": "seed", "base_world_revision": 0, "target_world_minute": 0},
        )
        self.assertEqual(blocked_snap["status"], "blocked")
        self.assertTrue(blocked_snap["blocked"])
        self.assertTrue(blocked_snap["skip"])
        self.assertEqual(blocked_snap["reason_code"], "PULSE_SOURCE_REQUIRED")
        self.assertIn("设定条目", blocked_snap["reason_message"])

        with self.assertRaises(PulseSourceRequiredError) as cm_req:
            build_seed_request_from_snapshot(blocked_snap)
        self.assertEqual(cm_req.exception.code, "PULSE_SOURCE_REQUIRED")

        with self.assertRaises(PulseSourceRequiredError) as cm_val:
            validate_proposal(
                {"kind": "seed", "operations": []},
                {"instance_id": "inst_empty_canon", "kind": "seed", "snapshot": blocked_snap},
            )
        self.assertEqual(cm_val.exception.code, "PULSE_SOURCE_REQUIRED")

        # 2. 创建用户自定义世界（云海浮空群岛世界），测试稳定排序、条数上限与字符预算
        self.conn.execute(
            """
            INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at)
            VALUES ('world_sky_archipelago', '苍穹浮岛群', '由风帆空艇与浮石矿脉连接的云海群岛世界。', '2026-11-09T00:00:00Z', '2026-11-09T00:00:00Z', NULL)
            """
        )
        ensure_instance_exists(
            self.conn,
            "inst_sky_01",
            world_id="world_sky_archipelago",
            adventure_id="adv_sky_01",
            world_minute=0,
            revision=3,
        )
        entries_data = [
            ("ent_char_01", "character", "云帆船长洛羽", "星风号空艇船长，常年往返白鸥港与浮石山。", "confirmed"),
            ("ent_rule_01", "world_rule", "浮石升力法则", "云海群岛依靠高纯度浮石抵抗重力，风暴季浮石活性会周期性衰减。", "confirmed"),
            ("ent_fac_01", "faction", "天风航运公会", "掌控白鸥港至千帆城空艇航线的航运同盟，近期拟上调浮石护航费。", "confirmed"),
            ("ent_geo_01", "geography", "白鸥港与千帆城", "白鸥港位于外环风带，千帆城坐落于中央主岛，两城贸易依赖云桥航道。", "confirmed"),
            ("ent_draft_99", "world_rule", "未确认草稿设定", "这条草稿不应进入有效 Canon。", "draft"),
        ]
        for eid, etype, etitle, esummary, estatus in entries_data:
            self.conn.execute(
                """
                INSERT INTO lore_entries (id, world_id, type, title, summary, status, revision, created_at, updated_at, deleted_at)
                VALUES (?, 'world_sky_archipelago', ?, ?, ?, ?, 1, '2026-11-09T00:00:00Z', '2026-11-09T00:00:00Z', NULL)
                """,
                (eid, etype, etitle, esummary, estatus),
            )

        self.conn.execute(
            "INSERT INTO documents (id, world_id, filename, status, created_at, deleted_at) VALUES ('doc_a', 'world_sky_archipelago', '群岛航路志.md', 'ready', '2026-11-09T01:00:00Z', NULL)"
        )
        self.conn.execute(
            "INSERT INTO documents (id, world_id, filename, status, created_at, deleted_at) VALUES ('doc_b', 'world_sky_archipelago', '浮石开采录.md', 'ready', '2026-11-09T02:00:00Z', NULL)"
        )
        chunks_data = [
            ("chk_a0", "doc_a", 0, "第一章 云桥航道", "云桥航道连接白鸥港与千帆城，每年季风转换时天风航运公会都会重编船队班次。" + "航路细节描述。" * 30),
            ("chk_a1", "doc_a", 1, "第二章 外环暗流", "外环风带常有雷云暗流，空艇需在赤岩灯塔补给浮石燃料。"),
            ("chk_b0", "doc_b", 0, "卷一 浮石矿脉", "浮石山深层矿脉由千帆城矿务院监管，提炼后的晶核优先供应主力空艇。"),
        ]
        for cid, doc_id, c_idx, heading, ctext in chunks_data:
            self.conn.execute(
                "INSERT INTO chunks (id, document_id, world_id, chunk_index, heading_path, text) VALUES (?, ?, 'world_sky_archipelago', ?, ?, ?)",
                (cid, doc_id, c_idx, heading, ctext),
            )

        sel = select_canon_sources(
            self.conn,
            "world_sky_archipelago",
            max_entries=3,
            max_chunks=2,
            max_entry_chars=120,
            max_chunk_chars=80,
            char_budget=350,
        )
        self.assertEqual(
            sel["selected_source_ids"],
            ["entry:ent_rule_01", "entry:ent_geo_01", "entry:ent_fac_01", "chunk:chk_a0", "chunk:chk_b0"],
        )
        self.assertNotIn("entry:ent_draft_99", sel["all_valid_source_ids"])
        self.assertLessEqual(sel["budget"]["used_chars"], 350)
        chk_a0_item = next(c for c in sel["canon_sources"] if c["source_id"] == "chunk:chk_a0")
        self.assertTrue(chk_a0_item["truncated"])
        self.assertLessEqual(chk_a0_item["char_count"], 80)

        # 3. 验证 read_snapshot 与动态初始主线生成：绝不把「霜木镇」「旅人交换线索」等静态标题套进用户的云海群岛世界
        seed_snap = read_snapshot(
            self.conn,
            "inst_sky_01",
            {"kind": "seed"},
        )
        self.assertEqual(seed_snap["status"], "ready")
        self.assertEqual(seed_snap["world_revision"], 3)
        self.assertEqual(seed_snap["base_world_revision"], 3)
        self.assertEqual(seed_snap["mode"], "seed")
        self.assertEqual(seed_snap["kind"], "seed")

        fake_client = FakeEventModelClient()
        seed_req = build_seed_request_from_snapshot(seed_snap)
        validated_seed = await generate_initial_situation(seed_req, fake_client)
        self.assertGreaterEqual(len(validated_seed.operations), 3)
        self.assertLessEqual(len(validated_seed.operations), 6)

        static_forbidden_terms = ("霜木镇", "寒铁城", "洛兰公国", "旅人交换线索", "商队核对消息")
        for op in validated_seed.operations:
            for term in static_forbidden_terms:
                self.assertNotIn(term, op.title)
                self.assertNotIn(term, op.description)
            self.assertTrue(set(op.source_ids or ()) <= set(seed_snap["allowed_source_ids"]))

    def test_advance_due_events_minimal_causal_neighborhood_and_skip_conditions(self) -> None:
        """验证 Advance 只传到期活跃事件与最小因果邻域，无到期或超前时间返回跳过，并统计快照外活跃临时事件上限。"""
        seed_northern_trade_world_canon(
            self.conn,
            world_id=NORTHERN_TRADE_WORLD_ID,
            instance_id=NORTHERN_TRADE_INSTANCE_ID,
        )
        id_iter = iter(["evt_parent", "evt_unrelated_future", "evt_stalled", "evt_due_child", "evt_outside_inc"])
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "seed",
                "operations": [
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "父事件：灰岩边关关税重审",
                        "description": "边关重审过境税率。",
                        "location": "灰岩边关",
                        "scope": "international",
                        "participants": ["克劳德专员"],
                        "stage": "review",
                        "status": "active",
                        "next_update_minute": 60,
                        "cause_event_ids": [],
                        "source_ids": ["entry:diplomacy_loran_tariff_02"],
                    },
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "无关远期主线：寒铁山脉勘探",
                        "description": "远期矿脉勘探。",
                        "location": "寒铁山脉",
                        "scope": "regional",
                        "participants": ["维克多矿监"],
                        "stage": "survey",
                        "status": "active",
                        "next_update_minute": 500,
                        "cause_event_ids": [],
                        "source_ids": ["entry:conflict_iron_vein_03"],
                    },
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "停滞主线：星辉仓储谈判",
                        "description": "仓储谈判。",
                        "location": "寒铁城",
                        "scope": "national",
                        "participants": ["林恩特使"],
                        "stage": "init",
                        "status": "active",
                        "next_update_minute": 60,
                        "cause_event_ids": [],
                        "source_ids": ["entry:faction_starlight_guild_01"],
                    },
                ],
            },
            world_minute=0,
            id_factory=lambda: next(id_iter),
        )

        # 1. 在 world_minute=30 时（最早事件到期时间 60 > 已确认分钟 30），read_snapshot 应返回 skipped
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 30, revision = 1 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        skip_snap_early = read_snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID, {"kind": "advance"})
        self.assertEqual(skip_snap_early["status"], "skipped")
        self.assertTrue(skip_snap_early["skip"])
        self.assertEqual(skip_snap_early["reason_code"], "DUE_TIME_EXCEEDS_CONFIRMED_MINUTE")
        self.assertEqual(skip_snap_early["earliest_next_update_minute"], 60)

        # 2. 若 job 请求的 target_world_minute (120) 超过当前已确认分钟 (30)，立即返回 skipped
        skip_snap_ahead = read_snapshot(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {"kind": "advance", "target_world_minute": 120},
        )
        self.assertEqual(skip_snap_ahead["status"], "skipped")
        self.assertTrue(skip_snap_ahead["skip"])
        self.assertEqual(skip_snap_ahead["reason_code"], "DUE_TIME_EXCEEDS_CONFIRMED_MINUTE")

        # 3. 推进到 world_minute=60
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60, revision = 2 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_parent",
                        "expected_event_revision": 1,
                        "stage": "enforced",
                        "next_update_minute": 300,
                    },
                    {
                        "op": "update",
                        "event_id": "evt_stalled",
                        "expected_event_revision": 1,
                        "status": "stalled",
                        "next_update_minute": 120,
                    },
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "子事件：灰鬃商队评估改道",
                        "description": "因父事件加税评估改道。",
                        "location": "北境主商道",
                        "scope": "regional",
                        "participants": ["加雷特首领"],
                        "stage": "evaluating",
                        "status": "active",
                        "next_update_minute": 120,
                        "cause_event_ids": ["evt_parent"],
                        "source_ids": ["chunk:north_trade_routes_02"],
                    },
                    {
                        "op": "create",
                        "kind": "incident",
                        "title": "无关存量未到期临时事件",
                        "description": "镇内修缮道路。",
                        "location": "霜木镇",
                        "scope": "local",
                        "participants": ["雷恩队长"],
                        "stage": "ongoing",
                        "status": "active",
                        "next_update_minute": 400,
                        "cause_event_ids": [],
                        "source_ids": ["chunk:north_geography_01"],
                    },
                ],
            },
            world_minute=60,
            id_factory=lambda: next(id_iter),
        )

        # 4. 推进到 world_minute=120
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120, revision = 3 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        adv_snap = read_snapshot(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {"kind": "advance", "target_world_minute": 120},
            max_active_incidents=1,
        )
        self.assertEqual(adv_snap["status"], "ready")
        self.assertFalse(adv_snap["skip"])
        self.assertEqual([e["event_id"] for e in adv_snap["due_events"]], ["evt_due_child"])
        self.assertEqual([e["event_id"] for e in adv_snap["causal_neighborhood"]], ["evt_parent"])
        self.assertEqual(adv_snap["allowed_event_ids"], ["evt_due_child", "evt_parent"])
        self.assertEqual(adv_snap["active_incident_count"], 1)

        # 5. 验证快照外活跃临时事件上限生效
        with self.assertRaises(EventValidationError) as cm_inc:
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "create",
                            "kind": "incident",
                            "title": "超限的新临时事件",
                            "description": "测试存量临时事件计数生效。",
                            "location": "霜木镇",
                            "scope": "local",
                            "participants": ["韩掌柜"],
                            "stage": "init",
                            "status": "active",
                            "next_update_minute": 240,
                            "cause_event_ids": ["evt_due_child"],
                            "source_ids": ["chunk:north_trade_routes_02"],
                        }
                    ],
                },
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "advance", "snapshot": adv_snap},
            )
        self.assertEqual(cm_inc.exception.code, "INCIDENT_LIMIT_EXCEEDED")

    def test_strict_output_schema_prompts_and_six_security_guards_plus_db_recheck(self) -> None:
        """验证输出结构 Schema、提示词构建，以及伪造 Canon、跨实例、旧版本、因果环、玩家账本、伪造时间六大拦截与 DB 复核。"""
        seed_northern_trade_world_canon(
            self.conn,
            world_id=NORTHERN_TRADE_WORLD_ID,
            instance_id=NORTHERN_TRADE_INSTANCE_ID,
        )
        ensure_instance_exists(
            self.conn,
            "inst_other_999",
            world_id=NORTHERN_TRADE_WORLD_ID,
            adventure_id="adv_other_999",
            world_minute=0,
            revision=0,
        )
        other_ids = iter(["evt_other_1", "evt_other_2", "evt_other_3"])
        apply_proposal(
            self.conn,
            "inst_other_999",
            {
                "mode": "seed",
                "operations": [
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": f"其他实例主线 {i}",
                        "description": "其他实例事件。",
                        "location": "白河城",
                        "scope": "local",
                        "participants": ["瑟蕾娜议员"],
                        "stage": "init",
                        "status": "active",
                        "next_update_minute": 120,
                        "cause_event_ids": [],
                        "source_ids": ["chunk:north_geography_01"],
                    }
                    for i in range(1, 4)
                ],
            },
            world_minute=0,
            id_factory=lambda: next(other_ids),
        )

        self.assertEqual(PULSE_EVENT_OUTPUT_SCHEMA["type"], "object")
        self.assertFalse(PULSE_EVENT_OUTPUT_SCHEMA["additionalProperties"])
        self.assertIn("seed_example", PROMPT_EXAMPLES)
        self.assertIn("advance_example", PROMPT_EXAMPLES)

        seed_snap = read_snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID, {"kind": "seed"})
        seed_prompts = build_pulse_event_prompts(seed_snap)
        self.assertIn("chunk:north_geography_01", seed_prompts["user_prompt"])
        self.assertIn("严禁将任何无关世界的静态示例标题套进用户的世界", seed_prompts["system_prompt"])

        north_ids = iter(["evt_n1", "evt_n2", "evt_n3"])
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "seed",
                "operations": [
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": f"北境主线 {i}",
                        "description": "北境局势描述。",
                        "location": "霜木镇",
                        "scope": "regional",
                        "participants": ["韩掌柜"],
                        "stage": "init",
                        "status": "active",
                        "next_update_minute": 120,
                        "cause_event_ids": [],
                        "source_ids": ["chunk:north_geography_01"],
                    }
                    for i in range(1, 4)
                ],
            },
            world_minute=0,
            id_factory=lambda: next(north_ids),
        )
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120, revision = 2 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        adv_snap = read_snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID, {"kind": "advance"})
        worker_ctx = {
            "instance_id": NORTHERN_TRADE_INSTANCE_ID,
            "kind": "advance",
            "base_world_revision": 2,
            "target_world_minute": 120,
            "snapshot": adv_snap,
        }

        # 拦截 1：伪造 Canon 来源
        with self.assertRaises(SourceFabricatedError):
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "source_ids": ["entry:forged_canon_id"],
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )

        # 拦截 2：跨实例 ID
        with self.assertRaises(CrossInstanceReferenceError):
            validate_proposal(
                {
                    "instance_id": "inst_other_999",
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )
        with self.assertRaises(CrossInstanceReferenceError):
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "cause_event_ids": ["evt_other_1"],
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )

        # 拦截 3：错误旧版本
        with self.assertRaises(WorldRevisionConflictError) as cm_wrev:
            validate_proposal(
                {
                    "kind": "advance",
                    "base_world_revision": 1,
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )
        self.assertEqual(cm_wrev.exception.code, "WORLD_REVISION_CONFLICT")

        with self.assertRaises(EventRevisionConflictError) as cm_erev:
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 99,
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )
        self.assertEqual(cm_erev.exception.code, "EVENT_REVISION_CONFLICT")

        # 拦截 4：因果环
        with self.assertRaises(CausalCycleError):
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "cause_event_ids": ["evt_n2"],
                            "next_update_minute": 240,
                        },
                        {
                            "op": "update",
                            "event_id": "evt_n2",
                            "expected_event_revision": 1,
                            "cause_event_ids": ["evt_n1"],
                            "next_update_minute": 240,
                        },
                    ],
                },
                worker_ctx,
            )

        # 拦截 5：玩家账本保护与任意 JSON 路径
        with self.assertRaises(PlayerLedgerProtectedError):
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "inventory_changes": [{"item": "生铁", "delta": -5}],
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )
        with self.assertRaises(ArbitraryPathForbiddenError):
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "$set": {"stage": "hacked"},
                            "next_update_minute": 240,
                        }
                    ],
                },
                worker_ctx,
            )

        # 拦截 6：伪造时间
        with self.assertRaises(EventValidationError) as cm_time:
            validate_proposal(
                {
                    "kind": "advance",
                    "target_world_minute": 999,
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_n1",
                            "expected_event_revision": 1,
                            "next_update_minute": 1200,
                        }
                    ],
                },
                worker_ctx,
            )
        self.assertEqual(cm_time.exception.code, "TIMESTAMP_OVERRIDE_FORBIDDEN")

        # 拦截 7：应用时重新核对实际数据库状态
        valid_prop = validate_proposal(
            {
                "kind": "advance",
                "base_world_revision": 2,
                "target_world_minute": 120,
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_n1",
                        "expected_event_revision": 1,
                        "stage": "step_forward",
                        "next_update_minute": 240,
                    }
                ],
            },
            worker_ctx,
        )
        self.conn.execute(
            "UPDATE world_instances SET revision = 3 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        with self.assertRaises(WorldRevisionConflictError):
            apply_proposal(
                self.conn,
                NORTHERN_TRADE_INSTANCE_ID,
                valid_prop,
                {"kind": "advance", "base_world_revision": 2, "target_world_minute": 120},
            )
        self.assertEqual(get_event(self.conn, NORTHERN_TRADE_INSTANCE_ID, "evt_n1")["revision"], 1)

    async def test_wave2_mock_http_causal_chain_and_concurrent_conflict_atomicity(self) -> None:
        """使用 Mock HTTP 跑通「邻国加税 -> 商队改道 -> 本地供给变化」多步链，断言来源/版本/时间/因果与并发冲突无半笔状态。"""
        bundle = await run_wave2_mock_causal_chain(self.conn, NORTHERN_TRADE_INSTANCE_ID)
        self.assertEqual(bundle["total_mock_http_calls"], 4)
        self.assertEqual(len(bundle["steps"]), 4)

        step0, step1, step2, step3 = bundle["steps"]
        self.assertEqual((step0["kind"], step0["world_minute"], step0["new_world_revision"]), ("seed", 0, 1))
        self.assertEqual(len(step0["applied_output"]["created_event_ids"]), 5)

        self.assertEqual((step1["kind"], step1["world_minute"], step1["new_world_revision"]), ("advance", 120, 2))
        self.assertIn("evt_north_thread_4", step1["snapshot"]["due_event_ids"])
        ev_tariff = step1["applied_output"]["events"][0]
        self.assertEqual(ev_tariff["event_id"], "evt_north_thread_4")
        self.assertEqual(ev_tariff["revision"], 2)
        self.assertEqual(ev_tariff["stage"], "tariff_enforced")
        self.assertEqual(ev_tariff["updated_minute"], 120)
        self.assertEqual(ev_tariff["next_update_minute"], 300)

        self.assertEqual((step2["kind"], step2["world_minute"], step2["new_world_revision"]), ("advance", 240, 3))
        self.assertEqual(step2["applied_output"]["updated_event_ids"], ["evt_north_thread_2"])
        self.assertEqual(step2["applied_output"]["created_event_ids"], ["evt_north_inc_1"])
        ev_inc1 = step2["applied_output"]["events"][1]
        self.assertEqual(ev_inc1["cause_event_ids"], ["evt_north_thread_4", "evt_north_thread_2"])
        self.assertEqual(ev_inc1["source_ids"], ["chunk:north_trade_routes_02"])

        self.assertIn("evt_north_inc_1", step3["snapshot"]["due_event_ids"])
        self.assertTrue(
            {"evt_north_thread_2", "evt_north_thread_4"}
            <= set(step3["snapshot"]["allowed_event_ids"])
        )
        self.assertEqual(step3["applied_output"]["updated_event_ids"], ["evt_north_inc_1"])
        self.assertEqual(step3["applied_output"]["created_event_ids"], ["evt_north_inc_2"])
        ev_inc1_done = step3["applied_output"]["events"][0]
        self.assertEqual(ev_inc1_done["status"], "completed")
        self.assertIsNone(ev_inc1_done["next_update_minute"])
        self.assertEqual(ev_inc1_done["revision"], 2)

        trace = bundle["causal_trace_for_local_shortage"]
        self.assertEqual(
            [e["event_id"] for e in trace["ancestors"]],
            ["evt_north_thread_2", "evt_north_thread_4", "evt_north_inc_1"],
        )
        self.assertEqual(len(bundle["final_consequences"]), 6)
        for csq in bundle["final_consequences"]:
            self.assertTrue(csq["source_event_id"].startswith("evt_north_"))
            self.assertGreaterEqual(csq["source_event_revision"], 1)

        events_before = list_events(self.conn, NORTHERN_TRADE_INSTANCE_ID)["items"]
        csq_before = list_consequences(self.conn, NORTHERN_TRADE_INSTANCE_ID)
        revs_inc2_before = list_event_revisions(self.conn, NORTHERN_TRADE_INSTANCE_ID, "evt_north_inc_2")

        self.conn.execute(
            "UPDATE world_instances SET world_minute = 540, revision = 4 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        snap_540 = read_snapshot(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {"kind": "advance", "base_world_revision": 4, "target_world_minute": 540},
        )
        multi_op_proposal = validate_proposal(
            {
                "kind": "advance",
                "base_world_revision": 4,
                "target_world_minute": 540,
                "operations": [
                    {
                        "op": "create",
                        "kind": "incident",
                        "title": "不应残留的半笔临时事件",
                        "description": "若后续操作版本冲突，本事件及后果必须完整回滚。",
                        "location": "霜木镇",
                        "scope": "local",
                        "participants": ["霍克铁匠"],
                        "stage": "init",
                        "status": "active",
                        "next_update_minute": 660,
                        "cause_event_ids": ["evt_north_inc_2"],
                        "source_ids": ["chunk:north_geography_01"],
                        "consequences": [
                            {
                                "domain": "shop_status",
                                "target": "霍克铁匠铺",
                                "summary": "不应残留的半笔后果。",
                                "impact_type": "direct",
                            }
                        ],
                    },
                    {
                        "op": "update",
                        "event_id": "evt_north_inc_2",
                        "expected_event_revision": 1,
                        "stage": "conflict_target",
                        "next_update_minute": 660,
                    },
                ],
            },
            {
                "instance_id": NORTHERN_TRADE_INSTANCE_ID,
                "kind": "advance",
                "base_world_revision": 4,
                "target_world_minute": 540,
                "snapshot": snap_540,
            },
        )

        self.conn.execute(
            "UPDATE world_events SET revision = 2, stage = 'concurrent_updated' WHERE instance_id = ? AND event_id = 'evt_north_inc_2'",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        self.conn.execute(
            """
            INSERT INTO world_event_revisions (instance_id, event_id, revision, operation, world_minute, reason, snapshot_json)
            VALUES (?, 'evt_north_inc_2', 2, 'update', 540, '并发抢先更新', '{}')
            """,
            (NORTHERN_TRADE_INSTANCE_ID,),
        )

        with self.assertRaises(EventRevisionConflictError) as cm_race:
            apply_proposal(
                self.conn,
                NORTHERN_TRADE_INSTANCE_ID,
                multi_op_proposal,
                {"kind": "advance", "base_world_revision": 4, "target_world_minute": 540},
                id_factory=lambda: "evt_half_written_must_not_exist",
                consequence_id_factory=lambda: "csq_half_written_must_not_exist",
            )
        self.assertEqual(cm_race.exception.code, "EVENT_REVISION_CONFLICT")

        events_after = list_events(self.conn, NORTHERN_TRADE_INSTANCE_ID)["items"]
        csq_after = list_consequences(self.conn, NORTHERN_TRADE_INSTANCE_ID)
        revs_inc2_after = list_event_revisions(self.conn, NORTHERN_TRADE_INSTANCE_ID, "evt_north_inc_2")
        self.assertEqual(len(events_after), len(events_before))
        self.assertEqual(len(csq_after), len(csq_before))
        self.assertEqual(len(revs_inc2_after), len(revs_inc2_before) + 1)
        self.assertNotIn("evt_half_written_must_not_exist", [e["event_id"] for e in events_after])
        self.assertNotIn("csq_half_written_must_not_exist", [c["consequence_id"] for c in csq_after])

        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "app"
            / "m3"
            / "events"
            / "wave2_mock_causal_chain_fixture.json"
        )
        self.assertTrue(fixture_path.is_file())
        loaded_wave2_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded_wave2_fixture, get_wave2_mock_causal_chain_bundle())

    async def test_pulse_worker_direct_wiring_end_to_end(self) -> None:
        """验证 read_snapshot, validate_proposal, apply_proposal 可直接挂入 PulseWorker 完成 Seed 与 Advance。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "pulse_events_wiring.db"

            @contextmanager
            def _db() -> Iterator[sqlite3.Connection]:
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA foreign_keys = ON")
                try:
                    yield c
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
                finally:
                    c.close()

            with _db() as c:
                ensure_pulse_schema(c, ensure_instance_table=True)
                ensure_events_schema(c)
                seed_northern_trade_world_canon(
                    c,
                    world_id=NORTHERN_TRADE_WORLD_ID,
                    instance_id=NORTHERN_TRADE_INSTANCE_ID,
                )
                configure_instance_pulse(
                    c,
                    NORTHERN_TRADE_INSTANCE_ID,
                    enabled=True,
                    cooldown_seconds=0.0,
                    now=1000.0,
                )
                enqueue(
                    c,
                    {
                        "instance_id": NORTHERN_TRADE_INSTANCE_ID,
                        "kind": "seed",
                        "trigger_adventure_id": "adv_northern_frontier_001",
                        "trigger_revision": 1,
                        "base_world_revision": 0,
                        "target_world_minute": 0,
                        "now": 1000.0,
                    },
                )

            router = PulseSessionRouter()
            router.bind_fake(
                NORTHERN_TRADE_INSTANCE_ID,
                FakePulseModelClient(generator=build_fake_pulse_generator()),
            )
            activity = ActivityLeaseRegistry(clock=lambda: 1000.0)
            activity.touch(NORTHERN_TRADE_INSTANCE_ID, ttl_seconds=600.0)

            worker = PulseWorker(
                _db,
                session_router=router,
                activity_registry=activity,
                snapshot_reader=read_snapshot,
                proposal_validator=validate_proposal,
                proposal_applier=apply_proposal,
                clock=lambda: 1000.0,
            )

            res_seed = await worker.run_once(instance_id=NORTHERN_TRADE_INSTANCE_ID)
            assert res_seed is not None
            self.assertEqual(res_seed["outcome"], "completed")
            self.assertEqual(res_seed["new_world_revision"], 1)
            self.assertEqual(res_seed["apply_result"]["applied_operations"], 5)

            with _db() as c:
                c.execute(
                    "UPDATE world_instances SET world_minute = 120 WHERE id = ?",
                    (NORTHERN_TRADE_INSTANCE_ID,),
                )
                enqueue(
                    c,
                    {
                        "instance_id": NORTHERN_TRADE_INSTANCE_ID,
                        "kind": "advance",
                        "trigger_adventure_id": "adv_northern_frontier_001",
                        "trigger_revision": 2,
                        "base_world_revision": 1,
                        "target_world_minute": 120,
                        "now": 1010.0,
                    },
                )

            res_adv = await worker.run_once(instance_id=NORTHERN_TRADE_INSTANCE_ID)
            assert res_adv is not None
            self.assertEqual(res_adv["outcome"], "completed")
            self.assertEqual(res_adv["new_world_revision"], 2)
            self.assertGreaterEqual(res_adv["apply_result"]["applied_operations"], 1)

    def test_transmission_envelope_guards_and_server_generated_ids(self) -> None:
        """验证首版传播引用边界（Seed 禁传、Advance 禁引本批新建临时 ID）及新事件 ID 仅由服务端生成。"""
        seed_northern_trade_world_canon(
            self.conn,
            world_id=NORTHERN_TRADE_WORLD_ID,
            instance_id=NORTHERN_TRADE_INSTANCE_ID,
        )
        seed_snap = read_snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID, {"kind": "seed"})
        base_ops = [
            {
                "op": "create",
                "kind": "thread",
                "title": f"主线 {i}",
                "description": "描述。",
                "location": "霜木镇",
                "scope": "local",
                "participants": ["韩掌柜"],
                "stage": "init",
                "status": "active",
                "next_update_minute": 120,
                "cause_event_ids": [],
                "source_ids": ["chunk:north_geography_01"],
            }
            for i in range(1, 4)
        ]

        with self.assertRaises(EventValidationError) as cm_seed_tx:
            validate_proposal(
                {
                    "kind": "seed",
                    "operations": base_ops,
                    "transmissions": [
                        {
                            "holder_id": "player",
                            "knowledge_type": "rumor",
                            "channel": "notice",
                            "content": "试图在 Seed 阶段直接塞入玩家消息",
                        }
                    ],
                },
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "seed", "snapshot": seed_snap},
            )
        self.assertEqual(cm_seed_tx.exception.code, "SEED_TRANSMISSIONS_FORBIDDEN")

        with self.assertRaises(EventValidationError) as cm_forged_id:
            validate_proposal(
                {
                    "kind": "seed",
                    "operations": [
                        {**base_ops[0], "event_id": "evt_model_supplied_id"},
                        base_ops[1],
                        base_ops[2],
                    ],
                },
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "seed", "snapshot": seed_snap},
            )
        self.assertEqual(cm_forged_id.exception.code, "SERVER_ID_FORBIDDEN")

        applied_seed = apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {"kind": "seed", "operations": base_ops},
            {"kind": "seed", "base_world_revision": 0, "target_world_minute": 0},
        )
        self.assertEqual(len(applied_seed["created_event_ids"]), 3)
        first_eid = applied_seed["created_event_ids"][0]
        self.assertTrue(first_eid.startswith("evt_"))

        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120, revision = 1 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        adv_snap = read_snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID, {"kind": "advance"})
        with self.assertRaises(EventValidationError) as cm_temp_tx:
            validate_proposal(
                {
                    "kind": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": first_eid,
                            "expected_event_revision": 1,
                            "stage": "advanced",
                            "next_update_minute": 240,
                        }
                    ],
                    "transmissions": [
                        {
                            "holder_id": "player",
                            "knowledge_type": "rumor",
                            "channel": "notice",
                            "source_event_id": "temp_new_event_1",
                            "content": "引用临时事件 ID",
                        }
                    ],
                },
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "advance", "snapshot": adv_snap},
            )
        self.assertEqual(cm_temp_tx.exception.code, "TEMP_EVENT_REFERENCE_FORBIDDEN")

        valid_with_tx = validate_proposal(
            {
                "kind": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": first_eid,
                        "expected_event_revision": 1,
                        "stage": "advanced",
                        "next_update_minute": 240,
                    }
                ],
                "transmissions": [
                    {
                        "holder_id": "player",
                        "knowledge_type": "rumor",
                        "channel": "notice",
                        "source_event_id": first_eid,
                        "learned_minute": 120,
                        "content": "公告栏传出合法存量事件的消息",
                    }
                ],
            },
            {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "advance", "snapshot": adv_snap},
        )
        self.assertEqual(len(valid_with_tx.transmissions), 1)
        self.assertEqual(valid_with_tx.transmissions[0]["source_event_id"], first_eid)

    def test_mode_kind_and_revision_world_revision_bidirectional_mapping(self) -> None:
        """验证 mode 与 kind、数据库 revision 与对外 world_revision / base_world_revision 的双向适配与冲突拦截。"""
        seed_northern_trade_world_canon(
            self.conn,
            world_id=NORTHERN_TRADE_WORLD_ID,
            instance_id=NORTHERN_TRADE_INSTANCE_ID,
        )
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 0, revision = 7 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        snap = read_snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID, {"kind": "seed"})
        self.assertEqual(snap["kind"], "seed")
        self.assertEqual(snap["mode"], "seed")
        self.assertEqual(snap["revision"], 7)
        self.assertEqual(snap["world_revision"], 7)
        self.assertEqual(snap["base_world_revision"], 7)

        with self.assertRaises(EventValidationError) as cm_mk:
            validate_proposal(
                {"mode": "seed", "kind": "advance", "operations": []},
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "seed", "snapshot": snap},
            )
        self.assertEqual(cm_mk.exception.code, "MODE_KIND_MISMATCH")

        with self.assertRaises(EventValidationError) as cm_mk2:
            validate_proposal(
                {"kind": "advance", "operations": []},
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "seed", "snapshot": snap},
            )
        self.assertEqual(cm_mk2.exception.code, "MODE_KIND_MISMATCH")

        with self.assertRaises(WorldRevisionConflictError):
            validate_proposal(
                {"kind": "seed", "world_revision": 7, "base_world_revision": 8, "operations": []},
                {"instance_id": NORTHERN_TRADE_INSTANCE_ID, "kind": "seed", "snapshot": snap},
            )


if __name__ == "__main__":
    unittest.main()
