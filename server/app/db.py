"""SQLite 持久层：建表、连接。

选型说明：本批只有 3 张表、1 种方言，直接用标准库 sqlite3，
不引入 SQLAlchemy/SQLModel —— 少一层依赖，schema 与 SQL 完全可控。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    deleted_at   TEXT
);

CREATE TABLE IF NOT EXISTS world_transfer_sources (
    world_id      TEXT PRIMARY KEY REFERENCES worlds(id),
    source_id     TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    local_digest  TEXT NOT NULL,
    id_map        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_transfer_source ON world_transfer_sources(source_id, source_digest);

CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    world_id        TEXT NOT NULL REFERENCES worlds(id),
    filename        TEXT NOT NULL,
    media_type      TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    encoding        TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    text            TEXT NOT NULL,
    text_length     INTEGER NOT NULL,
    line_count      INTEGER NOT NULL,
    chunk_count     INTEGER NOT NULL,
    source_kind     TEXT NOT NULL DEFAULT 'user_import',
    encoding_semantics TEXT NOT NULL DEFAULT 'source_bytes',
    warnings        TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'ready',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id),
    world_id     TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    heading_path TEXT NOT NULL,
    start_char   INTEGER NOT NULL,
    end_char     INTEGER NOT NULL,
    text         TEXT NOT NULL,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_documents_world ON documents(world_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(world_id, content_sha256);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, chunk_index);
"""


def init_db(path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        from .lore_entries.schema import ensure_lore_entries_schema

        ensure_lore_entries_schema(conn)
        from .adventures.service import SCHEMA as ADVENTURE_SCHEMA
        conn.executescript(ADVENTURE_SCHEMA)
        if 'character_json' not in {row[1] for row in conn.execute('PRAGMA table_info(adventures)')}:
            conn.execute('ALTER TABLE adventures ADD COLUMN character_json TEXT')
        for column in ('origin_checkpoint_id', 'restore_request_id'):
            if column not in {row[1] for row in conn.execute('PRAGMA table_info(adventures)')}:
                conn.execute(f'ALTER TABLE adventures ADD COLUMN {column} TEXT')
        from .adventures.checkpoints import SCHEMA as CHECKPOINT_SCHEMA
        conn.executescript(CHECKPOINT_SCHEMA)
        from .adventures.journeys import SCHEMA as JOURNEY_SCHEMA
        conn.executescript(JOURNEY_SCHEMA)
        from .adventures.opening_options import SCHEMA as START_REQUEST_SCHEMA
        conn.executescript(START_REQUEST_SCHEMA)
        if 'memory_cursor' not in {row[1] for row in conn.execute('PRAGMA table_info(adventure_checkpoints)')}:
            conn.execute('ALTER TABLE adventure_checkpoints ADD COLUMN memory_cursor INTEGER NOT NULL DEFAULT 0')
        if 'opening_json' not in {row[1] for row in conn.execute('PRAGMA table_info(adventures)')}:
            conn.execute('ALTER TABLE adventures ADD COLUMN opening_json TEXT')
        if 'advance_minutes' not in {row[1] for row in conn.execute('PRAGMA table_info(adventure_drafts)')}:
            conn.execute('ALTER TABLE adventure_drafts ADD COLUMN advance_minutes INTEGER')
        from .adventures.scenario import SCHEMA as SCENARIO_SCHEMA
        conn.executescript(SCENARIO_SCHEMA)
        from .adventures.memory import SCHEMA as MEMORY_SCHEMA
        conn.executescript(MEMORY_SCHEMA)
        from .adventures.item_descriptions import SCHEMA as ITEM_DESCRIPTION_SCHEMA
        conn.executescript(ITEM_DESCRIPTION_SCHEMA)
        if 'instance_snapshot' not in {row[1] for row in conn.execute('PRAGMA table_info(adventure_checkpoints)')}:
            conn.execute('ALTER TABLE adventure_checkpoints ADD COLUMN instance_snapshot TEXT')
        from .m3.core.schema import ensure_schema as ensure_m3_schema

        ensure_m3_schema(conn)
        from .m3.events.schema import ensure_events_schema
        from .m3.knowledge.schema import ensure_knowledge_schema
        from .m3.pulse.schema import ensure_pulse_schema

        ensure_events_schema(conn)
        ensure_knowledge_schema(conn)
        ensure_pulse_schema(conn, ensure_instance_table=False)
        from .m3.encounters.schema import ensure_encounters_schema
        from .m3.npcs.schema import ensure_npcs_schema
        from .m3.portability import ensure_portability_schema

        ensure_npcs_schema(conn)
        ensure_encounters_schema(conn)
        ensure_portability_schema(conn)
        from .m3.world_analysis.schema import ensure_world_analysis_schema

        ensure_world_analysis_schema(conn)
        conn.commit()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """幂等迁移：旧库补列，不重建表。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "encoding_semantics" not in cols:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN encoding_semantics TEXT NOT NULL DEFAULT 'source_bytes'"
        )
    if "warnings" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]'")


@contextmanager
def db(path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
