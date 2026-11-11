"""M3.2 可观察性（脱敏状态与用量汇总）及模块快照导入导出。"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from app.m3.pulse.budget import (
    PulseBudgetManager,
    game_day_for_minute,
    get_or_init_instance_config,
)
from app.m3.pulse.errors import PulseError, assert_no_credentials, sanitize_text
from app.m3.pulse.queue import _require_instance, list_jobs
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import PulseControlState, VALID_COMPLEXITY_MODES

SNAPSHOT_FORMAT_VERSION = 1

_MODEL_WAIT_CODES = frozenset({
    "MODEL_SESSION_UNBOUND",
    "MODEL_SESSION_EXPIRED",
    "MODEL_SESSION_INVALID",
    "MODEL_AUTH_FAILED",
    "NARRATION_STORE_UNAVAILABLE",
    "PULSE_SOURCE_REQUIRED",
    "CANON_SOURCES_EMPTY",
    "SEED_SOURCES_REQUIRED",
})

_BUDGET_LIMIT_CODES = frozenset({
    "PULSE_DAILY_CALL_BUDGET_EXHAUSTED",
    "PULSE_DAILY_TOKEN_BUDGET_EXHAUSTED",
    "PULSE_ROUND_CALL_BUDGET_EXCEEDED",
    "PULSE_ROUND_TOKEN_BUDGET_EXCEEDED",
    "PULSE_COOLDOWN_ACTIVE",
})

# 仅向手机端说明固定的错误类别，不回显模型正文、隐藏事件或校验器中的动态值。
_JOB_FAILURE_SUMMARIES = {
    "MODEL_OUTPUT_TRUNCATED": "模型回复达到输出长度上限，未能完整生成结果",
    "MODEL_OUTPUT_NOT_JSON": "模型回复不是完整、合法的 JSON",
    "MODEL_OUTPUT_INVALID": "模型回复结构不符合要求",
    "MODEL_OUTPUT_INCOMPLETE": "模型未正常完成回复",
    "MODEL_EMPTY_RESPONSE": "模型没有返回正文",
    "VALIDATION_ERROR": "模型回复缺少必需字段或字段值不合法",
    "SEED_THREAD_COUNT_INVALID": "模型生成的初始事件数量不符合要求",
    "MODEL_HTTP_ERROR": "模型服务拒绝了请求，请检查模型名称与接口配置",
    "MODEL_UPSTREAM_ERROR": "模型服务暂时出现故障",
    "MODEL_TIMEOUT": "等待模型回复超时",
    "MODEL_CONNECTION_FAILED": "未能连接模型服务",
    "MODEL_RATE_LIMITED": "模型服务限制了请求频率",
    "PROPOSAL_UNKNOWN_ROOT_FIELD": "模型回复包含未约定的顶层字段",
    "UNKNOWN_FIELD": "模型回复包含未约定的字段",
    "SOURCE_FABRICATED": "模型引用了本次未授权的资料来源",
}


def _derive_control_state(
    *,
    cfg: dict[str, Any],
    session_info: dict[str, Any],
    client_online: bool,
    counts: dict[str, int],
    recent_jobs: list[dict[str, Any]],
    day_usage: dict[str, Any],
    now_ts: float,
) -> tuple[PulseControlState, str | None]:
    """根据开关、会话、在线租约、预算与队列状态推导手机端 6 态契约状态与脱敏 reason_code。"""
    if not bool(cfg.get("enabled")):
        return "off", "PULSE_DISABLED"
    if bool(cfg.get("paused")):
        return "off", "PULSE_PAUSED"

    # 检查是否存在因缺少 Canon 资料或模型会话处于 waiting_for_model 的任务
    waiting_code: str | None = None
    deferred_budget_code: str | None = None
    ready_pending_count = 0
    for j in recent_jobs:
        st = j.get("status")
        ecode = j.get("error_code")
        if st == "waiting_for_model" and ecode and waiting_code is None:
            waiting_code = str(ecode)
        elif st == "pending":
            nra = j.get("next_retry_at")
            if nra is not None and float(nra) > now_ts and ecode in _BUDGET_LIMIT_CODES:
                deferred_budget_code = str(ecode)
            elif nra is None or float(nra) <= now_ts:
                ready_pending_count += 1

    if not bool(session_info.get("ready")):
        reason = str(session_info.get("reason_code") or waiting_code or "MODEL_SESSION_UNBOUND")
        return "waiting_for_model", reason

    if waiting_code in ("PULSE_SOURCE_REQUIRED", "CANON_SOURCES_EMPTY", "SEED_SOURCES_REQUIRED"):
        return "waiting_for_model", waiting_code

    if not client_online:
        return "offline", "CLIENT_OFFLINE"

    mode = str(session_info.get("mode") or "unbound")
    is_paid_mode = mode in ("independent", "reuse_narration")
    if is_paid_mode:
        if int(day_usage.get("remaining_calls", 1)) <= 0:
            return "budget_limited", "PULSE_DAILY_CALL_BUDGET_EXHAUSTED"
        if int(day_usage.get("remaining_tokens", 1)) <= 0:
            return "budget_limited", "PULSE_DAILY_TOKEN_BUDGET_EXHAUSTED"
    if deferred_budget_code is not None:
        return "budget_limited", deferred_budget_code

    if (
        int(counts.get("running", 0)) > 0
        or ready_pending_count > 0
        or int(day_usage.get("reserved_calls", 0)) > 0
    ):
        return "working", None

    if recent_jobs and recent_jobs[0]["status"] == "failed":
        return "ready", "PULSE_JOB_FAILED"
    return "ready", None


def get_pulse_status(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    session_router: PulseSessionRouter | None = None,
    activity_registry: ActivityLeaseRegistry | None = None,
    budget_manager: PulseBudgetManager | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """返回指定世界实例的脱敏脉动状态与队列概览（只读不续期，绝不含 API Key、session_id 或租约令牌）。"""
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    current = float(now) if now is not None else time.time()
    inst = _require_instance(conn, instance_id)
    cfg = get_or_init_instance_config(conn, instance_id, now=current)

    counts_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM pulse_jobs
        WHERE instance_id = ?
        GROUP BY status
        """,
        (instance_id,),
    ).fetchall()
    counts = {
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "waiting_for_model": 0,
        "cancelled": 0,
    }
    for r in counts_rows:
        counts[str(r["status"])] = int(r["n"])

    recent_jobs_raw = list_jobs(conn, instance_id, limit=20, recent=True)
    sanitized_jobs = [
        {
            "job_id": j["job_id"],
            "kind": j["kind"],
            "status": j["status"],
            "trigger_adventure_id": j["trigger_adventure_id"],
            "trigger_revision": j["trigger_revision"],
            "base_world_revision": j["base_world_revision"],
            "target_world_minute": j["target_world_minute"],
            "attempts": j["attempts"],
            "max_attempts": j["max_attempts"],
            "next_retry_at": j["next_retry_at"],
            "error_code": sanitize_text(j.get("error_code"), max_length=80) or None,
            "error_summary": sanitize_text(j.get("error_summary"), max_length=240) or None,
            "updated_at": j["updated_at"],
        }
        for j in recent_jobs_raw
    ]

    session_info = (
        session_router.status_for(instance_id)
        if session_router is not None
        else {"mode": "unbound", "ready": False, "model": None, "reason_code": "MODEL_SESSION_UNBOUND"}
    )
    client_online = (
        activity_registry.is_active(instance_id, now=current)
        if activity_registry is not None
        else True
    )

    current_day = game_day_for_minute(int(inst["world_minute"]))
    reserved = (
        budget_manager.reserved_snapshot(instance_id, game_day=current_day)
        if budget_manager is not None
        else {"reserved_calls": 0, "reserved_tokens": 0}
    )

    usage_summary = get_usage_summary(
        conn,
        instance_id,
        game_day=current_day,
        budget_manager=budget_manager,
    )
    day_usage = usage_summary["current_day_usage"]

    state, reason_code = _derive_control_state(
        cfg=cfg,
        session_info=session_info,
        client_online=client_online,
        counts=counts,
        recent_jobs=sanitized_jobs,
        day_usage=day_usage,
        now_ts=current,
    )

    result = {
        "instance_id": instance_id,
        "world_minute": int(inst["world_minute"]),
        "world_revision": int(inst["revision"]),
        "enabled": bool(cfg["enabled"]),
        "paused": bool(cfg["paused"]),
        "generation": int(cfg.get("generation", 1)),
        "mode": str(session_info.get("mode") or "unbound"),
        "state": state,
        "model": session_info.get("model"),
        "reason_code": reason_code,
        "complexity": cfg["complexity"],
        "last_pulsed_minute": int(cfg["last_pulsed_minute"]),
        "resume_anchor_minute": int(cfg["resume_anchor_minute"]),
        "session": {
            "mode": session_info.get("mode"),
            "ready": bool(session_info.get("ready")),
            "model": session_info.get("model"),
        },
        "client_online": client_online,
        "queue_counts": counts,
        "recent_jobs": sanitized_jobs,
        "in_flight_budget": reserved,
        "current_day_usage": day_usage,
    }
    if reason_code == "PULSE_JOB_FAILED":
        last_code = sanitized_jobs[0].get("error_code")
        explanation = _JOB_FAILURE_SUMMARIES.get(last_code)
        if explanation:
            result["reason_summary"] = (
                f"最近一次推演失败：{explanation}（{last_code}）。"
                "结果未写入世界，普通冒险可继续；可关闭后重新开启脉动。"
            )
        else:
            result["reason_summary"] = (
                "最近一次推演结果未通过世界规则校验，未写入世界。"
                "普通冒险可继续；可关闭后重新开启脉动。"
            )
    assert_no_credentials(result, context="pulse_status")
    return result


def get_usage_summary(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    game_day: int | None = None,
    budget_manager: PulseBudgetManager | None = None,
) -> dict[str, Any]:
    """返回指定实例的模型用量与预算汇总（严格区分 actual/estimated、可能计费项与模拟用量）。"""
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    inst = _require_instance(conn, instance_id)
    cfg = get_or_init_instance_config(conn, instance_id)

    target_day = game_day if game_day is not None else game_day_for_minute(int(inst["world_minute"]))

    day_rows = conn.execute(
        """
        SELECT
            game_day,
            purpose,
            usage_source,
            potentially_billed,
            outcome,
            COUNT(*) AS record_count,
            COALESCE(SUM(calls), 0) AS total_calls,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM pulse_usage_ledger
        WHERE instance_id = ?
        GROUP BY game_day, purpose, usage_source, potentially_billed, outcome
        ORDER BY game_day ASC, purpose ASC
        """,
        (instance_id,),
    ).fetchall()

    actual_tokens_today = 0
    estimated_tokens_today = 0
    timeout_maybe_billed_tokens_today = 0
    billed_calls_today = 0
    timeout_maybe_billed_calls_today = 0
    simulated_calls_today = 0
    simulated_tokens_today = 0
    aborted_unbilled_calls_today = 0

    by_game_day: dict[int, dict[str, Any]] = {}
    by_purpose: dict[str, dict[str, int]] = {}

    for r in day_rows:
        d = int(r["game_day"])
        purpose = str(r["purpose"])
        src = str(r["usage_source"])
        billed = bool(r["potentially_billed"])
        outcome = str(r["outcome"])
        rec_count = int(r["record_count"])
        calls = int(r["total_calls"])
        toks = int(r["total_tokens"])
        is_uncertain_billed = outcome in (
            "timeout_maybe_billed",
            "response_lost_maybe_billed",
            "upstream_error_maybe_billed",
        )
        is_simulated = (not billed) and outcome == "succeeded"
        is_aborted_unbilled = (not billed) and outcome != "succeeded"

        day_bucket = by_game_day.setdefault(
            d,
            {
                "game_day": d,
                "total_calls": 0,
                "billed_calls": 0,
                "actual_tokens": 0,
                "estimated_tokens": 0,
                "potentially_billed_tokens": 0,
                "timeout_or_uncertain_calls": 0,
                "simulated_calls": 0,
                "simulated_tokens": 0,
                "aborted_unbilled_calls": 0,
            },
        )
        day_bucket["total_calls"] += calls
        if billed:
            day_bucket["billed_calls"] += calls
            day_bucket["potentially_billed_tokens"] += toks
            if src == "actual":
                day_bucket["actual_tokens"] += toks
            else:
                day_bucket["estimated_tokens"] += toks
            if is_uncertain_billed:
                day_bucket["timeout_or_uncertain_calls"] += calls
        elif is_simulated:
            day_bucket["simulated_calls"] += calls
            day_bucket["simulated_tokens"] += toks
        elif is_aborted_unbilled:
            day_bucket["aborted_unbilled_calls"] += rec_count

        p_bucket = by_purpose.setdefault(
            purpose,
            {
                "calls": 0,
                "billed_calls": 0,
                "actual_tokens": 0,
                "estimated_tokens": 0,
                "potentially_billed_tokens": 0,
                "simulated_calls": 0,
                "simulated_tokens": 0,
                "total_tokens": 0,
            },
        )
        p_bucket["calls"] += calls
        p_bucket["total_tokens"] += toks
        if billed:
            p_bucket["billed_calls"] += calls
            p_bucket["potentially_billed_tokens"] += toks
            if src == "actual":
                p_bucket["actual_tokens"] += toks
            else:
                p_bucket["estimated_tokens"] += toks
        elif is_simulated:
            p_bucket["simulated_calls"] += calls
            p_bucket["simulated_tokens"] += toks

        if d == target_day:
            if billed:
                billed_calls_today += calls
                if src == "actual":
                    actual_tokens_today += toks
                else:
                    estimated_tokens_today += toks
                if is_uncertain_billed:
                    timeout_maybe_billed_tokens_today += toks
                    timeout_maybe_billed_calls_today += calls
            elif is_simulated:
                simulated_calls_today += calls
                simulated_tokens_today += toks
            elif is_aborted_unbilled:
                aborted_unbilled_calls_today += rec_count

    reserved = (
        budget_manager.reserved_snapshot(instance_id, game_day=target_day)
        if budget_manager is not None
        else {"reserved_calls": 0, "reserved_tokens": 0}
    )

    daily_call_limit = int(cfg["daily_call_budget"])
    daily_token_limit = int(cfg["daily_token_budget"])
    used_tokens_today = actual_tokens_today + estimated_tokens_today

    summary = {
        "instance_id": instance_id,
        "current_game_day": target_day,
        "budget": {
            "complexity": cfg["complexity"],
            "max_calls_per_round": int(cfg["max_calls_per_round"]),
            "max_tokens_per_round": int(cfg["max_tokens_per_round"]),
            "daily_call_budget": daily_call_limit,
            "daily_token_budget": daily_token_limit,
            "cooldown_seconds": float(cfg["cooldown_seconds"]),
            "min_game_minute_interval": int(cfg["min_game_minute_interval"]),
        },
        "current_day_usage": {
            "game_day": target_day,
            "billed_calls": billed_calls_today,
            "potentially_billed_calls": billed_calls_today,
            "reserved_calls": reserved["reserved_calls"],
            "daily_call_budget": daily_call_limit,
            "remaining_calls": max(0, daily_call_limit - billed_calls_today - reserved["reserved_calls"]),
            "actual_tokens": actual_tokens_today,
            "estimated_tokens": estimated_tokens_today,
            "potentially_billed_tokens": used_tokens_today,
            "timeout_maybe_billed_tokens": timeout_maybe_billed_tokens_today,
            "timeout_maybe_billed_calls": timeout_maybe_billed_calls_today,
            "simulated_calls": simulated_calls_today,
            "simulated_tokens": simulated_tokens_today,
            "aborted_unbilled_calls": aborted_unbilled_calls_today,
            "reserved_tokens": reserved["reserved_tokens"],
            "daily_token_budget": daily_token_limit,
            "remaining_tokens": max(0, daily_token_limit - used_tokens_today - reserved["reserved_tokens"]),
        },
        "by_game_day": [by_game_day[k] for k in sorted(by_game_day)],
        "by_purpose": by_purpose,
    }
    assert_no_credentials(summary, context="pulse_usage_summary")
    return summary


def snapshot(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    """导出世界脉动模块的可移植快照（不含活跃任务、API Key、会话 ID 和进程租约）。"""
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    cfg = get_or_init_instance_config(conn, instance_id)

    data = {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "config": {
            "enabled": bool(cfg["enabled"]),
            "paused": bool(cfg["paused"]),
            "complexity": cfg["complexity"],
            "max_calls_per_round": int(cfg["max_calls_per_round"]),
            "max_tokens_per_round": int(cfg["max_tokens_per_round"]),
            "daily_call_budget": int(cfg["daily_call_budget"]),
            "daily_token_budget": int(cfg["daily_token_budget"]),
            "cooldown_seconds": float(cfg["cooldown_seconds"]),
            "min_game_minute_interval": int(cfg["min_game_minute_interval"]),
            "defer_seconds": float(cfg["defer_seconds"]),
            "last_pulsed_minute": int(cfg["last_pulsed_minute"]),
            "resume_anchor_minute": int(cfg["resume_anchor_minute"]),
        },
    }
    assert_no_credentials(data, context="pulse_snapshot")
    return data


def restore(
    conn: sqlite3.Connection,
    target_instance_id: str,
    snapshot_data: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """从快照恢复目标实例的脉动配置（不复制旧实例的进行中任务、租约或内存凭据）。"""
    if not isinstance(snapshot_data, dict):
        raise PulseError("INVALID_SNAPSHOT", "脉动快照格式无效。", status=422)
    assert_no_credentials(snapshot_data, context="pulse_snapshot")
    version = snapshot_data.get("format_version")
    if version != SNAPSHOT_FORMAT_VERSION:
        raise PulseError(
            "UNSUPPORTED_SNAPSHOT_VERSION",
            f"不支持的脉动快照格式版本: {version}。",
            status=422,
        )
    raw_cfg = snapshot_data.get("config")
    if not isinstance(raw_cfg, dict):
        raise PulseError("INVALID_SNAPSHOT", "脉动快照缺少 config 字段。", status=422)

    complexity = raw_cfg.get("complexity", "standard")
    if complexity not in VALID_COMPLEXITY_MODES:
        raise PulseError("INVALID_SNAPSHOT", "快照中的 complexity 非法。", status=422)

    current = float(now) if now is not None else time.time()
    ensure_pulse_schema(conn, ensure_instance_table=False)
    inst = _require_instance(conn, target_instance_id)

    last_pulsed = int(raw_cfg.get("last_pulsed_minute", inst["world_minute"]))
    resume_anchor = int(raw_cfg.get("resume_anchor_minute", inst["world_minute"]))
    if last_pulsed < 0 or resume_anchor < 0:
        raise PulseError("INVALID_SNAPSHOT", "快照中的游戏分钟不能为负数。", status=422)

    conn.execute("DELETE FROM pulse_job_keys WHERE instance_id = ?", (target_instance_id,))
    conn.execute("DELETE FROM pulse_jobs WHERE instance_id = ?", (target_instance_id,))
    conn.execute("DELETE FROM pulse_instance_configs WHERE instance_id = ?", (target_instance_id,))

    conn.execute(
        """
        INSERT INTO pulse_instance_configs (
            instance_id, enabled, paused, generation, complexity,
            max_calls_per_round, max_tokens_per_round,
            daily_call_budget, daily_token_budget,
            cooldown_seconds, min_game_minute_interval, defer_seconds,
            last_pulsed_minute, resume_anchor_minute, last_call_at, updated_at
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            target_instance_id,
            1 if raw_cfg.get("enabled", False) else 0,
            1 if raw_cfg.get("paused", False) else 0,
            complexity,
            int(raw_cfg.get("max_calls_per_round", 2)),
            int(raw_cfg.get("max_tokens_per_round", 4096)),
            int(raw_cfg.get("daily_call_budget", 12)),
            int(raw_cfg.get("daily_token_budget", 24000)),
            float(raw_cfg.get("cooldown_seconds", 5.0)),
            int(raw_cfg.get("min_game_minute_interval", 0)),
            float(raw_cfg.get("defer_seconds", 60.0)),
            last_pulsed,
            resume_anchor,
            current,
        ),
    )
    return get_or_init_instance_config(conn, target_instance_id, now=current)
