"""测试北境贸易双 NPC 对同一局势不同安排、幂等重放、分支隔离、版本冲突回滚与 Ma 互动可接触性契约。"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path
import unittest

import httpx

from app.m3 import events, knowledge
from app.m3.core import adapters
from app.m3.npcs import (
    FakeNpcModelClient,
    NpcError,
    NpcRevisionConflictError,
    NpcValidationError,
    NpcsAdapter,
    OpenAINpcModelClient,
    SnapshotRestoreError,
    WorldRevisionConflictError,
    apply_proposal,
    build_fake_npc_pulse_generator,
    check_npc_accessibility,
    ensure_instance_exists,
    get_npc,
    get_wave3_northern_trade_npc_bundle,
    list_contactable_npcs,
    list_npc_action_log,
    read_snapshot,
    restore,
    run_northern_trade_npc_simulation,
    seed_northern_trade_npcs,
    validate_proposal,
)


class TestNorthernTradeAndBranches(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()

    def test_northern_trade_two_npcs_different_arrangements_and_mock_chain(self) -> None:
        """验证北境贸易下韩掌柜与霍克铁匠对同一商队改道局势采取不同安排，及完整 Mock 四步链与可接触性边界。"""
        result = run_northern_trade_npc_simulation(self.conn)

        han_final = get_npc(self.conn, "inst_northern_frontier_001", "npc_han_shopkeeper")
        hawk_final = get_npc(self.conn, "inst_northern_frontier_001", "npc_hawk_blacksmith")
        self.assertEqual(han_final["current_action_kind"], "travel")
        self.assertEqual(han_final["origin_location"], "霜木镇")
        self.assertEqual(han_final["location"], "白河城")
        self.assertEqual(han_final["availability"], "in_transit")
        self.assertIn("evt_caravan_reroute_02", han_final["cause_event_ids"])

        # 第 60 分钟后韩掌柜在霜木镇不可接触（途中离开 NPC_DEPARTED_IN_TRANSIT），霍克铁匠在霜木镇可接触
        self.assertFalse(result["step_60"]["han_accessibility"]["contactable"])
        self.assertEqual(result["step_60"]["han_accessibility"]["reason_code"], "NPC_DEPARTED_IN_TRANSIT")
        self.assertTrue(result["step_60"]["hawk_accessibility"]["contactable"])
        self.assertEqual(result["step_60"]["hawk_accessibility"]["reason_code"], "OK")

        # 第 90 分钟：无到期 NPC，跳过模型请求
        self.assertTrue(result["step_90_no_due"]["skip"])
        self.assertEqual(result["step_90_no_due"]["reason_code"], "NO_DUE_NPCS")
        self.assertTrue(result["step_90_no_due"]["model_skipped"])
        self.assertEqual(len(result["http_calls"]), 2)

        # 第 120 分钟后：霍克推进了废铁重熔任务，加雷特完成了白河谷地转运任务
        self.assertEqual(hawk_final["current_action_kind"], "task_progress")
        self.assertEqual(hawk_final["current_task_id"], "task_hawk_scrap_smelting")
        garret_120 = get_npc(self.conn, "inst_northern_frontier_001", "npc_garret_caravan")
        self.assertEqual(garret_120["tasks"][0]["status"], "completed")
        self.assertEqual(garret_120["tasks"][0]["progress_percent"], 100)

        # 核对与 JSON Fixture 文件的确定性关键字段一致
        bundle = get_wave3_northern_trade_npc_bundle()
        self.assertEqual(bundle["instance_id"], result["instance_id"])
        self.assertEqual(bundle["step_60"]["selected_npc_ids"], result["step_60"]["selected_npc_ids"])
        self.assertEqual(bundle["step_90_no_due"], result["step_90_no_due"])
        self.assertEqual(bundle["step_120"]["selected_npc_ids"], result["step_120"]["selected_npc_ids"])

    def test_idempotent_job_replay_protection(self) -> None:
        """验证相同 job_id / idempotency_key 重放时直接返回首次结果，不重复增加版本号或日志。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
        )
        proposal = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "idempotency_key": "job_pulse_60_001",
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_hawk_blacksmith",
                    "expected_npc_revision": 1,
                    "action_kind": "shift_change",
                    "location": "霜木镇",
                    "from_shift": "常规双炉锻造班",
                    "to_shift": "单炉节炭班",
                    "current_activity": "切换至单炉节炭班",
                    "motivation": "应对精煤延迟",
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                }
            ],
        }
        first = apply_proposal(self.conn, "inst_northern_frontier_001", proposal)
        self.assertFalse(first["idempotent_replay"])
        self.assertEqual(first["world_revision"], 2)
        log_count_1 = len(list_npc_action_log(self.conn, "inst_northern_frontier_001"))

        second = apply_proposal(self.conn, "inst_northern_frontier_001", proposal)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["world_revision"], 2)
        log_count_2 = len(list_npc_action_log(self.conn, "inst_northern_frontier_001"))
        self.assertEqual(log_count_1, log_count_2)
        self.assertEqual(get_npc(self.conn, "inst_northern_frontier_001", "npc_hawk_blacksmith")["revision"], 2)

    def test_revision_conflict_and_savepoint_atomic_rollback(self) -> None:
        """验证 NPC 版本冲突、世界版本冲突以及多操作批次部分失败时 SAVEPOINT 原子回滚，且保留外层未提交事务。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
        )

        self.conn.execute("CREATE TABLE outer_tx_marker (id TEXT PRIMARY KEY)")
        self.conn.execute("INSERT INTO outer_tx_marker (id) VALUES ('keep_me')")

        batch_with_conflict = {
            "instance_id": "inst_northern_frontier_001",
            "mode": "advance",
            "world_minute": 60,
            "operations": [
                {
                    "op": "act",
                    "npc_id": "npc_hawk_blacksmith",
                    "expected_npc_revision": 1,
                    "action_kind": "shift_change",
                    "location": "霜木镇",
                    "to_shift": "单炉节炭班",
                    "current_activity": "切换至单炉节炭班",
                    "motivation": "应对精煤延迟",
                    "next_due_minute": 120,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                },
                {
                    "op": "act",
                    "npc_id": "npc_han_shopkeeper",
                    "expected_npc_revision": 99,
                    "action_kind": "travel",
                    "from_location": "霜木镇",
                    "to_location": "白河城",
                    "current_activity": "前往白河城",
                    "motivation": "寻找水路铁料",
                    "next_due_minute": 150,
                    "cause_event_ids": ["evt_caravan_reroute_02"],
                },
            ],
        }
        with self.assertRaises(NpcRevisionConflictError) as ctx_rev:
            apply_proposal(self.conn, "inst_northern_frontier_001", batch_with_conflict)
        self.assertEqual(ctx_rev.exception.status, 409)
        self.assertEqual(ctx_rev.exception.code, "NPC_REVISION_CONFLICT")

        hawk = get_npc(self.conn, "inst_northern_frontier_001", "npc_hawk_blacksmith")
        self.assertEqual(hawk["revision"], 1)
        self.assertEqual(hawk["shift_name"], "常规双炉锻造班")

        marker = self.conn.execute("SELECT id FROM outer_tx_marker WHERE id = 'keep_me'").fetchone()
        self.assertIsNotNone(marker)

        with self.assertRaises(WorldRevisionConflictError) as ctx_wrev:
            apply_proposal(
                self.conn,
                "inst_northern_frontier_001",
                {
                    "instance_id": "inst_northern_frontier_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "base_world_revision": 999,
                    "operations": [],
                },
            )
        self.assertEqual(ctx_wrev.exception.status, 409)

    def test_snapshot_restore_branch_isolation_and_domain_adapter(self) -> None:
        """验证检查点快照恢复、NpcsAdapter 适配协议、两个分支对同一 NPC 采取不同后续行动且三方完全隔离。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
        )

        adapters.register_adapter(NpcsAdapter())
        try:
            all_snaps = adapters.snapshot_all(self.conn, "inst_northern_frontier_001")
            self.assertIn("npcs", all_snaps)

            ensure_instance_exists(
                self.conn, "inst_branch_a", world_id="world_northern_frontier_001", world_minute=60, revision=1
            )
            ensure_instance_exists(
                self.conn, "inst_branch_b", world_id="world_northern_frontier_001", world_minute=60, revision=1
            )
            adapters.restore_all(
                self.conn,
                all_snaps,
                new_instance_id="inst_branch_a",
                world_id="world_northern_frontier_001",
            )
            restore(
                self.conn,
                "inst_branch_b",
                all_snaps["npcs"],
                source_id_map={"chunk:north_geography_01": "chunk:remapped_geo_01"},
            )
        finally:
            adapters.unregister_adapter("npcs")

        from app.m3.npcs import register_event_stub

        for b_id in ("inst_branch_a", "inst_branch_b"):
            register_event_stub(
                self.conn,
                b_id,
                "evt_caravan_reroute_02",
                title="灰鬃商队改道",
                location="北境主商道",
                participants=["韩掌柜"],
                status="active",
                next_update_minute=60,
            )

        apply_proposal(
            self.conn,
            "inst_branch_a",
            {
                "instance_id": "inst_branch_a",
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
                        "current_activity": "赴白河城采购水路生铁",
                        "motivation": "分支 A：主动外购",
                        "next_due_minute": 150,
                        "cause_event_ids": ["evt_caravan_reroute_02"],
                    }
                ],
            },
        )
        apply_proposal(
            self.conn,
            "inst_branch_b",
            {
                "instance_id": "inst_branch_b",
                "mode": "advance",
                "world_minute": 60,
                "operations": [
                    {
                        "op": "act",
                        "npc_id": "npc_han_shopkeeper",
                        "expected_npc_revision": 1,
                        "action_kind": "conversation_plan",
                        "location": "霜木镇",
                        "target_npc_id": "npc_eilin_mayor",
                        "conversation_topic": "申请动用镇公署冬储生铁应急周转",
                        "current_activity": "在镇公署与艾琳镇长商议应急借铁",
                        "motivation": "分支 B：留镇向公署求助",
                        "next_due_minute": 120,
                        "cause_event_ids": ["evt_caravan_reroute_02"],
                    }
                ],
            },
        )

        han_orig = get_npc(self.conn, "inst_northern_frontier_001", "npc_han_shopkeeper")
        han_a = get_npc(self.conn, "inst_branch_a", "npc_han_shopkeeper")
        han_b = get_npc(self.conn, "inst_branch_b", "npc_han_shopkeeper")

        self.assertEqual(han_orig["revision"], 1)
        self.assertEqual(han_orig["location"], "霜木镇")

        self.assertEqual(han_a["revision"], 2)
        self.assertEqual(han_a["location"], "白河城")
        self.assertFalse(
            check_npc_accessibility(self.conn, "inst_branch_a", "npc_han_shopkeeper", player_location="霜木镇")["contactable"]
        )

        self.assertEqual(han_b["revision"], 2)
        self.assertEqual(han_b["location"], "霜木镇")
        self.assertEqual(han_b["counterpart_npc_id"], "npc_eilin_mayor")
        self.assertTrue(
            check_npc_accessibility(self.conn, "inst_branch_b", "npc_han_shopkeeper", player_location="霜木镇")["contactable"]
        )
        self.assertIn("chunk:remapped_geo_01", get_npc(self.conn, "inst_branch_b", "npc_hawk_blacksmith")["source_ids"])

        corrupted = dict(all_snaps["npcs"])
        corrupted["npcs"] = [{"npc_id": "bad_npc", "name": "坏数据"}]
        with self.assertRaises(SnapshotRestoreError):
            restore(self.conn, "inst_branch_b", corrupted)
        self.assertEqual(get_npc(self.conn, "inst_branch_b", "npc_han_shopkeeper")["counterpart_npc_id"], "npc_eilin_mayor")

    def test_fake_npc_model_client_and_accessibility_edge_cases(self) -> None:
        """验证 FakeNpcModelClient、可接触性边界（下班、异地、不存在、未确认时间）及客户端凭据脱敏。"""
        seed_northern_trade_npcs(self.conn, world_minute=0)
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
        )
        snap = read_snapshot(
            self.conn,
            "inst_northern_frontier_001",
            {"kind": "advance", "target_world_minute": 60, "player_location": "霜木镇"},
        )
        fake_client = FakeNpcModelClient()
        gen = build_fake_npc_pulse_generator(fake_client)
        proposal = gen(snap)
        validated = validate_proposal(proposal, snap)
        applied = apply_proposal(self.conn, "inst_northern_frontier_001", validated)
        self.assertEqual(applied["applied_operations"], 4)
        self.assertEqual(len(fake_client.calls), 1)

        contactable = list_contactable_npcs(
            self.conn,
            "inst_northern_frontier_001",
            player_location="霜木镇",
            include_unavailable_at_location=True,
        )
        self.assertTrue(any(i["npc_id"] == "npc_hawk_blacksmith" for i in contactable["items"]))
        self.assertTrue(any(i["npc_id"] == "npc_han_shopkeeper" for i in contactable["unavailable_items"]))

        # 异地 NPC（寒铁城林恩特使）在霜木镇不可接触
        linn_check = check_npc_accessibility(
            self.conn, "inst_northern_frontier_001", "npc_linn_envoy", player_location="霜木镇"
        )
        self.assertFalse(linn_check["contactable"])
        self.assertEqual(linn_check["reason_code"], "NPC_AT_DIFFERENT_LOCATION")

        # 不存在的 NPC
        missing_check = check_npc_accessibility(
            self.conn, "inst_northern_frontier_001", "npc_ghost_999", player_location="霜木镇"
        )
        self.assertFalse(missing_check["contactable"])
        self.assertEqual(missing_check["reason_code"], "NPC_NOT_FOUND")

        # 请求超过已确认世界分钟时拒绝
        with self.assertRaises(NpcValidationError) as ctx_unconfirmed:
            check_npc_accessibility(
                self.conn,
                "inst_northern_frontier_001",
                "npc_hawk_blacksmith",
                player_location="霜木镇",
                world_minute=999,
            )
        self.assertEqual(ctx_unconfirmed.exception.code, "UNCONFIRMED_WORLD_MINUTE")

        # 验证 OpenAINpcModelClient 绝不在 repr 或异常中泄露 API Key
        secret_key = "sk-super-secret-npc-key-999"

        def _fail_transport(_req: httpx.Request) -> httpx.Response:
            raise RuntimeError(f"Connection failed with auth {secret_key}")

        bad_client = OpenAINpcModelClient(
            base_url="https://mock.npc.invalid/v1",
            api_key=secret_key,
            model="test",
            transport=httpx.MockTransport(_fail_transport),
        )
        self.assertNotIn(secret_key, repr(bad_client))
        with self.assertRaises(NpcError) as ctx_net:
            asyncio.run(bad_client.generate_proposal(snap))
        self.assertNotIn(secret_key, str(ctx_net.exception.message))

    def test_cross_module_discovery_with_events_and_knowledge_tables(self) -> None:
        """验证与现有 m3/events（world_events）及 m3/knowledge（m3_knowledge_facts）同库自动联动。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cross.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                events.seed_northern_trade_world_canon(conn)
                seed_ev = events.build_northern_trade_seed_proposal(world_minute=0)
                ev_res = events.apply_proposal(conn, "inst_northern_frontier_001", seed_ev)
                first_event_id = ev_res["created_event_ids"][0]

                knowledge.ensure_knowledge_schema(conn)
                knowledge.register_holder(
                    conn, "inst_northern_frontier_001", "npc_han_shopkeeper", name="韩掌柜", role="merchant", location="霜木镇"
                )
                fact_res = knowledge.record_transmission(
                    conn,
                    "inst_northern_frontier_001",
                    {
                        "fact_id": "fact_han_from_real_event",
                        "holder_id": "npc_han_shopkeeper",
                        "knowledge_type": "confirmed",
                        "title": "商会压价",
                        "content": "星辉大商会分号正在压低铁器收购价。",
                        "source_event_id": first_event_id,
                        "channel": "observed",
                        "learned_minute": 0,
                        "confidence": 1.0,
                    },
                )
                self.assertEqual(len(fact_res), 1)

                seed_northern_trade_npcs(conn, world_minute=0)
                conn.execute(
                    "UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_northern_frontier_001'"
                )
                snap = read_snapshot(
                    conn,
                    "inst_northern_frontier_001",
                    {"kind": "advance", "target_world_minute": 60, "player_location": "霜木镇"},
                )
                # 确认自动发现了 world_events 中的真实事件 ID 与 m3_knowledge_facts 中的真实 fact_id
                self.assertIn(first_event_id, snap["allowed_event_ids"])
                self.assertIn(
                    "fact_han_from_real_event",
                    snap["allowed_fact_ids_by_npc"]["npc_han_shopkeeper"],
                )

                # 使用真实事件 ID 与真实知情 fact_id 提交韩掌柜的行动并成功落库
                applied = apply_proposal(
                    conn,
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
                                "action_kind": "conversation_plan",
                                "location": "霜木镇",
                                "target_npc_id": "npc_hawk_blacksmith",
                                "conversation_topic": "联合抵制星辉商会压价并互通铁料库存",
                                "current_activity": "前往铁匠坊与霍克铁匠密商稳价同盟",
                                "motivation": "基于已观测到的商会压价事件与自身知情记录，主动联络霍克铁匠",
                                "next_due_minute": 120,
                                "cause_event_ids": [first_event_id],
                                "cause_fact_ids": ["fact_han_from_real_event"],
                            }
                        ],
                    },
                )
                self.assertEqual(applied["applied_operations"], 1)
                self.assertEqual(applied["npcs"][0]["cause_event_ids"], [first_event_id])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
