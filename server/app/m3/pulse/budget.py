"""M3.2 世界模拟预算、冷却控制、并发额度预留与按游戏日归档账本。"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import sqlite3
import threading
import time
from typing import Any

from app.m3.pulse.errors import PulseError, assert_no_credentials
from app.m3.pulse.queue import _require_instance, cancel_instance_jobs
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.types import (
    ComplexityMode,
    PulseBudgetConfig,
    PulseUsageEvidence,
    VALID_COMPLEXITY_MODES,
)


def _now(override: float | None = None) -> float:
    return float(override) if override is not None else time.time()


def game_day_for_minute(world_minute: int) -> int:
    """将已确认游戏分钟映射为从 0 开始的游戏日编号（1440 分钟/天）。"""
    return max(0, int(world_minute)) // 1440


@dataclass(frozen=True)
class BudgetReservation:
    """请求前预留的并发预算凭证。"""

    reservation_id: str
    instance_id: str
    job_id: str | None
    purpose: str
    game_day: int
    world_minute: int
    reserved_calls: int
    reserved_tokens: int
    created_at: float


class BudgetExhaustedError(PulseError):
    """预算不足或处于冷却窗口时抛出，指示 Worker 延后任务而非忙循环。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after_seconds: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        info = dict(details or {})
        info["retry_after_seconds"] = float(retry_after_seconds)
        super().__init__(code=code, message=message, status=429, details=info)
        self.retry_after_seconds = float(retry_after_seconds)


def get_or_init_instance_config(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """读取或按默认标准复杂度初始化实例脉动配置（默认 enabled=False）。"""
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    inst = _require_instance(conn, instance_id)
    row = conn.execute(
        "SELECT * FROM pulse_instance_configs WHERE instance_id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        current = _now(now)
        default_cfg = PulseBudgetConfig.for_complexity("standard")
        current_min = int(inst["world_minute"])
        conn.execute(
            """
            INSERT INTO pulse_instance_configs (
                instance_id, enabled, paused, complexity,
                max_calls_per_round, max_tokens_per_round,
                daily_call_budget, daily_token_budget,
                cooldown_seconds, min_game_minute_interval, defer_seconds,
                last_pulsed_minute, resume_anchor_minute, last_call_at, updated_at
            ) VALUES (?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                instance_id,
                default_cfg.complexity,
                default_cfg.max_calls_per_round,
                default_cfg.max_tokens_per_round,
                default_cfg.daily_call_budget,
                default_cfg.daily_token_budget,
                default_cfg.cooldown_seconds,
                default_cfg.min_game_minute_interval,
                default_cfg.defer_seconds_on_exhausted,
                current_min,
                current_min,
                current,
            ),
        )
        row = conn.execute(
            "SELECT * FROM pulse_instance_configs WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
    return _config_row_to_dict(row)


def _config_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    d["paused"] = bool(d["paused"])
    return d


def configure_instance_pulse(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    enabled: bool | None = None,
    paused: bool | None = None,
    complexity: ComplexityMode | None = None,
    max_calls_per_round: int | None = None,
    max_tokens_per_round: int | None = None,
    daily_call_budget: int | None = None,
    daily_token_budget: int | None = None,
    cooldown_seconds: float | None = None,
    min_game_minute_interval: int | None = None,
    defer_seconds: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """更新指定实例的世界脉动开关、暂停状态或预算配置。"""
    current = _now(now)
    cfg = get_or_init_instance_config(conn, instance_id, now=current)
    inst = _require_instance(conn, instance_id)
    current_world_minute = int(inst["world_minute"])

    if complexity is not None:
        if complexity not in VALID_COMPLEXITY_MODES:
            raise PulseError("INVALID_COMPLEXITY", "复杂度模式仅支持 light、standard 或 deep。", status=422)
        preset = PulseBudgetConfig.for_complexity(complexity)
        cfg["complexity"] = preset.complexity
        if max_calls_per_round is None:
            cfg["max_calls_per_round"] = preset.max_calls_per_round
        if max_tokens_per_round is None:
            cfg["max_tokens_per_round"] = preset.max_tokens_per_round
        if daily_call_budget is None:
            cfg["daily_call_budget"] = preset.daily_call_budget
        if daily_token_budget is None:
            cfg["daily_token_budget"] = preset.daily_token_budget
        if cooldown_seconds is None:
            cfg["cooldown_seconds"] = preset.cooldown_seconds

    for field_name, val in (
        ("max_calls_per_round", max_calls_per_round),
        ("max_tokens_per_round", max_tokens_per_round),
        ("daily_call_budget", daily_call_budget),
        ("daily_token_budget", daily_token_budget),
    ):
        if val is not None:
            if type(val) is not int or val < 1:
                raise PulseError("INVALID_BUDGET", f"{field_name} 必须是正整数。", status=422)
            cfg[field_name] = val

    if min_game_minute_interval is not None:
        if type(min_game_minute_interval) is not int or min_game_minute_interval < 0:
            raise PulseError("INVALID_BUDGET", "min_game_minute_interval 必须是非负整数。", status=422)
        cfg["min_game_minute_interval"] = min_game_minute_interval

    if cooldown_seconds is not None:
        if not isinstance(cooldown_seconds, (int, float)) or float(cooldown_seconds) < 0:
            raise PulseError("INVALID_BUDGET", "cooldown_seconds 必须是非负数。", status=422)
        cfg["cooldown_seconds"] = float(cooldown_seconds)

    if defer_seconds is not None:
        if not isinstance(defer_seconds, (int, float)) or float(defer_seconds) <= 0:
            raise PulseError("INVALID_BUDGET", "defer_seconds 必须大于 0。", status=422)
        cfg["defer_seconds"] = float(defer_seconds)

    was_active = bool(cfg["enabled"]) and not bool(cfg["paused"])

    if enabled is not None:
        if type(enabled) is not bool:
            raise PulseError("INVALID_CONFIG", "enabled 必须是布尔值。", status=422)
        cfg["enabled"] = enabled

    if paused is not None:
        if type(paused) is not bool:
            raise PulseError("INVALID_CONFIG", "paused 必须是布尔值。", status=422)
        cfg["paused"] = paused

    is_now_active = bool(cfg["enabled"]) and not bool(cfg["paused"])

    # 从启用变为关闭或暂停时：取消尚未开始执行的排队任务
    if was_active and not is_now_active:
        cancel_instance_jobs(
            conn,
            instance_id,
            include_running=False,
            reason_code="PULSE_PAUSED_OR_DISABLED",
            reason_summary="世界脉动已暂停或关闭，未执行的排队任务已取消。",
            now=current,
        )

    # 从关闭或暂停恢复启用时：锚定当前已确认游戏分钟，不补造暂停期间经过的历史时间
    if not was_active and is_now_active:
        cancel_instance_jobs(
            conn,
            instance_id,
            include_running=False,
            reason_code="PULSE_RESUMED_SKIP_PAUSED_HISTORY",
            reason_summary="脉动恢复时清理暂停前的陈旧待办，从当前世界时刻继续。",
            now=current,
        )
        cfg["resume_anchor_minute"] = max(int(cfg["resume_anchor_minute"]), current_world_minute)
        cfg["last_pulsed_minute"] = max(int(cfg["last_pulsed_minute"]), current_world_minute)

    conn.execute(
        """
        UPDATE pulse_instance_configs
        SET enabled = ?,
            paused = ?,
            complexity = ?,
            max_calls_per_round = ?,
            max_tokens_per_round = ?,
            daily_call_budget = ?,
            daily_token_budget = ?,
            cooldown_seconds = ?,
            min_game_minute_interval = ?,
            defer_seconds = ?,
            last_pulsed_minute = ?,
            resume_anchor_minute = ?,
            updated_at = ?
        WHERE instance_id = ?
        """,
        (
            1 if cfg["enabled"] else 0,
            1 if cfg["paused"] else 0,
            cfg["complexity"],
            cfg["max_calls_per_round"],
            cfg["max_tokens_per_round"],
            cfg["daily_call_budget"],
            cfg["daily_token_budget"],
            cfg["cooldown_seconds"],
            cfg["min_game_minute_interval"],
            cfg["defer_seconds"],
            cfg["last_pulsed_minute"],
            cfg["resume_anchor_minute"],
            current,
            instance_id,
        ),
    )
    return get_or_init_instance_config(conn, instance_id, now=current)


def enable_pulse(conn: sqlite3.Connection, instance_id: str, *, now: float | None = None) -> dict[str, Any]:
    """启用并恢复实例世界脉动。"""
    return configure_instance_pulse(conn, instance_id, enabled=True, paused=False, now=now)


def disable_pulse(conn: sqlite3.Connection, instance_id: str, *, now: float | None = None) -> dict[str, Any]:
    """关闭实例世界脉动（保留已有历史事件与用量记录，不删除叙事会话）。"""
    return configure_instance_pulse(conn, instance_id, enabled=False, paused=False, now=now)


def pause_pulse(conn: sqlite3.Connection, instance_id: str, *, now: float | None = None) -> dict[str, Any]:
    """暂停实例世界脉动。"""
    return configure_instance_pulse(conn, instance_id, paused=True, now=now)


def resume_pulse(conn: sqlite3.Connection, instance_id: str, *, now: float | None = None) -> dict[str, Any]:
    """恢复已暂停的实例世界脉动（从当前已确认游戏时间继续，不补造暂停期间的历史）。"""
    return configure_instance_pulse(conn, instance_id, enabled=True, paused=False, now=now)


class PulseBudgetManager:
    """世界脉动预算管理器（支持请求前预留、并发扣额、冷却检查与按游戏日归档）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_reservations: dict[str, BudgetReservation] = {}
        self._round_calls_by_job: dict[str, int] = {}
        self._round_tokens_by_job: dict[str, int] = {}
        self._in_flight_last_reserved_at: dict[str, float] = {}

    def reserve(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        *,
        job_id: str | None = None,
        purpose: str = "world_pulse_advance",
        world_minute: int = 0,
        estimated_tokens: int = 512,
        now: float | None = None,
    ) -> BudgetReservation:
        """在发起模型网络请求前检查并预留预算额度。

        - 检查每轮（单任务）最大调用次数与单次最大 token 预算；
        - 检查调用冷却时间（cooldown_seconds）；
        - 检查当前游戏日（world_minute // 1440）已落盘用量 + 并发预留中的额度是否超限；
        - 超出预算或冷却未到时抛出 BudgetExhaustedError，并附带 retry_after_seconds 供队列延后。
        """
        current = _now(now)
        req_tokens = max(1, int(estimated_tokens))
        game_day = game_day_for_minute(world_minute)

        cfg = get_or_init_instance_config(conn, instance_id, now=current)
        defer_sec = float(cfg["defer_seconds"])
        cooldown_sec = float(cfg["cooldown_seconds"])
        max_calls_round = int(cfg["max_calls_per_round"])
        max_tokens_round = int(cfg["max_tokens_per_round"])
        daily_calls_limit = int(cfg["daily_call_budget"])
        daily_tokens_limit = int(cfg["daily_token_budget"])

        # 读取数据库中该实例在当前游戏日已产生的累计用量（含超时可能计费的调用）
        usage_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(calls), 0) AS used_calls,
                COALESCE(SUM(total_tokens), 0) AS used_tokens
            FROM pulse_usage_ledger
            WHERE instance_id = ?
              AND game_day = ?
            """,
            (instance_id, game_day),
        ).fetchone()
        db_used_calls = int(usage_row["used_calls"]) if usage_row else 0
        db_used_tokens = int(usage_row["used_tokens"]) if usage_row else 0
        db_last_call_at = float(cfg["last_call_at"]) if cfg.get("last_call_at") is not None else None

        with self._lock:
            # 1. 冷却时间检查（综合已落盘最后调用时间与并发预留时间）
            mem_last_at = self._in_flight_last_reserved_at.get(instance_id)
            effective_last_at = max(
                [t for t in (db_last_call_at, mem_last_at) if t is not None],
                default=None,
            )
            if cooldown_sec > 0 and effective_last_at is not None:
                elapsed = current - effective_last_at
                if elapsed < cooldown_sec:
                    wait_sec = max(0.5, cooldown_sec - elapsed)
                    raise BudgetExhaustedError(
                        code="PULSE_COOLDOWN_ACTIVE",
                        message="世界脉动处于调用冷却期内，已自动延后。",
                        retry_after_seconds=wait_sec,
                        details={"instance_id": instance_id, "cooldown_seconds": cooldown_sec},
                    )

            # 2. 统计当前实例在途预留额度
            reserved_calls_day = 0
            reserved_tokens_day = 0
            reserved_calls_job = 0
            reserved_tokens_job = 0
            for res in self._active_reservations.values():
                if res.instance_id == instance_id and res.game_day == game_day:
                    reserved_calls_day += res.reserved_calls
                    reserved_tokens_day += res.reserved_tokens
                if job_id is not None and res.job_id == job_id:
                    reserved_calls_job += res.reserved_calls
                    reserved_tokens_job += res.reserved_tokens

            # 3. 每轮（单任务）调用次数与 token 上限检查
            settled_job_calls = self._round_calls_by_job.get(job_id, 0) if job_id else 0
            settled_job_tokens = self._round_tokens_by_job.get(job_id, 0) if job_id else 0
            if settled_job_calls + reserved_calls_job + 1 > max_calls_round:
                raise BudgetExhaustedError(
                    code="PULSE_ROUND_CALL_BUDGET_EXCEEDED",
                    message="单次世界脉动模型调用次数已达上限，延后至下一轮。",
                    retry_after_seconds=defer_sec,
                    details={"max_calls_per_round": max_calls_round},
                )
            if req_tokens > max_tokens_round or (settled_job_tokens + reserved_tokens_job + req_tokens > max_tokens_round):
                raise BudgetExhaustedError(
                    code="PULSE_ROUND_TOKEN_BUDGET_EXCEEDED",
                    message="单次世界脉动 Token 预算不足，已拒绝或延后本次调用。",
                    retry_after_seconds=defer_sec,
                    details={"max_tokens_per_round": max_tokens_round, "requested_tokens": req_tokens},
                )

            # 4. 游戏日（daily）调用次数与 token 预算检查（含并发在途预留）
            if db_used_calls + reserved_calls_day + 1 > daily_calls_limit:
                raise BudgetExhaustedError(
                    code="PULSE_DAILY_CALL_BUDGET_EXHAUSTED",
                    message="当前游戏日的世界脉动调用次数预算已耗尽，暂停并延后模拟。",
                    retry_after_seconds=defer_sec,
                    details={
                        "game_day": game_day,
                        "daily_call_budget": daily_calls_limit,
                        "used_calls": db_used_calls,
                        "reserved_calls": reserved_calls_day,
                    },
                )
            if db_used_tokens + reserved_tokens_day + req_tokens > daily_tokens_limit:
                raise BudgetExhaustedError(
                    code="PULSE_DAILY_TOKEN_BUDGET_EXHAUSTED",
                    message="当前游戏日的世界脉动 Token 预算已耗尽，暂停并延后模拟。",
                    retry_after_seconds=defer_sec,
                    details={
                        "game_day": game_day,
                        "daily_token_budget": daily_tokens_limit,
                        "used_tokens": db_used_tokens,
                        "reserved_tokens": reserved_tokens_day,
                        "requested_tokens": req_tokens,
                    },
                )

            reservation = BudgetReservation(
                reservation_id=f"pres_{secrets.token_hex(8)}",
                instance_id=instance_id,
                job_id=job_id,
                purpose=purpose,
                game_day=game_day,
                world_minute=int(world_minute),
                reserved_calls=1,
                reserved_tokens=req_tokens,
                created_at=current,
            )
            self._active_reservations[reservation.reservation_id] = reservation
            self._in_flight_last_reserved_at[instance_id] = current
            return reservation

    def release(self, reservation_id: str) -> None:
        """直接释放未产生计费的预留额度（例如请求发送前取消或认证失败未扣费）。"""
        with self._lock:
            res = self._active_reservations.pop(reservation_id, None)
            if res is not None:
                # 若该实例无其他在途预留，清理内存冷却时间戳以数据库为准
                still_in_flight = any(
                    r.instance_id == res.instance_id for r in self._active_reservations.values()
                )
                if not still_in_flight:
                    self._in_flight_last_reserved_at.pop(res.instance_id, None)

    def settle(
        self,
        conn: sqlite3.Connection,
        reservation: BudgetReservation,
        evidence: PulseUsageEvidence,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """在短事务内归档模型调用的实际或估算用量，并释放内存预留。"""
        assert_no_credentials(evidence.model, context="evidence.model")
        current = _now(now)
        ensure_pulse_schema(conn, ensure_instance_table=False)

        with self._lock:
            self._active_reservations.pop(reservation.reservation_id, None)
            if evidence.potentially_billed and reservation.job_id:
                self._round_calls_by_job[reservation.job_id] = (
                    self._round_calls_by_job.get(reservation.job_id, 0) + 1
                )
                self._round_tokens_by_job[reservation.job_id] = (
                    self._round_tokens_by_job.get(reservation.job_id, 0) + max(0, int(evidence.total_tokens))
                )
            if not evidence.potentially_billed:
                still_in_flight = any(
                    r.instance_id == reservation.instance_id for r in self._active_reservations.values()
                )
                if not still_in_flight:
                    self._in_flight_last_reserved_at.pop(reservation.instance_id, None)

        ledger_id = f"pusg_{secrets.token_hex(10)}"
        calls_count = 1 if (evidence.potentially_billed or evidence.outcome == "succeeded") else 0
        prompt_toks = max(0, int(evidence.prompt_tokens))
        comp_toks = max(0, int(evidence.completion_tokens))
        total_toks = max(prompt_toks + comp_toks, int(evidence.total_tokens)) if calls_count > 0 else 0
        source = "actual" if evidence.usage_source == "actual" else "estimated"
        billed_flag = int(evidence.potentially_billed)

        conn.execute(
            """
            INSERT INTO pulse_usage_ledger (
                id, instance_id, job_id, purpose, model,
                game_day, world_minute, calls,
                prompt_tokens, completion_tokens, total_tokens,
                usage_source, potentially_billed, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ledger_id,
                reservation.instance_id,
                reservation.job_id,
                evidence.purpose or reservation.purpose,
                evidence.model,
                reservation.game_day,
                reservation.world_minute,
                calls_count,
                prompt_toks,
                comp_toks,
                total_toks,
                source,
                billed_flag,
                evidence.outcome,
                current,
            ),
        )

        if calls_count:
            conn.execute(
                """
                UPDATE pulse_instance_configs
                SET last_call_at = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (current, current, reservation.instance_id),
            )

        return {
            "id": ledger_id,
            "instance_id": reservation.instance_id,
            "job_id": reservation.job_id,
            "purpose": evidence.purpose or reservation.purpose,
            "model": evidence.model,
            "game_day": reservation.game_day,
            "world_minute": reservation.world_minute,
            "calls": calls_count,
            "prompt_tokens": prompt_toks,
            "completion_tokens": comp_toks,
            "total_tokens": total_toks,
            "usage_source": source,
            "potentially_billed": bool(billed_flag),
            "outcome": evidence.outcome,
        }

    def reserved_snapshot(self, instance_id: str, *, game_day: int | None = None) -> dict[str, int]:
        """查询当前内存中某实例在途预留的调用数与 token 数。"""
        with self._lock:
            calls = 0
            tokens = 0
            for res in self._active_reservations.values():
                if res.instance_id == instance_id and (game_day is None or res.game_day == game_day):
                    calls += res.reserved_calls
                    tokens += res.reserved_tokens
            return {"reserved_calls": calls, "reserved_tokens": tokens}

    def clear_job_round_counters(self, job_id: str) -> None:
        with self._lock:
            self._round_calls_by_job.pop(job_id, None)
            self._round_tokens_by_job.pop(job_id, None)
