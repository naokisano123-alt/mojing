"""M3.2 异步世界脉动调度器、后台 Worker 与 FastAPI Lifespan 接线。

严格遵循契约与事务边界：
1. 默认关闭；仅当实例脉动处于启用且未暂停状态时，才在已确认回合事务内登记任务。
2. 现实时间与游戏时间分离：Worker 只消费已确认的 world_minute，离线或现实时间流逝绝不推进 world_minute。
3. 网络/模型调用严格位于 SQLite 写事务之外，绝不在持有数据库写锁时等待模型响应，保证前台可随时确认新回合。
4. 后台提交前在同一短写事务内重新核验：
   - 队列任务租约令牌（lease_token）与租约有效期（lease_until）；
   - 实例脉动开关（enabled 且未 paused）；
   - 前台在线活动租约（client_online）；
   - 世界实例根版本（world_instances.revision == snapshot_revision），若前台已推进版本则拒绝覆盖并回滚，将任务更新到新版本退避重试。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import sqlite3
import time
from typing import Any, AsyncIterator, Callable, ContextManager

from app.m3.pulse.budget import (
    BudgetExhaustedError,
    PulseBudgetManager,
    get_or_init_instance_config,
)
from app.m3.pulse.client import UpstreamRequestFailedError, estimate_tokens
from app.m3.pulse.errors import PulseError, sanitize_text
from app.m3.pulse.queue import (
    _require_instance,
    cancel,
    claim,
    complete,
    enqueue,
    fail_or_retry,
    verify_active_lease,
    wait_for_model,
)
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import (
    JobKind,
    PulseModelRequest,
    PulseTrigger,
    PulseUsageEvidence,
)

# 回调类型定义（支持注入 Fake 或其他模块的真实实现）
SnapshotReader = Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]]
ProposalValidator = Callable[[dict[str, Any], dict[str, Any]], Any]
ProposalApplier = Callable[[sqlite3.Connection, str, Any, dict[str, Any]], dict[str, Any] | None]


def _default_snapshot_reader(
    conn: sqlite3.Connection,
    instance_id: str,
    job: dict[str, Any],
) -> dict[str, Any]:
    inst = _require_instance(conn, instance_id)
    return {
        "instance_id": instance_id,
        "world_id": inst["world_id"],
        "world_minute": int(inst["world_minute"]),
        "world_revision": int(inst["revision"]),
        "job_kind": job["kind"],
        "allowed_source_ids": list(job.get("payload", {}).get("allowed_source_ids") or ["chunk:canon-1"]),
    }


def _default_proposal_validator(proposal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise PulseError("PROPOSAL_INVALID", "模型提议必须是 JSON 对象。", status=422)
    ops = proposal.get("operations", [])
    if not isinstance(ops, list):
        raise PulseError("PROPOSAL_INVALID", "operations 必须是数组。", status=422)
    return proposal


def _default_proposal_applier(
    conn: sqlite3.Connection,
    instance_id: str,
    validated_proposal: Any,
    job: dict[str, Any],
) -> dict[str, Any]:
    ops = validated_proposal.get("operations", []) if isinstance(validated_proposal, dict) else []
    return {"applied_operations": len(ops)}


def schedule_on_turn_commit(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    adventure_id: str,
    adventure_revision: int,
    world_revision: int | None = None,
    world_minute: int | None = None,
    kind: JobKind = "advance",
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """在前台已确认回合的同一个短写事务内调用，按开关与已确认游戏分钟登记后台脉动任务。

    - 若该实例脉动未启用（默认关闭）或已暂停，不产生任何后台任务；
    - 仅消费已确认的 world_minute；若暂停恢复后或游戏时间未达到最小推进间隔，跳过登记。
    """
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    inst = _require_instance(conn, instance_id)
    cfg = get_or_init_instance_config(conn, instance_id, now=now)

    confirmed_rev = int(world_revision) if world_revision is not None else int(inst["revision"])
    confirmed_min = int(world_minute) if world_minute is not None else int(inst["world_minute"])

    if not cfg["enabled"] or cfg["paused"]:
        # 暂停或关闭期间只记录最新世界时刻锚点，绝不入队，确保恢复时不补造暂停期间的历史
        conn.execute(
            """
            UPDATE pulse_instance_configs
            SET resume_anchor_minute = MAX(resume_anchor_minute, ?),
                last_pulsed_minute = MAX(last_pulsed_minute, ?),
                updated_at = ?
            WHERE instance_id = ?
            """,
            (confirmed_min, confirmed_min, float(now) if now is not None else time.time(), instance_id),
        )
        return None

    if kind == "advance":
        baseline_min = max(int(cfg["last_pulsed_minute"]), int(cfg["resume_anchor_minute"]))
        min_interval = int(cfg["min_game_minute_interval"])
        if confirmed_min <= baseline_min:
            return None
        if min_interval > 0 and (confirmed_min - baseline_min) < min_interval:
            return None

    return enqueue(
        conn,
        PulseTrigger(
            instance_id=instance_id,
            kind=kind,
            trigger_adventure_id=adventure_id,
            trigger_revision=int(adventure_revision),
            base_world_revision=confirmed_rev,
            target_world_minute=confirmed_min,
            idempotency_key=idempotency_key,
            coalesce=(kind == "advance"),
            payload=dict(payload or {}),
            now=now,
        ),
    )


class PulseWorker:
    """可注入依赖的异步世界脉动后台 Worker。"""

    def __init__(
        self,
        db_factory: Callable[[], ContextManager[sqlite3.Connection]],
        *,
        session_router: PulseSessionRouter,
        activity_registry: ActivityLeaseRegistry | None = None,
        budget_manager: PulseBudgetManager | None = None,
        snapshot_reader: SnapshotReader | None = None,
        proposal_validator: ProposalValidator | None = None,
        proposal_applier: ProposalApplier | None = None,
        clock: Callable[[], float] | None = None,
        lease_seconds: float = 60.0,
        poll_interval_seconds: float = 1.0,
        require_activity_lease: bool = True,
    ) -> None:
        self._db_factory = db_factory
        self.session_router = session_router
        self.activity_registry = activity_registry or ActivityLeaseRegistry(clock=clock)
        self.budget_manager = budget_manager or PulseBudgetManager()
        self._snapshot_reader = snapshot_reader or _default_snapshot_reader
        self._proposal_validator = proposal_validator or _default_proposal_validator
        self._proposal_applier = proposal_applier or _default_proposal_applier
        self._clock = clock or time.time
        self.lease_seconds = float(lease_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.require_activity_lease = bool(require_activity_lease)
        self._bg_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    @contextmanager
    def _short_write_tx(self) -> Any:
        with self._db_factory() as conn:
            conn.row_factory = sqlite3.Row
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            yield conn

    async def run_once(self, *, instance_id: str | None = None) -> dict[str, Any] | None:
        """执行单次队列领取、事务外模型推演与事务内复核提交。

        若队列为空返回 None；否则返回包含最终任务状态与处理结果的结构化字典。
        """
        now = self._clock()

        # ==================== 阶段 1：短写事务领取任务并读取一致性快照 ====================
        with self._short_write_tx() as conn:
            job = claim(
                conn,
                now=now,
                lease_seconds=self.lease_seconds,
                instance_id=instance_id,
            )
            if job is None:
                return None

            job_id = str(job["job_id"])
            inst_id = str(job["instance_id"])
            lease_token = str(job["lease_token"])

            inst = _require_instance(conn, inst_id)
            cfg = get_or_init_instance_config(conn, inst_id, now=now)

            # 1a. 检查脉动开关是否已关闭或暂停
            if not cfg["enabled"] or cfg["paused"]:
                cancelled = cancel(
                    conn,
                    job_id,
                    reason_code="PULSE_DISABLED_OR_PAUSED",
                    reason_summary="世界脉动处于关闭或暂停状态，任务不执行。",
                    now=now,
                )
                return {"job": cancelled, "outcome": "cancelled_disabled"}

            # 1b. 若为恢复后的旧任务且目标游戏时间早于或等于已确认脉动基线，直接取消不补造历史
            if job["kind"] == "advance":
                baseline_min = max(int(cfg["last_pulsed_minute"]), int(cfg["resume_anchor_minute"]))
                if int(job["target_world_minute"]) <= baseline_min:
                    cancelled = cancel(
                        conn,
                        job_id,
                        reason_code="STALE_TARGET_MINUTE",
                        reason_summary="目标游戏时间已在暂停前或更早批次处理，不重复补造。",
                        now=now,
                    )
                    return {"job": cancelled, "outcome": "skipped_stale_minute"}

            # 1c. 检查前台在线活动租约（离线不发付费请求）
            if self.require_activity_lease and not self.activity_registry.is_active(inst_id, now=now):
                waiting = wait_for_model(
                    conn,
                    job_id,
                    lease_token,
                    error_code="CLIENT_OFFLINE",
                    error_summary="玩家当前离线或活动租约已过期，暂停后台模拟。",
                    now=now,
                )
                return {"job": waiting, "outcome": "waiting_offline"}

            # 1d. 检查内存模型会话是否可用（重启无 Key 或会话过期时不发请求）
            try:
                model_client, session_mode = self.session_router.resolve_client(inst_id)
            except PulseError as exc:
                waiting = wait_for_model(
                    conn,
                    job_id,
                    lease_token,
                    error_code=exc.code,
                    error_summary=exc.message,
                    now=now,
                )
                return {"job": waiting, "outcome": "waiting_for_model"}

            # 1e. 对齐任务基准版本与当前实例快照（若任务还是 pending 期间前台刚推进了版本，使用最新已确认快照）
            snapshot_revision = int(inst["revision"])
            snapshot_world_minute = max(int(inst["world_minute"]), int(job["target_world_minute"]))
            if snapshot_revision != int(job["base_world_revision"]):
                conn.execute(
                    """
                    UPDATE pulse_jobs
                    SET base_world_revision = ?, target_world_minute = ?, updated_at = ?
                    WHERE job_id = ? AND lease_token = ?
                    """,
                    (snapshot_revision, snapshot_world_minute, now, job_id, lease_token),
                )
                job["base_world_revision"] = snapshot_revision
                job["target_world_minute"] = snapshot_world_minute

            snapshot_data = self._snapshot_reader(conn, inst_id, job)
            est_tokens = max(
                64,
                min(
                    int(cfg["max_tokens_per_round"]),
                    estimate_tokens(str(snapshot_data)) + 256,
                ),
            )

            # 1f. 请求前预留预算额度（并发安全；若预算耗尽或冷却中则延后任务，避免忙循环）
            try:
                reservation = self.budget_manager.reserve(
                    conn,
                    inst_id,
                    job_id=job_id,
                    purpose=f"world_pulse_{job['kind']}",
                    world_minute=snapshot_world_minute,
                    estimated_tokens=est_tokens,
                    now=now,
                )
            except BudgetExhaustedError as exc:
                deferred = fail_or_retry(
                    conn,
                    job_id,
                    lease_token,
                    error_code=exc.code,
                    error_summary=exc.message,
                    now=now,
                    retryable=True,
                    retry_after_seconds=exc.retry_after_seconds,
                )
                # 预算延后不计入失败尝试次数
                conn.execute(
                    "UPDATE pulse_jobs SET attempts = MAX(0, attempts - 1) WHERE job_id = ?",
                    (job_id,),
                )
                deferred["attempts"] = max(0, int(deferred["attempts"]) - 1)
                return {
                    "job": deferred,
                    "outcome": "deferred_budget",
                    "retry_after_seconds": exc.retry_after_seconds,
                }

            max_tokens_for_call = min(2048, int(cfg["max_tokens_per_round"]))

        # ==================== 阶段 2：在 SQLite 事务外执行模型请求与纯计算校验 ====================
        model_req = PulseModelRequest(
            instance_id=inst_id,
            kind=job["kind"],
            base_world_revision=snapshot_revision,
            target_world_minute=snapshot_world_minute,
            snapshot=snapshot_data,
            max_tokens=max_tokens_for_call,
        )

        model_resp = None
        upstream_err: UpstreamRequestFailedError | None = None
        unexpected_err: Exception | None = None

        try:
            model_resp = await model_client.propose(model_req)
        except asyncio.CancelledError:
            self.budget_manager.release(reservation.reservation_id)
            raise
        except UpstreamRequestFailedError as exc:
            upstream_err = exc
        except Exception as exc:
            unexpected_err = exc

        now_after = self._clock()

        # 处理模型调用失败分支（在短事务中结算可能产生的上游用量并更新任务状态）
        if upstream_err is not None or unexpected_err is not None or model_resp is None:
            with self._short_write_tx() as conn:
                if upstream_err is not None:
                    self.budget_manager.settle(
                        conn,
                        reservation,
                        upstream_err.usage_evidence,
                        now=now_after,
                    )
                    err_code = upstream_err.code
                    err_msg = upstream_err.message
                    retryable = upstream_err.retryable
                else:
                    self.budget_manager.release(reservation.reservation_id)
                    err_code = getattr(unexpected_err, "code", None) or "MODEL_CALL_FAILED"
                    err_msg = sanitize_text(str(unexpected_err) or "模型调用异常。")
                    retryable = True

                try:
                    if err_code in ("MODEL_AUTH_FAILED", "MODEL_SESSION_EXPIRED", "MODEL_SESSION_UNBOUND"):
                        updated_job = wait_for_model(
                            conn,
                            job_id,
                            lease_token,
                            error_code=err_code,
                            error_summary=err_msg,
                            now=now_after,
                        )
                        return {"job": updated_job, "outcome": "waiting_for_model"}
                    updated_job = fail_or_retry(
                        conn,
                        job_id,
                        lease_token,
                        error_code=err_code,
                        error_summary=err_msg,
                        now=now_after,
                        retryable=retryable,
                    )
                    return {"job": updated_job, "outcome": "model_failed"}
                except PulseError as lease_exc:
                    return {"job": None, "outcome": "lease_lost_after_error", "error": lease_exc.code}

        # 纯计算结构化提议校验（仍在数据库写事务外）
        validation_err: Exception | None = None
        validated_proposal: Any = None
        try:
            validated_proposal = self._proposal_validator(
                model_resp.proposal,
                {
                    "instance_id": inst_id,
                    "kind": job["kind"],
                    "base_world_revision": snapshot_revision,
                    "target_world_minute": snapshot_world_minute,
                    "snapshot": snapshot_data,
                },
            )
        except Exception as exc:
            validation_err = exc

        # ==================== 阶段 3：短写事务复核租约、开关、实例版本并原子提交 ====================
        with self._short_write_tx() as conn:
            # 无论后续版本是否冲突，模型请求已实际返回，先如实归档本次调用用量
            usage_record = self.budget_manager.settle(
                conn,
                reservation,
                model_resp.usage,
                now=now_after,
            )

            # 3a. 复核队列领取令牌与租约未过期（旧 Worker 即使拿到模型响应也绝不能提交）
            try:
                verify_active_lease(conn, job_id, lease_token, now=now_after)
            except PulseError as exc:
                self.budget_manager.clear_job_round_counters(job_id)
                return {
                    "job": None,
                    "outcome": "rejected_stale_lease",
                    "error_code": exc.code,
                    "usage": usage_record,
                }

            # 3b. 若提议结构校验未通过，记录失败/重试并退出，不改写实例状态
            if validation_err is not None:
                v_code = getattr(validation_err, "code", None) or "PROPOSAL_VALIDATION_FAILED"
                v_msg = sanitize_text(str(validation_err) or "模型提议未通过结构校验。")
                updated_job = fail_or_retry(
                    conn,
                    job_id,
                    lease_token,
                    error_code=v_code,
                    error_summary=v_msg,
                    now=now_after,
                    retryable=False,
                )
                self.budget_manager.clear_job_round_counters(job_id)
                return {"job": updated_job, "outcome": "validation_failed", "usage": usage_record}

            # 3c. 复核脉动开关与暂停状态
            cfg_now = get_or_init_instance_config(conn, inst_id, now=now_after)
            if not cfg_now["enabled"] or cfg_now["paused"]:
                cancelled = cancel(
                    conn,
                    job_id,
                    reason_code="PULSE_PAUSED_BEFORE_COMMIT",
                    reason_summary="模型返回期间世界脉动被暂停或关闭，放弃提交。",
                    now=now_after,
                )
                self.budget_manager.clear_job_round_counters(job_id)
                return {"job": cancelled, "outcome": "cancelled_before_commit", "usage": usage_record}

            # 3d. 复核前台活动租约
            if self.require_activity_lease and not self.activity_registry.is_active(inst_id, now=now_after):
                waiting = wait_for_model(
                    conn,
                    job_id,
                    lease_token,
                    error_code="CLIENT_OFFLINE_BEFORE_COMMIT",
                    error_summary="模型返回期间客户端已离线，放弃提交并等待恢复。",
                    now=now_after,
                )
                self.budget_manager.clear_job_round_counters(job_id)
                return {"job": waiting, "outcome": "offline_before_commit", "usage": usage_record}

            # 3e. 复核世界实例版本（防止慢后台请求用旧快照覆盖前台新确认的回合状态）
            inst_now = _require_instance(conn, inst_id)
            current_rev = int(inst_now["revision"])
            current_min = int(inst_now["world_minute"])
            if current_rev != snapshot_revision:
                retried = fail_or_retry(
                    conn,
                    job_id,
                    lease_token,
                    error_code="WORLD_REVISION_CONFLICT",
                    error_summary=f"实例版本已由 {snapshot_revision} 推进至 {current_rev}，拒绝旧快照覆盖并排入重试。",
                    now=now_after,
                    retryable=True,
                    updated_base_world_revision=current_rev,
                    updated_target_world_minute=max(current_min, snapshot_world_minute),
                )
                self.budget_manager.clear_job_round_counters(job_id)
                return {
                    "job": retried,
                    "outcome": "revision_conflict",
                    "expected_revision": snapshot_revision,
                    "actual_revision": current_rev,
                    "usage": usage_record,
                }

            # 3f. 在 SAVEPOINT 内原子应用领域提议并推进 world_instances.revision
            conn.execute("SAVEPOINT sp_pulse_apply")
            try:
                apply_result = self._proposal_applier(conn, inst_id, validated_proposal, job)
                cur = conn.execute(
                    """
                    UPDATE world_instances
                    SET revision = revision + 1
                    WHERE id = ? AND revision = ?
                    """,
                    (inst_id, snapshot_revision),
                )
                if cur.rowcount != 1:
                    raise PulseError(
                        "WORLD_REVISION_CONFLICT",
                        "更新实例版本时检测到并发冲突。",
                        status=409,
                    )
                conn.execute(
                    """
                    UPDATE pulse_instance_configs
                    SET last_pulsed_minute = MAX(last_pulsed_minute, ?),
                        updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (snapshot_world_minute, now_after, inst_id),
                )
                completed_job = complete(conn, job_id, lease_token, now=now_after)
                conn.execute("RELEASE SAVEPOINT sp_pulse_apply")
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT sp_pulse_apply")
                conn.execute("RELEASE SAVEPOINT sp_pulse_apply")
                err_code = getattr(exc, "code", None) or "PROPOSAL_APPLY_FAILED"
                err_msg = sanitize_text(str(exc) or "应用世界脉动提议失败。")
                is_conflict = err_code == "WORLD_REVISION_CONFLICT"
                failed_job = fail_or_retry(
                    conn,
                    job_id,
                    lease_token,
                    error_code=err_code,
                    error_summary=err_msg,
                    now=now_after,
                    retryable=is_conflict,
                )
                self.budget_manager.clear_job_round_counters(job_id)
                return {
                    "job": failed_job,
                    "outcome": "apply_failed",
                    "error_code": err_code,
                    "usage": usage_record,
                }

            self.budget_manager.clear_job_round_counters(job_id)
            return {
                "job": completed_job,
                "outcome": "completed",
                "new_world_revision": snapshot_revision + 1,
                "pulsed_world_minute": snapshot_world_minute,
                "apply_result": apply_result,
                "usage": usage_record,
                "session_mode": session_mode,
            }

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                res = await self.run_once()
                if res is not None and res.get("outcome") == "completed":
                    # 若成功处理了一条任务，短暂让出事件循环后立即检查下一条
                    await asyncio.sleep(0)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                # 后台循环兜底隔离，避免单次意外异常杀死常驻协程
                pass

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        """启动后台常驻轮询协程。"""
        if self._bg_task is not None and not self._bg_task.done():
            return self._bg_task
        self._stop_event = asyncio.Event()
        self._bg_task = asyncio.create_task(self._loop())
        return self._bg_task

    async def stop(self) -> None:
        """优雅停止后台 Worker 并清理脉动自有内存会话池（不影响前台叙事会话）。"""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None


@asynccontextmanager
async def pulse_worker_lifespan(
    worker: PulseWorker,
    *,
    auto_start_loop: bool = True,
) -> AsyncIterator[PulseWorker]:
    """供 DeepSeekHarness 在 FastAPI create_app lifespan 中挂载的上下文管理器。"""
    if auto_start_loop:
        worker.start()
    try:
        yield worker
    finally:
        await worker.stop()
        worker.session_router.clear()
        worker.activity_registry.clear()
