"""测试纯函数校验器、四种动作类型、时间/因果约束、重复任务拦截与玩家账本/知情隔离。"""

from __future__ import annotations

import sqlite3
import unittest

from app.m3.npcs import (
    ArbitraryPathForbiddenError,
    CrossInstanceReferenceError,
    DuplicateNpcTaskError,
    InvalidCausalReferenceError,
    NpcNotDueError,
    NpcRevisionConflictError,
    NpcValidationError,
    PlayerKnowledgeProtectedError,
    PlayerLedgerProtectedError,
    SourceFabricatedError,
    apply_proposal,
    ensure_instance_exists,
    read_snapshot,
    seed_northern_trade_npcs,
    validate_proposal,
)


class TestValidatorAndActions(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        seed_northern_trade_npcs(self.conn, world_minute=0)
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
        )
        self.snap = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 60, "player_location": "霜木镇"},
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_four_action_kinds_and_time_causal_constraints(self) -> None:
        """验证出行、交谈安排、换班和任务进度四种动作及其时间、因果约束。"""
        valid_proposal = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "base_world_revision": 1,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "前往白河城洽谈水运生铁",
                    "motivation": "灰鬃商队改道导致陆路生铁延误",
                    "duration_minutes": 90,
                    "next_due_minute": 150,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_marta_innkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "conversation_plan",
                    "location": "霜木镇",
                    "target_npc_id": "npc_renn_captain",
                    "conversation_topic": "核实东谷商道护路排班",
                    "current_activity": "与雷恩队长商谈商队延误与治安",
                    "motivation": "核实酒馆旅人关于商队改道的传闻",
                    "duration_minutes": 60,
                    "next_due_minute": 120,
                    "cause_fact_ids": ["fact_marta_road_rumor"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_hawk_blacksmith",
                    "expected_npc_revision": 1,
                    "action_kind": "shift_change",
                    "location": "霜木镇",
                    "from_shift": "常规双炉锻造班",
                    "to_shift": "单炉节炭班",
                    "current_activity": "切换至单炉节炭班次",
                    "motivation": "应对精煤与生铁延迟到货",
                    "duration_minutes": 60,
                    "next_due_minute": 120,
                    "cause_fact_ids": ["fact_hawk_coal_shortage"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_garret_caravan",
                    "expected_npc_revision": 1,
                    "action_kind": "task_progress",
                    "location": "北境主商道",
                    "task_id": "task_reroute_eastern_valley",
                    "task_title": "灰鬃商队改走东侧白河谷地转运生铁",
                    "task_stage": "东谷水陆渡口装船",
                    "task_progress_percent": 65,
                    "current_activity": "督导商队在渡口转装驳船",
                    "motivation": "规避灰岩边关三成重税",
                    "duration_minutes": 60,
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_loran_tariff_01"],
                },
            ],
        }
        validated = validate_proposal(valid_proposal, self.snap)
        self.assertEqual(len(validated.operations), 4)
        self.assertEqual(validated.operations[0].action_kind, "travel")
        self.assertEqual(validated.operations[0].location, "白河城")
        self.assertEqual(validated.operations[0].availability, "in_transit")

        # 缺少任何因果引用时拒绝
        no_cause = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "无故出行",
                    "motivation": "没有任何实例内事件或事实依据",
                    "next_due_minute": 120,
                }
            ],
        }
        with self.assertRaises(InvalidCausalReferenceError) as ctx_err:
            validate_proposal(no_cause, self.snap)
        self.assertEqual(ctx_err.exception.code, "CAUSAL_MOTIVATION_UNGROUNDED")

        # 引用不存在的事件或他人事实被拒绝
        bad_event = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "前往白河城",
                    "motivation": "引用不存在的事件",
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_non_existent_999"],
                }
            ],
        }
        with self.assertRaises(InvalidCausalReferenceError) as ctx_ev:
            validate_proposal(bad_event, self.snap)
        self.assertEqual(ctx_ev.exception.code, "CAUSAL_EVENT_NOT_FOUND")

        # 韩掌柜引用霍克铁匠才持有的事实 fact_hawk_coal_shortage 被拒绝
        wrong_holder_fact = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "前往白河城",
                    "motivation": "越权引用他人知情记录",
                    "next_due_minute": 120,
                    "cause_fact_ids": ["fact_hawk_coal_shortage"],
                }
            ],
        }
        with self.assertRaises(InvalidCausalReferenceError) as ctx_fact:
            validate_proposal(wrong_holder_fact, self.snap)
        self.assertEqual(ctx_fact.exception.code, "CAUSAL_FACT_NOT_KNOWN")

        # next_due_minute <= world_minute 被拒绝
        bad_time = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "时间倒流",
                    "motivation": "测试时间约束",
                    "next_due_minute": 60,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                }
            ],
        }
        with self.assertRaises(NpcValidationError) as ctx_time:
            validate_proposal(bad_time, self.snap)
        self.assertEqual(ctx_time.exception.code, "INVALID_NEXT_DUE_MINUTE")

    def test_duplicate_tasks_and_duplicate_operations_rejected(self) -> None:
        """验证重复 NPC 操作、无进展重复任务以及已完成任务二次提交均被拦截。"""
        # 1. 同一批次对同一 NPC 提交两条操作
        dup_npc_op = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "前往白河城",
                    "motivation": "寻找生铁",
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 1,
                    "action_kind": "shift_change",
                    "location": "霜木镇",
                    "to_shift": "晚班",
                    "current_activity": "留店换班",
                    "motivation": "重复指令",
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                },
            ],
        }
        with self.assertRaises(DuplicateNpcTaskError) as ctx_dup_op:
            validate_proposal(dup_npc_op, self.snap)
        self.assertEqual(ctx_dup_op.exception.code, "DUPLICATE_NPC_OPERATION")

        # 2. 对已有任务提交完全相同的 stage 与 progress_percent（无进展重复任务）
        no_progress_task = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_garret_caravan",
                    "expected_npc_revision": 1,
                    "action_kind": "task_progress",
                    "location": "北境主商道",
                    "task_id": "task_reroute_eastern_valley",
                    "task_stage": "先遣伙计探路与重编驮队",
                    "task_progress_percent": 25,
                    "current_activity": "原地踏步",
                    "motivation": "重复提交已有阶段",
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_loran_tariff_01"],
                }
            ],
        }
        with self.assertRaises(DuplicateNpcTaskError) as ctx_dup_task:
            validate_proposal(no_progress_task, self.snap)
        self.assertEqual(ctx_dup_task.exception.code, "DUPLICATE_NPC_TASK")

        # 3. 任务完成后再次提交同一 task_id 被拒绝（TASK_ALREADY_COMPLETED）
        apply_proposal(
            self.conn,
            "inst_northern_frontier_001",
            {
                "instance_id": "inst_northern_frontier_001",
                "mode": "advance",
                "world_minute": 60,
                "operations": [
                    {
                        "op": "act",
                        "npc_id": "npc_garret_caravan",
                        "expected_npc_revision": 1,
                        "action_kind": "task_progress",
                        "location": "北境主商道",
                        "task_id": "task_reroute_eastern_valley",
                        "task_stage": "全队完成白河谷地转运",
                        "task_progress_percent": 100,
                        "task_status": "completed",
                        "current_activity": "完成转运",
                        "motivation": "完成改道",
                        "next_due_minute": 120,
                        "cause_event_ids": ["evt_loran_tariff_01"],
                    }
                ],
            },
        )
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120 WHERE id = 'inst_northern_frontier_001'"
        )
        snap_120 = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 120},
        )
        with self.assertRaises(DuplicateNpcTaskError) as ctx_done:
            validate_proposal(
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 120,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_garret_caravan",
                            "expected_npc_revision": 2,
                            "action_kind": "task_progress",
                            "location": "北境主商道",
                            "task_id": "task_reroute_eastern_valley",
                            "task_stage": "再次执行已完成任务",
                            "task_progress_percent": 100,
                            "current_activity": "重复完成",
                            "motivation": "测试已完成拦截",
                            "next_due_minute": 180,
                            "cause_event_ids": ["evt_loran_tariff_01"],
                        }
                    ],
                },
                snap_120,
            )
        self.assertEqual(ctx_done.exception.code, "TASK_ALREADY_COMPLETED")

    def test_player_ledger_player_knowledge_and_arbitrary_path_protected(self) -> None:
        """验证严禁直接写玩家金钱、背包、属性、知情事实以及任意 JSON 路径。"""
        # 1. 尝试修改玩家金币或背包
        for forbidden_key in ("coins", "inventory", "player_state", "stamina"):
            with self.assertRaises(PlayerLedgerProtectedError):
                validate_proposal(
                    {
                        "instance_id": "inst_northern_frontier_001",
                        "mode": "advance",
                        "world_minute": 60,
                        forbidden_key: 100,
                        "operations": [],
                    },
                    self.snap,
                )

        # 2. 尝试直接写玩家知情事实或将 player 作为后台交谈目标
        with self.assertRaises(PlayerKnowledgeProtectedError):
            validate_proposal(
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "player_knowledge": [{"fact_id": "f1"}],
                    "operations": [],
                },
                self.snap,
            )

        with self.assertRaises(PlayerKnowledgeProtectedError):
            validate_proposal(
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_marta_innkeeper",
                            "expected_npc_revision": 1,
                            "action_kind": "conversation_plan",
                            "location": "霜木镇",
                            "target_npc_id": "player",
                            "conversation_topic": "后台强制塞消息给玩家",
                            "current_activity": "找玩家说话",
                            "motivation": "违规直接授知",
                            "next_due_minute": 120,
                            "cause_event_ids": ["evt_caravan_reroute_02"],
                        }
                    ],
                },
                self.snap,
            )

        # 3. 尝试使用 $set 或点号路径
        with self.assertRaises(ArbitraryPathForbiddenError):
            validate_proposal(
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "$set": {"npc.location": "王都"},
                    "operations": [],
                },
                self.snap,
            )

        # 4. 尝试推进尚未到期的 NPC（艾琳镇长 next_due_minute=180 > 60）
        with self.assertRaises(NpcNotDueError):
            validate_proposal(
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_eilin_mayor",
                            "expected_npc_revision": 1,
                            "action_kind": "task_progress",
                            "location": "霜木镇",
                            "task_id": "task_mayor_winter_reserve",
                            "task_stage": "提前推进",
                            "task_progress_percent": 50,
                            "current_activity": "未到期抢跑",
                            "motivation": "违规提前行动",
                            "next_due_minute": 240,
                            "cause_event_ids": ["evt_local_competition_03"],
                        }
                    ],
                },
                self.snap,
            )

        # 5. 伪造 Canon 来源与跨实例引用被拒绝
        with self.assertRaises(SourceFabricatedError):
            validate_proposal(
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_han_shopkeeper",
                            "expected_npc_revision": 1,
                            "action_kind": "travel",
                            "from_location": "霜木镇",
                            "to_location": "白河城",
                            "current_activity": "出行",
                            "motivation": "伪造来源",
                            "next_due_minute": 120,
                            "source_ids": ["entry:fabricated_secret_99"],
                        }
                    ],
                },
                self.snap,
            )

        ensure_instance_exists(self.conn, "inst_other_world_999", world_id="w2")
        snap_with_other = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 60},
        )
        with self.assertRaises(CrossInstanceReferenceError):
            validate_proposal(
                {
                    "instance_id": "inst_other_world_999",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [],
                },
                snap_with_other,
            )


if __name__ == "__main__":
    unittest.main()
