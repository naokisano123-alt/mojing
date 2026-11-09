"""M3.3 人物知识与信息传播 SQLite 表结构与幂等迁移。"""

from __future__ import annotations

import sqlite3

INSTANCE_FALLBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_instances (
    id           TEXT PRIMARY KEY,
    world_id     TEXT NOT NULL,
    adventure_id TEXT,
    world_minute INTEGER NOT NULL DEFAULT 0,
    revision     INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS m3_knowledge_holders (
    instance_id  TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    holder_id    TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'npc',
    location     TEXT NOT NULL DEFAULT '',
    created_at_minute INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, holder_id)
);

CREATE TABLE IF NOT EXISTS m3_knowledge_event_stubs (
    instance_id        TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    event_id           TEXT NOT NULL,
    kind               TEXT NOT NULL DEFAULT 'thread',
    title              TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    location           TEXT NOT NULL DEFAULT '',
    scope              TEXT NOT NULL DEFAULT 'local',
    participants_json  TEXT NOT NULL DEFAULT '[]',
    stage              TEXT NOT NULL DEFAULT 'initial',
    status             TEXT NOT NULL DEFAULT 'active',
    created_minute     INTEGER NOT NULL DEFAULT 0,
    updated_minute     INTEGER NOT NULL DEFAULT 0,
    next_update_minute INTEGER,
    revision           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (instance_id, event_id)
);

CREATE TABLE IF NOT EXISTS m3_knowledge_facts (
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

CREATE INDEX IF NOT EXISTS idx_m3_knowledge_holder_lookup
    ON m3_knowledge_facts(instance_id, holder_id, status, holder_seq DESC);

CREATE INDEX IF NOT EXISTS idx_m3_knowledge_relay_lookup
    ON m3_knowledge_facts(instance_id, holder_id, source_event_id, status, learned_minute);

CREATE TABLE IF NOT EXISTS m3_knowledge_history (
    instance_id      TEXT NOT NULL REFERENCES world_instances(id) ON DELETE CASCADE,
    history_seq      INTEGER NOT NULL,
    fact_id          TEXT NOT NULL,
    holder_id        TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    op               TEXT NOT NULL,
    knowledge_type   TEXT NOT NULL,
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    source_event_id  TEXT NOT NULL,
    source_person_id TEXT,
    source_fact_id   TEXT,
    channel          TEXT NOT NULL,
    learned_minute   INTEGER NOT NULL,
    confidence       REAL NOT NULL,
    status           TEXT NOT NULL,
    reason           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (instance_id, history_seq)
);

CREATE INDEX IF NOT EXISTS idx_m3_knowledge_history_fact
    ON m3_knowledge_history(instance_id, fact_id, revision);
"""

_REQUIRED_TABLES = frozenset({
    "world_instances",
    "m3_knowledge_holders",
    "m3_knowledge_event_stubs",
    "m3_knowledge_facts",
    "m3_knowledge_history",
})


def _exec_ddl_statements(conn: sqlite3.Connection, script: str) -> None:
    """逐条执行 DDL，不使用 executescript 以避免隐式提交破坏外层事务或 SAVEPOINT。"""
    for raw_stmt in script.split(";"):
        stmt = raw_stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_knowledge_schema(conn: sqlite3.Connection) -> None:
    """幂等创建 M3 知识与传播所需的表和索引，不提交外部事务、不破坏 SAVEPOINT。"""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if _REQUIRED_TABLES <= tables:
        return
    if "world_instances" not in tables:
        _exec_ddl_statements(conn, INSTANCE_FALLBACK_SCHEMA)
    _exec_ddl_statements(conn, SCHEMA)
