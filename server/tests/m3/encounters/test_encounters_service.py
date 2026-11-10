"""M3.3 / M3.6 玩家主动查证、交谈、NPC 可接触性与知情边界单元测试（M3 Wave 3）。"""

from __future__ import annotations

import sqlite3
from typing import Any
import unittest

from app.m3.encounters import (
    CARAVAN_EVENT_ID,
    EncounterError,
    EncountersAdapter,
    NORTH_TOWN_INSTANCE_ID,
    NORTH_TOWN_WORLD_ID,
    commit_encounter_draft,
    ensure_instance,
    evaluate_encounter_inquiry,
    get_encounter_entrypoint_bundle,
    list_news_action_options,
    list_player_world_news,
    prepare_encounter_draft,
    register_holder_fact,
    register_npc_stub,
    restore,
    run_wave3_innkeeper_caravan_encounter_fixture,
    seed_innkeeper_caravan_scenario,
    select_action_option_for_composer,
    snapshot,
    update_npc_stub_state,
    validate_encounter_proposal,
)


class TestEncountersService(unittest.TestCase):
    """验证世界消息行动入口、NPC 知情边界、途中离开、冲突说法、传闻不改账与分支隔离。"""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.seeded = seed_innkeeper_caravan_scenario(self.conn)
        self.inst_id = self.seeded["instance_id"]
        self.rumor_fact_id = self.seeded["player_rumor_fact_id"]

    def tearDown(self) -> None:
        self.conn.close()

    def test_innkeeper_caravan_full_loop_option_edit_draft_commit_news(self) -> None:
        """验收1：旅店店主转述商队改道完整链路（选项 → 编辑行动 → 草案 → 确认 → 消息），验证选项与草案不推进时钟。"""
        # 1. 获取行动入口选项，验证不推进时钟、不自动确认回合
        opt_bundle = list_news_action_options(
            self.conn, self.inst_id, self.rumor_fact_id, player_location="霜木镇"
        )
        self.assertFalse(opt_bundle["advances_clock"])
        self.assertFalse(opt_bundle["auto_commits_turn"])
        self.assertEqual(opt_bundle["world_minute"], 240)

        kinds = [o["kind"] for o in opt_bundle["options"]]
        self.assertIn("ask_npc", kinds)
        self.assertIn("check_notice", kinds)
        self.assertIn("free_edit", kinds)

        # 2. 选择「询问玛塔老板娘」建议写入输入区草稿
        composer_res = select_action_option_for_composer(
            self.conn,
            self.inst_id,
            fact_id=self.rumor_fact_id,
            option_kind="ask_npc",
            target_npc_id="npc_marta_innkeeper",
            current_draft="",
        )
        self.assertFalse(composer_res["advances_clock"])
        self.assertFalse(composer_res["auto_commits_turn"])
        self.assertIn("玛塔老板娘", composer_res["composer_draft_text"])

        # 追加第二条建议验证换行拼接与长度限制保护
        composer_res_2 = select_action_option_for_composer(
            self.conn,
            self.inst_id,
            fact_id=self.rumor_fact_id,
            option_kind="free_edit",
            current_draft=composer_res["composer_draft_text"],
        )
        self.assertIn("\n", composer_res_2["composer_draft_text"])

        # 3. 玩家编辑行动文本并生成待确认草案（验证此时玩家消息仍只有 1 条传闻）
        edited_text = composer_res["composer_draft_text"] + "请她细说商队改走白河谷地的经过。"
        draft = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_marta_innkeeper",
                "action_text": edited_text,
            },
        )
        self.assertEqual(draft["outcome"], "clue")
        self.assertFalse(draft["clock_advanced"])
        self.assertFalse(draft["committed"])
        self.assertFalse(draft["can_settle_ledger"])
        self.assertEqual(len(draft["proposed_transmissions"]), 1)
        self.assertEqual(len(list_player_world_news(self.conn, self.inst_id)), 1)

        # 4. 确认回合：提交传播提议，验证新增玛塔掌柜转述的 confirmed 消息
        commit_res = commit_encounter_draft(
            self.conn,
            self.inst_id,
            draft["draft_id"],
            commit_world_minute=250,
            expected_world_revision=2,
        )
        self.assertTrue(commit_res["committed"])
        self.assertFalse(commit_res["ledger_modified"])
        self.assertEqual(commit_res["world_minute"], 250)
        self.assertEqual(commit_res["world_revision"], 3)
        self.assertEqual(len(commit_res["new_messages"]), 1)
        new_msg = commit_res["new_messages"][0]
        self.assertEqual(new_msg["knowledge_type"], "confirmed")
        self.assertEqual(new_msg["channel"], "conversation")
        self.assertEqual(new_msg["source_person_id"], "npc_marta_innkeeper")
        self.assertIn("白河谷地", new_msg["content"])
        self.assertNotIn("source_event_id", new_msg)

        # 幂等重放同一 draft_id 不重复写入消息
        replay_res = commit_encounter_draft(self.conn, self.inst_id, draft["draft_id"])
        self.assertTrue(replay_res["idempotent_replay"])
        self.assertEqual(len(list_player_world_news(self.conn, self.inst_id)), 2)

        # 5. 验证内置端到端 Fixture 函数
        fixture_out = run_wave3_innkeeper_caravan_encounter_fixture()
        self.assertEqual(fixture_out["news_before_commit_count"], 1)
        self.assertEqual(fixture_out["news_after_commit_count"], 2)
        self.assertEqual(fixture_out["branch_news_count"], 1)

    def test_npc_unknown_and_strict_own_knowledge_isolation(self) -> None:
        """验收2：NPC 只使用自身知情记录回答 — 询问不知情的雷恩队长或尚未到获知时刻的 NPC 返回 unknown 且零传播。"""
        # 雷恩队长在霜木镇可交谈，但没有 CARAVAN_EVENT_ID 的知情记录
        draft_unknown = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_renn_captain",
                "action_text": "向雷恩队长询问灰鬃商队今日为何迟迟未到。",
            },
        )
        self.assertEqual(draft_unknown["outcome"], "unknown")
        self.assertEqual(draft_unknown["reason_code"], "NPC_HAS_NO_KNOWLEDGE")
        self.assertEqual(draft_unknown["proposed_transmissions"], [])

        # 若雷恩队长在未来时刻（300 分钟）才得知消息，在当前 240 分钟询问仍必须返回 unknown
        register_holder_fact(
            self.conn,
            self.inst_id,
            {
                "fact_id": "fact_renn_late_300",
                "holder_id": "npc_renn_captain",
                "knowledge_type": "confirmed",
                "channel": "notice",
                "title": "雷恩稍后收到的巡防通报",
                "content": "白河谷地方向有商队绕行。",
                "source_event_id": CARAVAN_EVENT_ID,
                "learned_minute": 300,
                "confidence": 0.9,
            },
        )
        res_still_unknown = evaluate_encounter_inquiry(
            self.conn,
            self.inst_id,
            origin_fact_id=self.rumor_fact_id,
            option_kind="ask_npc",
            target_npc_id="npc_renn_captain",
            action_text="再次在 240 分钟询问雷恩队长。",
            world_minute=240,
        )
        self.assertEqual(res_still_unknown.outcome, "unknown")
        self.assertEqual(len(res_still_unknown.transmission_proposals), 0)

    def test_npc_departed_on_travel_and_departed_before_commit_rollback(self) -> None:
        """验收3：NPC 途中离开 — 已出行的韩掌柜返回 npc_departed；草案生成后、确认前离开则抛出 409 NPC_DEPARTED_BEFORE_COMMIT 并回滚。"""
        # 1. 韩掌柜已动身前往寒铁城，在霜木镇询问返回 npc_departed
        draft_departed = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_han_shopkeeper",
                "action_text": "去铁料铺找韩掌柜询问生铁到货情况。",
            },
        )
        self.assertEqual(draft_departed["outcome"], "npc_departed")
        self.assertEqual(draft_departed["reason_code"], "NPC_LEFT_FOR_TRAVEL")
        self.assertEqual(draft_departed["proposed_transmissions"], [])

        # 2. 玩家先向在场的玛塔老板娘生成草案，但确认前玛塔老板娘临时动身前往白河城
        draft_marta = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_marta_innkeeper",
                "action_text": "向玛塔老板娘打听商队动向。",
            },
        )
        self.assertEqual(draft_marta["outcome"], "clue")

        update_npc_stub_state(
            self.conn,
            self.inst_id,
            "npc_marta_innkeeper",
            location="白河城",
            current_activity="临时乘车前往白河城采购麦酒",
            current_action_kind="travel",
            updated_minute=245,
        )

        with self.assertRaises(EncounterError) as ctx_left:
            commit_encounter_draft(
                self.conn,
                self.inst_id,
                draft_marta["draft_id"],
                commit_world_minute=250,
            )
        self.assertEqual(ctx_left.exception.status, 409)
        self.assertEqual(ctx_left.exception.code, "NPC_DEPARTED_BEFORE_COMMIT")
        # 确认失败后玩家消息列表未增加任何记录
        self.assertEqual(len(list_player_world_news(self.conn, self.inst_id)), 1)

    def test_refused_vague_conflicting_accounts_and_notice_board(self) -> None:
        """验收4：验证拒答（refused）、含混回应（vague）、互相冲突的说法（conflicting）与查阅公告（check_notice）。"""
        # 1. 询问林恩特使 -> 拒答 (refused)
        res_refused = evaluate_encounter_inquiry(
            self.conn,
            self.inst_id,
            origin_fact_id=self.rumor_fact_id,
            option_kind="ask_npc",
            target_npc_id="npc_linn_envoy",
            action_text="向林恩特使询问星辉商会对灰鬃商队改道的看法。",
        )
        self.assertEqual(res_refused.outcome, "refused")
        self.assertEqual(res_refused.reason_code, "NPC_REFUSED_TO_ANSWER")
        self.assertEqual(len(res_refused.transmission_proposals), 0)

        # 2. 将老巴克设为 evasive 测试含混回应 (vague)
        update_npc_stub_state(
            self.conn,
            self.inst_id,
            "npc_buck_porter",
            response_policy="evasive",
        )
        res_vague = evaluate_encounter_inquiry(
            self.conn,
            self.inst_id,
            origin_fact_id=self.rumor_fact_id,
            option_kind="ask_npc",
            target_npc_id="npc_buck_porter",
            action_text="向老巴克打听货栈传言。",
        )
        self.assertEqual(res_vague.outcome, "vague")
        self.assertEqual(res_vague.reason_code, "NPC_VAGUE_RESPONSE")
        self.assertEqual(len(res_vague.transmission_proposals), 1)
        self.assertEqual(res_vague.transmission_proposals[0].knowledge_type, "suspected")

        # 3. 恢复老巴克为 cooperative（持有 mistaken 误信），先确认老巴克的误信说法，再询问玛塔掌柜（持有 confirmed 实情），触发 conflicting
        update_npc_stub_state(
            self.conn,
            self.inst_id,
            "npc_buck_porter",
            response_policy="cooperative",
        )
        draft_buck = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_buck_porter",
                "action_text": "听老巴克讲述边关扣押的传言。",
            },
        )
        commit_encounter_draft(self.conn, self.inst_id, draft_buck["draft_id"], commit_world_minute=245)

        draft_conflict = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_marta_innkeeper",
                "action_text": "拿老巴克的说法向玛塔老板娘当面核实。",
            },
        )
        self.assertEqual(draft_conflict["outcome"], "conflicting")
        self.assertEqual(draft_conflict["reason_code"], "CONFLICTING_ACCOUNTS_DETECTED")
        self.assertIsNotNone(draft_conflict["conflict_note"])
        self.assertGreater(len(draft_conflict["conflicting_fact_ids"]), 0)

        commit_conflict = commit_encounter_draft(
            self.conn, self.inst_id, draft_conflict["draft_id"], commit_world_minute=250
        )
        all_news = commit_conflict["all_player_news"]
        types_in_news = {m["knowledge_type"] for m in all_news}
        # 玩家消息同时保留 rumor、mistaken 与 confirmed 三种不同认知记录
        self.assertEqual(types_in_news, {"rumor", "mistaken", "confirmed"})

        # 4. 查阅公告栏 (check_notice)
        draft_notice = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "check_notice",
                "action_text": "前往霜木镇公告栏查阅商栈通告。",
            },
        )
        self.assertEqual(draft_notice["outcome"], "clue")
        self.assertEqual(draft_notice["proposed_transmissions"][0]["channel"], "notice")

    def test_custom_injectable_providers_and_no_rumor_ledger_settlement(self) -> None:
        """验收5：验证可注入的自定义 NPC 可接触性/知情查询边界，以及严禁传闻或查证直接改账。"""

        class CustomContactabilityProvider:
            def check_contactability(
                self, conn: Any, instance_id: str, npc_id: str, *, player_location: str, world_minute: int
            ) -> dict[str, Any]:
                return {
                    "instance_id": instance_id,
                    "npc_id": npc_id,
                    "name": "自定义注入掌柜",
                    "role": "注入测试角色",
                    "contactable": True,
                    "reason_code": "NPC_CONTACTABLE",
                    "reason": "由依赖注入提供可接触状态",
                    "player_location": player_location,
                    "npc_location": player_location,
                    "current_activity": "在注入测试场景中接待询问",
                    "current_action_kind": "idle",
                    "schedule_slot": "day_shift",
                    "response_policy": "cooperative",
                    "refusal_reason": "",
                    "world_minute": world_minute,
                    "revision": 1,
                }

            def list_contactable_npcs(
                self, conn: Any, instance_id: str, *, location: str, world_minute: int
            ) -> list[dict[str, Any]]:
                return [
                    self.check_contactability(
                        conn, instance_id, "npc_injected_01", player_location=location, world_minute=world_minute
                    )
                ]

        bundle = get_encounter_entrypoint_bundle(
            self.conn,
            self.inst_id,
            contactability_provider=CustomContactabilityProvider(),
        )
        self.assertEqual(bundle["contactable_npcs"][0]["npc_id"], "npc_injected_01")

        # 拦截任何尝试在查证草案或传播提议中夹带玩家金钱、背包、属性变更的请求
        for forbidden_key in ("coins", "coins_delta", "inventory", "inventory_changes", "stamina_delta", "proposed_changes"):
            with self.assertRaises(EncounterError) as ctx_ledger:
                prepare_encounter_draft(
                    self.conn,
                    self.inst_id,
                    {
                        "fact_id": self.rumor_fact_id,
                        "option_kind": "ask_npc",
                        "target_npc_id": "npc_marta_innkeeper",
                        "action_text": "询问商队改道",
                        forbidden_key: 999,
                    },
                )
            self.assertEqual(ctx_ledger.exception.code, "PLAYER_LEDGER_PROTECTED")

        with self.assertRaises(EncounterError) as ctx_path:
            validate_encounter_proposal(
                {
                    "instance_id": self.inst_id,
                    "transmissions": [{"$set": {"coins": 100}}],
                }
            )
        self.assertEqual(ctx_path.exception.code, "ARBITRARY_PATH_FORBIDDEN")

    def test_branch_isolation_snapshot_restore_and_cross_instance_guards(self) -> None:
        """验收6：验证检查点分支隔离（snapshot / restore / EncountersAdapter）与跨实例访问拦截。"""
        adapter = EncountersAdapter()
        snap_before = adapter.snapshot(self.conn, self.inst_id)

        # 主实例完成与玛塔老板娘的交谈并确认
        draft = prepare_encounter_draft(
            self.conn,
            self.inst_id,
            {
                "fact_id": self.rumor_fact_id,
                "option_kind": "ask_npc",
                "target_npc_id": "npc_marta_innkeeper",
                "action_text": "向玛塔老板娘核实商队改道。",
            },
        )
        commit_encounter_draft(self.conn, self.inst_id, draft["draft_id"], commit_world_minute=250)
        self.assertEqual(len(list_player_world_news(self.conn, self.inst_id)), 2)

        # 恢复快照到新分支 inst_branch_b
        branch_b = "inst_encounter_branch_b"
        ensure_instance(self.conn, branch_b, world_id=NORTH_TOWN_WORLD_ID, world_minute=240, revision=2)
        adapter.restore(self.conn, snap_before, new_instance_id=branch_b, world_id=NORTH_TOWN_WORLD_ID)

        # 分支 B 仍只有初始 1 条传闻
        self.assertEqual(len(list_player_world_news(self.conn, branch_b)), 1)

        # 在分支 B 中只存在的新传闻，在主实例中尝试查证必须抛出 403 CROSS_INSTANCE_ACCESS
        register_holder_fact(
            self.conn,
            branch_b,
            {
                "fact_id": "fact_only_in_branch_b",
                "holder_id": "player",
                "knowledge_type": "rumor",
                "channel": "rumor",
                "title": "分支 B 独有传闻",
                "content": "仅在分支 B 出现的传闻。",
                "source_event_id": CARAVAN_EVENT_ID,
                "learned_minute": 240,
                "confidence": 0.5,
            },
        )
        with self.assertRaises(EncounterError) as ctx_cross:
            list_news_action_options(self.conn, self.inst_id, "fact_only_in_branch_b")
        self.assertEqual(ctx_cross.exception.status, 403)
        self.assertEqual(ctx_cross.exception.code, "CROSS_INSTANCE_ACCESS")


if __name__ == "__main__":
    unittest.main()
