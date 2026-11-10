"""M3 Wave 4 单元测试：初始化输入、等待/无变化/改变计划决策、消除与 Worker 双重更新版本冲突、
双人不同反应、回头再遇见、任务重试、先离线后恢复及迁移续玩状态一致性。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterator
import unittest

import httpx

from app.m3 import encounters, events, knowledge
from app.m3.core import adapters as core_adapters
from app.m3.npcs import (
    DuplicateNpcTaskError,
    NpcPulseModelClientAdapter,
    NpcValidationError,
    NpcsAdapter,
    OpenAINpcModelClient,
    apply_proposal,
    apply_pulse_proposal_in_worker,
    build_adventure_narration_npc_context,
    build_npc_pulse_callbacks,
    build_wave4_custom_world_init_payload,
    check_npc_accessibility,
    ensure_instance_exists,
    ensure_npcs_schema,
    get_npc,
    initialize_npcs_from_draft,
    list_contactable_npcs,
    list_npc_action_log,
    list_npcs,
    read_snapshot,
    register_event_stub,
    register_knowledge_stub,
    restore,
    run_wave4_pulse_lifecycle_simulation,
    seed_npcs_from_initialization,
    snapshot,
    validate_proposal,
)
from app.m3.portability.service import PortabilitySnapshotAdapter
from app.m3.pulse.budget import configure_instance_pulse
from app.m3.pulse.queue import enqueue
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import PulseTrigger
from app.m3.pulse.worker import PulseWorker


class TestWave4PulseAndLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()

    def test_initialization_from_world_analysis_drafts_and_action_kinds(self) -> None:
        """验证从两套不同题材的 AI 初始化草案建立人物状态，覆盖出行、留在原处、等待、无变化、改变计划与交谈安排。"""
        # 1. 星澜港海贸题材初始化
        ensure_instance_exists(
            self.conn,
            "inst_wave4_harbor_001",
            world_id="world_harbor",
            world_minute=0,
            revision=0,
        )
        harbor_init = build_wave4_custom_world_init_payload(
            world_kind="harbor",
            instance_id="inst_wave4_harbor_001",
        )
        harbor_res = seed_npcs_from_initialization(
            self.conn,
            "inst_wave4_harbor_001",
            harbor_init,
            world_minute=0,
            allowed_source_ids=("chunk:harbor_tide_01", "entry:guild_charter_02"),
            player_location="星澜港码头",
        )
        self.assertEqual(harbor_res["applied_operations"], 4)
        self.assertEqual(harbor_res["world_revision"], 1)

        lin = get_npc(self.conn, "inst_wave4_harbor_001", "npc_lin_navigator")
        su = get_npc(self.conn, "inst_wave4_harbor_001", "npc_su_guildmaster")
        chen = get_npc(self.conn, "inst_wave4_harbor_001", "npc_chen_inspector")

        self.assertEqual(lin["current_action_kind"], "stay")
        self.assertEqual(lin["action_meta"]["grounding"], "canon")
        self.assertEqual(len(lin["action_meta"]["relationships"]), 2)
        self.assertEqual(su["current_action_kind"], "plan_change")
        self.assertEqual(su["action_meta"]["new_plan"], "将公开竞拍改为优先保供契约工坊的定量配售")
        self.assertEqual(chen["current_action_kind"], "conversation_plan")
        self.assertEqual(chen["action_meta"]["grounding"], "inferred")

        # 2. 云栖书院山门题材初始化（使用中文动作别名：留在原处、出行、改变计划、交谈安排）
        ensure_instance_exists(
            self.conn,
            "inst_wave4_academy_001",
            world_id="world_academy",
            world_minute=30,
            revision=0,
        )
        academy_init = build_wave4_custom_world_init_payload(
            world_kind="academy",
            instance_id="inst_wave4_academy_001",
            allowed_source_ids=("chunk:academy_rules_01", "entry:stele_forest_02"),
        )
        academy_res = initialize_npcs_from_draft(
            self.conn,
            "inst_wave4_academy_001",
            academy_init,
            world_minute=30,
            allowed_source_ids=("chunk:academy_rules_01", "entry:stele_forest_02"),
            player_location="云栖书院山门",
            advance_world_revision=False,
        )
        self.assertEqual(academy_res["applied_operations"], 4)
        self.assertFalse(academy_res["world_revision_advanced"])
        # advance_world_revision=False 时数据库内 world_instances.revision 保持为 0，交由外层事务统一更新
        db_rev = self.conn.execute(
            "SELECT revision FROM world_instances WHERE id = 'inst_wave4_academy_001'"
        ).fetchone()[0]
        self.assertEqual(int(db_rev), 0)

        shen = get_npc(self.conn, "inst_wave4_academy_001", "npc_shen_curator")
        lu = get_npc(self.conn, "inst_wave4_academy_001", "npc_lu_disciple")
        wen = get_npc(self.conn, "inst_wave4_academy_001", "npc_wen_elder")
        self.assertEqual(shen["current_action_kind"], "stay")
        self.assertEqual(lu["current_action_kind"], "travel")
        self.assertEqual(lu["location"], "青溪集")
        self.assertEqual(lu["availability"], "in_transit")
        self.assertEqual(wen["current_action_kind"], "plan_change")
        self.assertEqual(wen["goal"], "暂停公开授课，改为闭门加固碑林阵纹")

        # 3. 推进至第 60 分钟，测试 wait（等待）、no_change（无变化）、stay（留在原处）与非法跨地拦截
        self.conn.execute("UPDATE world_instances SET world_minute = 60 WHERE id = 'inst_wave4_harbor_001'")
        snap_60 = read_snapshot(
            self.conn,
            "inst_wave4_harbor_001",
            {"kind": "advance", "target_world_minute": 60, "player_location": "星澜港码头"},
        )
        valid_wait_and_no_change = validate_proposal(
            {
                "instance_id": "inst_wave4_harbor_001",
                "mode": "advance",
                "world_minute": 60,
                "operations": [
                    {
                        "op": "act",
                        "npc_id": "npc_lin_navigator",
                        "expected_npc_revision": 1,
                        "action_kind": "wait",
                        "next_due_minute": 120,
                    },
                    {
                        "op": "act",
                        "npc_id": "npc_zhou_shipwright",
                        "expected_npc_revision": 1,
                        "action_kind": "no_change",
                        "next_due_minute": 120,
                    },
                ],
            },
            snap_60,
        )
        self.assertEqual(valid_wait_and_no_change.operations[0].action_kind, "wait")
        self.assertEqual(valid_wait_and_no_change.operations[1].action_kind, "no_change")

        # stay / wait 时试图修改地点应被拒绝
        with self.assertRaises(NpcValidationError) as ctx_stay_loc:
            validate_proposal(
                {
                    "instance_id": "inst_wave4_harbor_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_lin_navigator",
                            "expected_npc_revision": 1,
                            "action_kind": "stay",
                            "to_location": "灯塔屿",
                            "next_due_minute": 120,
                        }
                    ],
                },
                snap_60,
            )
        self.assertEqual(ctx_stay_loc.exception.code, "STAY_LOCATION_MISMATCH")

        # plan_change 未产生任何变化应被拒绝
        with self.assertRaises(DuplicateNpcTaskError) as ctx_dup_plan:
            validate_proposal(
                {
                    "instance_id": "inst_wave4_harbor_001",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_su_guildmaster",
                            "expected_npc_revision": 1,
                            "action_kind": "plan_change",
                            "new_plan": su["goal"],
                            "current_activity": su["current_activity"],
                            "motivation": "重复原计划",
                            "next_due_minute": 120,
                            "cause_event_ids": ["evt_harbor_fog_01"],
                        }
                    ],
                },
                snap_60,
            )
        self.assertEqual(ctx_dup_plan.exception.code, "DUPLICATE_PLAN_CHANGE")

    def test_wave3_schema_in_place_migration_preserves_rows_and_savepoint(self) -> None:
        """验证旧版 Wave 3 四动作枚举表在调用 ensure_npcs_schema 时无损升级至 Wave 4 八动作枚举，且不破坏外层 SAVEPOINT。"""
        raw_conn = sqlite3.connect(":memory:")
        raw_conn.row_factory = sqlite3.Row
        try:
            raw_conn.execute(
                "CREATE TABLE world_instances (id TEXT PRIMARY KEY, world_id TEXT NOT NULL, adventure_id TEXT, world_minute INTEGER NOT NULL, revision INTEGER NOT NULL)"
            )
            raw_conn.execute(
                "INSERT INTO world_instances VALUES ('inst_legacy', 'w_legacy', 'adv_legacy', 60, 1)"
            )
            raw_conn.execute(
                """
                CREATE TABLE m3_npcs (
                    instance_id TEXT NOT NULL,
                    npc_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    faction TEXT NOT NULL DEFAULT '',
                    home_location TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL,
                    origin_location TEXT NOT NULL DEFAULT '',
                    destination_location TEXT NOT NULL DEFAULT '',
                    current_activity TEXT NOT NULL,
                    current_action_kind TEXT NOT NULL CHECK(current_action_kind IN ('travel', 'conversation_plan', 'shift_change', 'task_progress')),
                    motivation TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    depth_tier TEXT NOT NULL DEFAULT 'foreground',
                    depth_reason TEXT NOT NULL DEFAULT '',
                    shift_name TEXT NOT NULL DEFAULT '',
                    availability TEXT NOT NULL DEFAULT 'available',
                    counterpart_npc_id TEXT NOT NULL DEFAULT '',
                    conversation_topic TEXT NOT NULL DEFAULT '',
                    current_task_id TEXT NOT NULL DEFAULT '',
                    cause_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    cause_fact_ids_json TEXT NOT NULL DEFAULT '[]',
                    source_ids_json TEXT NOT NULL DEFAULT '["chunk:c1"]',
                    action_meta_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_minute INTEGER NOT NULL DEFAULT 0,
                    updated_minute INTEGER NOT NULL DEFAULT 0,
                    last_action_minute INTEGER NOT NULL DEFAULT 0,
                    next_due_minute INTEGER,
                    revision INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (instance_id, npc_id)
                )
                """
            )
            raw_conn.execute(
                """
                INSERT INTO m3_npcs (
                    instance_id, npc_id, name, role, location, current_activity,
                    current_action_kind, motivation, next_due_minute, revision
                ) VALUES ('inst_legacy', 'npc_old', '老掌柜', '商户', '霜木镇', '看店', 'shift_change', '守店', 60, 1)
                """
            )
            raw_conn.execute("SAVEPOINT sp_outer_migration_test")
            ensure_npcs_schema(raw_conn)
            register_event_stub(raw_conn, "inst_legacy", "evt_legacy_1", title="旧镇风声", next_update_minute=60)
            applied = apply_proposal(
                raw_conn,
                "inst_legacy",
                {
                    "instance_id": "inst_legacy",
                    "mode": "advance",
                    "world_minute": 60,
                    "operations": [
                        {
                            "op": "act",
                            "npc_id": "npc_old",
                            "expected_npc_revision": 1,
                            "action_kind": "wait",
                            "current_activity": "在店中等待",
                            "motivation": "观望局势",
                            "next_due_minute": 120,
                            "cause_event_ids": ["evt_legacy_1"],
                        }
                    ],
                },
            )
            raw_conn.execute("RELEASE SAVEPOINT sp_outer_migration_test")
            self.assertEqual(applied["npcs"][0]["current_action_kind"], "wait")
        finally:
            raw_conn.close()

    def test_single_revision_increment_in_pulse_worker_and_combined_commit_rollback(self) -> None:
        """验证解决 NPC 应用与 PulseWorker 双重更新实例版本的冲突，验证组合提交单次版本递增、失败整体回滚与幂等重放。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "combined_pulse.db"

            @contextmanager
            def _db_factory() -> Iterator[sqlite3.Connection]:
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                try:
                    yield c
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
                finally:
                    c.close()

            inst_id = "inst_combined_001"
            world_id = "world_combined_001"
            adv_id = "adv_combined_001"

            with _db_factory() as conn:
                ensure_pulse_schema(conn, ensure_instance_table=True)
                events.seed_northern_trade_world_canon(conn, world_id=world_id, instance_id=inst_id)
                seed_ev = events.build_northern_trade_seed_proposal(instance_id=inst_id, world_minute=0)
                ev_res = events.apply_proposal(conn, inst_id, seed_ev, world_minute=0)
                first_event_id = ev_res["created_event_ids"][0]
                conn.execute(
                    "UPDATE world_events SET next_update_minute = 60 WHERE instance_id = ? AND event_id = ?",
                    (inst_id, first_event_id),
                )

                knowledge.ensure_knowledge_schema(conn)
                knowledge.register_holder(
                    conn, inst_id, "npc_han_shopkeeper", name="韩掌柜", role="商户", location="霜木镇"
                )
                knowledge.register_holder(
                    conn, inst_id, "npc_hawk_blacksmith", name="霍克铁匠", role="铁匠", location="霜木镇"
                )

                # 初始化两名 NPC（此处传 advance_world_revision=True 独立调用，实例版本从 0 -> 1）
                seed_npcs_from_initialization(
                    conn,
                    inst_id,
                    [
                        {
                            "npc_id": "npc_han_shopkeeper",
                            "name": "韩掌柜",
                            "role": "铁料商",
                            "location": "霜木镇",
                            "action_kind": "stay",
                            "current_activity": "在霜木镇铺面盘点铁料",
                            "motivation": "备足工坊生铁",
                            "next_due_minute": 60,
                            "cause_event_ids": [first_event_id],
                        },
                        {
                            "npc_id": "npc_hawk_blacksmith",
                            "name": "霍克铁匠",
                            "role": "老铁匠",
                            "location": "霜木镇",
                            "action_kind": "stay",
                            "current_activity": "在工坊看护锻炉",
                            "motivation": "赶制护路队器械",
                            "next_due_minute": 60,
                            "cause_event_ids": [first_event_id],
                        },
                    ],
                    world_minute=0,
                    advance_world_revision=True,
                )
                rev_after_seed = int(
                    conn.execute("SELECT revision FROM world_instances WHERE id = ?", (inst_id,)).fetchone()[0]
                )
                self.assertEqual(rev_after_seed, 1)

                configure_instance_pulse(
                    conn,
                    inst_id,
                    enabled=True,
                    paused=False,
                    min_game_minute_interval=15,
                    cooldown_seconds=0,
                    now=100.0,
                )

            # 构建组合回调（同时包含 events + npcs + knowledge）
            should_fail_knowledge = False

            def _knowledge_applier(
                conn: sqlite3.Connection,
                iid: str,
                trans_list: list[dict[str, Any]],
                _job: dict[str, Any],
            ) -> list[dict[str, Any]]:
                if should_fail_knowledge:
                    raise RuntimeError("Simulated downstream transmission failure")
                return knowledge.apply_proposal(conn, iid, trans_list)

            read_cb, val_cb, app_cb = build_npc_pulse_callbacks(
                events_snapshot_reader=lambda c, iid, j: events.read_snapshot(c, iid, j),
                events_validator=events.validate_proposal,
                events_applier=lambda c, iid, v, j: events.apply_proposal(
                    c, iid, v, world_minute=int(j["target_world_minute"])
                ),
                knowledge_validator=knowledge.validate_proposal,
                knowledge_applier=_knowledge_applier,
            )

            def _mock_combined_http(request: httpx.Request) -> httpx.Response:
                proposal = {
                    "instance_id": inst_id,
                    "kind": "advance",
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "operations": [
                        {
                            "op": "update",
                            "event_id": first_event_id,
                            "expected_event_revision": 1,
                            "stage": "边关税吏设卡盘查",
                            "next_update_minute": 180,
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_han_shopkeeper",
                            "expected_npc_revision": 1,
                            "action_kind": "travel",
                            "from_location": "霜木镇",
                            "to_location": "白河城",
                            "location": "白河城",
                            "current_activity": "赶往白河城接洽水路生铁",
                            "motivation": "边关加税后改寻水路生铁",
                            "duration_minutes": 90,
                            "next_due_minute": 150,
                            "cause_event_ids": [first_event_id],
                        },
                        {
                            "op": "act",
                            "npc_id": "npc_hawk_blacksmith",
                            "expected_npc_revision": 1,
                            "action_kind": "plan_change",
                            "location": "霜木镇",
                            "new_plan": "改为回收旧铁器重熔以维持护路队订单",
                            "current_activity": "留在霜木镇铁匠坊清点废铁准备重熔",
                            "motivation": "生铁延误，改变计划自力维持锻造",
                            "duration_minutes": 60,
                            "next_due_minute": 120,
                            "cause_event_ids": [first_event_id],
                        },
                    ],
                    "transmissions": [
                        {
                            "holder_id": "player",
                            "knowledge_type": "rumor",
                            "channel": "notice",
                            "title": "镇公署告示：灰岩边关加税",
                            "content": "霜木镇揭示板贴出边关加税与商队改道通告。",
                            "source_event_id": first_event_id,
                            "learned_minute": 60,
                        }
                    ],
                }
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(proposal, ensure_ascii=False)},
                            }
                        ],
                        "usage": {"prompt_tokens": 500, "completion_tokens": 250, "total_tokens": 750},
                    },
                )

            now_val = 200.0
            router = PulseSessionRouter()
            npc_client = OpenAINpcModelClient(
                base_url="https://mock.combined.invalid/v1",
                api_key="sk-combined-mock",
                model="combined-v4",
                transport=httpx.MockTransport(_mock_combined_http),
            )
            router.bind_fake(inst_id, NpcPulseModelClientAdapter(npc_client))  # type: ignore[arg-type]
            activity_reg = ActivityLeaseRegistry(clock=lambda: now_val)
            activity_reg.touch(inst_id, now=now_val)

            worker = PulseWorker(
                _db_factory,
                session_router=router,
                activity_registry=activity_reg,
                snapshot_reader=read_cb,
                proposal_validator=val_cb,
                proposal_applier=app_cb,
                clock=lambda: now_val,
            )

            # 1. 先验证当下游 knowledge_applier 抛错时，events 与 npcs 的写入在同一 SAVEPOINT 内整体回滚
            with _db_factory() as conn:
                conn.execute("UPDATE world_instances SET world_minute = 60 WHERE id = ?", (inst_id,))
                enqueue(
                    conn,
                    PulseTrigger(
                        instance_id=inst_id,
                        kind="advance",
                        trigger_adventure_id=adv_id,
                        trigger_revision=1,
                        base_world_revision=1,
                        target_world_minute=60,
                        idempotency_key="job_combined_rollback_test",
                        now=now_val,
                    ),
                )

            should_fail_knowledge = True
            failed_out = asyncio.run(worker.run_once(instance_id=inst_id))
            self.assertIsNotNone(failed_out)
            self.assertEqual(failed_out["outcome"], "apply_failed")

            with _db_factory() as conn:
                # 确认实例版本仍为 1，韩掌柜仍在霜木镇（revision=1），事件版本仍为 1
                rev_now = int(
                    conn.execute("SELECT revision FROM world_instances WHERE id = ?", (inst_id,)).fetchone()[0]
                )
                self.assertEqual(rev_now, 1)
                han_now = get_npc(conn, inst_id, "npc_han_shopkeeper")
                self.assertEqual(han_now["revision"], 1)
                self.assertEqual(han_now["location"], "霜木镇")
                ev_now = events.get_event(conn, inst_id, first_event_id)
                self.assertEqual(ev_now["revision"], 1)

                # 2. 正常执行组合提交：验证事件、NPC 与知识传播全部落库，且 world_instances.revision 严格只 +1（1 -> 2）
                enqueue(
                    conn,
                    PulseTrigger(
                        instance_id=inst_id,
                        kind="advance",
                        trigger_adventure_id=adv_id,
                        trigger_revision=2,
                        base_world_revision=1,
                        target_world_minute=60,
                        idempotency_key="job_combined_commit_ok",
                        now=now_val,
                    ),
                )

            should_fail_knowledge = False
            ok_out = asyncio.run(worker.run_once(instance_id=inst_id))
            self.assertIsNotNone(ok_out)
            self.assertEqual(ok_out["outcome"], "completed")
            self.assertEqual(ok_out["new_world_revision"], 2)

            with _db_factory() as conn:
                rev_committed = int(
                    conn.execute("SELECT revision FROM world_instances WHERE id = ?", (inst_id,)).fetchone()[0]
                )
                self.assertEqual(rev_committed, 2)
                han_after = get_npc(conn, inst_id, "npc_han_shopkeeper")
                hawk_after = get_npc(conn, inst_id, "npc_hawk_blacksmith")
                self.assertEqual(han_after["location"], "白河城")
                self.assertEqual(han_after["revision"], 2)
                self.assertEqual(hawk_after["current_action_kind"], "plan_change")
                self.assertEqual(hawk_after["goal"], "改为回收旧铁器重熔以维持护路队订单")

                # 3. 验证在 world_instances.revision 已变为 2 后，用原来的 job（base_world_revision=1）重放 apply_pulse_proposal_in_worker 依然幂等返回，不抛冲突、不重复记日志
                committed_job_id = ok_out["job"]["job_id"]
                log_count_before = len(list_npc_action_log(conn, inst_id))
                replay_res = apply_pulse_proposal_in_worker(
                    conn,
                    inst_id,
                    {"instance_id": inst_id, "mode": "advance", "world_minute": 60, "operations": []},
                    {"job_id": committed_job_id, "base_world_revision": 1, "target_world_minute": 60},
                )
                self.assertTrue(replay_res["idempotent_replay"])
                self.assertEqual(len(list_npc_action_log(conn, inst_id)), log_count_before)

    def test_wave4_mock_full_lifecycle_different_reactions_re_encounter_retry_offline_and_migration(self) -> None:
        """端到端运行 Wave 4 Mock 全生命周期：双人不同反应、任务失败重试、先离线后恢复、回头再遇见、查证与迁移续玩一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "wave4_lifecycle.db"
            sim = run_wave4_pulse_lifecycle_simulation(db_path)

            # 1. 第 60 分钟：两名人物对同一海雾局势反应不同，且实例版本只递增 1 次（1 -> 2）
            self.assertEqual(sim["step_60"]["worker_outcome"], "completed")
            self.assertEqual(sim["step_60"]["rev_before"], 1)
            self.assertEqual(sim["step_60"]["rev_after"], 2)
            self.assertFalse(sim["step_60"]["lin_accessibility"]["contactable"])
            self.assertEqual(sim["step_60"]["lin_accessibility"]["reason_code"], "NPC_DEPARTED_IN_TRANSIT")
            self.assertTrue(sim["step_60"]["zhou_accessibility"]["contactable"])
            self.assertEqual(sim["step_60"]["zhou_accessibility"]["current_action_kind"], "plan_change")
            self.assertEqual(sim["step_60"]["su_action_kind"], "wait")

            # 2. 第 120 分钟：首次 HTTP 502 触发任务重试（pending），二次尝试成功完成（revision 2 -> 3）
            self.assertEqual(sim["step_120_retry"]["first_attempt_outcome"], "model_failed")
            self.assertEqual(sim["step_120_retry"]["first_attempt_status"], "pending")
            self.assertEqual(sim["step_120_retry"]["second_attempt_outcome"], "completed")
            self.assertEqual(sim["step_120_retry"]["second_attempt_world_revision"], 3)

            # 3. 第 180 分钟：离线期间推进时间不入队任务（scheduled_while_offline is None），恢复在线后时间锚为 180
            self.assertIsNone(sim["step_180_offline_resume"]["scheduled_while_offline"])
            self.assertEqual(sim["step_180_offline_resume"]["resume_anchor_minute"], 180)

            # 4. 第 210 分钟：林船长返航抵达星澜港码头（回头再遇见），可接触性恢复为 True，叙事上下文同步包含其归来
            self.assertEqual(sim["step_210_re_encounter"]["worker_outcome"], "completed")
            self.assertTrue(sim["step_210_re_encounter"]["lin_accessibility"]["contactable"])
            self.assertEqual(sim["step_210_re_encounter"]["lin_accessibility"]["location"], "星澜港码头")
            self.assertIn("林船长", sim["step_210_re_encounter"]["narration_context"]["prompt_block"])

            # 5. 验证与 Ma 查证模块（encounters）及可续玩迁移（PortabilitySnapshotAdapter）共用同一份状态
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                inst_id = "inst_wave4_harbor_001"
                events.ensure_events_schema(conn)
                knowledge.ensure_knowledge_schema(conn)
                encounters.ensure_encounters_schema(conn)

                # 在正式 events / knowledge 表登记海雾事件与玩家已获知的码头传闻，验证查证入口读取同一份 m3_npcs 状态
                conn.execute(
                    """
                    INSERT OR REPLACE INTO world_events (
                        instance_id, event_id, kind, title, description, location, scope,
                        participants_json, cause_event_ids_json, source_ids_json,
                        stage, status, created_minute, updated_minute, next_update_minute, revision
                    ) VALUES (?, 'evt_harbor_fog_01', 'thread', '外海浓雾船队改泊', '船队暂泊灯塔屿。', '星澜港码头', 'regional',
                              '["林船长","周匠师"]', '[]', '["chunk:harbor_tide_01"]', 'returned', 'active', 0, 210, 300, 1)
                    """,
                    (inst_id,),
                )
                encounters.register_holder_fact(
                    conn,
                    inst_id,
                    {
                        "fact_id": "fact_player_harbor_notice",
                        "holder_id": "player",
                        "knowledge_type": "rumor",
                        "channel": "notice",
                        "title": "港务揭示板：引航快艇已返港",
                        "content": "揭示板称引航大副林船长已率首舰冲破海雾靠泊星澜港码头。",
                        "source_event_id": "evt_harbor_fog_01",
                        "learned_minute": 210,
                    },
                )
                encounters.register_holder_fact(
                    conn,
                    inst_id,
                    {
                        "fact_id": "fact_lin_return_detail",
                        "holder_id": "npc_lin_navigator",
                        "knowledge_type": "confirmed",
                        "channel": "observed",
                        "title": "灯塔屿北水道暗流实情",
                        "content": "林船长亲历灯塔屿外锚地暗流，确认首舰香料完好且明日午时可全部卸舱。",
                        "source_event_id": "evt_harbor_fog_01",
                        "learned_minute": 180,
                    },
                )

                bundle_210 = encounters.get_encounter_entrypoint_bundle(
                    conn,
                    inst_id,
                    player_location="星澜港码头",
                    world_minute=210,
                )
                contactable_ids = {n["npc_id"]: n["contactable"] for n in bundle_210["contactable_npcs"]}
                self.assertTrue(contactable_ids["npc_lin_navigator"])
                self.assertTrue(contactable_ids["npc_zhou_shipwright"])

                # 向归来的林船长发起当面查证并确认
                draft = encounters.prepare_encounter_draft(
                    conn,
                    inst_id,
                    {
                        "fact_id": "fact_player_harbor_notice",
                        "option_kind": "ask_npc",
                        "target_npc_id": "npc_lin_navigator",
                        "action_text": "在星澜港码头向归来的林船长当面打听香料船队靠泊实情。",
                        "player_location": "星澜港码头",
                    },
                )
                committed_enc = encounters.commit_encounter_draft(conn, inst_id, draft["draft_id"])
                self.assertEqual(committed_enc["outcome"], "clue")

                # 使用 NpcsAdapter 与 PortabilitySnapshotAdapter 导出并恢复到新实例，验证迁移后续玩状态完全一致
                core_adapters.register_adapter(NpcsAdapter())
                try:
                    full_inst_snap = PortabilitySnapshotAdapter.build_instance_snapshot(conn, inst_id)
                    self.assertIn("npcs", full_inst_snap["domains"])

                    ensure_instance_exists(
                        conn,
                        "inst_wave4_portable_target",
                        world_id="world_wave4_harbor_001",
                        world_minute=210,
                        revision=4,
                    )
                    restore(
                        conn,
                        "inst_wave4_portable_target",
                        full_inst_snap["domains"]["npcs"],
                        world_id="world_wave4_harbor_001",
                    )
                finally:
                    core_adapters.unregister_adapter("npcs")

                # 在迁移后的实例中推进时间到 270 分钟并继续执行 NPC 自主行动
                conn.execute(
                    "UPDATE world_instances SET world_minute = 270 WHERE id = 'inst_wave4_portable_target'"
                )
                register_event_stub(
                    conn,
                    "inst_wave4_portable_target",
                    "evt_harbor_fog_01",
                    title="外海浓雾船队改泊",
                    location="星澜港码头",
                    next_update_minute=270,
                )
                cont_res = apply_proposal(
                    conn,
                    "inst_wave4_portable_target",
                    {
                        "instance_id": "inst_wave4_portable_target",
                        "mode": "advance",
                        "world_minute": 270,
                        "operations": [
                            {
                                "op": "act",
                                "npc_id": "npc_lin_navigator",
                                "expected_npc_revision": 4,
                                "action_kind": "plan_change",
                                "location": "星澜港码头",
                                "new_plan": "改赴港务司绘制新防雾航标图",
                                "current_activity": "在星澜港码头港务司绘测北航道新防雾航标图",
                                "motivation": "完成首舰引航后，着手修订航标图以防浓雾再起",
                                "duration_minutes": 60,
                                "next_due_minute": 330,
                                "cause_event_ids": ["evt_harbor_fog_01"],
                            }
                        ],
                    },
                )
                self.assertEqual(cont_res["applied_operations"], 1)
                self.assertEqual(cont_res["npcs"][0]["revision"], 5)
                self.assertEqual(cont_res["npcs"][0]["goal"], "改赴港务司绘制新防雾航标图")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
