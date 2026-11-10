"""测试三档动态模拟深度（近景/关联/背景）、可测量预算、升降档理由及无到期跳过机制。"""

from __future__ import annotations

import sqlite3
import unittest

from app.m3.npcs import (
    DepthBudget,
    apply_proposal,
    evaluate_npc_depths,
    get_npc,
    list_due_npcs,
    read_snapshot,
    register_event_stub,
    seed_northern_trade_npcs,
)


class TestDepthAndSelection(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()

    def test_three_tier_depth_classification_and_promotion_demotion_reasons(self) -> None:
        """验证近景（foreground）、关联（associated）、背景（background）三档动态判定及升降档理由。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
        )

        snap_60 = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 60, "player_location": "霜木镇"},
        )
        evals_by_id = {e["npc_id"]: e for e in snap_60["tier_evaluations"]}

        # 霜木镇同城 NPC 判定为 foreground（近景）
        self.assertEqual(evals_by_id["npc_han_shopkeeper"]["current_tier"], "foreground")
        self.assertIn("CO_LOCATED_WITH_PLAYER", evals_by_id["npc_han_shopkeeper"]["reason_codes"])

        # 北境主商道参与活跃/到期事件的加雷特首领判定为 associated（关联）
        self.assertEqual(evals_by_id["npc_garret_caravan"]["current_tier"], "associated")
        self.assertIn("ACTIVE_DUE_EVENT_PARTICIPANT", evals_by_id["npc_garret_caravan"]["reason_codes"])

        # 白河城无当前直接关联事件的瑟蕾娜议员判定为 background（背景）
        self.assertEqual(evals_by_id["npc_serena_councillor"]["current_tier"], "background")
        self.assertIn("DISTANT_NO_ACTIVE_LINK", evals_by_id["npc_serena_councillor"]["reason_codes"])

        # 让韩掌柜在 60 分钟动身前往白河城，同时让原本处于 background 的瑟蕾娜议员被新区域事件关联
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
                        "npc_id": "npc_han_shopkeeper",
                        "expected_npc_revision": 1,
                        "action_kind": "travel",
                        "from_location": "霜木镇",
                        "to_location": "白河城",
                        "location": "白河城",
                        "current_activity": "乘车前往白河城洽谈水路生铁",
                        "motivation": "灰鬃商队改道延误，赴白河城寻替代铁料",
                        "duration_minutes": 90,
                        "next_due_minute": 150,
                        "cause_event_ids": ["evt_caravan_reroute_02"],
                    }
                ],
            },
        )
        register_event_stub(
            self.conn,
            "inst_northern_frontier_001",
            "evt_river_barge_treaty_06",
            title="白河城开放北境应急铁矿水运配额",
            location="白河城",
            scope="regional",
            participants=["瑟蕾娜议员", "韩掌柜"],
            status="active",
            created_minute=60,
            updated_minute=60,
            next_update_minute=120,
        )

        # 重新评估：韩掌柜离开霜木镇，应从 foreground 降档为 associated（LEFT_PLAYER_LOCATION）；
        # 瑟蕾娜议员参与到期区域事件，应从 background 升档为 associated（ACTIVE_DUE_EVENT_PARTICIPANT）
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120 WHERE id = 'inst_northern_frontier_001'"
        )
        snap_120 = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 120, "player_location": "霜木镇"},
        )
        evals_120 = {e["npc_id"]: e for e in snap_120["tier_evaluations"]}

        han_eval = evals_120["npc_han_shopkeeper"]
        self.assertEqual(han_eval["previous_tier"], "foreground")
        self.assertEqual(han_eval["current_tier"], "associated")
        self.assertEqual(han_eval["transition"], "demoted")
        self.assertIn("LEFT_PLAYER_LOCATION", han_eval["reason_codes"])
        self.assertIn("降档(近景->关联)", han_eval["reason_summary"])

        serena_eval = evals_120["npc_serena_councillor"]
        self.assertEqual(serena_eval["previous_tier"], "background")
        self.assertEqual(serena_eval["current_tier"], "associated")
        self.assertEqual(serena_eval["transition"], "promoted")
        self.assertIn("ACTIVE_DUE_EVENT_PARTICIPANT", serena_eval["reason_codes"])
        self.assertIn("升档(背景->关联)", serena_eval["reason_summary"])

    def test_foreground_and_background_measurable_budgets(self) -> None:
        """验证近景、关联与远景的数量配额、单条字符上限和总字符预算可测量且严格生效。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        # 将所有 10 名 NPC 均设为在 240 分钟到期
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 240 WHERE id = 'inst_northern_frontier_001'"
        )

        tight_budget = DepthBudget(
            max_foreground_npcs=2,
            max_associated_npcs=1,
            max_background_npcs=1,
            max_total_npcs=3,
            foreground_char_budget=180,
            associated_char_budget=120,
            background_char_budget=80,
            total_char_budget=380,
        )
        snap = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 240, "player_location": "霜木镇"},
            budget=tight_budget,
        )
        budget_info = snap["budget"]
        self.assertEqual(budget_info["total_evaluated_npcs"], 10)
        self.assertEqual(budget_info["total_due_candidates"], 10)
        self.assertLessEqual(budget_info["selected_foreground_count"], 2)
        self.assertLessEqual(budget_info["selected_associated_count"], 1)
        self.assertLessEqual(budget_info["selected_background_count"], 1)
        self.assertLessEqual(budget_info["selected_total_count"], 3)
        self.assertLessEqual(budget_info["used_chars"], 380)

        # 确认被排除的到期 NPC 均记录了明确的 excluded_reason
        excluded = [
            e for e in snap["tier_evaluations"]
            if e["is_due"] and not e["selected_for_simulation"]
        ]
        self.assertEqual(len(excluded), 10 - len(snap["due_npcs"]))
        for item in excluded:
            self.assertIn(
                item["excluded_reason"],
                {
                    "TIER_FOREGROUND_COUNT_BUDGET_EXCEEDED",
                    "TIER_ASSOCIATED_COUNT_BUDGET_EXCEEDED",
                    "TIER_BACKGROUND_COUNT_BUDGET_EXCEEDED",
                    "TOTAL_COUNT_BUDGET_EXCEEDED",
                    "TOTAL_CHAR_BUDGET_EXCEEDED",
                },
            )

    def test_no_due_npcs_skip_and_no_state_mutation(self) -> None:
        """验证无到期 NPC 或目标时间超前时返回 skip 且不发生任何状态变更。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        # 在第 30 分钟，所有 NPC 最早到期时间为 60 分钟
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 30 WHERE id = 'inst_northern_frontier_001'"
        )
        due_list = list_due_npcs(self.conn, "inst_northern_frontier_001")
        self.assertEqual(due_list["items"], [])

        snap_30 = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 30},
        )
        self.assertTrue(snap_30["skip"])
        self.assertEqual(snap_30["status"], "skipped")
        self.assertEqual(snap_30["reason_code"], "NO_DUE_NPCS")
        self.assertEqual(snap_30["earliest_next_due_minute"], 60)

        # 应用空操作提议时，世界版本与 NPC 版本完全不变
        han_before = get_npc(self.conn, "inst_northern_frontier_001", "npc_han_shopkeeper")
        res = apply_proposal(
            self.conn,
            "inst_northern_frontier_001",
            {"instance_id": "inst_northern_frontier_001", "mode": "advance", "world_minute": 30, "operations": []},
        )
        self.assertEqual(res["applied_operations"], 0)
        self.assertEqual(res["world_revision"], 1)
        han_after = get_npc(self.conn, "inst_northern_frontier_001", "npc_han_shopkeeper")
        self.assertEqual(han_before, han_after)

        # 目标分钟超前于已确认分钟时直接跳过
        snap_future = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 90},
        )
        self.assertTrue(snap_future["skip"])
        self.assertEqual(snap_future["reason_code"], "DUE_TIME_EXCEEDS_CONFIRMED_MINUTE")


if __name__ == "__main__":
    unittest.main()
