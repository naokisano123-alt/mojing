"""M3.1 / M3.5 世界事件账本 SQLite 持久层 Schema 与幂等迁移。

契约依据（`docs/contracts/M3_WAVE1_API.md`）：
- 实例根表约定：`world_instances(id, world_id, adventure_id, world_minute, revision)`
- 子表外键引用 `world_instances.id`，领域对象身份在实例内唯一（分支和导入可在新实例内复用领域对象 ID）。
- 提供 `ensure_events_schema(conn)` 供独立 fixture 测试与 DeepSeekHarness 在 `db.py` 中无损幂等调用。
"""

from __future__ import annotations

import sqlite3

EVENTS_SCHEMA_VERSION = "1.0-m3-wave1"

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_instances (
    id              TEXT PRIMARY KEY,
    world_id        TEXT NOT NULL DEFAULT '',
    adventure_id    TEXT,
    world_minute    INTEGER NOT NULL DEFAULT 0,
    revision        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS world_events (
    instance_id         TEXT NOT NULL REFERENCES world_instances(id),
    event_id            TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK(kind IN ('thread', 'incident')),
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    location            TEXT NOT NULL,
    scope               TEXT NOT NULL CHECK(scope IN ('local', 'regional', 'national', 'international')),
    participants_json   TEXT NOT NULL DEFAULT '[]',
    stage               TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('active', 'stalled', 'completed', 'failed')),
    created_minute      INTEGER NOT NULL,
    updated_minute      INTEGER NOT NULL,
    next_update_minute  INTEGER,
    cause_event_ids_json TEXT NOT NULL DEFAULT '[]',
    source_ids_json     TEXT NOT NULL DEFAULT '[]',
    turnpoint_type      TEXT,
    precondition_ids_json TEXT NOT NULL DEFAULT '[]',
    revision            INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (instance_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_world_events_instance_due
    ON world_events(instance_id, status, next_update_minute ASC, created_minute ASC, event_id ASC);

CREATE INDEX IF NOT EXISTS idx_world_events_instance_kind
    ON world_events(instance_id, kind, status, created_minute ASC, event_id ASC);

CREATE TABLE IF NOT EXISTS world_event_revisions (
    instance_id         TEXT NOT NULL REFERENCES world_instances(id),
    event_id            TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    operation           TEXT NOT NULL CHECK(operation IN ('create', 'update', 'restore')),
    world_minute        INTEGER NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    snapshot_json       TEXT NOT NULL,
    PRIMARY KEY (instance_id, event_id, revision),
    FOREIGN KEY (instance_id, event_id) REFERENCES world_events(instance_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_world_event_revisions_lookup
    ON world_event_revisions(instance_id, event_id, revision ASC);

CREATE TABLE IF NOT EXISTS world_event_consequences (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id),
    consequence_id          TEXT NOT NULL,
    source_event_id         TEXT NOT NULL,
    source_event_revision   INTEGER NOT NULL,
    impact_type             TEXT NOT NULL CHECK(impact_type IN ('direct', 'indirect', 'independent')),
    domain                  TEXT NOT NULL,
    target                  TEXT NOT NULL,
    summary                 TEXT NOT NULL,
    effective_minute        INTEGER NOT NULL,
    PRIMARY KEY (instance_id, consequence_id),
    FOREIGN KEY (instance_id, source_event_id) REFERENCES world_events(instance_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_world_event_consequences_event
    ON world_event_consequences(instance_id, source_event_id, effective_minute ASC, consequence_id ASC);

CREATE TABLE IF NOT EXISTS world_event_preconditions (
    instance_id         TEXT NOT NULL REFERENCES world_instances(id),
    precondition_id     TEXT NOT NULL,
    kind                TEXT NOT NULL,
    title               TEXT NOT NULL,
    participants_json   TEXT NOT NULL DEFAULT '[]',
    source_ids_json     TEXT NOT NULL DEFAULT '[]',
    cause_event_ids_json TEXT NOT NULL DEFAULT '[]',
    registered_minute   INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'satisfied' CHECK(status IN ('satisfied', 'revoked')),
    PRIMARY KEY (instance_id, precondition_id)
);

CREATE INDEX IF NOT EXISTS idx_world_event_preconditions_instance
    ON world_event_preconditions(instance_id, status, registered_minute ASC, precondition_id ASC);
"""


def ensure_events_schema(conn: sqlite3.Connection) -> None:
    """幂等创建世界事件账本相关表、索引并补齐增量列。

    注意：本函数不主动调用 `conn.commit()`，事务交由外层调用方控制。
    """
    # executescript 会在执行前隐式提交事务，因此逐条执行 DDL 以保持调用方事务边界
    for statement in _split_sql_statements(EVENTS_SCHEMA):
        conn.execute(statement)
    _ensure_columns(conn)


def _split_sql_statements(sql_script: str) -> list[str]:
    statements: list[str] = []
    for raw in sql_script.split(";"):
        stmt = raw.strip()
        if stmt:
            statements.append(stmt)
    return statements


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """幂等检查并补齐可能缺失的增量列，保证旧库平滑升级。"""
    event_cols = {row[1] for row in conn.execute("PRAGMA table_info(world_events)").fetchall()}
    if event_cols:
        if "turnpoint_type" not in event_cols:
            conn.execute("ALTER TABLE world_events ADD COLUMN turnpoint_type TEXT")
        if "precondition_ids_json" not in event_cols:
            conn.execute(
                "ALTER TABLE world_events ADD COLUMN precondition_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
