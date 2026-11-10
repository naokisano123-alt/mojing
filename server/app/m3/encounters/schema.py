"""M3.3 / M3.6 玩家主动查证、交谈与消息后续行动 SQLite 表结构与幂等迁移（M3 Wave 3）。"""

from __future__ import annotations

import sqlite3

INSTANCE_FALLBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_instances (
    id           TEXT PRIMARY KEY,
    world_id     TEXT NOT NULL DEFAULT '',
    adventure_id TEXT,
    world_minute INTEGER NOT NULL DEFAULT 0,
    revision     INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS m3_encounter_npc_stubs (
    instance_id          TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    npc_id               TEXT NOT NULL,
    name                 TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT '',
    location             TEXT NOT NULL,
    current_activity     TEXT NOT NULL DEFAULT '',
    current_action_kind  TEXT NOT NULL DEFAULT 'idle',
    schedule_slot        TEXT NOT NULL DEFAULT 'day_shift',
    contactable          INTEGER NOT NULL DEFAULT 1 CHECK (contactable IN (0, 1)),
    response_policy      TEXT NOT NULL DEFAULT 'cooperative'
                         CHECK (response_policy IN ('cooperative', 'reluctant', 'evasive')),
    refusal_reason       TEXT NOT NULL DEFAULT '',
    updated_minute       INTEGER NOT NULL DEFAULT 0,
    next_due_minute      INTEGER,
    revision             INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    PRIMARY KEY (instance_id, npc_id)
);

CREATE INDEX IF NOT EXISTS idx_m3_encounter_npc_loc
    ON m3_encounter_npc_stubs(instance_id, location, contactable);

CREATE TABLE IF NOT EXISTS m3_encounter_knowledge_stubs (
    instance_id      TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    fact_id          TEXT NOT NULL,
    holder_id        TEXT NOT NULL,
    holder_seq       INTEGER NOT NULL,
    sequence         INTEGER NOT NULL,
    knowledge_type   TEXT NOT NULL CHECK (knowledge_type IN ('confirmed', 'suspected', 'rumor', 'mistaken')),
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    source_event_id  TEXT NOT NULL,
    source_person_id TEXT,
    source_fact_id   TEXT,
    channel          TEXT NOT NULL CHECK (channel IN ('observed', 'notice', 'conversation', 'rumor')),
    learned_minute   INTEGER NOT NULL CHECK (learned_minute >= 0),
    confidence       REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'retracted')),
    revision         INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    PRIMARY KEY (instance_id, fact_id),
    UNIQUE (instance_id, holder_id, holder_seq),
    UNIQUE (instance_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_m3_encounter_knowledge_holder
    ON m3_encounter_knowledge_stubs(instance_id, holder_id, status, learned_minute ASC, holder_seq DESC);

CREATE TABLE IF NOT EXISTS m3_encounter_notices (
    instance_id      TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    notice_id        TEXT NOT NULL,
    location         TEXT NOT NULL,
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    source_event_id  TEXT NOT NULL,
    knowledge_type   TEXT NOT NULL DEFAULT 'confirmed'
                     CHECK (knowledge_type IN ('confirmed', 'suspected', 'rumor', 'mistaken')),
    posted_minute    INTEGER NOT NULL DEFAULT 0 CHECK (posted_minute >= 0),
    confidence       REAL NOT NULL DEFAULT 0.9 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retracted')),
    PRIMARY KEY (instance_id, notice_id)
);

CREATE INDEX IF NOT EXISTS idx_m3_encounter_notices_loc
    ON m3_encounter_notices(instance_id, location, status, posted_minute DESC);

CREATE TABLE IF NOT EXISTS m3_encounter_drafts (
    instance_id                 TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    draft_id                    TEXT NOT NULL,
    adventure_id                TEXT NOT NULL DEFAULT '',
    origin_fact_id              TEXT NOT NULL,
    option_kind                 TEXT NOT NULL CHECK (option_kind IN ('ask_npc', 'check_notice', 'free_edit')),
    target_npc_id               TEXT,
    player_location             TEXT NOT NULL,
    action_text                 TEXT NOT NULL,
    outcome                     TEXT NOT NULL
                                CHECK (outcome IN ('clue', 'refused', 'vague', 'conflicting', 'unknown', 'npc_departed')),
    reason_code                 TEXT NOT NULL,
    narrative_reply             TEXT NOT NULL,
    conflict_summary            TEXT,
    conflicting_fact_ids_json   TEXT NOT NULL DEFAULT '[]',
    transmission_proposals_json TEXT NOT NULL DEFAULT '[]',
    base_world_revision         INTEGER NOT NULL DEFAULT 0,
    draft_world_minute          INTEGER NOT NULL DEFAULT 0,
    status                      TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'committed', 'discarded')),
    committed_minute            INTEGER,
    revision                    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (instance_id, draft_id)
);

CREATE TABLE IF NOT EXISTS m3_encounter_history (
    instance_id             TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    history_seq             INTEGER NOT NULL,
    draft_id                TEXT NOT NULL,
    origin_fact_id          TEXT NOT NULL,
    option_kind             TEXT NOT NULL,
    target_npc_id           TEXT,
    player_location         TEXT NOT NULL,
    action_text             TEXT NOT NULL,
    outcome                 TEXT NOT NULL,
    world_minute            INTEGER NOT NULL,
    applied_fact_ids_json   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (instance_id, history_seq)
);
"""

_REQUIRED_TABLES = frozenset(
    {
        "world_instances",
        "m3_encounter_npc_stubs",
        "m3_encounter_knowledge_stubs",
        "m3_encounter_notices",
        "m3_encounter_drafts",
        "m3_encounter_history",
    }
)


def _exec_ddl_statements(conn: sqlite3.Connection, script: str) -> None:
    """逐条执行 DDL，不使用 executescript 以避免隐式提交外层事务或破坏 SAVEPOINT。"""
    for raw_stmt in script.split(";"):
        stmt = raw_stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_encounters_schema(conn: sqlite3.Connection) -> None:
    """幂等创建玩家主动查证与交谈模块所需的表与索引，不提交外部事务、不破坏 SAVEPOINT。"""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if _REQUIRED_TABLES <= tables:
        return
    if "world_instances" not in tables:
        _exec_ddl_statements(conn, INSTANCE_FALLBACK_SCHEMA)
    _exec_ddl_statements(conn, SCHEMA)
