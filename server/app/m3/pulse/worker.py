"""M3.2 异步世界脉动调度器、后台 Worker、两道门代次校验与 FastAPI Lifespan 接线。

严格遵循契约与事务边界（M3_WAVE1_API.md & M3_WAVE2_API.md）：
1. 默认关闭；仅当实例脉动处于启用、未暂停、模型会话有效且在线租约活跃时才发起请求。
2. 离线、暂停、关闭和未绑定时推进已确认时间锚，不积压将来要补演的历史任务。
3. 网络/模型调用与纯计算校验严格位于 SQLite 写事务之外，绝不在持有数据库写锁时等待模型响应，保证前台可随时确认新回合。
4. 请求前与提交前两道门：
   - 第一道门（请求前短写事务）：核验开关、时间锚、在线租约、模型会话与快照有效性（无到期事件直接跳过，缺资料阻塞等待），并按上下文估算+输出上限预留额度，记录配置代次、会话代次、在线代次与实例版本。
   - 第二道门（提交前短写事务）：先如实结算已发请求的模型用量证据；随后依次核验开关、代次（generation，阻止关开切换或重连后旧在途响应提交）、任务租约令牌（lease_token）、在线活动租约与实例根版本（world_instances.revision == snapshot_revision），任何一项失败则拒绝或回滚领域写入。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager
import sqlite3
import time
from typing import Any, AsyncIterator, Callable, ContextManager

from app.errors import ApiError
from app.m3.pulse.budget import (
    BudgetExhaustedError,
    PulseBudgetManager,
    advance_pulse_time_anchor,
    estimate_call_reservation_tokens,
    get_or_init_instance_config,
)
from app.m3.pulse.client import UpstreamRequestFailedError, build_pulse_prompts
from app.m3.pulse.errors import PulseError, sanitize_text
from app.m3.pulse.queue import (
    _require_instance,
    cancel,
    claim,
    complete,
    enqueue,
    fail_or_retry,
    recover_jobs_on_startup,
    verify_active_lease,
    wait_for_model,
)
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import (
    JobKind,
    PulseModelRequest,
    PulseTrigger,
)

# 回调类型定义（支持注入 Fake 或其他模块的真实实现）
SnapshotReader = Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]]
ProposalValidator = Callable[[dict[str, Any], dict[str, Any]], Any]
ProposalApplier = Callable[[sqlite3.Connection, str, Any, dict[str, Any]], dict[str, Any] | None]

_ALLOWED_PROPOSAL_ROOT_KEYS = frozenset({
    "operations",
    "transmissions",
    "kind",
    "mode",
    "summary",
    "instance_id",
    "world_instance_id",
    "base_world_revision",
    "world_revision",
    "target_world_minute",
    "world_minute",
})

_PROTECTED_PLAYER_LEDGER_KEYS = frozenset({
    "player_state",
    "player_ledger",
    "player_changes",
    "character",
    "character_json",
    "character_state",
    "coins",
    "coins_delta",
    "money",
    "gold",
    "inventory",
    "inventory_changes",
    "equipment",
    "stamina",
    "stamina_delta",
    "spirit",
    "spirit_delta",
    "personal_ledger",
})

_FORBIDDEN_PATH_KEYS = frozenset({
    "json_path",
    "target_path",
    "path",
    "pointer",
    "json_pointer",
    "patch",
    "json_patch",
    "$set",
    "$inc",
    "$unset",
    "$push",
    "raw_sql",
    "mutation_path",
})

_FORBIDDEN_BROADCAST_HOLDERS = frozenset({
    "*",
    "all",
    "everyone",
    "all_npcs",
    "npcs",
    "world",
    "global",
    "public",
    "system",
})


def _check_dict_security(obj: Any, *, instance_id: str, path: str = "proposal") -> None:
    """递归拦截任意 JSON 路径写入、玩家个人账本字段和跨实例标识。"""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise PulseError("PROPOSAL_INVALID", f"{path} 包含非字符串键。", status=422)
            k_low = k.strip().lower()
            if k_low in _PROTECTED_PLAYER_LEDGER_KEYS or k_low.startswith(("player_ledger", "character_")):
                raise PulseError(
                    "PLAYER_LEDGER_PROTECTED",
                    f"后台脉动严禁修改玩家个人账本字段: {k}。",
                    status=422,
                )
            if (
                k_low in _FORBIDDEN_PATH_KEYS
                or "." in k
                or "/" in k
                or "[" in k
                or k.startswith("$")
            ):
                raise PulseError(
                    "ARBITRARY_PATH_FORBIDDEN",
                    f"禁止任意字段路径或补丁操作: {k}。",
                    status=422,
                )
            if k_low in ("instance_id", "world_instance_id") and v is not None:
                if str(v).strip() != instance_id:
                    raise PulseError(
                        "CROSS_INSTANCE_REFERENCE",
                        f"{path}.{k} 与当前任务绑定实例不一致。",
                        status=422,
                    )
            _check_dict_security(v, instance_id=instance_id, path=f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _check_dict_security(item, instance_id=instance_id, path=f"{path}[{idx}]")


def validate_pulse_envelope(
    proposal: Any,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """在 SQLite 事务外校验模型输出的根结构、身份/时间回显一致性、来源授权与首版传播引用边界。"""
    if not isinstance(proposal, Mapping):
        raise PulseError("PROPOSAL_INVALID", "模型提议顶层必须是 JSON 对象。", status=422)

    inst_id = str(context.get("instance_id") or "")
    expected_kind = str(context.get("kind") or "advance")
    expected_rev = context.get("base_world_revision")
    expected_min = context.get("target_world_minute")
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), Mapping) else {}

    unknown_root = set(proposal.keys()) - _ALLOWED_PROPOSAL_ROOT_KEYS
    if unknown_root:
        raise PulseError(
            "PROPOSAL_UNKNOWN_ROOT_FIELD",
            f"模型提议包含未知根字段: {sorted(unknown_root)}。",
            status=422,
        )

    _check_dict_security(proposal, instance_id=inst_id, path="proposal")

    # 校验回显的 kind / mode、版本与时间必须与任务绑定值一致
    for k_field in ("kind", "mode"):
        if k_field in proposal and proposal[k_field] is not None:
            if str(proposal[k_field]).strip() != expected_kind:
                raise PulseError(
                    "PROPOSAL_KIND_MISMATCH",
                    f"模型回显的 {k_field} ({proposal[k_field]!r}) 与任务绑定类型 ({expected_kind!r}) 不一致。",
                    status=422,
                )

    for r_field in ("base_world_revision", "world_revision"):
        if r_field in proposal and proposal[r_field] is not None and expected_rev is not None:
            if type(proposal[r_field]) is not int or int(proposal[r_field]) != int(expected_rev):
                raise PulseError(
                    "PROPOSAL_REVISION_MISMATCH",
                    f"模型回显的版本 ({proposal[r_field]!r}) 与任务基准版本 ({expected_rev}) 不一致。",
                    status=422,
                )

    for m_field in ("target_world_minute", "world_minute"):
        if m_field in proposal and proposal[m_field] is not None and expected_min is not None:
            if type(proposal[m_field]) is not int or int(proposal[m_field]) != int(expected_min):
                raise PulseError(
                    "PROPOSAL_MINUTE_MISMATCH",
                    f"模型回显的世界分钟 ({proposal[m_field]!r}) 与任务目标分钟 ({expected_min}) 不一致。",
                    status=422,
                )

    ops = proposal.get("operations", [])
    if not isinstance(ops, list):
        raise PulseError("PROPOSAL_INVALID", "operations 必须是数组。", status=422)

    transmissions = proposal.get("transmissions", [])
    if transmissions is None:
        transmissions = []
    if not isinstance(transmissions, list):
        raise PulseError("PROPOSAL_INVALID", "transmissions 必须是数组。", status=422)

    # 校验授权 Canon 来源（若快照显式提供了 allowed_source_ids）
    raw_allowed_sources = snapshot.get("allowed_source_ids")
    allowed_sources: set[str] | None = (
        {str(s).strip() for s in raw_allowed_sources if str(s).strip()}
        if isinstance(raw_allowed_sources, (list, tuple, set, frozenset))
        else None
    )

    for idx, op in enumerate(ops):
        if not isinstance(op, Mapping):
            raise PulseError("PROPOSAL_INVALID", f"operations[{idx}] 必须是对象。", status=422)
        if op.get("op") == "create" and ("event_id" in op or "revision" in op):
            raise PulseError(
                "SERVER_ID_FORBIDDEN",
                f"operations[{idx}] 新事件 ID 与版本由程序分配，模型不得预设。",
                status=422,
            )
        if allowed_sources is not None and "source_ids" in op and op["source_ids"] is not None:
            op_sources = op["source_ids"]
            if not isinstance(op_sources, (list, tuple)):
                raise PulseError("PROPOSAL_INVALID", f"operations[{idx}].source_ids 必须是数组。", status=422)
            for sid in op_sources:
                if str(sid).strip() not in allowed_sources:
                    raise PulseError(
                        "SOURCE_FABRICATED",
                        f"operations[{idx}] 引用了未授权或伪造的 Canon 来源: {sid}。",
                        status=422,
                    )

    # 首版传播引用约束：Seed 阶段省略传播；Advance 阶段仅可引用本次输入中已存在、获准使用的事件 ID
    if expected_kind == "seed" and len(transmissions) > 0:
        raise PulseError(
            "SEED_TRANSMISSIONS_FORBIDDEN",
            "Seed 阶段只写入权威初始事件线，首版禁止直接附带知识传播。",
            status=422,
        )

    raw_allowed_events = snapshot.get("allowed_event_ids")
    if raw_allowed_events is None and isinstance(snapshot.get("due_events"), (list, tuple)):
        raw_allowed_events = [
            ev["event_id"]
            for ev in snapshot["due_events"]
            if isinstance(ev, Mapping) and "event_id" in ev
        ]
    allowed_events: set[str] | None = (
        {str(eid).strip() for eid in raw_allowed_events if str(eid).strip()}
        if isinstance(raw_allowed_events, (list, tuple, set, frozenset))
        else None
    )

    for idx, tr in enumerate(transmissions):
        if not isinstance(tr, Mapping):
            raise PulseError("PROPOSAL_INVALID", f"transmissions[{idx}] 必须是对象。", status=422)
        holder = str(tr.get("holder_id") or "").strip()
        if holder.casefold() in _FORBIDDEN_BROADCAST_HOLDERS:
            raise PulseError(
                "GLOBAL_BROADCAST_FORBIDDEN",
                f"transmissions[{idx}] 禁止批量或通配符全局授知 ({holder!r})。",
                status=422,
            )
        src_eid = str(tr.get("source_event_id") or "").strip()
        if src_eid.startswith("__pending") or src_eid.startswith("temp_"):
            raise PulseError(
                "TEMP_EVENT_REFERENCE_FORBIDDEN",
                f"transmissions[{idx}] 禁止引用本批新建事件的临时 ID ({src_eid})。",
                status=422,
            )
        if allowed_events is not None and src_eid and src_eid not in allowed_events:
            raise PulseError(
                "UNAUTHORIZED_TRANSMISSION_EVENT",
                f"transmissions[{idx}] 引用的事件 {src_eid} 不在本次后台输入允许列表中。",
                status=422,
            )

    normalized = dict(proposal)
    normalized.setdefault("operations", ops)
    return normalized


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
    return validate_pulse_envelope(proposal, context)


def _default_proposal_applier(
    conn: sqlite3.Connection,
    instance_id: str,
    validated_proposal: Any,
    job: dict[str, Any],
) -> dict[str, Any]:
    ops = validated_proposal.get("operations", []) if isinstance(validated_proposal, dict) else []
    trans = validated_proposal.get("transmissions", []) if isinstance(validated_proposal, dict) else []
    return {
        "applied_operations": len(ops),
        "applied_transmissions": len(trans),
    }


def _inspect_snapshot_directive(
    snapshot_data: dict[str, Any],
    *,
    job_kind: str,
) -> tuple[str, str | None, str | None]:
    """检查 read_snapshot 返回字典是否指示跳过（skip）或阻塞（blocked）。

    返回 (directive, reason_code, reason_summary)，其中 directive 取 'proceed' | 'skip' | 'blocked'。
    """
    if not isinstance(snapshot_data, Mapping):
        return "proceed", None, None

    if snapshot_data.get("blocked") is True or snapshot_data.get("status") == "blocked":
        code = str(
            snapshot_data.get("reason_code")
            or snapshot_data.get("block_reason")
            or snapshot_data.get("error_code")
            or "PULSE_SOURCE_REQUIRED"
        )
        summary = str(
            snapshot_data.get("reason_summary")
            or snapshot_data.get("message")
            or "缺少可用的授权世界资料，世界脉动暂停等待。"
        )
        return "blocked", code, summary

    if (
        snapshot_data.get("skip") is True
        or snapshot_data.get("skipped") is True
        or snapshot_data.get("status") in ("skip", "skipped")
        or snapshot_data.get("action") == "skip"
    ):
        code = str(
            snapshot_data.get("reason_code")
            or snapshot_data.get("skip_reason")
            or "SNAPSHOT_SKIPPED"
        )
        summary = str(
            snapshot_data.get("reason_summary")
            or snapshot_data.get("message")
            or "当前无需要处理的到期事件，跳过模型调用。"
        )
        return "skip", code, summary

    if job_kind == "advance" and "due_events" in snapshot_data:
        due = snapshot_data.get("due_events")
        if isinstance(due, (list, tuple)) and len(due) == 0:
            code = str(snapshot_data.get("reason_code") or "NO_DUE_EVENTS")
            summary = str(snapshot_data.get("reason_summary") or "当前已确认时间无到期事件，不发起模型请求。")
            return "skip", code, summary

    if job_kind == "seed" and "sources" in snapshot_data:
        sources = snapshot_data.get("sources")
        if isinstance(sources, (list, tuple)) and len(sources) == 0:
            code = str(snapshot_data.get("reason_code") or "PULSE_SOURCE_REQUIRED")
            summary = str(snapshot_data.get("reason_summary") or "当前世界无有效 Canon 资料，无法生成初始事件线。")
            return "blocked", code, summary

    return "proceed", None, None


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
    session_router: PulseSessionRouter | None = None,
    activity_registry: ActivityLeaseRegistry | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """在前台已确认回合的同一个短写事务内调用，按开关、在线与会话状态及已确认分钟登记后台脉动任务。

    - 若该实例脉动未启用（默认关闭）、已暂停、或在传入路由器/租约表时处于未绑定/离线状态，
      仅推进已确认时间锚点（resume_anchor_minute / last_pulsed_minute）并清理旧待办，绝不积压将来要补演的历史任务；
    - 仅消费已确认的 world_minute；若游戏时间未推进或未达到最小推进间隔，跳过登记。
    """
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    inst = _require_instance(conn, instance_id)
    cfg = get_or_init_instance_config(conn, instance_id, now=now)

    confirmed_rev = int(world_revision) if world_revision is not None else int(inst["revision"])
    confirmed_min = int(world_minute) if world_minute is not None else int(inst["world_minute"])

    session_unready = session_router is not None and not session_router.is_ready(instance_id)
    client_offline = activity_registry is not None and not activity_registry.is_active(instance_id, now=now)

    if not cfg["enabled"] or cfg["paused"] or session_unready or client_offline:
        # 关闭、暂停、未绑定或离线期间只推进最新世界时刻锚点，绝不入队积压旧任务
        advance_pulse_time_anchor(
            conn,
            instance_id,
            world_minute=confirmed_min,
            cancel_pending_advance=True,
            now=now,
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
        default_max_output_tokens: int = 256,
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
        self.default_max_output_tokens = int(default_max_output_tokens)
        self._bg_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    @contextmanager
    def _short_write_tx(self) -> Any:
        with self._db_factory() as conn:
            conn.row_factory = sqlite3.Row
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            yield conn

    def recover_on_startup(self, *, instance_id: str | None = None) -> dict[str, int]:
        """在 Worker 启动或进程恢复时扫描队列，核对当下开关、会话与在线状态，安全恢复可重试任务。"""
        now = self._clock()
        with self._short_write_tx() as conn:
            return recover_jobs_on_startup(
                conn,
                instance_id=instance_id,
                session_ready_fn=self.session_router.is_ready,
                activity_online_fn=(
                    (lambda iid: self.activity_registry.is_active(iid, now=now))
                    if self.require_activity_lease
                    else None
                ),
                now=now,
            )

    async def run_once(self, *, instance_id: str | None = None) -> dict[str, Any] | None:
        """执行单次队列领取（第一道门）、事务外模型推演与事务内复核提交（第二道门）。

        若队列为空返回 None；否则返回包含最终任务状态与处理结果的结构化字典。
        """
        now = self._clock()

        # ==================== 阶段 1：短写事务领取任务并通过第一道门（请求前核验） ====================
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

            # 1a. 第一道门：检查脉动开关是否已关闭或暂停
            if not cfg["enabled"] or cfg["paused"]:
                advance_pulse_time_anchor(
                    conn,
                    inst_id,
                    world_minute=int(inst["world_minute"]),
                    cancel_pending_advance=False,
                    now=now,
                )
                cancelled = cancel(
                    conn,
                    job_id,
                    reason_code="PULSE_DISABLED_OR_PAUSED",
                    reason_summary="世界脉动处于关闭或暂停状态，任务不执行。",
                    now=now,
                )
                return {"job": cancelled, "outcome": "cancelled_disabled"}

            # 1b. 第一道门：若为恢复后的旧任务且目标游戏时间早于或等于已确认脉动基线，直接取消不补造历史
            if job["kind"] == "advance":
                baseline_min = max(int(cfg["last_pulsed_minute"]), int(cfg["resume_anchor_minute"]))
                if int(job["target_world_minute"]) <= baseline_min:
                    cancelled = cancel(
                        conn,
                        job_id,
                        reason_code="STALE_TARGET_MINUTE",
                        reason_summary="目标游戏时间已在暂停/离线前或更早批次处理，不重复补造。",
                        now=now,
                    )
                    return {"job": cancelled, "outcome": "skipped_stale_minute"}

            # 1c. 第一道门：检查前台在线活动租约（离线不发付费请求）
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

            # 1d. 第一道门：检查内存模型会话是否可用（重启无 Key、认证失败或会话过期时不发请求）
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

            # 记录第一道门代次快照，供阶段 3 第二道门比对
            start_config_gen = int(cfg.get("generation", 1))
            start_session_gen = self.session_router.generation_for(inst_id)
            start_activity_gen = self.activity_registry.generation_for(inst_id, now=now)

            # 1e. 对齐任务基准版本与当前已确认实例快照
            snapshot_revision = int(inst["revision"])
            snapshot_world_minute = max(int(inst["world_minute"]), int(job["target_world_minute"]))
            if (
                snapshot_revision != int(job["base_world_revision"])
                or snapshot_world_minute != int(job["target_world_minute"])
            ):
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

            # 1f. 读取领域快照，并处理无到期事件跳过（skip）或缺少资料阻塞（blocked）
            try:
                snapshot_data = self._snapshot_reader(conn, inst_id, job)
            except Exception as snap_exc:
                snap_code = getattr(snap_exc, "code", None) or "SNAPSHOT_READ_FAILED"
                snap_status = getattr(snap_exc, "status", 500)
                snap_msg = sanitize_text(str(snap_exc) or "读取世界快照失败。")
                if snap_status == 409 or snap_code in (
                    "PULSE_SOURCE_REQUIRED",
                    "CANON_SOURCES_EMPTY",
                    "SEED_SOURCES_REQUIRED",
                ):
                    waiting = wait_for_model(
                        conn,
                        job_id,
                        lease_token,
                        error_code=snap_code,
                        error_summary=snap_msg,
                        now=now,
                    )
                    return {
                        "job": waiting,
                        "outcome": "blocked_snapshot",
                        "reason_code": snap_code,
                    }
                failed = fail_or_retry(
                    conn,
                    job_id,
                    lease_token,
                    error_code=snap_code,
                    error_summary=snap_msg,
                    now=now,
                    retryable=False,
                )
                return {
                    "job": failed,
                    "outcome": "snapshot_failed",
                    "reason_code": snap_code,
                }

            directive, dir_code, dir_summary = _inspect_snapshot_directive(
                snapshot_data,
                job_kind=str(job["kind"]),
            )
            if directive == "blocked":
                waiting = wait_for_model(
                    conn,
                    job_id,
                    lease_token,
                    error_code=dir_code or "PULSE_SOURCE_REQUIRED",
                    error_summary=dir_summary or "缺少授权世界资料，暂停等待。",
                    now=now,
                )
                return {
                    "job": waiting,
                    "outcome": "blocked_snapshot",
                    "reason_code": dir_code,
                }
            if directive == "skip":
                conn.execute(
                    """
                    UPDATE pulse_instance_configs
                    SET last_pulsed_minute = MAX(last_pulsed_minute, ?),
                        updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (snapshot_world_minute, now, inst_id),
                )
                completed_skip = complete(conn, job_id, lease_token, now=now)
                return {
                    "job": completed_skip,
                    "outcome": "skipped_no_due_events",
                    "reason_code": dir_code,
                    "pulsed_world_minute": snapshot_world_minute,
                }

            # 1g. 按上下文估算 + 输出上限预留预算额度（模拟用量与付费计费用量分离）
            requested_max_out = int(
                job.get("payload", {}).get("max_tokens") or self.default_max_output_tokens
            )
            is_paid_call = session_mode in ("independent", "reuse_narration")
            budget_input = snapshot_data
            if is_paid_call and snapshot_data.get("use_event_prompts"):
                try:
                    budget_input = "\n".join(build_pulse_prompts(PulseModelRequest(
                        instance_id=inst_id, kind=job["kind"], base_world_revision=snapshot_revision,
                        target_world_minute=snapshot_world_minute, snapshot=snapshot_data,
                    )))
                except ApiError as exc:
                    failed = fail_or_retry(
                        conn, job_id, lease_token, error_code=exc.code,
                        error_summary="本次推演输入未通过校验。", now=now, retryable=False,
                    )
                    return {"job": failed, "outcome": "snapshot_failed", "reason_code": exc.code}

            est_prompt_toks, max_tokens_for_call, est_total_toks = estimate_call_reservation_tokens(
                budget_input,
                kind=str(job["kind"]),
                max_output_tokens=requested_max_out,
                max_tokens_per_round=int(cfg["max_tokens_per_round"]),
            )
            try:
                reservation = self.budget_manager.reserve(
                    conn,
                    inst_id,
                    job_id=job_id,
                    purpose=f"world_pulse_{job['kind']}",
                    world_minute=snapshot_world_minute,
                    estimated_tokens=est_total_toks,
                    estimated_prompt_tokens=est_prompt_toks,
                    max_output_tokens=max_tokens_for_call,
                    is_paid=is_paid_call,
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
                    if err_code == "MODEL_AUTH_FAILED":
                        self.session_router.mark_auth_failed(inst_id, error_code=err_code)
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

        # 纯计算结构化提议校验（仍在数据库写事务外：先执行通用信封与边界校验，再执行注入校验器）
        validation_err: Exception | None = None
        validated_proposal: Any = None
        validation_context = {
            "instance_id": inst_id,
            "kind": job["kind"],
            "mode": job["kind"],
            "base_world_revision": snapshot_revision,
            "world_revision": snapshot_revision,
            "target_world_minute": snapshot_world_minute,
            "world_minute": snapshot_world_minute,
            "snapshot": snapshot_data,
        }
        try:
            envelope_checked = validate_pulse_envelope(model_resp.proposal, validation_context)
            validated_proposal = self._proposal_validator(envelope_checked, validation_context)
        except Exception as exc:
            validation_err = exc

        # ==================== 阶段 3：短写事务结算用量并通过第二道门（提交前核验） ====================
        with self._short_write_tx() as conn:
            # 无论后续租约、代次或版本是否冲突，模型请求已实际发出并返回，先单独结算用量证据
            usage_record = self.budget_manager.settle(
                conn,
                reservation,
                model_resp.usage,
                now=now_after,
            )

            cfg_now = get_or_init_instance_config(conn, inst_id, now=now_after)
            current_config_gen = int(cfg_now.get("generation", 1))
            current_session_gen = self.session_router.generation_for(inst_id)

            # 3a. 第二道门：核对脉动当前是否已关闭或暂停（例如慢请求期间用户关闭了脉动）
            if not cfg_now["enabled"] or cfg_now["paused"]:
                job_row = conn.execute("SELECT * FROM pulse_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if job_row is not None and job_row["status"] != "cancelled":
                    cancelled = cancel(
                        conn,
                        job_id,
                        reason_code="PULSE_PAUSED_BEFORE_COMMIT",
                        reason_summary="模型返回期间世界脉动被暂停或关闭，丢弃未提交响应。",
                        now=now_after,
                    )
                else:
                    from app.m3.pulse.queue import get_job

                    cancelled = get_job(conn, job_id)
                self.budget_manager.clear_job_round_counters(job_id)
                return {
                    "job": cancelled,
                    "outcome": "cancelled_before_commit",
                    "reason_code": "PULSE_PAUSED_BEFORE_COMMIT",
                    "usage": usage_record,
                }

            # 3b. 第二道门：核对开关代次与会话代次（阻止慢请求期间发生「关->开」切换或重连后旧响应提交）
            if current_config_gen != start_config_gen or current_session_gen != start_session_gen:
                job_row = conn.execute("SELECT * FROM pulse_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if job_row is not None and job_row["status"] == "running" and job_row["lease_token"] == lease_token:
                    discarded = cancel(
                        conn,
                        job_id,
                        reason_code="PULSE_GENERATION_CHANGED",
                        reason_summary="请求在途期间脉动开关或模型会话发生切换（代次变更），丢弃旧代次响应。",
                        now=now_after,
                    )
                else:
                    from app.m3.pulse.queue import get_job

                    discarded = get_job(conn, job_id) if job_row is not None else None
                self.budget_manager.clear_job_round_counters(job_id)
                return {
                    "job": discarded,
                    "outcome": "discarded_stale_generation",
                    "reason_code": "PULSE_GENERATION_CHANGED",
                    "expected_generation": start_config_gen,
                    "actual_generation": current_config_gen,
                    "usage": usage_record,
                }

            # 3c. 第二道门：复核队列领取令牌与租约未过期（旧 Worker 即使拿到模型响应也绝不能提交）
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

            # 3d. 第二道门：复核前台在线活动租约与在线代次
            if self.require_activity_lease:
                still_online = self.activity_registry.is_active(inst_id, now=now_after)
                current_activity_gen = self.activity_registry.generation_for(inst_id, now=now_after)
                if not still_online or current_activity_gen != start_activity_gen:
                    waiting = wait_for_model(
                        conn,
                        job_id,
                        lease_token,
                        error_code="CLIENT_OFFLINE_BEFORE_COMMIT",
                        error_summary="模型返回期间客户端已离线或在线周期已切换，放弃提交并等待恢复。",
                        now=now_after,
                    )
                    self.budget_manager.clear_job_round_counters(job_id)
                    return {"job": waiting, "outcome": "offline_before_commit", "usage": usage_record}

            # 3e. 若提议结构校验未通过，记录失败并退出，不改写实例状态
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
                return {
                    "job": updated_job,
                    "outcome": "validation_failed",
                    "error_code": v_code,
                    "usage": usage_record,
                }

            # 3f. 第二道门：复核世界实例版本（防止慢后台请求用旧快照覆盖前台新确认的回合状态）
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

            # 3g. 在 SAVEPOINT 内原子应用领域提议（事件 + 知识传播）并推进 world_instances.revision
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
                is_conflict = err_code in ("WORLD_REVISION_CONFLICT", "EVENT_REVISION_CONFLICT", "KNOWLEDGE_CONFLICT")
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
                if res is not None and res.get("outcome") in ("completed", "skipped_no_due_events"):
                    # 若成功处理或跳过了一条任务，短暂让出事件循环后立即检查下一条
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
        """优雅停止后台 Worker。"""
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
    recover_on_start: bool = True,
) -> AsyncIterator[PulseWorker]:
    """供 DeepSeekHarness 在 FastAPI create_app lifespan 中挂载的上下文管理器。"""
    if recover_on_start:
        try:
            worker.recover_on_startup()
        except Exception:
            pass
    if auto_start_loop:
        worker.start()
    try:
        yield worker
    finally:
        await worker.stop()
        worker.session_router.clear()
        worker.activity_registry.clear()
