"""人工设定条目持久层 Schema 定义与幂等迁移。

契约与规范依据（ROUND3.md、M1_R3_API.md §3）：
1. 提供两张新表：lore_entries 与 lore_entry_sources；
2. 严格外键约束：world_id -> worlds(id)，entry_id -> lore_entries(id)，document_id -> documents(id)，chunk_id -> chunks(id)；
3. revision 乐观并发初值 1，更新递增；软删除标记 deleted_at；
4. 幂等建表与迁移：不 DROP 旧表，安全保留旧 worlds、documents、chunks 数据；
5. 提供 ensure_lore_entries_schema(conn) 供 DSH 在 db.py 或系统启动时无损调用。
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "1.0-r3"

LORE_ENTRIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS lore_entries (
    id                  TEXT PRIMARY KEY,
    world_id            TEXT NOT NULL REFERENCES worlds(id),
    type                TEXT NOT NULL,
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft',
    revision            INTEGER NOT NULL DEFAULT 1,
    origin              TEXT NOT NULL DEFAULT 'manual',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    deleted_at          TEXT
);

CREATE TABLE IF NOT EXISTS lore_entry_sources (
    id                  TEXT PRIMARY KEY,
    entry_id            TEXT NOT NULL REFERENCES lore_entries(id),
    world_id            TEXT NOT NULL,
    document_id         TEXT NOT NULL REFERENCES documents(id),
    chunk_id            TEXT NOT NULL REFERENCES chunks(id),
    start_char          INTEGER NOT NULL,
    end_char            INTEGER NOT NULL,
    excerpt             TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    deleted_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_lore_entries_world ON lore_entries(world_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_lore_entries_sort ON lore_entries(world_id, updated_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_lore_entry_sources_entry ON lore_entry_sources(entry_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_lore_entry_sources_doc ON lore_entry_sources(document_id);
CREATE INDEX IF NOT EXISTS idx_lore_entry_sources_chunk ON lore_entry_sources(chunk_id);
"""


def ensure_lore_entries_schema(conn: sqlite3.Connection) -> None:
    """幂等创建表和索引，并校验表结构。可安全重复执行多次。"""
    conn.executescript(LORE_ENTRIES_SCHEMA)
    _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """检查增量列以防未来补列需求，确保已有库幂等升级。"""
    # 检查 lore_entries 列
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(lore_entries)")
    cols = {row[1] for row in cur.fetchall()}
    if cols:
        if "revision" not in cols:
            conn.execute("ALTER TABLE lore_entries ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "origin" not in cols:
            conn.execute("ALTER TABLE lore_entries ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'")
        if "deleted_at" not in cols:
            conn.execute("ALTER TABLE lore_entries ADD COLUMN deleted_at TEXT")

    # 检查 lore_entry_sources 列
    cur.execute("PRAGMA table_info(lore_entry_sources)")
    source_cols = {row[1] for row in cur.fetchall()}
    if source_cols:
        if "excerpt" not in source_cols:
            conn.execute("ALTER TABLE lore_entry_sources ADD COLUMN excerpt TEXT NOT NULL DEFAULT ''")
        if "deleted_at" not in source_cols:
            conn.execute("ALTER TABLE lore_entry_sources ADD COLUMN deleted_at TEXT")
