"""M3.2 调度与游戏时间、离线暂停、前后台慢请求并发与旧版本冲突、快照及可观察性测试。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterator
import unittest

from app.m3.pulse.budget import (
    configure_instance_pulse,
    disable_pulse,
    enable_pulse,
    pause_pulse,
    resume_pulse,
)
from app.m3.pulse.client import FakePulseModelClient
from app.m3.pulse.errors import PulseError
from app.m3.pulse.observability import (
    get_pulse_status,
    get_usage_summary,
    restore,
    snapshot,
)
from app.m3.pulse.queue import enqueue, list_jobs, resume_waiting_jobs
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import (
    PulseModelRequest,
    PulseModelResponse,
    PulseUsageEvidence,
)
from app.m3.pulse.worker import (
    PulseWorker,
    pulse_worker_lifespan,
    schedule_on_turn_commit,
)


class _SlowModelClient:
    """用于模拟慢速后台模型调用的异步客户端。"""

    def __init__(self, started_event: asyncio.Event, release_event: asyncio.Event) -> None:
        self.model = "slow-mock-model"
        self._started = started_event
        self._release = release_event

    async def propose(self, request: PulseModelRequest) -> PulseModelResponse:
        self._started.set()
        await self._release.wait()
        return PulseModelResponse(
            proposal={
                "kind": request.kind,
                "base_world_revision": request.base_world_revision,
                "target_world_minute": request.target_world_minute,
                "operations": [{"op": "create", "title": "后台慢事件"}],
            },
            usage=PulseUsageEvidence(
                purpose=f"world_pulse_{request.kind}",
                model=self.model,
                prompt_tokens=80,
                completion_tokens=40,
                total_tokens=120,
                usage_source="actual",
                potentially_billed=True,
                outcome="succeeded",
            ),
        )


class TestPulseWorkerAndObservability(unittest.IsolatedAsyncioTestCase):
    """验证调度语义、离线暂停、前后台慢请求并发、旧版本冲突、原子回滚与可观察性脱敏。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_pulse_worker.db"
        self.now_ts = 5000.0

        with self._db() as conn:
            ensure_pulse_schema(conn, ensure_instance_table=True)
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES (?, ?, ?, ?, ?)",
                ("inst-1", "world-1", "adv-1", 60, 1),
            )
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES (?, ?, ?, ?, ?)",
                ("inst-branch", "world-1", "adv-branch", 60, 1),
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

    async def test_default_disabled_and_pause_resume_no_retroactive_history(self) -> None:
        """验收：默认关闭；暂停期间推进游戏时间不产生后台模拟，恢复时从当前时刻继续，不补造暂停期间的历史。"""
        with self._db() as conn:
            # 1. 默认关闭时确认回合：不入队
            scheduled = schedule_on_turn_commit(
                conn,
                instance_id="inst-1",
                adventure_id="adv-1",
                adventure_revision=1,
                world_revision=1,
                world_minute=60,
                now=self.now_ts,
            )
            self.assertIsNone(scheduled)
            self.assertEqual(len(list_jobs(conn, "inst-1")), 0)

            # 2. 启用脉动并推进到 120 分钟：正常入队
            enable_pulse(conn, "inst-1", now=self.now_ts)
            conn.execute("UPDATE world_instances SET world_minute = 120, revision = 2 WHERE id = 'inst-1'")
            job_120 = schedule_on_turn_commit(
                conn,
                instance_id="inst-1",
                adventure_id="adv-1",
                adventure_revision=2,
                world_revision=2,
                world_minute=120,
                now=self.now_ts + 1,
            )
            self.assertIsNotNone(job_120)

            # 3. 用户立即暂停脉动：未执行的 120 分钟待办自动取消，且暂停期间推进到 240、360 分钟不产生新任务
            pause_pulse(conn, "inst-1", now=self.now_ts + 2)
            conn.execute("UPDATE world_instances SET world_minute = 360, revision = 4 WHERE id = 'inst-1'")
            scheduled_while_paused = schedule_on_turn_commit(
                conn,
                instance_id="inst-1",
                adventure_id="adv-1",
                adventure_revision=4,
                world_revision=4,
                world_minute=360,
                now=self.now_ts + 10,
            )
            self.assertIsNone(scheduled_while_paused)

            # 4. 在 360 分钟恢复脉动：锚点更新为 360，不补造 60..360 的历史
            resumed_cfg = resume_pulse(conn, "inst-1", now=self.now_ts + 20)
            self.assertEqual(resumed_cfg["resume_anchor_minute"], 360)
            self.assertEqual(resumed_cfg["last_pulsed_minute"], 360)

            # 同在 360 分钟尝试调度：因未超过恢复锚点 360 分钟，不补造任务
            self.assertIsNone(
                schedule_on_turn_commit(
                    conn,
                    instance_id="inst-1",
                    adventure_id="adv-1",
                    adventure_revision=4,
                    world_revision=4,
                    world_minute=360,
                    now=self.now_ts + 21,
                )
            )

            # 5. 恢复后玩家继续行动推进到 420 分钟：正常为 420 分钟登记任务
            conn.execute("UPDATE world_instances SET world_minute = 420, revision = 5 WHERE id = 'inst-1'")
            job_420 = schedule_on_turn_commit(
                conn,
                instance_id="inst-1",
                adventure_id="adv-1",
                adventure_revision=5,
                world_revision=5,
                world_minute=420,
                now=self.now_ts + 30,
            )
            self.assertIsNotNone(job_420)
            assert job_420 is not None
            self.assertEqual(job_420["target_world_minute"], 420)

    async def test_offline_activity_lease_pauses_paid_requests_and_preserves_clock(self) -> None:
        """验收：玩家离线或活动租约过期时不发付费请求，现实时间流逝不推进游戏分钟。"""
        with self._db() as conn:
            configure_instance_pulse(conn, "inst-1", enabled=True, cooldown_seconds=0.0, now=self.now_ts)
            conn.execute("UPDATE world_instances SET world_minute = 180, revision = 2 WHERE id = 'inst-1'")
            schedule_on_turn_commit(
                conn,
                instance_id="inst-1",
                adventure_id="adv-1",
                adventure_revision=2,
                world_revision=2,
                world_minute=180,
                now=self.now_ts,
            )

        fake_client = FakePulseModelClient()
        router = PulseSessionRouter()
        router.bind_fake("inst-1", fake_client)
        activity = ActivityLeaseRegistry(default_ttl_seconds=60.0, clock=lambda: self.now_ts)
        # 玩家在线活动租约在 t=5000 刷新，60 秒后（t=5060）过期
        activity.touch("inst-1")

        # 现实时间流逝 3600 秒（t=8600），玩家早已离线
        self.now_ts += 3600.0
        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            clock=lambda: self.now_ts,
        )

        res = await worker.run_once()
        assert res is not None
        self.assertEqual(res["outcome"], "waiting_offline")
        self.assertEqual(res["job"]["status"], "waiting_for_model")
        self.assertEqual(res["job"]["error_code"], "CLIENT_OFFLINE")
        # 离线期间模型调用次数为 0
        self.assertEqual(len(fake_client.calls), 0)

        # 检查数据库中的游戏时间：仍严格停留在 180 分钟，未随现实 1 小时流逝而改变
        with self._db() as conn:
            row = conn.execute("SELECT world_minute, revision FROM world_instances WHERE id = 'inst-1'").fetchone()
            self.assertEqual(row["world_minute"], 180)
            self.assertEqual(row["revision"], 2)

        # 玩家重新上线刷新活动租约并唤醒等待任务
        activity.touch("inst-1")
        with self._db() as conn:
            resume_waiting_jobs(conn, "inst-1", now=self.now_ts)

        res_online = await worker.run_once()
        assert res_online is not None
        self.assertEqual(res_online["outcome"], "completed")
        self.assertEqual(len(fake_client.calls), 1)

    async def test_foreground_commits_turn_during_slow_background_model_and_stale_revision_conflict(self) -> None:
        """验收：网络调用在 SQLite 写事务外，后台慢请求期间前台可立即确认新回合；后台返回后检测到旧版本冲突，拒绝覆盖并重排。"""
        with self._db() as conn:
            configure_instance_pulse(conn, "inst-1", enabled=True, cooldown_seconds=0.0, now=self.now_ts)
            conn.execute("UPDATE world_instances SET world_minute = 120, revision = 1 WHERE id = 'inst-1'")
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "now": self.now_ts,
                },
            )

        started_evt = asyncio.Event()
        release_evt = asyncio.Event()
        slow_client = _SlowModelClient(started_evt, release_evt)

        router = PulseSessionRouter()
        router.bind_fake("inst-1", slow_client)  # type: ignore[arg-type]
        activity = ActivityLeaseRegistry(clock=lambda: self.now_ts)
        activity.touch("inst-1", ttl_seconds=600.0)

        applied_calls: list[Any] = []

        def tracking_applier(
            conn: sqlite3.Connection,
            inst_id: str,
            proposal: Any,
            job: dict[str, Any],
        ) -> dict[str, Any]:
            applied_calls.append((inst_id, proposal, job["base_world_revision"]))
            return {"applied": True}

        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            proposal_applier=tracking_applier,
            clock=lambda: self.now_ts,
        )

        # 启动后台 Worker 处理慢请求
        bg_task = asyncio.create_task(worker.run_once())
        await started_evt.wait()

        # 此时后台正阻塞在模型网络请求中：验证前台可以毫无阻塞地开启写事务并确认新回合（revision: 1 -> 2, world_minute: 120 -> 150）
        with self._db() as fg_conn:
            fg_conn.execute("BEGIN IMMEDIATE")
            fg_conn.execute(
                "UPDATE world_instances SET world_minute = 150, revision = 2 WHERE id = 'inst-1' AND revision = 1"
            )

        # 放行慢速后台模型响应
        self.now_ts += 2.0
        release_evt.set()
        conflict_res = await bg_task

        assert conflict_res is not None
        self.assertEqual(conflict_res["outcome"], "revision_conflict")
        self.assertEqual(conflict_res["expected_revision"], 1)
        self.assertEqual(conflict_res["actual_revision"], 2)
        # 旧快照提议绝未被写入
        self.assertEqual(len(applied_calls), 0)

        # 任务被自动更新为最新基准版本（revision=2, target_world_minute=150）并处于 pending 待重试
        retried_job = conflict_res["job"]
        self.assertEqual(retried_job["status"], "pending")
        self.assertEqual(retried_job["base_world_revision"], 2)
        self.assertEqual(retried_job["target_world_minute"], 150)

        # 推进时间跨过退避窗口，换成快速 Fake 客户端完成重试
        router.bind_fake("inst-1", FakePulseModelClient())
        self.now_ts += 20.0
        activity.touch("inst-1", ttl_seconds=600.0)
        ok_res = await worker.run_once()
        assert ok_res is not None
        self.assertEqual(ok_res["outcome"], "completed")
        self.assertEqual(ok_res["new_world_revision"], 3)
        self.assertEqual(len(applied_calls), 1)
        self.assertEqual(applied_calls[0][2], 2)

    async def test_stale_worker_rejected_after_lease_reclaimed_during_model_call(self) -> None:
        """验收：Worker A 在慢模型请求期间租约过期被 Worker B 接管完成，Worker A 随后拿到模型响应也无法提交。"""
        with self._db() as conn:
            configure_instance_pulse(conn, "inst-1", enabled=True, cooldown_seconds=0.0, now=self.now_ts)
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "now": self.now_ts,
                },
            )

        started_evt = asyncio.Event()
        release_evt = asyncio.Event()
        slow_client = _SlowModelClient(started_evt, release_evt)

        router_a = PulseSessionRouter()
        router_a.bind_fake("inst-1", slow_client)  # type: ignore[arg-type]
        router_b = PulseSessionRouter()
        router_b.bind_fake("inst-1", FakePulseModelClient())

        activity = ActivityLeaseRegistry(clock=lambda: self.now_ts)
        activity.touch("inst-1", ttl_seconds=600.0)
        applied_count = 0

        def counting_applier(conn: sqlite3.Connection, inst_id: str, proposal: Any, job: dict[str, Any]) -> dict[str, Any]:
            nonlocal applied_count
            applied_count += 1
            return {"count": applied_count}

        worker_a = PulseWorker(
            self._db,
            session_router=router_a,
            activity_registry=activity,
            proposal_applier=counting_applier,
            lease_seconds=20.0,
            clock=lambda: self.now_ts,
        )
        worker_b = PulseWorker(
            self._db,
            session_router=router_b,
            activity_registry=activity,
            proposal_applier=counting_applier,
            lease_seconds=20.0,
            clock=lambda: self.now_ts,
        )

        task_a = asyncio.create_task(worker_a.run_once())
        await started_evt.wait()

        # 租约过期（+25s > 20s），Worker B 回收并完成任务
        self.now_ts += 25.0
        res_b = await worker_b.run_once()
        assert res_b is not None
        self.assertEqual(res_b["outcome"], "completed")
        self.assertEqual(applied_count, 1)

        # 旧 Worker A 终于拿到模型响应：在提交复核阶段因租约失效被拒，不会二次写入
        release_evt.set()
        res_a = await task_a
        assert res_a is not None
        self.assertEqual(res_a["outcome"], "rejected_stale_lease")
        self.assertEqual(applied_count, 1)

    async def test_atomic_rollback_when_proposal_applier_fails(self) -> None:
        """验收：当 proposal_applier 内部抛出异常时，SAVEPOINT 原子回滚，不留半写入数据且不推进实例版本。"""
        with self._db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test_side_effects (instance_id TEXT, note TEXT)")
            configure_instance_pulse(conn, "inst-1", enabled=True, cooldown_seconds=0.0, now=self.now_ts)
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "now": self.now_ts,
                },
            )

        def failing_applier(
            conn: sqlite3.Connection,
            inst_id: str,
            proposal: Any,
            job: dict[str, Any],
        ) -> dict[str, Any]:
            # 先写一条临时记录，随后抛出异常验证 SAVEPOINT 回滚
            conn.execute("INSERT INTO test_side_effects VALUES (?, ?)", (inst_id, "dirty_write"))
            raise PulseError("EVENT_CAUSALITY_INVALID", "因果引用校验失败。", status=422)

        router = PulseSessionRouter()
        router.bind_fake("inst-1")
        activity = ActivityLeaseRegistry(clock=lambda: self.now_ts)
        activity.touch("inst-1")
        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            proposal_applier=failing_applier,
            clock=lambda: self.now_ts,
        )

        res = await worker.run_once()
        assert res is not None
        self.assertEqual(res["outcome"], "apply_failed")
        self.assertEqual(res["job"]["status"], "failed")

        with self._db() as conn:
            side_rows = conn.execute("SELECT * FROM test_side_effects").fetchall()
            self.assertEqual(len(side_rows), 0)
            inst_row = conn.execute("SELECT revision FROM world_instances WHERE id = 'inst-1'").fetchone()
            self.assertEqual(inst_row["revision"], 1)

    async def test_observability_redaction_snapshot_restore_and_lifespan(self) -> None:
        """验收：可观察性状态与用量汇总脱敏、快照不含活跃任务与凭据、以及 lifespan 正常启停。"""
        router = PulseSessionRouter()
        secret_key = "sk-super-sensitive-pulse-key-999999"
        router.bind_independent(
            "inst-1",
            base_url="https://api.example.com/v1",
            model="pulse-pro",
            api_key=secret_key,
        )
        activity = ActivityLeaseRegistry(clock=lambda: self.now_ts)
        activity.touch("inst-1")

        with self._db() as conn:
            configure_instance_pulse(
                conn,
                "inst-1",
                enabled=True,
                complexity="deep",
                now=self.now_ts,
            )
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 2,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "now": self.now_ts,
                },
            )

            status_view = get_pulse_status(
                conn,
                "inst-1",
                session_router=router,
                activity_registry=activity,
                now=self.now_ts,
            )
            status_dump = str(status_view)
            self.assertNotIn(secret_key, status_dump)
            self.assertNotIn("session_id", status_dump)
            self.assertEqual(status_view["session"]["mode"], "independent")
            self.assertTrue(status_view["session"]["ready"])
            self.assertEqual(status_view["queue_counts"]["pending"], 1)

            usage_view = get_usage_summary(conn, "inst-1")
            self.assertEqual(usage_view["budget"]["complexity"], "deep")
            self.assertNotIn(secret_key, str(usage_view))

            # 导出快照并恢复到新分支实例 inst-branch
            snap = snapshot(conn, "inst-1")
            self.assertNotIn(secret_key, str(snap))
            self.assertNotIn("jobs", snap)
            restored_cfg = restore(conn, "inst-branch", snap, now=self.now_ts + 10)
            self.assertEqual(restored_cfg["complexity"], "deep")
            self.assertTrue(restored_cfg["enabled"])
            # 新分支实例队列为空，不复制旧实例的进行中/排队任务
            self.assertEqual(len(list_jobs(conn, "inst-branch")), 0)

            # 关闭脉动不影响历史配置读取
            disable_pulse(conn, "inst-1", now=self.now_ts + 20)

        # 验证 lifespan 辅助上下文管理器正常启动与清理
        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            poll_interval_seconds=0.05,
            clock=lambda: self.now_ts,
        )
        async with pulse_worker_lifespan(worker, auto_start_loop=True):
            await asyncio.sleep(0.02)
        # 退出 lifespan 后，脉动自有会话与活动租约已清理
        self.assertEqual(router.pulse_store.active_count, 0)
        self.assertFalse(activity.is_active("inst-1", now=self.now_ts))


if __name__ == "__main__":
    unittest.main()
