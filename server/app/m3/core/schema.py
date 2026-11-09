"""M3.0 世界实例根：一张表 + 一个版本位。

契约见 docs/contracts/M3_WAVE1_API.md：
- world_instances(id, world_id, adventure_id, world_minute, revision)
- 一个正式旅程一个实例；M0 无实例
- world_revision 与 adventure_revision 分离
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "m3-wave1/1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_instances (
    id            TEXT PRIMARY KEY,
    world_id      TEXT NOT NULL REFERENCES worlds(id),
    adventure_id  TEXT UNIQUE REFERENCES adventures(id),
    world_minute  INTEGER NOT NULL DEFAULT 0,
    revision      INTEGER NOT NULL DEFAULT 0,
    canon_version TEXT,
    created_at    REAL NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_world_instances_world ON world_instances(world_id);
CREATE TABLE IF NOT EXISTS m3_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS m3_model_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose    TEXT NOT NULL,
    calls      INTEGER NOT NULL,
    tokens     INTEGER NOT NULL,
    game_day   TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .service import create_instance

    """幂等建表 + 旧旅程补实例。重复调用不产生重复行。"""
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute('PRAGMA table_info(world_instances)')}
    if 'revision' not in columns and 'world_revision' in columns:
        # 旧版 DSH 分支用 world_revision 命名，按契约统一改成 revision
        conn.execute("ALTER TABLE world_instances ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE world_instances SET revision = world_revision")
    conn.execute(
        "INSERT OR REPLACE INTO m3_schema_meta(key, value) VALUES('version', ?)",
        (SCHEMA_VERSION,),
    )
    missing = conn.execute(
        """
        SELECT a.id AS adventure_id, a.world_id AS world_id, a.revision AS revision
        FROM adventures a
        LEFT JOIN world_instances i ON i.adventure_id = a.id
        WHERE i.id IS NULL
        """
    ).fetchall()
    for row in missing:
        create_instance(
            conn,
            row["world_id"] if hasattr(row, "keys") else row[1],
            adventure_id=row["adventure_id"] if hasattr(row, "keys") else row[0],
            world_revision=int(row["revision"] if hasattr(row, "keys") else row[2]),
        )