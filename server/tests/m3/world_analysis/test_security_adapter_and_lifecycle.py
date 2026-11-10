"""验证越界引用拦截、未公开信息限制、重复分析/失败重试/重开/分支隔离，以及与 m3/events、m3/npcs、m3/knowledge 三大账本的无损对接。"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

from app.m3 import knowledge, npcs
from app.m3.npcs.errors import DuplicateNpcTaskError
from app.m3.world_analysis import (
    WORLD_A_ID,
    WORLD_B_ID,
    AnalysisStateConflictError,
    CrossInstanceAnalysisError,
    CrossWorldSourceError,
    FakeWorldAnalysisModelClient,
    PlayerLedgerProtectedError,
    SourceFabricatedError,
    StartingPointSpec,
    UnpublicInfoLeakError,
    WorldAnalysisValidationContext,
    analyze_world_initial_situation,
    apply_draft_to_instance_in_savepoint,
    extract_and_batch_world_sources,
    record_failed_analysis_attempt,
    save_analysis_draft,
    seed_maritime_salt_world,
    seed_skyforge_academy_world,
    validate_analysis_draft,
)


class TestSecurityAdapterAndLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.world_a = seed_maritime_salt_world(
            self.conn, world_id=WORLD_A_ID, instance_id="inst_yunze_001"
        )
        self.world_b = seed_skyforge_academy_world(
            self.conn, world_id=WORLD_B_ID, instance_id="inst_skyforge_001"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_cross_world_source_and_fabricated_source_rejected(self) -> None:
        """验证在资料挑选阶段或草案校验阶段引用其他世界（world_b）的来源或伪造来源均被严格拦截。"""
        with self.assertRaises(CrossWorldSourceError) as cw_ctx:
            extract_and_batch_world_sources(
                self.conn,
                WORLD_A_ID,
                "白鹭水驿",
                selected_source_ids=["entry:skyforge_geography_01"],
            )
        self.assertEqual(cw_ctx.exception.status, 403)
        self.assertEqual(cw_ctx.exception.code, "CROSS_WORLD_SOURCE_FORBIDDEN")

        with self.assertRaises(SourceFabricatedError) as fab_ctx:
            extract_and_batch_world_sources(
                self.conn,
                WORLD_A_ID,
                "白鹭水驿",
                selected_source_ids=["chunk:non_existent_fake_chunk"],
            )
        self.assertEqual(fab_ctx.exception.status, 422)

        ext_a = extract_and_batch_world_sources(self.conn, WORLD_A_ID, "白鹭水驿")
        fake_model = FakeWorldAnalysisModelClient()
        valid_draft = asyncio.run(
            analyze_world_initial_situation(ext_a, fake_model, instance_id="inst_yunze_001")
        )
        tampered = valid_draft.to_dict()
        tampered["events"][0]["source_ids"] = ["entry:skyforge_geography_01"]

        val_ctx = WorldAnalysisValidationContext(
            world_id=WORLD_A_ID,
            instance_id="inst_yunze_001",
            starting_point=StartingPointSpec(location="白鹭水驿", world_minute=0),
            allowed_source_ids=frozenset(ext_a["allowed_source_ids"]),
            other_world_source_ids=ext_a["other_world_source_ids"],
            other_instance_ids=frozenset({"inst_skyforge_001"}),
        )
        with self.assertRaises(CrossWorldSourceError):
            validate_analysis_draft(tampered, val_ctx)

        tampered_inst = valid_draft.to_dict()
        tampered_inst["npcs"][0]["npc_id"] = "npc_from_inst_skyforge_001"
        with self.assertRaises(CrossInstanceAnalysisError):
            validate_analysis_draft(tampered_inst, val_ctx)

    def test_unpublic_info_restriction_and_player_ledger_protection(self) -> None:
        """验证角色未获知的远方/隐藏设定、NPC 私密动机不得进入玩家开局摘要，且严禁修改玩家个人账本。"""
        ext_a = extract_and_batch_world_sources(self.conn, WORLD_A_ID, "白鹭水驿")
        fake_model = FakeWorldAnalysisModelClient()
        draft = asyncio.run(
            analyze_world_initial_situation(ext_a, fake_model, instance_id="inst_yunze_001")
        )

        hidden_events = [e for e in draft.events if not e.player_known_at_start]
        self.assertGreaterEqual(len(hidden_events), 1)
        summary_titles = {s["title"] for s in draft.player_opening_summary["known_situations"]}
        for h_ev in hidden_events:
            self.assertNotIn(h_ev.title, summary_titles)

        for pub_npc in draft.player_opening_summary["present_npcs"]:
            self.assertEqual(pub_npc["location"], "白鹭水驿")
            self.assertNotIn("motivation", pub_npc)
            self.assertNotIn("goal", pub_npc)
            self.assertNotIn("cause_event_ids", pub_npc)

        val_ctx = WorldAnalysisValidationContext(
            world_id=WORLD_A_ID,
            instance_id="inst_yunze_001",
            starting_point=StartingPointSpec(location="白鹭水驿", world_minute=0),
            allowed_source_ids=frozenset(ext_a["allowed_source_ids"]),
        )

        leaked_draft = draft.to_dict()
        leaked_draft["player_opening_summary"]["known_situations"].append(
            {
                "event_id": hidden_events[0].event_id,
                "title": hidden_events[0].title,
                "summary": hidden_events[0].description,
            }
        )
        with self.assertRaises(UnpublicInfoLeakError):
            validate_analysis_draft(leaked_draft, val_ctx)

        leaked_npc_draft = draft.to_dict()
        leaked_npc_draft["player_opening_summary"]["present_npcs"][0]["motivation"] = "暗中联络私盐船"
        with self.assertRaises(UnpublicInfoLeakError):
            validate_analysis_draft(leaked_npc_draft, val_ctx)

        ledger_tampered = draft.to_dict()
        ledger_tampered["coins_delta"] = 500
        with self.assertRaises(PlayerLedgerProtectedError):
            validate_analysis_draft(ledger_tampered, val_ctx)

    def test_duplicate_analysis_retry_reopen_branch_and_atomic_domain_integration(self) -> None:
        """验证未确认草案不写正式表、重复分析幂等与冲突拦截、失败后重试，以及确认后原子写入 events/npcs/knowledge 并可被 Li 模块读取。"""
        ext_a = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            {"location": "白鹭水驿", "world_minute": 60, "request_id": "req_init_001"},
        )
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_yunze_001'"
        )
        fake_model = FakeWorldAnalysisModelClient()

        # 1. 先模拟一次失败的分析请求（status='failed_retryable'）
        fail_rec = record_failed_analysis_attempt(
            self.conn,
            world_id=WORLD_A_ID,
            instance_id="inst_yunze_001",
            request_id="req_init_001",
            starting_location="白鹭水驿",
            world_minute=60,
            error_code="MODEL_TIMEOUT",
            error_message="连接超时",
        )
        self.assertEqual(fail_rec["status"], "failed_retryable")
        self.assertTrue(fail_rec["retryable"])

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM world_events WHERE instance_id='inst_yunze_001'").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM m3_npcs WHERE instance_id='inst_yunze_001'").fetchone()[0],
            0,
        )

        # 2. 对同一 request_id 执行重试并暂存草案（status='ready_to_confirm'）
        draft = asyncio.run(
            analyze_world_initial_situation(ext_a, fake_model, instance_id="inst_yunze_001")
        )
        saved = save_analysis_draft(self.conn, draft)
        self.assertEqual(saved["status"], "ready_to_confirm")
        self.assertEqual(saved["run_mode"], "retry")
        self.assertEqual(saved["attempt_count"], 2)
        self.assertFalse(saved["idempotent_replay"])

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM world_events WHERE instance_id='inst_yunze_001'").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM m3_npcs WHERE instance_id='inst_yunze_001'").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM m3_knowledge_facts WHERE instance_id='inst_yunze_001'").fetchone()[0],
            0,
        )

        # 3. 重复提交相同 request_id 与相同参数 -> 返回幂等缓存结果
        replay = save_analysis_draft(self.conn, draft)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["draft_id"], saved["draft_id"])

        # 若复用同一 request_id 但篡改起点为「千帆港」-> 409 DUPLICATE_ANALYSIS_CONFLICT
        ext_diff = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            {"location": "千帆港", "world_minute": 60, "request_id": "req_init_001"},
        )
        draft_diff = asyncio.run(
            analyze_world_initial_situation(ext_diff, fake_model, instance_id="inst_yunze_001")
        )
        with self.assertRaises(AnalysisStateConflictError) as conf_err:
            save_analysis_draft(self.conn, draft_diff)
        self.assertEqual(conf_err.exception.status, 409)
        self.assertEqual(conf_err.exception.code, "DUPLICATE_ANALYSIS_CONFLICT")

        # 4. 玩家确认草案 -> 原子写入 events、npcs、knowledge 三大账本
        applied = apply_draft_to_instance_in_savepoint(
            self.conn, "inst_yunze_001", draft
        )
        self.assertEqual(applied["status"], "applied")
        self.assertFalse(applied["idempotent_replay"])
        self.assertEqual(len(applied["created_event_ids"]), len(draft.events))
        self.assertEqual(len(applied["created_npc_ids"]), len(draft.npcs))

        applied_again = apply_draft_to_instance_in_savepoint(
            self.conn, "inst_yunze_001", draft
        )
        self.assertTrue(applied_again["idempotent_replay"])

        # 5. 验证 Li 的 NPC 模块可直接读取初始化后的人物状态与到期动作
        contactable = npcs.list_contactable_npcs(
            self.conn,
            "inst_yunze_001",
            player_location="白鹭水驿",
            world_minute=60,
            include_unavailable_at_location=True,
        )
        self.assertGreaterEqual(len(contactable["items"]), 1)

        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120 WHERE id = 'inst_yunze_001'"
        )
        npc_snap = npcs.read_snapshot(
            self.conn,
            "inst_yunze_001",
            {"mode": "advance", "target_world_minute": 120, "player_location": "白鹭水驿"},
        )
        self.assertEqual(npc_snap["status"], "ready")
        self.assertFalse(npc_snap["skip"])
        self.assertGreaterEqual(len(npc_snap["due_npcs"]), 1)

        # 6. 验证知识账本中玩家仅获知公开/本地可见事件，未获知隐藏事件
        player_known_page = knowledge.list_known(self.conn, "inst_yunze_001", "player")
        expected_public_count = sum(1 for e in draft.events if e.player_known_at_start)
        self.assertEqual(len(player_known_page["items"]), expected_public_count)

        # 7. 验证重开新局（reopen）在新实例（inst_yunze_reopen_002）中可安全复用相同的实例内领域 ID
        self.conn.execute(
            """
            INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision)
            VALUES ('inst_yunze_reopen_002', ?, 'adv_yunze_002', 60, 0)
            """,
            (WORLD_A_ID,),
        )
        ext_reopen = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            {
                "location": "白鹭水驿",
                "world_minute": 60,
                "run_mode": "reopen",
                "request_id": "req_reopen_002",
            },
        )
        draft_reopen = asyncio.run(
            analyze_world_initial_situation(
                ext_reopen, fake_model, instance_id="inst_yunze_reopen_002"
            )
        )
        self.assertEqual(draft_reopen.run_mode, "reopen")
        applied_reopen = apply_draft_to_instance_in_savepoint(
            self.conn, "inst_yunze_reopen_002", draft_reopen
        )
        self.assertEqual(applied_reopen["created_npc_ids"], applied["created_npc_ids"])
        self.assertEqual(applied_reopen["created_event_ids"], applied["created_event_ids"])

    def test_savepoint_atomic_rollback_on_mid_apply_conflict(self) -> None:
        """验证确认应用过程中若下游模块（如 NPC 已存在冲突）报错，SAVEPOINT 将同批次已写入的 world_events 完整回滚且不破坏外层未提交事务。"""
        ext_b = extract_and_batch_world_sources(self.conn, WORLD_B_ID, "天工外院")
        fake_model = FakeWorldAnalysisModelClient()
        draft_b = asyncio.run(
            analyze_world_initial_situation(ext_b, fake_model, instance_id="inst_skyforge_001")
        )

        # 在外层事务中预先插入一条无关世界记录，以及一个同 ID 的冲突 NPC 以触发中途失败
        self.conn.execute(
            """
            INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at)
            VALUES ('world_outer_tx_marker', '外层事务标记', '', '2026-11-10T00:00:00Z', '2026-11-10T00:00:00Z', NULL)
            """
        )
        colliding_npc_id = draft_b.npcs[0].npc_id
        self.conn.execute(
            """
            INSERT INTO m3_npcs (
                instance_id, npc_id, name, role, location, current_activity,
                current_action_kind, motivation, created_minute, updated_minute, last_action_minute, next_due_minute, revision
            ) VALUES ('inst_skyforge_001', ?, '预置冲突人物', '工匠', '天工外院', '值守', 'shift_change', '测试冲突', 0, 0, 0, 30, 1)
            """,
            (colliding_npc_id,),
        )

        with self.assertRaises(DuplicateNpcTaskError):
            apply_draft_to_instance_in_savepoint(
                self.conn, "inst_skyforge_001", draft_b
            )

        # 验证中途先写入的 world_events 已被 SAVEPOINT 完整回滚（数量为 0），且外层事务标记仍在！
        ev_count = self.conn.execute(
            "SELECT COUNT(*) FROM world_events WHERE instance_id='inst_skyforge_001'"
        ).fetchone()[0]
        self.assertEqual(ev_count, 0)
        marker_exists = self.conn.execute(
            "SELECT COUNT(*) FROM worlds WHERE id='world_outer_tx_marker'"
        ).fetchone()[0]
        self.assertEqual(marker_exists, 1)


if __name__ == "__main__":
    unittest.main()
