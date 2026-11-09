"""M3.2 持久化任务队列（登记、幂等、合并、原子领取、带令牌租约、超时回收、重试退避、等待模型与取消）。"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any

from app.m3.pulse.errors import PulseError, assert_no_credentials, sanitize_text
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.types import (
    JobKind,
    PulseTrigger,
    VALID_JOB_KINDS,
)


def _now(override: float | None = None) -> float:
    return float(override) if override is not None else time.time()


def _row_to_job_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    raw_payload = data.pop("payload_json", "{}") or "{}"
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}
    data["payload"] = payload
    return data


def _require_instance(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise PulseError(
            code="INSTANCE_NOT_FOUND",
            message="未找到对应的世界实例。",
            status=404,
            details={"instance_id": instance_id},
        )
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {
        "id": row[0],
        "world_id": row[1],
        "adventure_id": row[2],
        "world_minute": row[3],
        "revision": row[4],
    }


def _normalize_trigger(trigger: PulseTrigger | dict[str, Any]) -> PulseTrigger:
    if isinstance(trigger, PulseTrigger):
        t = trigger
    elif isinstance(trigger, dict):
        assert_no_credentials(trigger, context="trigger")
        kind_val = trigger.get("kind", "advance")
        t = PulseTrigger(
            instance_id=str(trigger.get("instance_id") or "").strip(),
            kind=kind_val,  # type: ignore[arg-type]
            trigger_adventure_id=str(trigger.get("trigger_adventure_id") or "").strip(),
            trigger_revision=trigger.get("trigger_revision", 0),
            base_world_revision=trigger.get("base_world_revision", 0),
            target_world_minute=trigger.get("target_world_minute", 0),
            idempotency_key=trigger.get("idempotency_key"),
            coalesce=bool(trigger.get("coalesce", kind_val == "advance")),
            max_attempts=int(trigger.get("max_attempts", 3)),
            payload=dict(trigger.get("payload") or {}),
            now=trigger.get("now"),
        )
    else:
        raise PulseError("INVALID_TRIGGER", "任务触发器格式无效。", status=422)

    if not t.instance_id:
        raise PulseError("INVALID_TRIGGER", "instance_id 不能为空。", status=422)
    if t.kind not in VALID_JOB_KINDS:
        raise PulseError("INVALID_TRIGGER", "kind 只能是 seed 或 advance。", status=422)
    if not t.trigger_adventure_id:
        raise PulseError("INVALID_TRIGGER", "trigger_adventure_id 不能为空。", status=422)
    for field_name, val in (
        ("trigger_revision", t.trigger_revision),
        ("base_world_revision", t.base_world_revision),
        ("target_world_minute", t.target_world_minute),
    ):
        if type(val) is not int or val < 0:
            raise PulseError("INVALID_TRIGGER", f"{field_name} 必须是非负整数。", status=422)
    if type(t.max_attempts) is not int or t.max_attempts < 1:
        raise PulseError("INVALID_TRIGGER", "max_attempts 必须是正整数。", status=422)

    assert_no_credentials(t.payload, context="trigger.payload")
    if t.idempotency_key:
        assert_no_credentials(t.idempotency_key, context="trigger.idempotency_key")
    return t


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    """按 job_id 读取单条任务记录。"""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM pulse_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise PulseError("JOB_NOT_FOUND", "脉动任务不存在。", status=404, details={"job_id": job_id})
    return _row_to_job_dict(row)


def list_jobs(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按实例列出任务记录（脱敏）。"""
    conn.row_factory = sqlite3.Row
    if status is not None:
        rows = conn.execute(
            """
            SELECT * FROM pulse_jobs
            WHERE instance_id = ? AND status = ?
            ORDER BY created_at ASC, rowid ASC LIMIT ?
            """,
            (instance_id, status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM pulse_jobs
            WHERE instance_id = ?
            ORDER BY created_at ASC, rowid ASC LIMIT ?
            """,
            (instance_id, limit),
        ).fetchall()
    return [_row_to_job_dict(r) for r in rows]


def enqueue(conn: sqlite3.Connection, trigger: PulseTrigger | dict[str, Any]) -> dict[str, Any]:
    """登记脉动任务。

    - 按 (instance_id, idempotency_key) 保证幂等：已完成或已存在的同键任务直接返回，绝不重复执行；
    - 支持连续回合合并（coalesce）：当同一实例已有处于 pending / waiting_for_model 的 advance 任务时，
      自动合并至更新的已确认时钟目标（target_world_minute）与世界基准版本（base_world_revision），
      并登记新的幂等键映射；
    - 严禁写入任何 API Key、模型会话 ID 或密钥请求头。
    - 遵循契约：接收已打开的 sqlite3.Connection，不自行 commit。
    """
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)
    t = _normalize_trigger(trigger)
    _require_instance(conn, t.instance_id)

    now = _now(t.now)
    idem_key = (
        t.idempotency_key.strip()
        if isinstance(t.idempotency_key, str) and t.idempotency_key.strip()
        else f"{t.kind}:{t.trigger_adventure_id}:{t.trigger_revision}"
    )

    # 1. 检查幂等键映射表（包括曾被合并进其他任务的幂等键）
    key_row = conn.execute(
        "SELECT job_id FROM pulse_job_keys WHERE instance_id = ? AND idempotency_key = ?",
        (t.instance_id, idem_key),
    ).fetchone()
    if key_row is not None:
        existing = get_job(conn, key_row["job_id"])
        existing["deduplicated"] = True
        return existing

    # 直接按主表 (instance_id, idempotency_key) 复核
    direct_row = conn.execute(
        "SELECT * FROM pulse_jobs WHERE instance_id = ? AND idempotency_key = ?",
        (t.instance_id, idem_key),
    ).fetchone()
    if direct_row is not None:
        existing = _row_to_job_dict(direct_row)
        existing["deduplicated"] = True
        return existing

    # 2. 连续回合待办合并：仅合并未开始执行（pending / waiting_for_model）的 advance 任务
    if t.kind == "advance" and t.coalesce:
        merge_candidate = conn.execute(
            """
            SELECT * FROM pulse_jobs
            WHERE instance_id = ?
              AND kind = 'advance'
              AND status IN ('pending', 'waiting_for_model')
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (t.instance_id,),
        ).fetchone()
        if merge_candidate is not None:
            cand = _row_to_job_dict(merge_candidate)
            if (
                t.target_world_minute >= cand["target_world_minute"]
                and t.base_world_revision >= cand["base_world_revision"]
            ):
                merged_payload = dict(cand.get("payload") or {})
                merged_payload.update(t.payload)
                coalesced_revs = list(merged_payload.get("coalesced_trigger_revisions") or [cand["trigger_revision"]])
                if t.trigger_revision not in coalesced_revs:
                    coalesced_revs.append(t.trigger_revision)
                merged_payload["coalesced_trigger_revisions"] = coalesced_revs
                merged_payload["coalesced_count"] = len(coalesced_revs)

                conn.execute(
                    """
                    UPDATE pulse_jobs
                    SET trigger_adventure_id = ?,
                        trigger_revision = ?,
                        base_world_revision = ?,
                        target_world_minute = ?,
                        payload_json = ?,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        t.trigger_adventure_id,
                        max(int(cand["trigger_revision"]), t.trigger_revision),
                        max(int(cand["base_world_revision"]), t.base_world_revision),
                        max(int(cand["target_world_minute"]), t.target_world_minute),
                        json.dumps(merged_payload, ensure_ascii=False),
                        now,
                        cand["job_id"],
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO pulse_job_keys (
                        instance_id, idempotency_key, job_id, trigger_revision, target_world_minute, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.instance_id,
                        idem_key,
                        cand["job_id"],
                        t.trigger_revision,
                        t.target_world_minute,
                        now,
                    ),
                )
                updated = get_job(conn, cand["job_id"])
                updated["coalesced"] = True
                return updated

    # 3. 插入新任务
    job_id = f"pjob_{secrets.token_hex(10)}"
    initial_payload = dict(t.payload)
    if t.kind == "advance":
        initial_payload.setdefault("coalesced_trigger_revisions", [t.trigger_revision])
        initial_payload.setdefault("coalesced_count", 1)

    conn.execute(
        """
        INSERT INTO pulse_jobs (
            job_id, instance_id, idempotency_key, kind,
            trigger_adventure_id, trigger_revision, base_world_revision, target_world_minute,
            payload_json, status, attempts, max_attempts,
            next_retry_at, lease_token, lease_until, error_code, error_summary,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)
        """,
        (
            job_id,
            t.instance_id,
            idem_key,
            t.kind,
            t.trigger_adventure_id,
            t.trigger_revision,
            t.base_world_revision,
            t.target_world_minute,
            json.dumps(initial_payload, ensure_ascii=False),
            t.max_attempts,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO pulse_job_keys (
            instance_id, idempotency_key, job_id, trigger_revision, target_world_minute, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            t.instance_id,
            idem_key,
            job_id,
            t.trigger_revision,
            t.target_world_minute,
            now,
        ),
    )
    created = get_job(conn, job_id)
    created["coalesced"] = False
    created["deduplicated"] = False
    return created


def reclaim_expired(conn: sqlite3.Connection, now: float | None = None) -> int:
    """回收所有租约已过期的 running 任务，将其重置为 pending（或超出最大次数时标记 failed）。"""
    conn.row_factory = sqlite3.Row
    current = _now(now)
    expired_rows = conn.execute(
        """
        SELECT job_id, attempts, max_attempts FROM pulse_jobs
        WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until <= ?
        """,
        (current,),
    ).fetchall()
    reclaimed = 0
    for r in expired_rows:
        if int(r["attempts"]) >= int(r["max_attempts"]):
            cur = conn.execute(
                """
                UPDATE pulse_jobs
                SET status = 'failed',
                    lease_token = NULL,
                    lease_until = NULL,
                    error_code = 'LEASE_EXPIRED_MAX_ATTEMPTS',
                    error_summary = '任务租约过期且已达最大重试次数。',
                    updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_until <= ?
                """,
                (current, r["job_id"], current),
            )
        else:
            cur = conn.execute(
                """
                UPDATE pulse_jobs
                SET status = 'pending',
                    lease_token = NULL,
                    lease_until = NULL,
                    next_retry_at = ?,
                    error_code = 'LEASE_EXPIRED_RECLAIMED',
                    error_summary = '前序 Worker 租约超时，已回收待重新领取。',
                    updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_until <= ?
                """,
                (current, current, r["job_id"], current),
            )
        reclaimed += cur.rowcount
    return reclaimed


def claim(
    conn: sqlite3.Connection,
    now: float | None = None,
    *,
    lease_seconds: float = 60.0,
    instance_id: str | None = None,
) -> dict[str, Any] | None:
    """原子领取下一条可执行任务并颁发唯一 lease_token。

    - 支持直接回收租约已过期的 running 任务并更换新 lease_token；
    - 旧 Worker 持有的过期 lease_token 随即失效，即使稍后收到模型响应也无法通过 complete 校验；
    - 多连接争抢时通过条件 UPDATE + rowcount 保证同一时刻只有一个调用者成功领取。
    """
    if lease_seconds <= 0:
        raise PulseError("INVALID_LEASE", "lease_seconds 必须大于 0。", status=422)

    conn.row_factory = sqlite3.Row
    current = _now(now)

    instance_filter = "AND instance_id = ?" if instance_id is not None else ""
    params: list[Any] = [current, current]
    if instance_id is not None:
        params.append(instance_id)

    candidates = conn.execute(
        f"""
        SELECT job_id, status, attempts, max_attempts, lease_token, lease_until
        FROM pulse_jobs
        WHERE (
            (status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?))
            OR (status = 'running' AND lease_until IS NOT NULL AND lease_until <= ?)
        )
        {instance_filter}
        ORDER BY created_at ASC, rowid ASC
        LIMIT 10
        """,
        tuple(params),
    ).fetchall()

    for cand in candidates:
        job_id = cand["job_id"]
        old_status = cand["status"]
        old_token = cand["lease_token"] or ""
        attempts = int(cand["attempts"])
        max_attempts = int(cand["max_attempts"])

        # 若过期 running 任务已超过最大尝试次数，归档为 failed 并继续找下一条
        if old_status == "running" and attempts >= max_attempts:
            conn.execute(
                """
                UPDATE pulse_jobs
                SET status = 'failed',
                    lease_token = NULL,
                    lease_until = NULL,
                    error_code = 'LEASE_EXPIRED_MAX_ATTEMPTS',
                    error_summary = '任务租约过期且已达最大重试次数。',
                    updated_at = ?
                WHERE job_id = ? AND status = 'running' AND IFNULL(lease_token, '') = ? AND lease_until <= ?
                """,
                (current, job_id, old_token, current),
            )
            continue

        new_token = f"lease_{secrets.token_urlsafe(18)}"
        lease_until = current + float(lease_seconds)

        cur = conn.execute(
            """
            UPDATE pulse_jobs
            SET status = 'running',
                attempts = attempts + 1,
                lease_token = ?,
                lease_until = ?,
                updated_at = ?
            WHERE job_id = ?
              AND status = ?
              AND IFNULL(lease_token, '') = ?
              AND (
                  (status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?))
                  OR (status = 'running' AND lease_until IS NOT NULL AND lease_until <= ?)
              )
            """,
            (new_token, lease_until, current, job_id, old_status, old_token, current, current),
        )
        if cur.rowcount == 1:
            return get_job(conn, job_id)

    return None


def verify_active_lease(
    conn: sqlite3.Connection,
    job_id: str,
    lease_token: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """校验给定任务的租约令牌与有效期是否仍然有效。"""
    if not isinstance(lease_token, str) or not lease_token.strip():
        raise PulseError("PULSE_LEASE_INVALID", "缺少有效的任务租约令牌。", status=409)

    conn.row_factory = sqlite3.Row
    current = _now(now)
    row = conn.execute("SELECT * FROM pulse_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise PulseError("JOB_NOT_FOUND", "脉动任务不存在。", status=404, details={"job_id": job_id})

    if row["status"] != "running":
        raise PulseError(
            code="PULSE_LEASE_INVALID",
            message=f"任务当前状态为 {row['status']}，不在运行租约内。",
            status=409,
            details={"job_id": job_id, "status": row["status"]},
        )
    if row["lease_token"] != lease_token:
        raise PulseError(
            code="PULSE_LEASE_STOLEN",
            message="任务租约令牌不匹配，已被新 Worker 回收或接管，旧 Worker 禁止提交。",
            status=409,
            details={"job_id": job_id},
        )
    if row["lease_until"] is not None and float(row["lease_until"]) <= current:
        raise PulseError(
            code="PULSE_LEASE_EXPIRED",
            message="任务租约已超时过期，禁止提交。",
            status=409,
            details={"job_id": job_id},
        )
    return _row_to_job_dict(row)


def complete(
    conn: sqlite3.Connection,
    job_id: str,
    lease_token: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """完成任务提交（需核验活动 lease_token 与租约未过期）。"""
    current = _now(now)
    verify_active_lease(conn, job_id, lease_token, now=current)

    cur = conn.execute(
        """
        UPDATE pulse_jobs
        SET status = 'completed',
            lease_token = NULL,
            lease_until = NULL,
            next_retry_at = NULL,
            error_code = NULL,
            error_summary = NULL,
            updated_at = ?
        WHERE job_id = ?
          AND status = 'running'
          AND lease_token = ?
          AND (lease_until IS NULL OR lease_until > ?)
        """,
        (current, job_id, lease_token, current),
    )
    if cur.rowcount != 1:
        raise PulseError("PULSE_LEASE_INVALID", "任务租约校验失败，无法标记完成。", status=409)
    return get_job(conn, job_id)


def fail_or_retry(
    conn: sqlite3.Connection,
    job_id: str,
    lease_token: str,
    error_code: str,
    error_summary: str = "",
    *,
    now: float | None = None,
    retryable: bool = True,
    base_backoff_seconds: float = 5.0,
    max_backoff_seconds: float = 300.0,
    retry_after_seconds: float | None = None,
    updated_base_world_revision: int | None = None,
    updated_target_world_minute: int | None = None,
) -> dict[str, Any]:
    """处理任务执行失败：若可重试且未达 max_attempts 则按指数退避回退至 pending，否则标记为 failed。"""
    current = _now(now)
    job = verify_active_lease(conn, job_id, lease_token, now=current)

    safe_code = sanitize_text(error_code or "PULSE_EXECUTION_FAILED", max_length=80) or "PULSE_EXECUTION_FAILED"
    safe_summary = sanitize_text(error_summary, max_length=240)
    attempts = int(job["attempts"])
    max_attempts = int(job["max_attempts"])

    new_base_rev = (
        updated_base_world_revision
        if updated_base_world_revision is not None
        else int(job["base_world_revision"])
    )
    new_target_min = (
        updated_target_world_minute
        if updated_target_world_minute is not None
        else int(job["target_world_minute"])
    )

    if retryable and attempts < max_attempts:
        if retry_after_seconds is not None:
            delay = max(0.0, float(retry_after_seconds))
        else:
            delay = min(float(max_backoff_seconds), float(base_backoff_seconds) * (2 ** max(0, attempts - 1)))
        next_retry = current + delay
        conn.execute(
            """
            UPDATE pulse_jobs
            SET status = 'pending',
                base_world_revision = ?,
                target_world_minute = ?,
                lease_token = NULL,
                lease_until = NULL,
                next_retry_at = ?,
                error_code = ?,
                error_summary = ?,
                updated_at = ?
            WHERE job_id = ? AND lease_token = ?
            """,
            (new_base_rev, new_target_min, next_retry, safe_code, safe_summary, current, job_id, lease_token),
        )
    else:
        conn.execute(
            """
            UPDATE pulse_jobs
            SET status = 'failed',
                lease_token = NULL,
                lease_until = NULL,
                next_retry_at = NULL,
                error_code = ?,
                error_summary = ?,
                updated_at = ?
            WHERE job_id = ? AND lease_token = ?
            """,
            (safe_code, safe_summary, current, job_id, lease_token),
        )
    return get_job(conn, job_id)


def fail(
    conn: sqlite3.Connection,
    job_id: str,
    lease_token: str,
    error_code: str,
    error_summary: str = "",
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """将当前持租任务直接标记为不可重试的 failed。"""
    return fail_or_retry(
        conn,
        job_id,
        lease_token,
        error_code,
        error_summary,
        now=now,
        retryable=False,
    )


def retry(
    conn: sqlite3.Connection,
    job_id: str,
    lease_token: str,
    error_code: str,
    error_summary: str = "",
    *,
    now: float | None = None,
    retry_after_seconds: float | None = None,
    updated_base_world_revision: int | None = None,
    updated_target_world_minute: int | None = None,
) -> dict[str, Any]:
    """将当前持租任务按退避策略放回 pending 等待重试。"""
    return fail_or_retry(
        conn,
        job_id,
        lease_token,
        error_code,
        error_summary,
        now=now,
        retryable=True,
        retry_after_seconds=retry_after_seconds,
        updated_base_world_revision=updated_base_world_revision,
        updated_target_world_minute=updated_target_world_minute,
    )


def wait_for_model(
    conn: sqlite3.Connection,
    job_id: str,
    lease_token: str,
    error_code: str = "MODEL_SESSION_UNAVAILABLE",
    error_summary: str = "等待用户连接或恢复模型会话。",
    *,
    now: float | None = None,
    consume_attempt: bool = False,
) -> dict[str, Any]:
    """将任务置为 waiting_for_model 状态（如重启后无 Key、会话过期或客户端离线）。

    默认不消耗失败重试次数（attempts 回退 1），以便用户重连模型后任务能够正常执行。
    """
    current = _now(now)
    job = verify_active_lease(conn, job_id, lease_token, now=current)
    safe_code = sanitize_text(error_code or "MODEL_SESSION_UNAVAILABLE", max_length=80)
    safe_summary = sanitize_text(error_summary, max_length=240)
    new_attempts = int(job["attempts"]) if consume_attempt else max(0, int(job["attempts"]) - 1)

    conn.execute(
        """
        UPDATE pulse_jobs
        SET status = 'waiting_for_model',
            attempts = ?,
            lease_token = NULL,
            lease_until = NULL,
            next_retry_at = NULL,
            error_code = ?,
            error_summary = ?,
            updated_at = ?
        WHERE job_id = ? AND lease_token = ?
        """,
        (new_attempts, safe_code, safe_summary, current, job_id, lease_token),
    )
    return get_job(conn, job_id)


def resume_waiting_jobs(
    conn: sqlite3.Connection,
    instance_id: str | None = None,
    *,
    now: float | None = None,
) -> int:
    """当模型会话重连或恢复在线时，将 waiting_for_model 任务唤醒为 pending。"""
    current = _now(now)
    if instance_id is not None:
        cur = conn.execute(
            """
            UPDATE pulse_jobs
            SET status = 'pending',
                next_retry_at = ?,
                updated_at = ?
            WHERE instance_id = ? AND status = 'waiting_for_model'
            """,
            (current, current, instance_id),
        )
    else:
        cur = conn.execute(
            """
            UPDATE pulse_jobs
            SET status = 'pending',
                next_retry_at = ?,
                updated_at = ?
            WHERE status = 'waiting_for_model'
            """,
            (current, current),
        )
    return cur.rowcount


def cancel(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    reason_code: str = "JOB_CANCELLED",
    reason_summary: str = "任务已取消。",
    now: float | None = None,
) -> dict[str, Any]:
    """取消指定任务（已 completed 的任务不可取消）。"""
    conn.row_factory = sqlite3.Row
    current = _now(now)
    job = get_job(conn, job_id)
    if job["status"] == "completed":
        raise PulseError(
            code="JOB_ALREADY_COMPLETED",
            message="已完成的脉动任务不能取消。",
            status=409,
            details={"job_id": job_id},
        )
    safe_code = sanitize_text(reason_code, max_length=80) or "JOB_CANCELLED"
    safe_summary = sanitize_text(reason_summary, max_length=240)
    conn.execute(
        """
        UPDATE pulse_jobs
        SET status = 'cancelled',
            lease_token = NULL,
            lease_until = NULL,
            next_retry_at = NULL,
            error_code = ?,
            error_summary = ?,
            updated_at = ?
        WHERE job_id = ? AND status != 'completed'
        """,
        (safe_code, safe_summary, current, job_id),
    )
    return get_job(conn, job_id)


def cancel_instance_jobs(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    include_running: bool = False,
    reason_code: str = "PULSE_PAUSED_OR_DISABLED",
    reason_summary: str = "世界脉动已暂停或关闭，待处理任务已取消。",
    now: float | None = None,
) -> int:
    """批量取消某个世界实例下尚未完成的排队任务。"""
    current = _now(now)
    safe_code = sanitize_text(reason_code, max_length=80) or "PULSE_PAUSED_OR_DISABLED"
    safe_summary = sanitize_text(reason_summary, max_length=240)
    target_statuses = ("pending", "waiting_for_model", "running") if include_running else ("pending", "waiting_for_model")
    placeholders = ", ".join("?" for _ in target_statuses)
    cur = conn.execute(
        f"""
        UPDATE pulse_jobs
        SET status = 'cancelled',
            lease_token = NULL,
            lease_until = NULL,
            next_retry_at = NULL,
            error_code = ?,
            error_summary = ?,
            updated_at = ?
        WHERE instance_id = ? AND status IN ({placeholders})
        """,
        (safe_code, safe_summary, current, instance_id, *target_statuses),
    )
    return cur.rowcount
