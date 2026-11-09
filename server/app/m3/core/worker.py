"""Worker 生命周期骨架（脉动开关、租约位、用量记账）。

真正的队列由 Li 的 `m3/pulse` 实现；这里只提供：
- 开关的持久化读写（暂停期间不补造事件）；
- 进程生命周期钩子的占位（start/stop 由 main 的 lifespan 调用）；
- 模型用量表，供队列在发请求前记账。
"""

from __future__ import annotations

import time
from typing import Any

from ...errors import ApiError

PAUSE_KEY = "pulse_enabled"


def set_enabled(conn: Any, enabled: bool, reason: str | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO m3_schema_meta(key, value) VALUES(?, ?)",
        (PAUSE_KEY, "1" if enabled else "0"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO m3_schema_meta(key, value) VALUES(?, ?)",
        ("pulse_pause_reason", reason or ""),
    )


def is_enabled(conn: Any) -> bool:
    row = conn.execute("SELECT value FROM m3_schema_meta WHERE key=?", (PAUSE_KEY,)).fetchone()
    return bool(row) and (row[0] if not hasattr(row, "keys") else row["value"]) == "1"


def pause_reason(conn: Any) -> str:
    row = conn.execute("SELECT value FROM m3_schema_meta WHERE key=?", ("pulse_pause_reason",)).fetchone()
    if not row:
        return ""
    return row[0] if not hasattr(row, "keys") else row["value"]


def ensure_running(conn: Any) -> None:
    """后台任务发请求前调用；关闭时明确拒绝，避免离线发起付费调用。"""
    if not is_enabled(conn):
        raise ApiError(409, "PULSE_DISABLED", "世界脉动当前关闭，不会产生新事件。")


def record_usage(
    conn: Any,
    *,
    purpose: str,
    calls: int,
    tokens: int,
    game_day: str,
) -> None:
    if not isinstance(calls, int) or calls < 0 or not isinstance(tokens, int) or tokens < 0:
        raise ApiError(422, "VALIDATION_ERROR", "用量必须是非负整数。")
    conn.execute(
        "INSERT INTO m3_model_usage(purpose, calls, tokens, game_day, created_at) VALUES(?,?,?,?,?)",
        (purpose, calls, tokens, game_day, time.time()),
    )


def usage_summary(conn: Any, game_day: str | None = None) -> dict[str, int]:
    sql = "SELECT COALESCE(SUM(calls),0) AS calls, COALESCE(SUM(tokens),0) AS tokens FROM m3_model_usage"
    params: tuple[Any, ...] = ()
    if game_day is not None:
        sql += " WHERE game_day=?"
        params = (game_day,)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return {"calls": 0, "tokens": 0}
    if hasattr(row, "keys"):
        return {"calls": int(row["calls"]), "tokens": int(row["tokens"])}
    return {"calls": int(row[0]), "tokens": int(row[1])}
