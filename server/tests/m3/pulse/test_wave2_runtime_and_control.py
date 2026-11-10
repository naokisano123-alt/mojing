"""M3 第二轮（Wave 2）世界脉动付费运行期、在线闸门、代次隔离、实测结算与手机端控制契约单元测试。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterator
import unittest

import httpx

from app.m3.pulse.budget import (
    PulseBudgetManager,
    configure_instance_pulse,
    estimate_call_reservation_tokens,
)
from app.m3.pulse.control import (
    FakePulseDomainHarness,
    PulseRuntimeController,
)
from app.m3.pulse.errors import PulseError
from app.m3.pulse.observability import get_usage_summary
from app.m3.pulse.queue import cancel_instance_jobs, claim, enqueue, get_job, list_jobs
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import (
    PulseModelRequest,
    PulseModelResponse,
    PulseUsageEvidence,
)
from app.m3.pulse.worker import PulseWorker
from app.model_gateway.store import ModelSessionStore


class _ControllableSlowClient:
    """可控慢响应 Mock 客户端，用于确定性测试在途请求期间的关开切换、断网与前台确认。"""

    def __init__(
        self,
        started_event: asyncio.Event,
        release_event: asyncio.Event,
        *,
        proposal: dict[str, Any] | None = None,
        prompt_tokens: int = 120,
        completion_tokens: int = 80,
    ) -> None:
        self.model = "mock-paid-slow-v1"
        self._started = started_event
        self._release = release_event
        self._proposal = proposal
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.calls: list[PulseModelRequest] = []

    async def propose(self, request: PulseModelRequest) -> PulseModelResponse:
        self.calls.append(request)
        self._started.set()
        await self._release.wait()
        prop = self._proposal or {
            "kind": request.kind,
            "base_world_revision": request.base_world_revision,
            "target_world_minute": request.target_world_minute,
            "operations": [
                {
                    "op": "update",
                    "event_id": "fevt_1",
                    "expected_event_revision": 1,
                    "stage": "developing",
                    "status": "active",
                    "next_update_minute": request.target_world_minute + 120,
                }
            ],
            "transmissions": [],
        }
        return PulseModelResponse(
            proposal=prop,
            usage=PulseUsageEvidence(
                purpose=f"world_pulse_{request.kind}",
                model=self.model,
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                total_tokens=self._prompt_tokens + self._completion_tokens,
                usage_source="actual",
                potentially_billed=True,
                outcome="succeeded",
            ),
        )


class TestM3Wave2RuntimeAndControl(unittest.IsolatedAsyncioTestCase):
    """覆盖 M3_WAVE2_GEMINI_LI.md 与 M3_WAVE2_API.md 的全部运行期要求。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_pulse_wave2.db"
        self.now_ts = 10_000.0
        self.utc_now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.domain = FakePulseDomainHarness()

        with self._db() as conn:
            ensure_pulse_schema(conn, ensure_instance_table=True)
            self.domain.ensure_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worlds (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    deleted_at REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS adventures (
                    id TEXT PRIMARY KEY,
                    world_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("INSERT INTO worlds (id, name, deleted_at) VALUES ('world-1', '北境世界', NULL)")
            conn.execute(
                "INSERT INTO adventures (id, world_id, name, mode, revision) VALUES ('adv-live-1', 'world-1', '正式旅程1', 'live', 1)"
            )
            conn.execute(
                "INSERT INTO adventures (id, world_id, name, mode, revision) VALUES ('adv-live-2', 'world-1', '正式旅程2', 'live', 1)"
            )
            conn.execute(
                "INSERT INTO adventures (id, world_id, name, mode, revision) VALUES ('adv-fake-1', 'world-1', '模拟旅程1', 'fake', 1)"
            )
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES ('inst-live-1', 'world-1', 'adv-live-1', 60, 1)"
            )
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES ('inst-live-2', 'world-1', 'adv-live-2', 60, 1)"
            )
            conn.execute(
                "INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision) VALUES ('inst-fake-1', 'world-1', 'adv-fake-1', 60, 1)"
            )
            self.domain.add_canon_source(conn, "world-1", "chunk:canon-north-1", "北境霜木镇与灰岩边关生铁商道设定。")

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

    def _make_controller(
        self,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        narration_store: ModelSessionStore | None = None,
    ) -> tuple[PulseRuntimeController, ModelSessionStore, ModelSessionStore]:
        n_store = narration_store or ModelSessionStore(
            clock=lambda: self.utc_now,
            http_transport=http_transport,
        )
        p_store = ModelSessionStore(
            clock=lambda: self.utc_now,
            http_transport=http_transport,
        )
        router = PulseSessionRouter(
            narration_store=n_store,
            pulse_store=p_store,
            http_transport=http_transport,
        )
        activity = ActivityLeaseRegistry(default_ttl_seconds=45.0, clock=lambda: self.now_ts)
        budget = PulseBudgetManager()
        snap_fn, val_fn, app_fn = self.domain.callbacks()
        worker = PulseWorker(
            self._db,
            session_router=router,
            activity_registry=activity,
            budget_manager=budget,
            snapshot_reader=snap_fn,
            proposal_validator=val_fn,
            proposal_applier=app_fn,
            clock=lambda: self.now_ts,
        )
        ctrl = PulseRuntimeController(self._db, worker=worker)
        return ctrl, n_store, p_store

    def test_control_entrypoints_session_lifecycle_and_dto_redaction(self) -> None:
        """验收1：独立与复用会话控制入口、旅程校验、开关与会话分离、过期等待重连、安全状态 DTO 零凭据泄漏。"""
        http_calls = 0

        def forbidden_network_handler(req: httpx.Request) -> httpx.Response:
            nonlocal http_calls
            http_calls += 1
            return httpx.Response(200, json={})

        ctrl, narr_store, pulse_store = self._make_controller(
            http_transport=httpx.MockTransport(forbidden_network_handler)
        )

        # 1. 校验未知旅程报错 404
        with self.assertRaises(PulseError) as ctx_404:
            ctrl.get_status("adv-non-existent")
        self.assertEqual(ctx_404.exception.status, 404)

        # 2. 初始默认关闭，状态为 off，读取状态绝不续期活动租约
        st0 = ctrl.get_status("adv-live-1", now=self.now_ts)
        self.assertFalse(st0["enabled"])
        self.assertEqual(st0["mode"], "unbound")
        self.assertEqual(st0["state"], "off")
        self.assertFalse(ctrl.activity_registry.is_active("inst-live-1", now=self.now_ts))

        # 3. 正式旅程禁止绑定 fake 模式；模拟旅程禁止绑定付费会话
        with self.assertRaises(PulseError) as ctx_fake_in_live:
            ctrl.connect("adv-live-1", {"mode": "fake"}, now=self.now_ts)
        self.assertEqual(ctx_fake_in_live.exception.code, "PULSE_FAKE_FORBIDDEN_IN_LIVE")

        with self.assertRaises(PulseError) as ctx_live_in_fake:
            ctrl.connect(
                "adv-fake-1",
                {
                    "mode": "independent",
                    "base_url": "https://api.example.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": "sk-secret-do-not-leak-111",
                },
                now=self.now_ts,
            )
        self.assertEqual(ctx_live_in_fake.exception.code, "PULSE_LIVE_FORBIDDEN_IN_FAKE")

        # 4. 独立连接：只绑定有效内存会话，不自动启用脉动、不发付费测试请求
        secret_ind_key = "sk-independent-secret-key-998877"
        st_conn = ctrl.connect(
            "adv-live-1",
            {
                "mode": "independent",
                "base_url": "https://api.example.com/v1",
                "model": "pulse-model-ind",
                "api_key": secret_ind_key,
            },
            now=self.now_ts,
        )
        self.assertEqual(http_calls, 0)
        self.assertFalse(st_conn["enabled"])
        self.assertEqual(st_conn["mode"], "independent")
        self.assertEqual(st_conn["state"], "off")
        self.assertEqual(st_conn["model"], "pulse-model-ind")
        self.assertNotIn(secret_ind_key, json.dumps(st_conn))
        self.assertNotIn("session_id", json.dumps(st_conn))

        # 5. 切换为复用叙事会话：自动清理原独立脉动会话，且关闭脉动/解绑绝不删除叙事会话
        secret_narr_key = "sk-narration-shared-key-554433"
        narr_sess = narr_store.create(
            base_url="https://api.example.com/v1",
            model="narration-shared-gpt",
            api_key=secret_narr_key,
        )
        narr_sid = str(narr_sess["session_id"])

        st_reuse = ctrl.connect(
            "adv-live-1",
            {"mode": "reuse_narration", "narration_session_id": narr_sid},
            now=self.now_ts,
        )
        self.assertEqual(pulse_store.active_count, 0)
        self.assertEqual(narr_store.active_count, 1)
        self.assertEqual(st_reuse["mode"], "reuse_narration")
        self.assertEqual(st_reuse["model"], "narration-shared-gpt")
        self.assertNotIn(secret_narr_key, json.dumps(st_reuse))
        self.assertNotIn(narr_sid, json.dumps(st_reuse))

        # 6. 开启脉动但尚未激活在线租约 -> state 为 offline
        st_en_offline = ctrl.set_enabled("adv-live-1", True, now=self.now_ts)
        self.assertTrue(st_en_offline["enabled"])
        self.assertEqual(st_en_offline["state"], "offline")
        self.assertEqual(st_en_offline["reason_code"], "CLIENT_OFFLINE")

        # 7. 激活在线租约 -> state 变为 working（因首次开启自动入队了 seed 任务）
        st_online = ctrl.set_activity("adv-live-1", True, now=self.now_ts)
        self.assertEqual(st_online["state"], "working")

        # 8. 关闭脉动：保留叙事会话，状态回到 off
        st_off = ctrl.set_enabled("adv-live-1", False, now=self.now_ts + 1)
        self.assertFalse(st_off["enabled"])
        self.assertEqual(st_off["state"], "off")
        self.assertEqual(narr_store.active_count, 1)

        # 9. 重新启用后模拟叙事会话 TTL 过期（+31 分钟）：进入 waiting_for_model，绝不静默切换
        ctrl.set_enabled("adv-live-1", True, now=self.now_ts + 2)
        self.utc_now = self.utc_now + timedelta(minutes=31)
        st_expired = ctrl.get_status("adv-live-1", now=self.now_ts + 10)
        self.assertTrue(st_expired["enabled"])
        self.assertEqual(st_expired["state"], "waiting_for_model")
        self.assertEqual(st_expired["reason_code"], "MODEL_SESSION_EXPIRED")

    async def test_online_gate_exclusive_activity_and_offline_time_anchor_no_backlog(self) -> None:
        """验收2：在线闸门短租约、切旅程独占续期、离线/未绑定推进时间锚不积压旧任务、前台恢复不推进世界时间。"""
        ctrl, _, _ = self._make_controller()
        ctrl.session_router.bind_fake("inst-live-1")
        ctrl.session_router.bind_fake("inst-live-2")

        # 启用旅程 1 与旅程 2，并首先在旅程 1 激活在线租约
        ctrl.set_enabled("adv-live-1", True, auto_enqueue_seed=False, now=self.now_ts)
        ctrl.set_enabled("adv-live-2", True, auto_enqueue_seed=False, now=self.now_ts)
        ctrl.set_activity("adv-live-1", True, ttl_seconds=30.0, exclusive=True, now=self.now_ts)
        self.assertTrue(ctrl.activity_registry.is_active("inst-live-1", now=self.now_ts))

        # 手机端切换到旅程 2（exclusive=True）：旅程 1 的在线租约立即失效
        ctrl.set_activity("adv-live-2", True, ttl_seconds=30.0, exclusive=True, now=self.now_ts + 5)
        self.assertFalse(ctrl.activity_registry.is_active("inst-live-1", now=self.now_ts + 5))
        self.assertTrue(ctrl.activity_registry.is_active("inst-live-2", now=self.now_ts + 5))

        # 旅程 1 处于离线期间，玩家确认了 3 个回合（world_minute: 60 -> 120 -> 180 -> 240）
        with self._db() as conn:
            for rev, w_min in ((2, 120), (3, 180), (4, 240)):
                conn.execute(
                    "UPDATE world_instances SET world_minute = ?, revision = ? WHERE id = 'inst-live-1'",
                    (w_min, rev),
                )
                scheduled = ctrl.on_turn_committed(
                    conn,
                    instance_id="inst-live-1",
                    adventure_id="adv-live-1",
                    adventure_revision=rev,
                    world_revision=rev,
                    world_minute=w_min,
                    now=self.now_ts + 10 + rev,
                )
                self.assertIsNone(scheduled)

            # 离线期间不积压任何可重演旧任务，且时间锚已推进到 240
            self.assertEqual(len(list_jobs(conn, "inst-live-1")), 0)
            cfg = conn.execute(
                "SELECT last_pulsed_minute, resume_anchor_minute FROM pulse_instance_configs WHERE instance_id = 'inst-live-1'"
            ).fetchone()
            self.assertEqual(int(cfg["resume_anchor_minute"]), 240)
            self.assertEqual(int(cfg["last_pulsed_minute"]), 240)

        # 玩家切回旅程 1 前台恢复（set_activity(True)）：不改变 world_minute（仍为 240），也不补演离线期间事件
        self.now_ts += 20.0
        st_resumed = ctrl.set_activity("adv-live-1", True, ttl_seconds=30.0, exclusive=True, now=self.now_ts)
        self.assertEqual(st_resumed["world_minute"], 240)
        self.assertEqual(st_resumed["state"], "ready")
        self.assertIsNone(await ctrl.worker.run_once(instance_id="inst-live-1"))

        # 心跳缺失超过 30s 短租约后自动兜底失效
        self.now_ts += 35.0
        st_ttl_expired = ctrl.get_status("adv-live-1", now=self.now_ts)
        self.assertEqual(st_ttl_expired["state"], "offline")
        self.assertEqual(st_ttl_expired["reason_code"], "CLIENT_OFFLINE")

    async def test_two_gates_and_generation_blocks_slow_response_after_off_on_toggle(self) -> None:
        """验收3：请求前和提交前两道门 + 代次机制阻止「关->开」切换后在途慢响应提交，且已发请求用量如实结算。"""
        ctrl, _, _ = self._make_controller()

        with self._db() as conn:
            # 预先放入一条到期事件 fevt_1
            conn.execute(
                """
                INSERT INTO m3_pulse_fake_events(
                    instance_id, event_id, kind, title, description, location, scope,
                    stage, status, created_minute, updated_minute, next_update_minute,
                    cause_event_ids, source_ids, revision
                ) VALUES ('inst-live-1', 'fevt_1', 'thread', '灰岩边关加税', '关税上调', '灰岩边关', 'regional',
                          'initial', 'active', 0, 0, 60, '[]', '["chunk:canon-north-1"]', 1)
                """
            )

        started_evt = asyncio.Event()
        release_evt = asyncio.Event()
        slow_client = _ControllableSlowClient(started_evt, release_evt, prompt_tokens=140, completion_tokens=60)
        ctrl.session_router.bind_fake("inst-live-1", slow_client)  # type: ignore[arg-type]
        ctrl.set_activity("adv-live-1", True, now=self.now_ts)
        ctrl.set_enabled("adv-live-1", True, auto_enqueue_seed=False, now=self.now_ts)

        with self._db() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 120, revision = 2 WHERE id = 'inst-live-1'")
            ctrl.on_turn_committed(
                conn,
                instance_id="inst-live-1",
                adventure_id="adv-live-1",
                adventure_revision=2,
                world_revision=2,
                world_minute=120,
                now=self.now_ts + 1,
            )

        # 启动 Worker 领取任务并发起慢模型调用（通过第一道门时 generation=2）
        bg_task = asyncio.create_task(ctrl.worker.run_once(instance_id="inst-live-1"))
        await started_evt.wait()

        # 在模型请求在途期间：用户先关闭脉动（generation -> 3），紧接着又重新开启脉动（generation -> 4）！
        self.now_ts += 2.0
        ctrl.set_enabled("adv-live-1", False, now=self.now_ts)
        self.now_ts += 1.0
        ctrl.set_enabled("adv-live-1", True, auto_enqueue_seed=False, now=self.now_ts)

        # 放行旧代次的慢响应：第二道门必须检测到 generation 已变更（2 != 4），丢弃旧结果，不修改事件与实例版本！
        self.now_ts += 1.0
        release_evt.set()
        res = await bg_task
        assert res is not None
        self.assertEqual(res["outcome"], "discarded_stale_generation")
        self.assertEqual(res["reason_code"], "PULSE_GENERATION_CHANGED")
        self.assertEqual(res["expected_generation"], 2)
        self.assertEqual(res["actual_generation"], 4)

        with self._db() as conn:
            # 领域状态未受污染：fevt_1 仍为 revision=1, stage='initial'，实例版本仍为 2
            ev = conn.execute(
                "SELECT stage, revision FROM m3_pulse_fake_events WHERE instance_id = 'inst-live-1' AND event_id = 'fevt_1'"
            ).fetchone()
            self.assertEqual(ev["stage"], "initial")
            self.assertEqual(int(ev["revision"]), 1)
            inst = conn.execute("SELECT revision FROM world_instances WHERE id = 'inst-live-1'").fetchone()
            self.assertEqual(int(inst["revision"]), 2)

            # 但已经发出的模型请求产生的 200 tokens 仍单独如实结算进账本
            summary = get_usage_summary(conn, "inst-live-1")
            self.assertEqual(summary["current_day_usage"]["potentially_billed_tokens"], 200)
            self.assertEqual(summary["current_day_usage"]["billed_calls"], 1)

    async def test_startup_recovery_checks_switch_connection_and_activity(self) -> None:
        """验收4：进程重启恢复（recover_on_startup）核对当下开关、连接与活动条件，只恢复安全可重试任务。"""
        with self._db() as conn:
            configure_instance_pulse(conn, "inst-live-1", enabled=True, now=self.now_ts)
            configure_instance_pulse(conn, "inst-live-2", enabled=False, now=self.now_ts)

            # inst-live-1 有一条在崩溃前处于 running 的任务（attempts=1 < max_attempts=3）
            j1 = enqueue(
                conn,
                {
                    "instance_id": "inst-live-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-live-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "now": self.now_ts,
                },
            )
            claim(conn, now=self.now_ts, lease_seconds=60.0, instance_id="inst-live-1")

            # inst-live-2（已关闭脉动）有一条遗留 running 任务
            enqueue(
                conn,
                {
                    "instance_id": "inst-live-2",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-live-2",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "now": self.now_ts,
                },
            )
            claim(conn, now=self.now_ts, lease_seconds=60.0, instance_id="inst-live-2")

        # 模拟进程重启：新建控制器（内存无会话绑定、无在线租约）
        self.now_ts += 10.0
        ctrl, _, _ = self._make_controller()
        rec_summary = ctrl.worker.recover_on_startup()
        self.assertEqual(rec_summary["cancelled_disabled"], 1)
        self.assertEqual(rec_summary["waiting_for_model"], 1)

        with self._db() as conn:
            j1_after = get_job(conn, j1["job_id"])
            self.assertEqual(j1_after["status"], "waiting_for_model")
            self.assertEqual(j1_after["error_code"], "MODEL_SESSION_UNBOUND")
            # 重启恢复不额外扣减重试次数（回退至 0）
            self.assertEqual(j1_after["attempts"], 0)

    async def test_mock_http_paid_path_actual_settlement_simulated_separation_and_failures(self) -> None:
        """验收5：Mock HTTP 覆盖正常 usage（实测结算约束后续调用）、缺失 usage、401 认证失败、响应丢失、超时计费及模拟与计费用量分离。"""
        http_scenario = "actual_high"

        def mock_openai_handler(req: httpx.Request) -> httpx.Response:
            if http_scenario == "actual_high":
                # 实测消耗 700 tokens（高于预留估算），验证以实测结算并约束后续调用
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        {
                                            "kind": "seed",
                                            "operations": [
                                                {
                                                    "op": "create",
                                                    "kind": "thread",
                                                    "title": f"主线事件{i}",
                                                    "description": "由实测模型生成的事件线",
                                                    "location": "霜木镇",
                                                    "scope": "local",
                                                    "stage": "initial",
                                                    "status": "active",
                                                    "next_update_minute": 120 if i == 1 else 600,
                                                    "source_ids": ["chunk:canon-north-1"],
                                                }
                                                for i in range(1, 4)
                                            ],
                                            "transmissions": [],
                                        }
                                    ),
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 500,
                            "completion_tokens": 200,
                            "total_tokens": 700,
                        },
                    },
                )
            if http_scenario == "missing_usage":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        {
                                            "kind": "advance",
                                            "operations": [
                                                {
                                                    "op": "update",
                                                    "event_id": "fevt_4",
                                                    "expected_event_revision": 1,
                                                    "stage": "step2",
                                                    "status": "active",
                                                    "next_update_minute": 240,
                                                }
                                            ],
                                            "transmissions": [
                                                {
                                                    "holder_id": "player",
                                                    "knowledge_type": "rumor",
                                                    "channel": "notice",
                                                    "title": "镇公所告示",
                                                    "content": "公告栏贴出商道税率变动传闻。",
                                                    "source_event_id": "fevt_4",
                                                    "learned_minute": 120,
                                                }
                                            ],
                                        }
                                    ),
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    },
                )
            if http_scenario == "response_lost":
                raise httpx.RemoteProtocolError("Server disconnected mid-stream before finishing response")
            if http_scenario == "timeout":
                raise httpx.ReadTimeout("Upstream read timed out")
            return httpx.Response(401, json={"error": {"message": "Invalid API Key sk-bad-key-000"}})

        ctrl, _, _ = self._make_controller(http_transport=httpx.MockTransport(mock_openai_handler))

        # 1. 先在 inst-live-1 上跑一次 Fake 模拟调用（产生 120 simulated_tokens），验证模拟用量不占用后续付费预算
        ctrl.session_router.bind_fake("inst-live-1")
        ctrl.set_activity("adv-live-1", True, now=self.now_ts)
        with self._db() as conn:
            configure_instance_pulse(
                conn,
                "inst-live-1",
                enabled=True,
                daily_call_budget=6,
                daily_token_budget=3000,
                max_tokens_per_round=1200,
                cooldown_seconds=0.0,
                now=self.now_ts,
            )
            enqueue(
                conn,
                {
                    "instance_id": "inst-live-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-live-1",
                    "trigger_revision": 1,
                    "base_world_revision": 1,
                    "target_world_minute": 60,
                    "idempotency_key": "fake-seed-run",
                    "now": self.now_ts,
                },
            )
        res_fake = await ctrl.worker.run_once(instance_id="inst-live-1")
        assert res_fake is not None
        self.assertEqual(res_fake["outcome"], "completed")

        # 清理 Fake 生成的事件，切换绑定真实独立会话（MockTransport）
        with self._db() as conn:
            conn.execute("DELETE FROM m3_pulse_fake_events WHERE instance_id = 'inst-live-1'")

        ctrl.connect(
            "adv-live-1",
            {
                "mode": "independent",
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o-mini",
                "api_key": "sk-live-valid-key-123456",
            },
            now=self.now_ts + 1,
        )

        # 2. 发起付费 Seed 请求（http_scenario = "actual_high"）：
        # 调用前预留约 450 tokens，上游实测返回 700 tokens -> 以 700 实测结算（生成 fevt_4, fevt_5, fevt_6）！
        with self._db() as conn:
            enqueue(
                conn,
                {
                    "instance_id": "inst-live-1",
                    "kind": "seed",
                    "trigger_adventure_id": "adv-live-1",
                    "trigger_revision": 2,
                    "base_world_revision": 2,
                    "target_world_minute": 60,
                    "idempotency_key": "paid-seed-1",
                    "now": self.now_ts + 2,
                },
            )
        self.now_ts += 3.0
        res_paid_1 = await ctrl.worker.run_once(instance_id="inst-live-1")
        assert res_paid_1 is not None
        self.assertEqual(res_paid_1["outcome"], "completed")
        self.assertEqual(res_paid_1["usage"]["usage_source"], "actual")
        self.assertEqual(res_paid_1["usage"]["total_tokens"], 700)
        self.assertGreater(res_paid_1["usage"]["settlement_delta_tokens"], 0)

        # 验证状态 DTO 中：paid actual_tokens=700，simulated_tokens=120，二者严格分离，剩余付费额度 = 3000 - 700 = 2300
        st_after_1 = ctrl.get_status("adv-live-1", now=self.now_ts)
        self.assertEqual(st_after_1["usage"]["actual_tokens"], 700)
        self.assertEqual(st_after_1["usage"]["simulated_tokens"], 120)
        self.assertEqual(st_after_1["usage"]["remaining_tokens"], 2300)

        # 3. 推进到 120 分钟（fevt_4 到期），测试缺失 usage 场景（http_scenario = "missing_usage"）
        http_scenario = "missing_usage"
        with self._db() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 120, revision = 3 WHERE id = 'inst-live-1'")
            ctrl.on_turn_committed(
                conn,
                instance_id="inst-live-1",
                adventure_id="adv-live-1",
                adventure_revision=3,
                world_revision=3,
                world_minute=120,
                now=self.now_ts + 1,
            )
        self.now_ts += 2.0
        res_est = await ctrl.worker.run_once(instance_id="inst-live-1")
        assert res_est is not None
        self.assertEqual(res_est["outcome"], "completed")
        self.assertEqual(res_est["usage"]["usage_source"], "estimated")
        self.assertTrue(res_est["usage"]["potentially_billed"])

        # 4. 推进到 180 分钟（无到期事件，因 fevt_4 下次更新在 240 分钟）：测试无到期事件直接跳过，不发请求、不扣费！
        with self._db() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 180, revision = 4 WHERE id = 'inst-live-1'")
            ctrl.on_turn_committed(
                conn,
                instance_id="inst-live-1",
                adventure_id="adv-live-1",
                adventure_revision=4,
                world_revision=4,
                world_minute=180,
                now=self.now_ts + 1,
            )
        self.now_ts += 2.0
        res_skip = await ctrl.worker.run_once(instance_id="inst-live-1")
        assert res_skip is not None
        self.assertEqual(res_skip["outcome"], "skipped_no_due_events")
        self.assertEqual(res_skip["reason_code"], "NO_DUE_EVENTS")

        # 5. 推进到 240 分钟（fevt_4 再次到期）：测试响应中途丢失（http_scenario = "response_lost"）计入可能计费
        http_scenario = "response_lost"
        with self._db() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 240, revision = 5 WHERE id = 'inst-live-1'")
            ctrl.on_turn_committed(
                conn,
                instance_id="inst-live-1",
                adventure_id="adv-live-1",
                adventure_revision=5,
                world_revision=5,
                world_minute=240,
                now=self.now_ts + 1,
            )
        self.now_ts += 2.0
        res_lost = await ctrl.worker.run_once(instance_id="inst-live-1")
        assert res_lost is not None
        self.assertEqual(res_lost["outcome"], "model_failed")
        self.assertEqual(res_lost["job"]["error_code"], "MODEL_RESPONSE_LOST")

        st_after_lost = ctrl.get_status("adv-live-1", now=self.now_ts)
        self.assertGreaterEqual(st_after_lost["usage"]["timeout_maybe_billed_calls"], 1)
        self.assertGreater(st_after_lost["usage"]["timeout_maybe_billed_tokens"], 0)

        # 6. 跨过重试退避后测试 401 认证失败（http_scenario = "auth_fail"）：
        # 不计费，且会话自动标记失效，状态转为 waiting_for_model (MODEL_AUTH_FAILED)
        http_scenario = "auth_fail"
        self.now_ts += 10.0
        ctrl.set_activity("adv-live-1", True, now=self.now_ts)
        res_auth = await ctrl.worker.run_once(instance_id="inst-live-1")
        assert res_auth is not None
        self.assertEqual(res_auth["outcome"], "waiting_for_model")
        self.assertEqual(res_auth["job"]["error_code"], "MODEL_AUTH_FAILED")
        self.assertNotIn("sk-bad-key-000", str(res_auth))

        st_auth_fail = ctrl.get_status("adv-live-1", now=self.now_ts)
        self.assertEqual(st_auth_fail["state"], "waiting_for_model")
        self.assertEqual(st_auth_fail["reason_code"], "MODEL_AUTH_FAILED")
        self.assertEqual(st_auth_fail["usage"]["aborted_unbilled_calls"], 1)

    async def test_fake_domain_callbacks_seed_block_and_atomic_rollback_on_invalid_relay(self) -> None:
        """验收6：缺少 Canon 时 Seed 阻塞等待；知识传播校验失败时回滚同事务内的事件写入，同时保留用量结算。"""
        ctrl, _, _ = self._make_controller()
        ctrl.session_router.bind_fake("inst-live-2")
        ctrl.set_activity("adv-live-2", True, now=self.now_ts)

        # 1. 删除 world-1 的 Canon 资料，测试 Seed 阻塞（PULSE_SOURCE_REQUIRED）
        with self._db() as conn:
            conn.execute("DELETE FROM m3_pulse_fake_sources WHERE world_id = 'world-1'")
        ctrl.set_enabled("adv-live-2", True, auto_enqueue_seed=True, now=self.now_ts)

        res_blocked = await ctrl.worker.run_once(instance_id="inst-live-2")
        assert res_blocked is not None
        self.assertEqual(res_blocked["outcome"], "blocked_snapshot")
        self.assertEqual(res_blocked["reason_code"], "PULSE_SOURCE_REQUIRED")
        st_blocked = ctrl.get_status("adv-live-2", now=self.now_ts)
        self.assertEqual(st_blocked["state"], "waiting_for_model")
        self.assertEqual(st_blocked["reason_code"], "PULSE_SOURCE_REQUIRED")

        # 2. 补充 Canon 资料并预先放入一条到期事件，测试 Advance 批次中「事件更新成功 + 传播转述人未获知失败」的原子回滚
        with self._db() as conn:
            self.domain.add_canon_source(conn, "world-1", "chunk:canon-north-1", "北境贸易设定")
            cancel_instance_jobs(conn, "inst-live-2", include_running=True, now=self.now_ts + 1)
            conn.execute(
                """
                INSERT INTO m3_pulse_fake_events(
                    instance_id, event_id, kind, title, description, location, scope,
                    stage, status, created_minute, updated_minute, next_update_minute,
                    cause_event_ids, source_ids, revision
                ) VALUES ('inst-live-2', 'fevt_relay_1', 'thread', '商队改道', '灰鬃商队改走白河线', '白河谷地', 'regional',
                          'initial', 'active', 0, 0, 60, '[]', '["chunk:canon-north-1"]', 1)
                """
            )

        # 构造一个模型响应：先更新 fevt_relay_1 并新建一条临时事件，随后附带一条由「尚未获知此事的 npc:uninformed_innkeeper」转述给 player 的非法传播
        def bad_relay_generator(req: PulseModelRequest) -> dict[str, Any]:
            return {
                "kind": "advance",
                "base_world_revision": req.base_world_revision,
                "target_world_minute": req.target_world_minute,
                "operations": [
                    {
                        "op": "update",
                        "event_id": "fevt_relay_1",
                        "expected_event_revision": 1,
                        "stage": "rerouted",
                        "status": "active",
                        "next_update_minute": req.target_world_minute + 60,
                    },
                    {
                        "op": "create",
                        "kind": "incident",
                        "title": "生铁延迟抵镇",
                        "description": "生铁推迟三日",
                        "location": "霜木镇",
                        "scope": "local",
                        "stage": "active",
                        "status": "active",
                        "next_update_minute": req.target_world_minute + 60,
                        "cause_event_ids": ["fevt_relay_1"],
                        "source_ids": ["chunk:canon-north-1"],
                    },
                ],
                "transmissions": [
                    {
                        "holder_id": "player",
                        "knowledge_type": "confirmed",
                        "channel": "conversation",
                        "source_person_id": "npc:uninformed_innkeeper",
                        "source_event_id": "fevt_relay_1",
                        "title": "掌柜转述商队改道",
                        "content": "掌柜告诉你商队改道了。",
                        "learned_minute": req.target_world_minute,
                    }
                ],
            }

        from app.m3.pulse.client import FakePulseModelClient

        ctrl.session_router.bind_fake("inst-live-2", FakePulseModelClient(generator=bad_relay_generator))
        with self._db() as conn:
            conn.execute("UPDATE world_instances SET world_minute = 120, revision = 2 WHERE id = 'inst-live-2'")
            ctrl.on_turn_committed(
                conn,
                instance_id="inst-live-2",
                adventure_id="adv-live-2",
                adventure_revision=2,
                world_revision=2,
                world_minute=120,
                now=self.now_ts + 5,
            )

        self.now_ts += 6.0
        res_rollback = await ctrl.worker.run_once(instance_id="inst-live-2")
        assert res_rollback is not None
        self.assertEqual(res_rollback["outcome"], "apply_failed")
        self.assertEqual(res_rollback["error_code"], "RELAY_HOLDER_UNKNOWN")

        with self._db() as conn:
            # 验证事件表与实例版本均已完整回滚，没有任何半写入事件！
            events = conn.execute(
                "SELECT event_id, stage, revision FROM m3_pulse_fake_events WHERE instance_id = 'inst-live-2'"
            ).fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], "fevt_relay_1")
            self.assertEqual(events[0]["stage"], "initial")
            self.assertEqual(int(events[0]["revision"]), 1)

            facts = conn.execute(
                "SELECT * FROM m3_pulse_fake_knowledge WHERE instance_id = 'inst-live-2'"
            ).fetchall()
            self.assertEqual(len(facts), 0)

            inst = conn.execute("SELECT revision FROM world_instances WHERE id = 'inst-live-2'").fetchone()
            self.assertEqual(int(inst["revision"]), 2)

            # 但本次调用的用量证据已单独结算保留
            self.assertEqual(res_rollback["usage"]["total_tokens"], 120)

    def test_envelope_validation_rejects_unknown_root_player_ledger_and_seed_transmissions(self) -> None:
        """验收7：纯校验拦截未知根字段、玩家账本修改、任意路径补丁、Seed 传播及未授权事件引用。"""
        from app.m3.pulse.worker import validate_pulse_envelope

        ctx = {
            "instance_id": "inst-live-1",
            "kind": "advance",
            "base_world_revision": 2,
            "target_world_minute": 120,
            "snapshot": {
                "allowed_source_ids": ["chunk:canon-north-1"],
                "allowed_event_ids": ["fevt_1"],
            },
        }

        # 1. 未知根字段
        with self.assertRaises(PulseError) as c1:
            validate_pulse_envelope({"operations": [], "malicious_root": 123}, ctx)
        self.assertEqual(c1.exception.code, "PROPOSAL_UNKNOWN_ROOT_FIELD")

        # 2. 试图修改玩家账本
        with self.assertRaises(PulseError) as c2:
            validate_pulse_envelope({"operations": [{"op": "create", "coins_delta": 999}]}, ctx)
        self.assertEqual(c2.exception.code, "PLAYER_LEDGER_PROTECTED")

        # 3. 试图使用任意 JSON 路径补丁
        with self.assertRaises(PulseError) as c3:
            validate_pulse_envelope({"operations": [{"op": "update", "$set": {"a": 1}}]}, ctx)
        self.assertEqual(c3.exception.code, "ARBITRARY_PATH_FORBIDDEN")

        # 4. Seed 阶段夹带 transmissions
        seed_ctx = {**ctx, "kind": "seed"}
        with self.assertRaises(PulseError) as c4:
            validate_pulse_envelope(
                {
                    "operations": [],
                    "transmissions": [{"holder_id": "player", "source_event_id": "fevt_1", "channel": "notice"}],
                },
                seed_ctx,
            )
        self.assertEqual(c4.exception.code, "SEED_TRANSMISSIONS_FORBIDDEN")

        # 5. Advance 阶段引用本批新建临时事件 ID 或未授权事件 ID
        with self.assertRaises(PulseError) as c5:
            validate_pulse_envelope(
                {
                    "operations": [],
                    "transmissions": [{"holder_id": "player", "source_event_id": "temp_new_1", "channel": "notice"}],
                },
                ctx,
            )
        self.assertEqual(c5.exception.code, "TEMP_EVENT_REFERENCE_FORBIDDEN")

        with self.assertRaises(PulseError) as c6:
            validate_pulse_envelope(
                {
                    "operations": [],
                    "transmissions": [{"holder_id": "player", "source_event_id": "fevt_other_99", "channel": "notice"}],
                },
                ctx,
            )
        self.assertEqual(c6.exception.code, "UNAUTHORIZED_TRANSMISSION_EVENT")

        # 6. 上下文 + 输出上限预留计算验证
        p_est, out_cap, total_res = estimate_call_reservation_tokens(
            ctx["snapshot"],
            kind="advance",
            max_output_tokens=300,
            max_tokens_per_round=2048,
        )
        self.assertGreater(p_est, 0)
        self.assertEqual(out_cap, 300)
        self.assertEqual(total_res, p_est + 300)


if __name__ == "__main__":
    unittest.main()
