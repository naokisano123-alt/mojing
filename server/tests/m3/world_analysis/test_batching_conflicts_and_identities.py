"""验证缺资料拦截、已有设定冲突检测与保留、长文分批与合并策略（含每次请求输入范围）、重名消歧与跨批次身份复用。"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

from app.m3.world_analysis import (
    WORLD_A_ID,
    AnalysisSourceRequiredError,
    FakeWorldAnalysisModelClient,
    IdentityCollisionError,
    OpenAIWorldAnalysisModelClient,
    SourceFabricatedError,
    StartingPointSpec,
    WorldAnalysisValidationContext,
    analyze_world_initial_situation,
    append_long_document_and_injection_sample,
    build_mock_http_analysis_transport,
    ensure_base_world_tables,
    extract_and_batch_world_sources,
    merge_batch_extractions,
    seed_maritime_salt_world,
    validate_analysis_draft,
)


class TestBatchingConflictsAndIdentities(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_base_world_tables(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_missing_or_unconfirmed_sources_blocked_clearly(self) -> None:
        """验证空世界、仅有未确认草稿条目或已软删除文档时返回 insufficient_sources 并拦截模型分析。"""
        self.conn.execute(
            """
            INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at)
            VALUES ('world_empty', '空白世界', '', '2026-11-10T00:00:00Z', '2026-11-10T00:00:00Z', NULL)
            """
        )
        self.conn.execute(
            """
            INSERT INTO lore_entries (
                id, world_id, type, title, summary, status, revision, origin, created_at, updated_at, deleted_at
            ) VALUES ('draft_only_01', 'world_empty', 'location', '未确认草稿', '尚处于草稿状态', 'draft', 1, 'manual', '2026-11-10T00:00:00Z', '2026-11-10T00:00:00Z', NULL)
            """
        )

        ext = extract_and_batch_world_sources(self.conn, "world_empty", "未知起点")
        self.assertEqual(ext["status"], "insufficient_sources")
        self.assertTrue(ext["blocked"])
        self.assertEqual(ext["reason_code"], "ANALYSIS_SOURCE_REQUIRED")
        self.assertEqual(len(ext["batches"]), 0)

        with self.assertRaises(AnalysisSourceRequiredError) as ctx:
            extract_and_batch_world_sources(
                self.conn, "world_empty", "未知起点", raise_on_empty=True
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "ANALYSIS_SOURCE_REQUIRED")

        fake_model = FakeWorldAnalysisModelClient()
        with self.assertRaises(AnalysisSourceRequiredError):
            asyncio.run(
                analyze_world_initial_situation(ext, fake_model, instance_id="inst_empty")
            )

    def test_setting_conflicts_detected_and_preserved_in_draft_and_summary(self) -> None:
        """验证已确认设定条目（亥时宵禁）与导入文档传闻分块（从不宵禁）冲突时被自动识别、优先采用确认设定并在草案中提示。"""
        seed_maritime_salt_world(
            self.conn,
            world_id=WORLD_A_ID,
            instance_id="inst_yunze_001",
            include_conflict_chunk=True,
        )
        ext = extract_and_batch_world_sources(self.conn, WORLD_A_ID, "白鹭水驿")
        self.assertGreaterEqual(len(ext["detected_conflicts"]), 1)

        conflict = ext["detected_conflicts"][0]
        self.assertEqual(conflict.resolution_policy, "prefer_confirmed_entry")
        self.assertTrue(conflict.primary_source_id.startswith("entry:"))
        self.assertTrue(conflict.conflicting_source_id.startswith("chunk:"))

        fake_model = FakeWorldAnalysisModelClient()
        draft = asyncio.run(
            analyze_world_initial_situation(ext, fake_model, instance_id="inst_yunze_001")
        )
        self.assertGreaterEqual(len(draft.conflicts), 1)
        self.assertGreaterEqual(len(draft.player_opening_summary["conflicts"]), 1)
        summary_conflict = draft.player_opening_summary["conflicts"][0]
        self.assertIn("宵禁", summary_conflict["summary"])

    def test_long_document_batching_input_ranges_and_multi_batch_merging(self) -> None:
        """验证长文多章节分批策略、每次请求的精确 BatchInputRange，以及跨批次同人别名合并与同名不同人消歧。"""
        seed_maritime_salt_world(
            self.conn,
            world_id=WORLD_A_ID,
            instance_id="inst_yunze_001",
            include_homonym_npc=True,
        )
        append_long_document_and_injection_sample(
            self.conn,
            WORLD_A_ID,
            doc_id="doc_long_chronicle",
            chunk_count=9,
        )

        ext = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            "白鹭水驿",
            batch_max_entries=2,
            batch_max_chunks=3,
            batch_char_budget=1400,
            total_char_budget=4200,
            max_batches=3,
        )
        self.assertGreaterEqual(len(ext["batches"]), 2)
        self.assertEqual(len(ext["batches"]), len(ext["input_ranges"]))

        seen_batch_sources: set[str] = set()
        for idx, b_range in enumerate(ext["input_ranges"]):
            self.assertEqual(b_range.batch_index, idx)
            self.assertEqual(b_range.total_batches, len(ext["batches"]))
            self.assertLessEqual(b_range.used_chars, b_range.char_budget)
            self.assertTrue(len(b_range.source_ids) > 0)
            self.assertTrue(seen_batch_sources.isdisjoint(b_range.source_ids))
            seen_batch_sources.update(b_range.source_ids)
            for doc_span in b_range.document_ranges:
                self.assertLessEqual(doc_span.start_chunk_index, doc_span.end_chunk_index)
                self.assertLess(doc_span.start_char, doc_span.end_char)

        injected_items = [
            s for s in ext["selected_sources"] if s.prompt_injection_filtered
        ]
        self.assertGreaterEqual(len(injected_items), 1)
        for item in injected_items:
            self.assertNotIn("coins=99999", item.text)
            self.assertIn("[已隔离文内非设定指令]", item.text)

        fake_model = FakeWorldAnalysisModelClient()
        draft = asyncio.run(
            analyze_world_initial_situation(ext, fake_model, instance_id="inst_yunze_001")
        )
        self.assertEqual(len(draft.input_ranges), len(ext["batches"]))

        shen_npcs = [
            n for n in draft.npcs if n.name == "沈渡头" or "老沈" in n.aliases
        ]
        self.assertEqual(len(shen_npcs), 1)
        self.assertIn("老沈", shen_npcs[0].aliases)

        han_npcs = [n for n in draft.npcs if n.name == "韩掌柜"]
        self.assertGreaterEqual(len(han_npcs), 2)
        self.assertNotEqual(han_npcs[0].npc_id, han_npcs[1].npc_id)
        self.assertTrue(bool(han_npcs[0].homonym_disambiguator))
        self.assertTrue(bool(han_npcs[1].homonym_disambiguator))
        self.assertNotEqual(
            han_npcs[0].homonym_disambiguator,
            han_npcs[1].homonym_disambiguator,
        )

    def test_multi_batch_http_mock_and_out_of_batch_source_rejection(self) -> None:
        """验证多批次 HTTP Mock 请求序列与单批次越界引用相邻批次 source_id 被拦截。"""
        seed_maritime_salt_world(self.conn, world_id=WORLD_A_ID, instance_id="inst_yunze_001")
        append_long_document_and_injection_sample(self.conn, WORLD_A_ID, chunk_count=9)

        ext = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            "白鹭水驿",
            batch_char_budget=1400,
        )
        self.assertGreaterEqual(len(ext["batches"]), 2)

        transport = build_mock_http_analysis_transport(
            self.conn,
            WORLD_A_ID,
            "白鹭水驿",
            instance_id="inst_yunze_001",
            batch_char_budget=1400,
        )
        client = OpenAIWorldAnalysisModelClient(
            base_url="https://api.example.com/v1",
            api_key="sk-test-multi-batch",
            model="gpt-4o-mini",
            transport=transport,
        )
        draft = asyncio.run(
            analyze_world_initial_situation(ext, client, instance_id="inst_yunze_001")
        )
        self.assertEqual(len(client.call_history), len(ext["batches"]))
        for idx, call_rec in enumerate(client.call_history):
            self.assertEqual(call_rec["batch_index"], idx)
            self.assertEqual(
                call_rec["allowed_source_ids"],
                list(ext["batches"][idx].input_range.source_ids),
            )
        self.assertEqual(draft.world_id, WORLD_A_ID)

        # 验证若第 1 批输出越界引用了第 0 批专属的 source_id -> merge_batch_extractions 立即报 422 SOURCE_FABRICATED
        fake_model = FakeWorldAnalysisModelClient()
        partials = [
            fake_model.analyze_batch_sync(b, instance_id="inst_yunze_001")
            for b in ext["batches"]
        ]
        batch0_only_sid = ext["batches"][0].input_range.source_ids[0]
        partials[1]["events"][0]["source_ids"] = [batch0_only_sid]
        with self.assertRaises(SourceFabricatedError):
            merge_batch_extractions(
                partials,
                ext["batches"],
                world_id=WORLD_A_ID,
                instance_id="inst_yunze_001",
                starting_point=StartingPointSpec(location="白鹭水驿", world_minute=0),
            )

    def test_unresolved_duplicate_npc_or_duplicate_id_rejected(self) -> None:
        """验证未消歧的同名同职同地重复人物或重复 npc_id 被校验器拦截。"""
        val_ctx = WorldAnalysisValidationContext(
            world_id=WORLD_A_ID,
            instance_id="inst_yunze_001",
            starting_point=StartingPointSpec(location="白鹭水驿", world_minute=0),
            allowed_source_ids=frozenset({"entry:yunze_geography_01"}),
        )
        base_events = [
            {
                "event_id": f"evt_0{i}",
                "title": f"初始事件{i}",
                "description": "描述",
                "location": "白鹭水驿",
                "scope": "local",
                "participants": ["沈渡头"],
                "stage": "initial",
                "next_update_minute": 60,
                "grounding_type": "canon",
                "source_ids": ["entry:yunze_geography_01"],
            }
            for i in (1, 2, 3)
        ]

        raw_duplicate = {
            "world_id": WORLD_A_ID,
            "instance_id": "inst_yunze_001",
            "events": base_events,
            "npcs": [
                {
                    "npc_id": "npc_shen_1",
                    "name": "沈渡头",
                    "role": "白鹭水驿总渡头",
                    "location": "白鹭水驿",
                    "home_location": "白鹭水驿",
                    "goal": "守闸",
                    "motivation": "守闸",
                    "plan_kind": "stay",
                    "action_kind": "shift_change",
                    "current_activity": "值守",
                    "duration_minutes": 30,
                    "next_due_minute": 30,
                    "grounding_type": "canon",
                    "source_ids": ["entry:yunze_geography_01"],
                },
                {
                    "npc_id": "npc_shen_2",
                    "name": "沈渡头",
                    "role": "白鹭水驿总渡头",
                    "location": "白鹭水驿",
                    "home_location": "白鹭水驿",
                    "goal": "守闸",
                    "motivation": "守闸",
                    "plan_kind": "stay",
                    "action_kind": "shift_change",
                    "current_activity": "值守",
                    "duration_minutes": 30,
                    "next_due_minute": 30,
                    "grounding_type": "canon",
                    "source_ids": ["entry:yunze_geography_01"],
                },
            ],
        }
        with self.assertRaises(IdentityCollisionError) as err_ctx:
            validate_analysis_draft(raw_duplicate, val_ctx)
        self.assertEqual(err_ctx.exception.code, "UNRESOLVED_DUPLICATE_NPC")


if __name__ == "__main__":
    unittest.main()
