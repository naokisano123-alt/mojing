"""M3.4 NPC 自主行动、行程与模拟深度 SQLite 持久层 Schema 与幂等迁移（M3 Wave 3）。

设计约束：
1. 实例根表复用 `world_instances(id, world_id, adventure_id, world_minute, revision)`。
2. 所有 NPC 表均以 `instance_id` 隔离，主键包含 `instance_id`，支持分支恢复时在新实例内复用稳定 `npc_id`。
3. `ensure_npcs_schema(conn)` 逐条执行 DDL，不使用 `executescript`，绝不隐式提交外层事务或破坏 `SAVEPOINT`。
"""

from __future__ import annotations

import sqlite3

NPCS_SCHEMA_VERSION = "1.0-m3-wave3"

NPCS_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_instances (
    id              TEXT PRIMARY KEY,
    world_id        TEXT NOT NULL DEFAULT '',
    adventure_id    TEXT,
    world_minute    INTEGER NOT NULL DEFAULT 0,
    revision        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS m3_npcs (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    npc_id                  TEXT NOT NULL,
    name                    TEXT NOT NULL,
    role                    TEXT NOT NULL,
    faction                 TEXT NOT NULL DEFAULT '',
    home_location           TEXT NOT NULL DEFAULT '',
    location                TEXT NOT NULL,
    origin_location         TEXT NOT NULL DEFAULT '',
    destination_location    TEXT NOT NULL DEFAULT '',
    current_activity        TEXT NOT NULL,
    current_action_kind     TEXT NOT NULL CHECK(current_action_kind IN ('travel', 'conversation_plan', 'shift_change', 'task_progress')),
    motivation              TEXT NOT NULL,
    goal                    TEXT NOT NULL DEFAULT '',
    depth_tier              TEXT NOT NULL DEFAULT 'background' CHECK(depth_tier IN ('foreground', 'associated', 'background')),
    depth_reason            TEXT NOT NULL DEFAULT '',
    shift_name              TEXT NOT NULL DEFAULT '',
    availability            TEXT NOT NULL DEFAULT 'available' CHECK(availability IN ('available', 'in_transit', 'busy', 'off_duty')),
    counterpart_npc_id      TEXT NOT NULL DEFAULT '',
    conversation_topic      TEXT NOT NULL DEFAULT '',
    current_task_id         TEXT NOT NULL DEFAULT '',
    cause_event_ids_json    TEXT NOT NULL DEFAULT '[]',
    cause_fact_ids_json     TEXT NOT NULL DEFAULT '[]',
    source_ids_json         TEXT NOT NULL DEFAULT '[]',
    action_meta_json        TEXT NOT NULL DEFAULT '{}',
    status                  TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'inactive')),
    created_minute          INTEGER NOT NULL DEFAULT 0,
    updated_minute          INTEGER NOT NULL DEFAULT 0,
    last_action_minute      INTEGER NOT NULL DEFAULT 0,
    next_due_minute         INTEGER,
    revision                INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    PRIMARY KEY (instance_id, npc_id)
);

CREATE INDEX IF NOT EXISTS idx_m3_npcs_instance_due
    ON m3_npcs(instance_id, status, next_due_minute ASC, depth_tier ASC, npc_id ASC);

CREATE INDEX IF NOT EXISTS idx_m3_npcs_instance_location
    ON m3_npcs(instance_id, location, status, npc_id ASC);

CREATE TABLE IF NOT EXISTS m3_npc_tasks (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    npc_id                  TEXT NOT NULL,
    task_id                 TEXT NOT NULL,
    title                   TEXT NOT NULL,
    stage                   TEXT NOT NULL,
    progress_percent        INTEGER NOT NULL DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100),
    status                  TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'blocked')),
    motivation              TEXT NOT NULL DEFAULT '',
    cause_event_ids_json    TEXT NOT NULL DEFAULT '[]',
    cause_fact_ids_json     TEXT NOT NULL DEFAULT '[]',
    source_ids_json         TEXT NOT NULL DEFAULT '[]',
    created_minute          INTEGER NOT NULL DEFAULT 0,
    updated_minute          INTEGER NOT NULL DEFAULT 0,
    revision                INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    PRIMARY KEY (instance_id, npc_id, task_id),
    FOREIGN KEY (instance_id, npc_id) REFERENCES m3_npcs(instance_id, npc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_m3_npc_tasks_lookup
    ON m3_npc_tasks(instance_id, npc_id, status, updated_minute DESC);

CREATE TABLE IF NOT EXISTS m3_npc_action_log (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    action_seq              INTEGER NOT NULL,
    action_id               TEXT NOT NULL,
    npc_id                  TEXT NOT NULL,
    npc_revision            INTEGER NOT NULL,
    op                      TEXT NOT NULL CHECK(op IN ('seed_npc', 'act', 'restore')),
    action_kind             TEXT NOT NULL CHECK(action_kind IN ('travel', 'conversation_plan', 'shift_change', 'task_progress')),
    from_location           TEXT NOT NULL DEFAULT '',
    to_location             TEXT NOT NULL DEFAULT '',
    current_activity        TEXT NOT NULL,
    motivation              TEXT NOT NULL,
    depth_tier              TEXT NOT NULL,
    depth_reason            TEXT NOT NULL DEFAULT '',
    world_minute            INTEGER NOT NULL,
    next_due_minute         INTEGER,
    cause_event_ids_json    TEXT NOT NULL DEFAULT '[]',
    cause_fact_ids_json     TEXT NOT NULL DEFAULT '[]',
    source_ids_json         TEXT NOT NULL DEFAULT '[]',
    details_json            TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (instance_id, action_seq),
    UNIQUE (instance_id, action_id)
);

CREATE INDEX IF NOT EXISTS idx_m3_npc_action_log_npc
    ON m3_npc_action_log(instance_id, npc_id, action_seq ASC);

CREATE TABLE IF NOT EXISTS m3_npc_applied_jobs (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    idempotency_key         TEXT NOT NULL,
    world_minute            INTEGER NOT NULL,
    world_revision          INTEGER NOT NULL,
    result_json             TEXT NOT NULL,
    PRIMARY KEY (instance_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS m3_npc_event_stubs (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    event_id                TEXT NOT NULL,
    kind                    TEXT NOT NULL DEFAULT 'thread',
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    location                TEXT NOT NULL DEFAULT '',
    scope                   TEXT NOT NULL DEFAULT 'local',
    participants_json       TEXT NOT NULL DEFAULT '[]',
    stage                   TEXT NOT NULL DEFAULT 'active',
    status                  TEXT NOT NULL DEFAULT 'active',
    created_minute          INTEGER NOT NULL DEFAULT 0,
    updated_minute          INTEGER NOT NULL DEFAULT 0,
    next_update_minute      INTEGER,
    revision                INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (instance_id, event_id)
);

CREATE TABLE IF NOT EXISTS m3_npc_knowledge_stubs (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    fact_id                 TEXT NOT NULL,
    holder_id               TEXT NOT NULL,
    knowledge_type          TEXT NOT NULL DEFAULT 'confirmed',
    title                   TEXT NOT NULL DEFAULT '',
    content                 TEXT NOT NULL DEFAULT '',
    source_event_id         TEXT NOT NULL DEFAULT '',
    learned_minute          INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (instance_id, fact_id)
);
"""


def _split_sql_statements(sql_script: str) -> list[str]:
    statements: list[str] = []
    for raw in sql_script.split(";"):
        stmt = raw.strip()
        if stmt:
            statements.append(stmt)
    return statements


def ensure_npcs_schema(conn: sqlite3.Connection) -> None:
    """幂等创建 NPC 自主行动、任务与日志表结构，不提交外层事务、不破坏 SAVEPOINT。"""
    for statement in _split_sql_statements(NPCS_SCHEMA):
        conn.execute(statement)
