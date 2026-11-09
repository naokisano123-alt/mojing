"""M3.2 模型会话绑定（独立与复用）、重启无 Key 等待、预算预留、并发扣额与 MockTransport 用量测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator
import unittest

import httpx

from app.m3.pulse.budget import (
    BudgetExhaustedError,
    PulseBudgetManager,
    configure_instance_pulse,
    disable_pulse,
    enable_pulse,
    get_or_init_instance_config,
)
from app.m3.pulse.client import OpenAIPulseModelClient, UpstreamRequestFailedError
from app.m3.pulse.observability import get_usage_summary
from app.m3.pulse.queue import enqueue, resume_waiting_jobs
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import PulseModelRequest
from app.m3.pulse.worker import PulseWorker
from app.model_gateway.store import ModelSessionStore


class TestPulseSessionAndBudget(unittest.IsolatedAsyncioTestCase):
    """测试模型会话模式、重启无 Key 等待、并发预算预留与 MockTransport 用量核算。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_pulse_budget.db"
        self.now_ts = 2000.0
        self.utc_now = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

        with self._db() as conn:
            ensure_pulse_schema(conn, ensure_instance_table=True)
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES (?, ?, ?, ?, ?)",
                ("inst-1", "world-1", "adv-1", 120, 1),
            )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def test_independent_and_reuse_narration_session_modes(self) -> None:
        """验收：支持独立会话与复用叙事会话两种模式，关闭/清理脉动绝不删除复用的叙事会话。"""
        narration_store = ModelSessionStore(clock=lambda: self.utc_now)
        pulse_store = ModelSessionStore(clock=lambda: self.utc_now)
        router = PulseSessionRouter(narration_store=narration_store, pulse_store=pulse_store)

        # 1. 在前台叙事会话池创建一个叙事会话
        narration_sess = narration_store.create(
            base_url="https://api.example.com/v1",
            model="narration-gpt",
            api_key="sk-narration-secret-111",
        )
        narr_sid = str(narration_sess["session_id"])

        # 2. 为实例绑定独立世界模拟模型会话
        ind_info = router.bind_independent(
            "inst-1",
            base_url="https://pulse.example.com/v1",
            model="pulse-simulator-model",
            api_key="sk-pulse-secret-222",
        )
        self.assertEqual(ind_info["mode"], "independent")
        self.assertEqual(pulse_store.active_count, 1)
        client_ind, mode_ind = router.resolve_client("inst-1")
        self.assertEqual(mode_ind, "independent")
        self.assertEqual(client_ind.model, "pulse-simulator-model")
        self.assertNotIn("sk-pulse-secret-222", repr(client_ind))

        # 3. 切换为复用前台叙事会话：独立脉动会话被自動释放，叙事会话保留
        reuse_info = router.bind_reuse_narration("inst-1", narr_sid)
        self.assertEqual(reuse_info["mode"], "reuse_narration")
        self.assertEqual(pulse_store.active_count, 0)
        self.assertEqual(narration_store.active_count, 1)
        client_reuse, mode_reuse = router.resolve_client("inst-1")
        self.assertEqual(mode_reuse, "reuse_narration")
        self.assertEqual(client_reuse.model, "narration-gpt")
        self.assertNotIn("sk-narration-secret-111", repr(client_reuse))

        # 4. 解绑或清空脉动路由器：前台叙事会话依然完好可用
        router.unbind("inst-1")
        router.clear()
        self.assertEqual(narration_store.active_count, 1)
        fg_client = narration_store.get_client(narr_sid)
        self.assertEqual(fg_client.model, "narration-gpt")

    async def test_restart_without_key_waits_for_model_and_resumes_on_reconnect(self) -> None:
        """验收：服务重启后内存无 Key 时，已入队任务转入 waiting_for_model 不发请求；重连后正常恢复完成。"""
        with self._db() as conn:
            enable_pulse(conn, "inst-1", now=self.now_ts)
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "now": self.now_ts,
                },
            )

        # 模拟重启：新建空内存 PulseSessionRouter（无任何绑定 Key）
        router = PulseSessionRouter()
        activity = ActivityLeaseRegistry(clock=lambda: self.now_ts)
        activity.touch("inst-1")
        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            clock=lambda: self.now_ts,
        )

        # 第 1 次执行：因无模型会话进入 waiting_for_model
        res1 = await worker.run_once()
        assert res1 is not None
        self.assertEqual(res1["outcome"], "waiting_for_model")
        self.assertEqual(res1["job"]["status"], "waiting_for_model")
        self.assertEqual(res1["job"]["error_code"], "MODEL_SESSION_UNBOUND")
        self.assertEqual(res1["job"]["attempts"], 0)

        # 用户重新绑定模型（此处演示绑定 Fake 或 Mock 会话）并唤醒等待任务
        router.bind_fake("inst-1")
        with self._db() as conn:
            resumed = resume_waiting_jobs(conn, "inst-1", now=self.now_ts + 5.0)
            self.assertEqual(resumed, 1)

        self.now_ts += 6.0
        res2 = await worker.run_once()
        assert res2 is not None
        self.assertEqual(res2["outcome"], "completed")
        self.assertEqual(res2["job"]["status"], "completed")

    def test_pre_request_reservation_and_concurrent_deduction(self) -> None:
        """验收：标准复杂度默认配置、请求前预算预留与并发扣额防超支。"""
        budget_mgr = PulseBudgetManager()
        with self._db() as conn:
            cfg = get_or_init_instance_config(conn, "inst-1", now=self.now_ts)
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["complexity"], "standard")

            # 配置游戏日预算上限为 2 次调用、1000 tokens，冷却 0s 以便测试并发预留
            configure_instance_pulse(
                conn,
                "inst-1",
                enabled=True,
                daily_call_budget=2,
                daily_token_budget=1000,
                max_calls_per_round=2,
                max_tokens_per_round=800,
                cooldown_seconds=0.0,
                now=self.now_ts,
            )

            # 并发预留 1：占用 600 tokens
            r1 = budget_mgr.reserve(
                conn,
                "inst-1",
                job_id="job-a",
                world_minute=120,
                estimated_tokens=600,
                now=self.now_ts,
            )
            # 并发预留 2：尝试再占用 500 tokens（600 + 500 > 1000 游戏日预算），在 r1 尚未落盘时即被拦截
            with self.assertRaises(BudgetExhaustedError) as ctx:
                budget_mgr.reserve(
                    conn,
                    "inst-1",
                    job_id="job-b",
                    world_minute=120,
                    estimated_tokens=500,
                    now=self.now_ts,
                )
            self.assertEqual(ctx.exception.code, "PULSE_DAILY_TOKEN_BUDGET_EXHAUSTED")

            # 并发预留 2 改为请求 300 tokens（600 + 300 <= 1000）：成功预留
            r2 = budget_mgr.reserve(
                conn,
                "inst-1",
                job_id="job-b",
                world_minute=120,
                estimated_tokens=300,
                now=self.now_ts,
            )

            # 并发预留 3：已达 daily_call_budget=2，第 3 个请求被拦截
            with self.assertRaises(BudgetExhaustedError) as ctx_calls:
                budget_mgr.reserve(
                    conn,
                    "inst-1",
                    job_id="job-c",
                    world_minute=120,
                    estimated_tokens=50,
                    now=self.now_ts,
                )
            self.assertEqual(ctx_calls.exception.code, "PULSE_DAILY_CALL_BUDGET_EXHAUSTED")

            budget_mgr.release(r1.reservation_id)
            budget_mgr.release(r2.reservation_id)

    async def test_mock_transport_actual_vs_estimated_usage_and_timeout_billing(self) -> None:
        """验收：MockTransport 下服务商返回 actual usage、缺失时标记 estimated、以及请求超时可能计费的账本记录。"""
        mode = "actual"

        def handler(req: httpx.Request) -> httpx.Response:
            if mode == "actual":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps({"operations": [], "transmissions": []}),
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 110,
                            "completion_tokens": 45,
                            "total_tokens": 155,
                        },
                    },
                )
            if mode == "estimated":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps({"operations": [{"op": "create"}], "transmissions": []}),
                                },
                                "finish_reason": "stop",
                            }
                        ]
                        # 故意不返回 usage 字段
                    },
                )
            if mode == "timeout":
                raise httpx.ReadTimeout("Upstream timed out after receiving prompt")
            return httpx.Response(401, json={"error": "Unauthorized"})

        transport = httpx.MockTransport(handler)
        client = OpenAIPulseModelClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="sk-mock-secret-key-888",
            transport=transport,
        )
        req = PulseModelRequest(
            instance_id="inst-1",
            kind="advance",
            base_world_revision=1,
            target_world_minute=120,
            snapshot={"world_minute": 120},
        )

        budget_mgr = PulseBudgetManager()
        with self._db() as conn:
            configure_instance_pulse(conn, "inst-1", enabled=True, cooldown_seconds=0.0, now=self.now_ts)

            # 1. 上游返回精确 usage
            res_actual = budget_mgr.reserve(conn, "inst-1", job_id="j1", world_minute=120, now=self.now_ts)
            resp_actual = await client.propose(req)
            self.assertEqual(resp_actual.usage.usage_source, "actual")
            self.assertEqual(resp_actual.usage.total_tokens, 155)
            budget_mgr.settle(conn, res_actual, resp_actual.usage, now=self.now_ts + 1)

            # 2. 上游未返回 usage -> 自动估算并标为 estimated
            mode = "estimated"
            res_est = budget_mgr.reserve(conn, "inst-1", job_id="j2", world_minute=120, now=self.now_ts + 2)
            resp_est = await client.propose(req)
            self.assertEqual(resp_est.usage.usage_source, "estimated")
            self.assertGreater(resp_est.usage.total_tokens, 0)
            budget_mgr.settle(conn, res_est, resp_est.usage, now=self.now_ts + 3)

            # 3. 请求发出后超时 -> 抛出 UpstreamRequestFailedError 且 potentially_billed=True
            mode = "timeout"
            res_to = budget_mgr.reserve(conn, "inst-1", job_id="j3", world_minute=120, now=self.now_ts + 4)
            with self.assertRaises(UpstreamRequestFailedError) as ctx_to:
                await client.propose(req)
            self.assertEqual(ctx_to.exception.code, "MODEL_TIMEOUT")
            self.assertTrue(ctx_to.exception.usage_evidence.potentially_billed)
            self.assertEqual(ctx_to.exception.usage_evidence.outcome, "timeout_maybe_billed")
            budget_mgr.settle(conn, res_to, ctx_to.exception.usage_evidence, now=self.now_ts + 5)

            # 4. 401 认证失败 -> potentially_billed=False，不计入扣费 token
            mode = "auth_fail"
            res_auth = budget_mgr.reserve(conn, "inst-1", job_id="j4", world_minute=120, now=self.now_ts + 6)
            with self.assertRaises(UpstreamRequestFailedError) as ctx_auth:
                await client.propose(req)
            self.assertFalse(ctx_auth.exception.usage_evidence.potentially_billed)
            budget_mgr.settle(conn, res_auth, ctx_auth.exception.usage_evidence, now=self.now_ts + 7)

            summary = get_usage_summary(conn, "inst-1", budget_manager=budget_mgr)
            day_usage = summary["current_day_usage"]
            self.assertEqual(day_usage["billed_calls"], 3)
            self.assertEqual(day_usage["actual_tokens"], 155)
            self.assertGreater(day_usage["estimated_tokens"], 0)
            self.assertEqual(day_usage["timeout_maybe_billed_calls"], 1)
            self.assertGreater(day_usage["timeout_maybe_billed_tokens"], 0)

    async def test_budget_exhausted_defers_job_without_busy_loop(self) -> None:
        """验收：预算耗尽时将任务延后（设置 next_retry_at），不消耗失败次数，避免忙循环。"""
        with self._db() as conn:
            configure_instance_pulse(
                conn,
                "inst-1",
                enabled=True,
                daily_call_budget=1,
                cooldown_seconds=0.0,
                defer_seconds=120.0,
                now=self.now_ts,
            )
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "now": self.now_ts,
                },
            )
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 2,
                    "base_world_revision": 2,
                    "target_world_minute": 180,
                    "now": self.now_ts + 1,
                },
            )

        router = PulseSessionRouter()
        router.bind_fake("inst-1")
        activity = ActivityLeaseRegistry(clock=lambda: self.now_ts)
        activity.touch("inst-1")
        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            clock=lambda: self.now_ts,
        )

        # 第 1 条任务正常完成，耗尽当日 1 次调用额度
        r1 = await worker.run_once()
        assert r1 is not None
        self.assertEqual(r1["outcome"], "completed")

        # 第 2 条任务在同一游戏日执行：触发预算耗尽延后（deferred_budget）
        self.now_ts += 2.0
        r2 = await worker.run_once()
        assert r2 is not None
        self.assertEqual(r2["outcome"], "deferred_budget")
        self.assertEqual(r2["job"]["status"], "pending")
        self.assertEqual(r2["job"]["error_code"], "PULSE_DAILY_CALL_BUDGET_EXHAUSTED")
        self.assertEqual(r2["job"]["attempts"], 0)
        self.assertAlmostEqual(r2["job"]["next_retry_at"], self.now_ts + 120.0)

        # 在延后窗口内再次轮询：直接返回 None，绝不忙循环重试
        self.now_ts += 10.0
        self.assertIsNone(await worker.run_once())


if __name__ == "__main__":
    unittest.main()
