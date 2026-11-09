"""M3.2 世界脉动 SQLite 持久层表结构与幂等迁移。"""

from __future__ import annotations

import sqlite3

# 兼容独立测试：当底座尚未创建 world_instances 时，按 M3_WAVE1_API.md 契约建表；
# 若底座已创建该表，IF NOT EXISTS 保持无副作用。
WORLD_INSTANCES_COMPAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_instances (
    id           TEXT PRIMARY KEY,
    world_id     TEXT NOT NULL,
    adventure_id TEXT,
    world_minute INTEGER NOT NULL DEFAULT 0,
    revision     INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS pulse_instance_configs (
    instance_id              TEXT PRIMARY KEY REFERENCES world_instances(id),
    enabled                  INTEGER NOT NULL DEFAULT 0,
    paused                   INTEGER NOT NULL DEFAULT 0,
    complexity               TEXT NOT NULL DEFAULT 'standard',
    max_calls_per_round      INTEGER NOT NULL DEFAULT 2,
    max_tokens_per_round     INTEGER NOT NULL DEFAULT 4096,
    daily_call_budget        INTEGER NOT NULL DEFAULT 12,
    daily_token_budget       INTEGER NOT NULL DEFAULT 24000,
    cooldown_seconds         REAL NOT NULL DEFAULT 5.0,
    min_game_minute_interval INTEGER NOT NULL DEFAULT 0,
    defer_seconds            REAL NOT NULL DEFAULT 60.0,
    last_pulsed_minute       INTEGER NOT NULL DEFAULT 0,
    resume_anchor_minute     INTEGER NOT NULL DEFAULT 0,
    last_call_at             REAL,
    updated_at               REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pulse_jobs (
    job_id               TEXT PRIMARY KEY,
    instance_id          TEXT NOT NULL REFERENCES world_instances(id),
    idempotency_key      TEXT NOT NULL,
    kind                 TEXT NOT NULL CHECK (kind IN ('seed', 'advance')),
    trigger_adventure_id TEXT NOT NULL,
    trigger_revision     INTEGER NOT NULL,
    base_world_revision  INTEGER NOT NULL,
    target_world_minute  INTEGER NOT NULL,
    payload_json         TEXT NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL CHECK (
                             status IN (
                                 'pending',
                                 'running',
                                 'completed',
                                 'failed',
                                 'waiting_for_model',
                                 'cancelled'
                             )
                         ),
    attempts             INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 3,
    next_retry_at        REAL,
    lease_token          TEXT,
    lease_until          REAL,
    error_code           TEXT,
    error_summary        TEXT,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    UNIQUE (instance_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_pulse_jobs_claim
    ON pulse_jobs(status, next_retry_at, lease_until, created_at);

CREATE INDEX IF NOT EXISTS idx_pulse_jobs_instance
    ON pulse_jobs(instance_id, status, created_at);

CREATE TABLE IF NOT EXISTS pulse_job_keys (
    instance_id          TEXT NOT NULL REFERENCES world_instances(id),
    idempotency_key      TEXT NOT NULL,
    job_id               TEXT NOT NULL REFERENCES pulse_jobs(job_id),
    trigger_revision     INTEGER NOT NULL,
    target_world_minute  INTEGER NOT NULL,
    created_at           REAL NOT NULL,
    PRIMARY KEY (instance_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_pulse_job_keys_job
    ON pulse_job_keys(job_id);

CREATE TABLE IF NOT EXISTS pulse_usage_ledger (
    id                  TEXT PRIMARY KEY,
    instance_id        TEXT NOT NULL REFERENCES world_instances(id),
    job_id             TEXT,
    purpose            TEXT NOT NULL,
    model              TEXT NOT NULL,
    game_day           INTEGER NOT NULL,
    world_minute       INTEGER NOT NULL,
    calls              INTEGER NOT NULL DEFAULT 1,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens       INTEGER NOT NULL DEFAULT 0,
    usage_source       TEXT NOT NULL CHECK (usage_source IN ('actual', 'estimated')),
    potentially_billed INTEGER NOT NULL DEFAULT 1,
    outcome            TEXT NOT NULL DEFAULT 'succeeded',
    created_at         REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pulse_usage_instance_day
    ON pulse_usage_ledger(instance_id, game_day, created_at);
"""


def ensure_pulse_schema(conn: sqlite3.Connection, *, ensure_instance_table: bool = True) -> None:
    """幂等初始化世界脉动所需的 SQLite 表结构。

    注意：按照 M3 集成契约，本函数不自行调用 conn.commit()，事务由调用方控制。
    """
    if ensure_instance_table:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS world_instances (
                id           TEXT PRIMARY KEY,
                world_id     TEXT NOT NULL,
                adventure_id TEXT,
                world_minute INTEGER NOT NULL DEFAULT 0,
                revision     INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    for statement in SCHEMA.split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
