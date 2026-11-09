"""M3.2 持久队列单元测试（幂等、连续回合合并、多连接争抢、租约超时回收与旧 Worker 提交拒绝、凭据脱敏）。"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.m3.pulse.errors import PulseError
from app.m3.pulse.queue import (
    cancel,
    cancel_instance_jobs,
    claim,
    complete,
    enqueue,
    fail_or_retry,
    get_job,
    list_jobs,
    reclaim_expired,
    resume_waiting_jobs,
    wait_for_model,
)
from app.m3.pulse.schema import ensure_pulse_schema


class TestPulseQueue(unittest.TestCase):
    """测试持久化队列的所有状态流转与并发租约保护。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_pulse_queue.db"
        with closing(self._connect()) as conn, conn:
            ensure_pulse_schema(conn, ensure_instance_table=True)
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES (?, ?, ?, ?, ?)",
                ("inst-1", "world-1", "adv-1", 60, 1),
            )
            conn.commit()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_enqueue_idempotency_and_completed_never_reruns(self) -> None:
        """验收：相同任务只完成一次，已完成任务再次登记不重复执行。"""
        with closing(self._connect()) as conn, conn:
            job1 = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "idempotency_key": "seed:inst-1:init",
                    "now": 1000.0,
                },
            )
            self.assertFalse(job1["deduplicated"])
            self.assertEqual(job1["status"], "pending")

            # 同一幂等键重复入队：直接返回同一 job_id
            job_dup = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "idempotency_key": "seed:inst-1:init",
                    "now": 1001.0,
                },
            )
            self.assertTrue(job_dup["deduplicated"])
            self.assertEqual(job_dup["job_id"], job1["job_id"])

            # 领取并完成该任务
            claimed = claim(conn, now=1002.0, lease_seconds=30.0)
            self.assertIsNotNone(claimed)
            assert claimed is not None
            done = complete(conn, claimed["job_id"], claimed["lease_token"], now=1005.0)
            self.assertEqual(done["status"], "completed")

            # 完成后再次以同一幂等键入队：保持 completed，绝不重置为 pending
            job_after = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "idempotency_key": "seed:inst-1:init",
                    "now": 1010.0,
                },
            )
            self.assertTrue(job_after["deduplicated"])
            self.assertEqual(job_after["status"], "completed")
            self.assertIsNone(claim(conn, now=1011.0))
            conn.commit()

    def test_consecutive_turns_coalesce_pending_advance_jobs(self) -> None:
        """验收：连续回合可合并待办为更新的已确认时钟目标与基准版本，且旧/新幂等键均不重复执行。"""
        with closing(self._connect()) as conn, conn:
            j1 = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 2,
                    "base_world_revision": 2,
                    "target_world_minute": 90,
                    "idempotency_key": "adv:turn-2",
                    "now": 1000.0,
                },
            )
            j2 = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 3,
                    "base_world_revision": 3,
                    "target_world_minute": 150,
                    "idempotency_key": "adv:turn-3",
                    "now": 1005.0,
                },
            )
            self.assertEqual(j1["job_id"], j2["job_id"])
            self.assertTrue(j2["coalesced"])
            self.assertEqual(j2["trigger_revision"], 3)
            self.assertEqual(j2["base_world_revision"], 3)
            self.assertEqual(j2["target_world_minute"], 150)
            self.assertEqual(j2["payload"]["coalesced_trigger_revisions"], [2, 3])

            # 队列中只有 1 条待运行任务
            jobs = list_jobs(conn, "inst-1")
            self.assertEqual(len(jobs), 1)

            # 领取并完成后，无论用 turn-2 还是 turn-3 的幂等键再次登记，都识别为已完成
            claimed = claim(conn, now=1010.0)
            assert claimed is not None
            complete(conn, claimed["job_id"], claimed["lease_token"], now=1012.0)

            re_t2 = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 2,
                    "base_world_revision": 2,
                    "target_world_minute": 90,
                    "idempotency_key": "adv:turn-2",
                    "now": 1020.0,
                },
            )
            self.assertTrue(re_t2["deduplicated"])
            self.assertEqual(re_t2["status"], "completed")
            conn.commit()

    def test_multi_connection_contention_atomic_claim(self) -> None:
        """验收：多连接争抢同一条待办任务时，只有一个连接能成功领取。"""
        with closing(self._connect()) as conn, conn:
            enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "now": 1000.0,
                },
            )
            conn.commit()

        conn_a = self._connect()
        conn_b = self._connect()
        try:
            conn_a.execute("BEGIN IMMEDIATE")
            claimed_a = claim(conn_a, now=1005.0, lease_seconds=60.0)
            conn_a.commit()

            conn_b.execute("BEGIN IMMEDIATE")
            claimed_b = claim(conn_b, now=1005.0, lease_seconds=60.0)
            conn_b.commit()

            self.assertIsNotNone(claimed_a)
            self.assertIsNone(claimed_b)
        finally:
            conn_a.close()
            conn_b.close()

    def test_lease_expiration_recovery_and_stale_worker_commit_rejected(self) -> None:
        """验收：租约过期后被新 Worker 回收接管，旧 Worker 即使拿到模型响应也绝不能提交。"""
        with closing(self._connect()) as conn, conn:
            job = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "now": 1000.0,
                },
            )

            # Worker A 在 t=1000 领取，租约 30s（到期 1030）
            claim_a = claim(conn, now=1000.0, lease_seconds=30.0)
            assert claim_a is not None
            token_a = claim_a["lease_token"]
            self.assertEqual(claim_a["attempts"], 1)

            # t=1035 时租约已超时，Worker B 直接回收并获得新令牌 token_b
            claim_b = claim(conn, now=1035.0, lease_seconds=30.0)
            assert claim_b is not None
            token_b = claim_b["lease_token"]
            self.assertEqual(claim_b["job_id"], job["job_id"])
            self.assertEqual(claim_b["attempts"], 2)
            self.assertNotEqual(token_a, token_b)

            # 旧 Worker A 迟到返回，尝试用 token_a 提交完成或报错 -> 必须被拒绝（409）
            with self.assertRaises(PulseError) as ctx:
                complete(conn, job["job_id"], token_a, now=1040.0)
            self.assertEqual(ctx.exception.code, "PULSE_LEASE_STOLEN")
            self.assertEqual(ctx.exception.status, 409)

            # 新 Worker B 用 token_b 正常提交成功
            done = complete(conn, job["job_id"], token_b, now=1042.0)
            self.assertEqual(done["status"], "completed")
            conn.commit()

    def test_retry_exponential_backoff_and_max_attempts_failure(self) -> None:
        """验收：失败重试退避与达到最大重试次数后归档为 failed。"""
        with closing(self._connect()) as conn, conn:
            job = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "advance",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 120,
                    "max_attempts": 2,
                    "now": 1000.0,
                },
            )

            # 第 1 次领取并退避重试（base_backoff=10s -> next_retry_at=1015）
            c1 = claim(conn, now=1005.0, lease_seconds=30.0)
            assert c1 is not None
            r1 = fail_or_retry(
                conn,
                job["job_id"],
                c1["lease_token"],
                "MODEL_TIMEOUT",
                "第一次请求超时",
                now=1005.0,
                retryable=True,
                base_backoff_seconds=10.0,
            )
            self.assertEqual(r1["status"], "pending")
            self.assertEqual(r1["next_retry_at"], 1015.0)

            # 退避时间未到（t=1010）时不可被 claim
            self.assertIsNone(claim(conn, now=1010.0))

            # 退避到期（t=1016）可再次领取（attempts=2 == max_attempts）
            c2 = claim(conn, now=1016.0, lease_seconds=30.0)
            assert c2 is not None
            self.assertEqual(c2["attempts"], 2)

            # 第 2 次再失败：已达 max_attempts，标记为 failed
            r2 = fail_or_retry(
                conn,
                job["job_id"],
                c2["lease_token"],
                "MODEL_TIMEOUT",
                "第二次请求超时",
                now=1020.0,
                retryable=True,
            )
            self.assertEqual(r2["status"], "failed")
            self.assertIsNone(claim(conn, now=2000.0))
            conn.commit()

    def test_wait_for_model_reclaim_and_cancel_transitions(self) -> None:
        """验收：等待模型、唤醒恢复、过期显式回收与取消语义。"""
        with closing(self._connect()) as conn, conn:
            job = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "now": 1000.0,
                },
            )
            c1 = claim(conn, now=1001.0, lease_seconds=20.0)
            assert c1 is not None

            # 转入 waiting_for_model（不消耗重试次数）
            w = wait_for_model(
                conn,
                job["job_id"],
                c1["lease_token"],
                error_code="MODEL_SESSION_UNBOUND",
                error_summary="重启后尚未配置 Key",
                now=1002.0,
            )
            self.assertEqual(w["status"], "waiting_for_model")
            self.assertEqual(w["attempts"], 0)
            self.assertIsNone(claim(conn, now=1003.0))

            # 唤醒后重新变回 pending
            resumed_count = resume_waiting_jobs(conn, "inst-1", now=1010.0)
            self.assertEqual(resumed_count, 1)

            # 再次领取后模拟租约过期，测试 reclaim_expired
            c2 = claim(conn, now=1011.0, lease_seconds=10.0)
            assert c2 is not None
            reclaimed = reclaim_expired(conn, now=1025.0)
            self.assertEqual(reclaimed, 1)
            self.assertEqual(get_job(conn, job["job_id"])["status"], "pending")

            # 取消任务
            cancelled = cancel(conn, job["job_id"], reason_code="USER_CANCELLED", now=1030.0)
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(
                cancel_instance_jobs(conn, "inst-1", now=1031.0),
                0,
            )
            conn.commit()

    def test_queue_rejects_credential_storage_and_redacts_errors(self) -> None:
        """验收：队列严禁写入 API Key、会话 ID 或密钥请求头，错误信息自动脱敏。"""
        with closing(self._connect()) as conn, conn:
            with self.assertRaises(PulseError) as ctx1:
                enqueue(
                    conn,
                    {
                        "instance_id": "inst-1",
                        "kind": "seed",
                        "trigger_adventure_id": "adv-1",
                        "trigger_revision": 1,
                        "base_world_revision": 1,
                        "target_world_minute": 60,
                        "payload": {"api_key": "sk-secret-value-123456"},
                    },
                )
            self.assertEqual(ctx1.exception.code, "PULSE_CREDENTIAL_STORAGE_FORBIDDEN")

            with self.assertRaises(PulseError) as ctx2:
                enqueue(
                    conn,
                    {
                        "instance_id": "inst-1",
                        "kind": "seed",
                        "trigger_adventure_id": "adv-1",
                        "trigger_revision": 1,
                        "base_world_revision": 1,
                        "target_world_minute": 60,
                        "payload": {"session_id": "sess_abc123"},
                    },
                )
            self.assertEqual(ctx2.exception.code, "PULSE_CREDENTIAL_STORAGE_FORBIDDEN")

            # 正常入队但在错误摘要中混入 Bearer / sk- 密钥时自动脱敏
            job = enqueue(
                conn,
                {
                    "instance_id": "inst-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "now": 1000.0,
                },
            )
            claimed = claim(conn, now=1001.0)
            assert claimed is not None
            failed = fail_or_retry(
                conn,
                job["job_id"],
                claimed["lease_token"],
                "MODEL_AUTH_FAILED",
                "上游拒绝 Authorization: Bearer sk-live-9876543210abcdef，请检查 api_key=my_secret_token",
                now=1002.0,
                retryable=False,
            )
            raw_dump = str(failed)
            self.assertNotIn("sk-live-9876543210abcdef", raw_dump)
            self.assertNotIn("my_secret_token", raw_dump)
            self.assertIn("[REDACTED]", failed["error_summary"])
            conn.commit()


if __name__ == "__main__":
    unittest.main()
