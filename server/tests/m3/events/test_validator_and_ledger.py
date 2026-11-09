"""M3.1 权威事件账本、版本历史、因果环与原子回滚单元测试。

覆盖验收项：
1. 种子数量校验（<3 或 >6 拒绝，3～6 通过）；
2. 来源伪造拒绝（未授权 chunk/entry 或非法前缀）；
3. 过期版本冲突拒绝（409 EVENT_REVISION_CONFLICT）；
4. 跨实例引用拒绝（422 CROSS_INSTANCE_REFERENCE）；
5. 自引用与多跳因果环拒绝（422 CAUSAL_SELF_REFERENCE / CAUSAL_CYCLE_DETECTED）；
6. 合法完成后产生衍生临时事件（completed 事件作为新 incident 的父事件）；
7. 同一提议部分非法时整体回滚无写入（SAVEPOINT 原子性）及不擅自 commit 外层事务；
8. 任意 JSON 路径写入、未来/历史时间戳覆盖与直接更改玩家账本拒绝；
9. 到期事件分页读取（offset 与不透明 cursor 游标）与追加式修订历史。
"""

from __future__ import annotations

import sqlite3
import unittest

from app.m3.events import (
    ArbitraryPathForbiddenError,
    CausalCycleError,
    CrossInstanceReferenceError,
    EventNotFoundError,
    EventRevisionConflictError,
    EventValidationError,
    PlayerLedgerProtectedError,
    ProposalValidationContext,
    SourceFabricatedError,
    apply_proposal,
    ensure_events_schema,
    ensure_instance_exists,
    get_event,
    list_due_events,
    list_event_revisions,
    list_events,
    validate_proposal,
)


def _make_seed_op(idx: int, source_id: str = "chunk:doc1_c0", next_minute: int = 120) -> dict:
    return {
        "op": "create",
        "kind": "thread",
        "title": f"主线事件 {idx}",
        "description": f"这是第 {idx} 条初始主要事件线的局势描述。",
        "location": f"地点_{idx}",
        "scope": "regional",
        "participants": [f"人物_{idx}A", f"人物_{idx}B"],
        "stage": "initial",
        "status": "active",
        "next_update_minute": next_minute,
        "cause_event_ids": [],
        "source_ids": [source_id],
        "reason": f"初始化主线 {idx}",
    }


class TestValidatorAndLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_events_schema(self.conn)
        ensure_instance_exists(
            self.conn,
            "inst_alpha",
            world_id="world_1",
            adventure_id="adv_1",
            world_minute=0,
        )
        ensure_instance_exists(
            self.conn,
            "inst_beta",
            world_id="world_1",
            adventure_id="adv_2",
            world_minute=0,
        )
        self.allowed_sources = frozenset({"chunk:doc1_c0", "chunk:doc1_c1", "entry:ent_1"})

    def tearDown(self) -> None:
        self.conn.close()

    def test_seed_thread_count_boundaries(self) -> None:
        """测试初始局势主线数量边界：少于 3 条或多于 6 条均拒绝，3～6 条通过。"""
        ctx = ProposalValidationContext(
            instance_id="inst_alpha",
            world_minute=0,
            mode="seed",
            allowed_source_ids=self.allowed_sources,
        )
        # 仅 2 条 -> 拒绝
        with self.assertRaises(EventValidationError) as cm_low:
            validate_proposal(
                {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2)]},
                ctx,
            )
        self.assertEqual(cm_low.exception.code, "SEED_THREAD_COUNT_INVALID")

        # 7 条 -> 拒绝
        with self.assertRaises(EventValidationError) as cm_high:
            validate_proposal(
                {"mode": "seed", "operations": [_make_seed_op(i) for i in range(1, 8)]},
                ctx,
            )
        self.assertEqual(cm_high.exception.code, "SEED_THREAD_COUNT_INVALID")

        # 3 条与 6 条 -> 通过
        prop_3 = validate_proposal(
            {"mode": "seed", "operations": [_make_seed_op(i) for i in range(1, 4)]},
            ctx,
        )
        self.assertEqual(len(prop_3.operations), 3)

        prop_6 = validate_proposal(
            {"mode": "seed", "operations": [_make_seed_op(i) for i in range(1, 7)]},
            ctx,
        )
        self.assertEqual(len(prop_6.operations), 6)

    def test_source_fabrication_rejected(self) -> None:
        """测试伪造来源、未授权来源、空来源在校验与应用阶段均被拒绝。"""
        ctx = ProposalValidationContext(
            instance_id="inst_alpha",
            world_minute=0,
            mode="seed",
            allowed_source_ids=self.allowed_sources,
        )
        # 1. 引用不在 allowed_source_ids 中的伪造 chunk
        bad_ops = [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3, source_id="chunk:forged_999")]
        with self.assertRaises(SourceFabricatedError) as cm1:
            validate_proposal({"mode": "seed", "operations": bad_ops}, ctx)
        self.assertEqual(cm1.exception.code, "SOURCE_FABRICATED")
        self.assertEqual(cm1.exception.details["source_id"], "chunk:forged_999")

        # 2. 非法前缀
        bad_prefix = [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3, source_id="http://evil.com")]
        with self.assertRaises(SourceFabricatedError):
            validate_proposal({"mode": "seed", "operations": bad_prefix}, ctx)

        # 3. 初始主线无任何来源
        empty_src = [_make_seed_op(1), _make_seed_op(2), {**_make_seed_op(3), "source_ids": []}]
        with self.assertRaises(SourceFabricatedError):
            validate_proposal({"mode": "seed", "operations": empty_src}, ctx)

    def test_stale_revision_conflict_409(self) -> None:
        """测试事件更新时使用过期 expected_event_revision 抛出 409 EVENT_REVISION_CONFLICT。"""
        ids = iter(["evt_a1", "evt_a2", "evt_a3"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )

        # 第一次合法推进 evt_a1 (rev 1 -> rev 2)
        res = apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_a1",
                        "expected_event_revision": 1,
                        "stage": "phase_2",
                        "next_update_minute": 240,
                        "reason": "首次推进",
                    }
                ],
            },
            world_minute=120,
            allowed_source_ids=self.allowed_sources,
        )
        self.assertEqual(res["events"][0]["revision"], 2)

        # 再次使用过期的 expected_event_revision=1 -> 409 冲突
        with self.assertRaises(EventRevisionConflictError) as cm:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_a1",
                            "expected_event_revision": 1,
                            "stage": "phase_stale",
                            "next_update_minute": 360,
                        }
                    ],
                },
                world_minute=180,
                allowed_source_ids=self.allowed_sources,
            )
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.code, "EVENT_REVISION_CONFLICT")
        self.assertEqual(cm.exception.details["expected_event_revision"], 1)
        self.assertEqual(cm.exception.details["current_event_revision"], 2)

        # 数据库内事件仍保持 rev 2 与 phase_2
        ev = get_event(self.conn, "inst_alpha", "evt_a1")
        self.assertEqual(ev["revision"], 2)
        self.assertEqual(ev["stage"], "phase_2")

    def test_cross_instance_reference_rejected(self) -> None:
        """测试跨实例更新事件、跨实例引用父事件以及跨实例读取均被拒绝。"""
        alpha_ids = iter(["evt_alpha_1", "evt_alpha_2", "evt_alpha_3"])
        beta_ids = iter(["evt_beta_1", "evt_beta_2", "evt_beta_3"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(alpha_ids),
        )
        apply_proposal(
            self.conn,
            "inst_beta",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(beta_ids),
        )

        # 1. 在 inst_alpha 中尝试更新属于 inst_beta 的事件 evt_beta_1
        with self.assertRaises(CrossInstanceReferenceError) as cm_upd:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_beta_1",
                            "expected_event_revision": 1,
                            "stage": "hacked",
                            "next_update_minute": 240,
                        }
                    ],
                },
                world_minute=120,
                allowed_source_ids=self.allowed_sources,
            )
        self.assertEqual(cm_upd.exception.code, "CROSS_INSTANCE_REFERENCE")
        self.assertEqual(cm_upd.exception.details["other_instance_id"], "inst_beta")

        # 2. 在 inst_alpha 中创建临时事件但引用 inst_beta 的 evt_beta_1 作为父事件
        with self.assertRaises(CrossInstanceReferenceError) as cm_cause:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "create",
                            "kind": "incident",
                            "title": "跨实例非法衍生",
                            "description": "试图引用另一平行世界实例的事件。",
                            "location": "霜木镇",
                            "scope": "local",
                            "participants": ["韩掌柜"],
                            "stage": "initial",
                            "status": "active",
                            "next_update_minute": 240,
                            "cause_event_ids": ["evt_beta_1"],
                            "source_ids": ["chunk:doc1_c0"],
                        }
                    ],
                },
                world_minute=120,
                allowed_source_ids=self.allowed_sources,
            )
        self.assertEqual(cm_cause.exception.code, "CROSS_INSTANCE_REFERENCE")

        # 3. 通过 get_event 在 inst_alpha 下读取 evt_beta_1
        with self.assertRaises(CrossInstanceReferenceError):
            get_event(self.conn, "inst_alpha", "evt_beta_1")

    def test_self_reference_and_causal_cycle_rejected(self) -> None:
        """测试事件自引用（A->A）、双节点环（A<->B）与三节点因果环（A->B->C->A）均被拦截。"""
        ids = iter(["evt_c1", "evt_c2", "evt_c3"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )

        # 1. 自引用：evt_c1 将自己列为 cause_event_ids
        with self.assertRaises(CausalCycleError) as cm_self:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_c1",
                            "expected_event_revision": 1,
                            "cause_event_ids": ["evt_c1"],
                            "next_update_minute": 240,
                        }
                    ],
                },
                world_minute=120,
                allowed_source_ids=self.allowed_sources,
            )
        self.assertEqual(cm_self.exception.code, "CAUSAL_SELF_REFERENCE")

        # 2. 建立合法因果链：evt_c2 依赖 evt_c1，evt_c3 依赖 evt_c2
        apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_c2",
                        "expected_event_revision": 1,
                        "cause_event_ids": ["evt_c1"],
                        "next_update_minute": 240,
                    },
                    {
                        "op": "update",
                        "event_id": "evt_c3",
                        "expected_event_revision": 1,
                        "cause_event_ids": ["evt_c2"],
                        "next_update_minute": 240,
                    },
                ],
            },
            world_minute=120,
            allowed_source_ids=self.allowed_sources,
        )

        # 3. 尝试让 evt_c1 依赖 evt_c3，形成 evt_c1 -> evt_c3 -> evt_c2 -> evt_c1 环路
        with self.assertRaises(CausalCycleError) as cm_cycle:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_c1",
                            "expected_event_revision": 1,
                            "cause_event_ids": ["evt_c3"],
                            "next_update_minute": 360,
                        }
                    ],
                },
                world_minute=180,
                allowed_source_ids=self.allowed_sources,
            )
        self.assertEqual(cm_cycle.exception.code, "CAUSAL_CYCLE_DETECTED")
        self.assertIn("evt_c1", cm_cycle.exception.details["cycle_path"])

    def test_legal_completion_spawns_derived_incident_and_terminal_protection(self) -> None:
        """测试事件合法完成后衍生临时事件，且已完成（completed）事件不可再次修改。"""
        ids = iter(["evt_t1", "evt_t2", "evt_t3", "evt_inc_derived"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )

        # 在同一提议中将 evt_t1 更新为 completed，并衍生临时事件 evt_inc_derived
        res = apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_t1",
                        "expected_event_revision": 1,
                        "stage": "concluded",
                        "status": "completed",
                        "next_update_minute": None,
                        "reason": "主线顺利完结",
                    },
                    {
                        "op": "create",
                        "kind": "incident",
                        "title": "主线完结后的余波集市集会",
                        "description": "主线落定后本地行商举办临时庆祝集市。",
                        "location": "霜木镇广场",
                        "scope": "local",
                        "participants": ["人物_1A"],
                        "stage": "gathering",
                        "status": "active",
                        "next_update_minute": 240,
                        "cause_event_ids": ["evt_t1"],
                        "source_ids": ["chunk:doc1_c0"],
                        "reason": "由 evt_t1 完结衍生",
                    },
                ],
            },
            world_minute=120,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )
        self.assertEqual(len(res["events"]), 2)
        completed_ev = get_event(self.conn, "inst_alpha", "evt_t1")
        self.assertEqual(completed_ev["status"], "completed")
        self.assertIsNone(completed_ev["next_update_minute"])
        self.assertEqual(completed_ev["revision"], 2)

        derived_inc = get_event(self.conn, "inst_alpha", "evt_inc_derived")
        self.assertEqual(derived_inc["kind"], "incident")
        self.assertEqual(derived_inc["cause_event_ids"], ["evt_t1"])

        # 尝试再次更新已处于终态 completed 的 evt_t1 -> 拒绝
        with self.assertRaises(EventValidationError) as cm_term:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_t1",
                            "expected_event_revision": 2,
                            "stage": "reopen",
                            "status": "active",
                            "next_update_minute": 300,
                        }
                    ],
                },
                world_minute=180,
                allowed_source_ids=self.allowed_sources,
            )
        self.assertEqual(cm_term.exception.code, "EVENT_TERMINAL_STATE")

    def test_atomic_rollback_when_partial_proposal_invalid_and_preserve_outer_tx(self) -> None:
        """测试同一提议中前序操作合法、后序操作非法时整体回滚无写入，且不自动提交外层事务。"""
        ids = iter(["evt_r1", "evt_r2", "evt_r3", "evt_should_rollback"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )
        # 验证模块未擅自调用 commit，当前连接仍处于未提交事务中
        self.assertTrue(self.conn.in_transaction)

        # 构造包含 3 个操作的提议：Op 0 合法更新 evt_r1，Op 1 合法新建临时事件，Op 2 版本冲突非法
        with self.assertRaises(EventRevisionConflictError):
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_r1",
                            "expected_event_revision": 1,
                            "stage": "should_not_persist",
                            "next_update_minute": 240,
                        },
                        {
                            "op": "create",
                            "kind": "incident",
                            "title": "不应被写入的临时事件",
                            "description": "因同批第 3 项失败，本条必须回滚。",
                            "location": "地点_1",
                            "scope": "local",
                            "participants": ["人物_1A"],
                            "stage": "init",
                            "status": "active",
                            "next_update_minute": 240,
                            "cause_event_ids": ["evt_r1"],
                            "source_ids": ["chunk:doc1_c0"],
                        },
                        {
                            "op": "update",
                            "event_id": "evt_r2",
                            "expected_event_revision": 99,  # 故意传入错误版本号
                            "stage": "boom",
                            "next_update_minute": 240,
                        },
                    ],
                },
                world_minute=120,
                allowed_source_ids=self.allowed_sources,
                id_factory=lambda: next(ids),
            )

        # 验证 Op 0 与 Op 1 均未写入，evt_r1 仍为 revision=1, stage='initial'
        ev_r1 = get_event(self.conn, "inst_alpha", "evt_r1")
        self.assertEqual(ev_r1["revision"], 1)
        self.assertEqual(ev_r1["stage"], "initial")
        self.assertEqual(len(list_event_revisions(self.conn, "inst_alpha", "evt_r1")), 1)
        with self.assertRaises(EventNotFoundError):
            get_event(self.conn, "inst_alpha", "evt_should_rollback")

    def test_reject_player_ledger_arbitrary_path_and_future_backfill(self) -> None:
        """测试直接更改玩家账本、任意 JSON 路径写入、时间戳覆写与未来时间回填均被拒绝。"""
        ctx = ProposalValidationContext(
            instance_id="inst_alpha",
            world_minute=120,
            mode="advance",
            allowed_source_ids=self.allowed_sources,
            existing_events={
                "evt_1": {
                    "event_id": "evt_1",
                    "instance_id": "inst_alpha",
                    "kind": "thread",
                    "title": "标题",
                    "description": "描述",
                    "location": "地点",
                    "scope": "local",
                    "participants": ["甲"],
                    "stage": "initial",
                    "status": "active",
                    "created_minute": 0,
                    "updated_minute": 60,
                    "next_update_minute": 120,
                    "cause_event_ids": [],
                    "source_ids": ["chunk:doc1_c0"],
                    "revision": 1,
                }
            },
        )

        # 1. 尝试在操作中夹带 coins_delta 更改玩家金钱
        with self.assertRaises(PlayerLedgerProtectedError):
            validate_proposal(
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_1",
                            "expected_event_revision": 1,
                            "coins_delta": -100,
                            "next_update_minute": 240,
                        }
                    ],
                },
                ctx,
            )

        # 2. 尝试在后果中将 target 指向玩家背包
        with self.assertRaises(PlayerLedgerProtectedError):
            validate_proposal(
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_1",
                            "expected_event_revision": 1,
                            "next_update_minute": 240,
                            "consequences": [
                                {
                                    "domain": "npc_inventory",
                                    "target": "玩家背包",
                                    "summary": "试图直接扣除玩家铁矿石",
                                }
                            ],
                        }
                    ],
                },
                ctx,
            )

        # 3. 尝试任意 JSON 路径写入（如 json_path 或 $set）
        with self.assertRaises(ArbitraryPathForbiddenError):
            validate_proposal(
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_1",
                            "expected_event_revision": 1,
                            "json_path": "$.world.economy.tax",
                            "next_update_minute": 240,
                        }
                    ],
                },
                ctx,
            )

        # 4. 尝试覆写 created_minute / updated_minute（回填时间）
        with self.assertRaises(EventValidationError) as cm_ts:
            validate_proposal(
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_1",
                            "expected_event_revision": 1,
                            "updated_minute": 999,
                            "next_update_minute": 1200,
                        }
                    ],
                },
                ctx,
            )
        self.assertEqual(cm_ts.exception.code, "TIMESTAMP_OVERRIDE_FORBIDDEN")

        # 5. 非终态事件 next_update_minute <= world_minute
        with self.assertRaises(EventValidationError) as cm_next:
            validate_proposal(
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_1",
                            "expected_event_revision": 1,
                            "next_update_minute": 120,
                        }
                    ],
                },
                ctx,
            )
        self.assertEqual(cm_next.exception.code, "INVALID_NEXT_UPDATE_MINUTE")

    def test_due_events_pagination_and_revision_history(self) -> None:
        """测试到期事件分页读取（offset 与 cursor 游标）以及追加式版本修订历史。"""
        ids = iter(["evt_p1", "evt_p2", "evt_p3", "evt_p4"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "seed",
                "operations": [
                    _make_seed_op(1, next_minute=60),
                    _make_seed_op(2, next_minute=120),
                    _make_seed_op(3, next_minute=180),
                    _make_seed_op(4, next_minute=300),
                ],
            },
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )

        # 在 world_minute=200 时，前 3 条事件到期（60, 120, 180），第 4 条（300）未到期
        page1 = list_due_events(self.conn, "inst_alpha", world_minute=200, limit=2)
        self.assertEqual(page1["total"], 3)
        self.assertEqual([e["event_id"] for e in page1["items"]], ["evt_p1", "evt_p2"])
        self.assertTrue(page1["has_more"])
        self.assertIsNotNone(page1["next_cursor"])

        # 使用游标读取下一页
        page2 = list_due_events(
            self.conn,
            "inst_alpha",
            world_minute=200,
            limit=2,
            cursor=page1["next_cursor"],
        )
        self.assertEqual([e["event_id"] for e in page2["items"]], ["evt_p3"])
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["next_cursor"])

        # 推进 evt_p1 两次，检查追加式修订历史包含 rev 1, 2, 3
        apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_p1",
                        "expected_event_revision": 1,
                        "stage": "stalled_phase",
                        "status": "stalled",
                        "next_update_minute": 240,
                        "reason": "因大雪暂时停滞",
                    }
                ],
            },
            world_minute=100,
            allowed_source_ids=self.allowed_sources,
        )
        apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_p1",
                        "expected_event_revision": 2,
                        "stage": "resumed_phase",
                        "status": "active",
                        "next_update_minute": 360,
                        "reason": "道路疏通恢复推进",
                    }
                ],
            },
            world_minute=240,
            allowed_source_ids=self.allowed_sources,
        )

        revs = list_event_revisions(self.conn, "inst_alpha", "evt_p1")
        self.assertEqual([r["revision"] for r in revs], [1, 2, 3])
        self.assertEqual([r["operation"] for r in revs], ["create", "update", "update"])
        self.assertEqual(revs[0]["snapshot"]["stage"], "initial")
        self.assertEqual(revs[1]["snapshot"]["status"], "stalled")
        self.assertEqual(revs[2]["snapshot"]["status"], "active")

    def test_incident_limit_enforced(self) -> None:
        """测试未完成临时事件数量上限拦截，完结旧临时事件后可继续创建。"""
        ids = iter(["evt_m1", "evt_m2", "evt_m3", "evt_inc_1", "evt_inc_2", "evt_inc_3"])
        apply_proposal(
            self.conn,
            "inst_alpha",
            {"mode": "seed", "operations": [_make_seed_op(1), _make_seed_op(2), _make_seed_op(3)]},
            world_minute=0,
            allowed_source_ids=self.allowed_sources,
            id_factory=lambda: next(ids),
        )

        # 设 max_active_incidents = 1，创建第 1 条临时事件通过
        apply_proposal(
            self.conn,
            "inst_alpha",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "create",
                        "kind": "incident",
                        "title": "临时事件 1",
                        "description": "描述 1",
                        "location": "地点_1",
                        "scope": "local",
                        "participants": ["人物_1A"],
                        "stage": "init",
                        "status": "active",
                        "next_update_minute": 180,
                        "cause_event_ids": ["evt_m1"],
                        "source_ids": ["chunk:doc1_c0"],
                    }
                ],
            },
            world_minute=60,
            allowed_source_ids=self.allowed_sources,
            max_active_incidents=1,
            id_factory=lambda: next(ids),
        )

        # 未结束第 1 条时尝试创建第 2 条 -> 超出上限被拒
        with self.assertRaises(EventValidationError) as cm_cap:
            apply_proposal(
                self.conn,
                "inst_alpha",
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "create",
                            "kind": "incident",
                            "title": "临时事件 2",
                            "description": "描述 2",
                            "location": "地点_1",
                            "scope": "local",
                            "participants": ["人物_1A"],
                            "stage": "init",
                            "status": "active",
                            "next_update_minute": 240,
                            "cause_event_ids": ["evt_m1"],
                            "source_ids": ["chunk:doc1_c0"],
                        }
                    ],
                },
                world_minute=120,
                allowed_source_ids=self.allowed_sources,
                max_active_incidents=1,
                id_factory=lambda: next(ids),
            )
        self.assertEqual(cm_cap.exception.code, "INCIDENT_LIMIT_EXCEEDED")
        self.assertEqual(len(list_events(self.conn, "inst_alpha", kind="incident")["items"]), 1)


if __name__ == "__main__":
    unittest.main()
