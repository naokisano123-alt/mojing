"""验证两套不同世界设定资料（人物、地理、矛盾不同）的内容跟随变化、起点切换及资料依据/旅程补写区分。"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

from app.m3.world_analysis import (
    WORLD_A_ID,
    WORLD_B_ID,
    FakeWorldAnalysisModelClient,
    OpenAIWorldAnalysisModelClient,
    adapt_draft_to_domain_proposals,
    analyze_world_initial_situation,
    build_mock_http_analysis_transport,
    extract_and_batch_world_sources,
    seed_maritime_salt_world,
    seed_skyforge_academy_world,
)


class TestTwoWorldsAndStartingPoints(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        seed_maritime_salt_world(self.conn, world_id=WORLD_A_ID, instance_id="inst_yunze_001")
        seed_skyforge_academy_world(self.conn, world_id=WORLD_B_ID, instance_id="inst_skyforge_001")

    def tearDown(self) -> None:
        self.conn.close()

    def test_two_distinct_worlds_produce_world_specific_npcs_locations_and_events(self) -> None:
        """验证两套人物、地理与矛盾完全不同的导入资料生成的初始局势各自跟随实际资料变化，绝不串味。"""
        fake_model = FakeWorldAnalysisModelClient()

        ext_a = extract_and_batch_world_sources(self.conn, WORLD_A_ID, "白鹭水驿")
        draft_a = asyncio.run(
            analyze_world_initial_situation(ext_a, fake_model, instance_id="inst_yunze_001")
        )

        ext_b = extract_and_batch_world_sources(self.conn, WORLD_B_ID, "天工外院")
        draft_b = asyncio.run(
            analyze_world_initial_situation(ext_b, fake_model, instance_id="inst_skyforge_001")
        )

        # 1. 核对地理与起点完全跟随各自世界资料
        locs_a = {loc.name for loc in draft_a.locations}
        locs_b = {loc.name for loc in draft_b.locations}
        self.assertIn("白鹭水驿", locs_a)
        self.assertIn("千帆港", locs_a)
        self.assertTrue(locs_a.isdisjoint({"天工外院", "玄枢塔", "赤铜矿坑"}))

        self.assertIn("天工外院", locs_b)
        self.assertIn("玄枢塔", locs_b)
        self.assertTrue(locs_b.isdisjoint({"白鹭水驿", "千帆港", "沉香渡"}))

        # 2. 核对人物姓名与职责完全跟随各自世界资料
        npc_names_a = {n.name for n in draft_a.npcs}
        npc_names_b = {n.name for n in draft_b.npcs}
        self.assertIn("沈渡头", npc_names_a)
        self.assertIn("苏晚晴", npc_names_a)
        self.assertTrue(npc_names_a.isdisjoint({"墨衡", "楚星河", "拓跋烈", "南宫羽"}))

        self.assertIn("墨衡", npc_names_b)
        self.assertIn("楚星河", npc_names_b)
        self.assertTrue(npc_names_b.isdisjoint({"沈渡头", "苏晚晴", "赵铁锚"}))

        # 3. 核对初始事件线矛盾主题与来源绑定各自属于本世界
        self.assertTrue(3 <= len(draft_a.events) <= 6)
        self.assertTrue(3 <= len(draft_b.events) <= 6)
        for sid in draft_a.referenced_source_ids:
            self.assertIn(sid, ext_a["allowed_source_ids"])
            self.assertNotIn(sid, ext_b["allowed_source_ids"])
        for sid in draft_b.referenced_source_ids:
            self.assertIn(sid, ext_b["allowed_source_ids"])
            self.assertNotIn(sid, ext_a["allowed_source_ids"])

        # 4. 核对资料依据（canon）与旅程内补写（adventure_supplement）均明确标明且有审计记录
        canon_npcs_a = [n for n in draft_a.npcs if n.grounding_type == "canon"]
        supp_npcs_a = [n for n in draft_a.npcs if n.grounding_type == "adventure_supplement"]
        self.assertGreaterEqual(len(canon_npcs_a), 2)
        self.assertGreaterEqual(len(supp_npcs_a), 1)
        for n in supp_npcs_a:
            self.assertTrue(bool(n.supplement_reason))

        canon_events_b = [e for e in draft_b.events if e.grounding_type == "canon"]
        supp_events_b = [e for e in draft_b.events if e.grounding_type == "adventure_supplement"]
        self.assertGreaterEqual(len(canon_events_b), 2)
        self.assertGreaterEqual(len(supp_events_b), 1)
        self.assertGreaterEqual(len(draft_b.supplement_ledger), 2)

    def test_changing_starting_point_reorders_excerpts_and_changes_present_npcs(self) -> None:
        """验证同一世界选择不同起点（白鹭水驿 vs 千帆港）时，节选排序、在场人物与玩家开局摘要随之变化。"""
        fake_model = FakeWorldAnalysisModelClient()

        ext_bailu = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            {"location": "白鹭水驿", "world_minute": 60, "character_name": "陆行舟"},
        )
        draft_bailu = asyncio.run(
            analyze_world_initial_situation(ext_bailu, fake_model, instance_id="inst_yunze_001")
        )

        ext_qianfan = extract_and_batch_world_sources(
            self.conn,
            WORLD_A_ID,
            {"location": "千帆港", "world_minute": 120, "character_name": "陆行舟"},
        )
        draft_qianfan = asyncio.run(
            analyze_world_initial_situation(ext_qianfan, fake_model, instance_id="inst_yunze_001")
        )

        self.assertEqual(draft_bailu.starting_point.location, "白鹭水驿")
        self.assertEqual(draft_bailu.world_minute, 60)
        self.assertEqual(draft_qianfan.starting_point.location, "千帆港")
        self.assertEqual(draft_qianfan.world_minute, 120)

        # 在场人物必须严格位于当前所选起点
        for p_npc in draft_bailu.player_opening_summary["present_npcs"]:
            self.assertEqual(p_npc["location"], "白鹭水驿")
        for p_npc in draft_qianfan.player_opening_summary["present_npcs"]:
            self.assertEqual(p_npc["location"], "千帆港")

        # 核对适配后的 Li NPC 到期时间基于各自的开局 world_minute
        bundle_qianfan = adapt_draft_to_domain_proposals(draft_qianfan)
        for li_state in bundle_qianfan["li_npc_initial_states"]:
            self.assertEqual(li_state["world_minute"], 120)
            self.assertGreater(li_state["next_due_minute"], 120)

    def test_openai_model_client_with_http_mock_transport_and_key_redaction(self) -> None:
        """验证 OpenAIWorldAnalysisModelClient 配合 httpx.MockTransport 跑通分析且绝不泄露 API Key。"""
        transport = build_mock_http_analysis_transport(
            self.conn,
            WORLD_B_ID,
            "天工外院",
            instance_id="inst_skyforge_001",
        )
        secret_key = "sk-live-secret-wave4-key-do-not-leak"
        client = OpenAIWorldAnalysisModelClient(
            base_url="https://api.example.com/v1",
            api_key=secret_key,
            model="gpt-4o-mini",
            transport=transport,
        )
        self.assertNotIn(secret_key, repr(client))
        self.assertIn("REDACTED", repr(client))

        ext = extract_and_batch_world_sources(self.conn, WORLD_B_ID, "天工外院")
        draft = asyncio.run(
            analyze_world_initial_situation(ext, client, instance_id="inst_skyforge_001")
        )
        self.assertEqual(draft.world_id, WORLD_B_ID)
        self.assertEqual(len(client.call_history), len(ext["batches"]))
        self.assertIsNotNone(client.last_usage)
        self.assertEqual(client.last_usage["total_tokens"], 900)


if __name__ == "__main__":
    unittest.main()
