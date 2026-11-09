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
from app.m3.pulse.types import VALID_COMPLEXITY_MODES

SNAPSHOT_FORMAT_VERSION = 1


def get_pulse_status(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    session_router: PulseSessionRouter | None = None,
    activity_registry: ActivityLeaseRegistry | None = None,
    budget_manager: PulseBudgetManager | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """返回指定世界实例的脱敏脉动状态与队列概览（绝不含 API Key、session_id 或租约令牌）。"""
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    inst = _require_instance(conn, instance_id)
    cfg = get_or_init_instance_config(conn, instance_id, now=now)

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

    recent_jobs_raw = list_jobs(conn, instance_id, limit=20)
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
        else {"mode": "unbound", "ready": False, "model": None}
    )
    client_online = (
        activity_registry.is_active(instance_id, now=now)
        if activity_registry is not None
        else True
    )

    current_day = game_day_for_minute(int(inst["world_minute"]))
    reserved = (
        budget_manager.reserved_snapshot(instance_id, game_day=current_day)
        if budget_manager is not None
        else {"reserved_calls": 0, "reserved_tokens": 0}
    )

    result = {
        "instance_id": instance_id,
        "world_minute": int(inst["world_minute"]),
        "world_revision": int(inst["revision"]),
        "enabled": bool(cfg["enabled"]),
        "paused": bool(cfg["paused"]),
        "complexity": cfg["complexity"],
        "last_pulsed_minute": int(cfg["last_pulsed_minute"]),
        "resume_anchor_minute": int(cfg["resume_anchor_minute"]),
        "session": session_info,
        "client_online": client_online,
        "queue_counts": counts,
        "recent_jobs": sanitized_jobs,
        "in_flight_budget": reserved,
    }
    assert_no_credentials(result, context="pulse_status")
    return result


def get_usage_summary(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    game_day: int | None = None,
    budget_manager: PulseBudgetManager | None = None,
) -> dict[str, Any]:
    """返回指定实例的模型用量与预算汇总（区分 actual 与 estimated，以及超时可能计费项）。"""
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

    by_game_day: dict[int, dict[str, Any]] = {}
    by_purpose: dict[str, dict[str, int]] = {}

    for r in day_rows:
        d = int(r["game_day"])
        purpose = str(r["purpose"])
        src = str(r["usage_source"])
        billed = bool(r["potentially_billed"])
        outcome = str(r["outcome"])
        calls = int(r["total_calls"])
        toks = int(r["total_tokens"])

        day_bucket = by_game_day.setdefault(
            d,
            {
                "game_day": d,
                "total_calls": 0,
                "actual_tokens": 0,
                "estimated_tokens": 0,
                "potentially_billed_tokens": 0,
                "timeout_or_uncertain_calls": 0,
            },
        )
        day_bucket["total_calls"] += calls
        if src == "actual":
            day_bucket["actual_tokens"] += toks
        else:
            day_bucket["estimated_tokens"] += toks
        if billed:
            day_bucket["potentially_billed_tokens"] += toks
        if outcome in ("timeout_maybe_billed", "upstream_error_maybe_billed"):
            day_bucket["timeout_or_uncertain_calls"] += calls

        p_bucket = by_purpose.setdefault(
            purpose,
            {"calls": 0, "actual_tokens": 0, "estimated_tokens": 0, "total_tokens": 0},
        )
        p_bucket["calls"] += calls
        p_bucket["total_tokens"] += toks
        if src == "actual":
            p_bucket["actual_tokens"] += toks
        else:
            p_bucket["estimated_tokens"] += toks

        if d == target_day and billed:
            billed_calls_today += calls
            if src == "actual":
                actual_tokens_today += toks
            else:
                estimated_tokens_today += toks
            if outcome in ("timeout_maybe_billed", "upstream_error_maybe_billed"):
                timeout_maybe_billed_tokens_today += toks
                timeout_maybe_billed_calls_today += calls

    reserved = (
        budget_manager.reserved_snapshot(instance_id, game_day=target_day)
        if budget_manager is not None
        else {"reserved_calls": 0, "reserved_tokens": 0}
    )

    daily_call_limit = int(cfg["daily_call_budget"])
    daily_token_limit = int(cfg["daily_token_budget"])
    used_tokens_today = actual_tokens_today + estimated_tokens_today

    return {
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
            "reserved_calls": reserved["reserved_calls"],
            "remaining_calls": max(0, daily_call_limit - billed_calls_today - reserved["reserved_calls"]),
            "actual_tokens": actual_tokens_today,
            "estimated_tokens": estimated_tokens_today,
            "timeout_maybe_billed_tokens": timeout_maybe_billed_tokens_today,
            "timeout_maybe_billed_calls": timeout_maybe_billed_calls_today,
            "reserved_tokens": reserved["reserved_tokens"],
            "remaining_tokens": max(0, daily_token_limit - used_tokens_today - reserved["reserved_tokens"]),
        },
        "by_game_day": [by_game_day[k] for k in sorted(by_game_day)],
        "by_purpose": by_purpose,
    }


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
            instance_id, enabled, paused, complexity,
            max_calls_per_round, max_tokens_per_round,
            daily_call_budget, daily_token_budget,
            cooldown_seconds, min_game_minute_interval, defer_seconds,
            last_pulsed_minute, resume_anchor_minute, last_call_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
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
