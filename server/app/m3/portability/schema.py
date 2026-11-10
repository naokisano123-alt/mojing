"""M3 Wave 3 可审计历史来源墓碑与迁移溯源扩展表 Schema（幂等创建，不破坏外层事务）。

契约依据（`docs/collaboration/M3_WAVE3_GEMINI_LI.md`）：
1. 为已软删除但被历史事件、前置条件或检查点引用的来源提供专门的可审计历史引用表 `m3_historical_source_refs`：
   - 独立于 `documents`、`chunks`、`lore_entries` 表存储；
   - 绝对不向 `documents`、`chunks`、`lore_entries` 插入已删除记录，因此历史来源永远不会恢复为可搜索条目，也绝不会被 `events.select_canon_sources` 或叙事检索误用为当前活跃 Canon；
   - 显式携带 `selectable_as_canon = 0`、`searchable = 0`、`status = 'deleted_historical'` 与删除时间戳及标题摘要，供历史事件因果审计与多次再导出/再导入使用。
"""

from __future__ import annotations

import sqlite3

PORTABILITY_SCHEMA_VERSION = "1.0-m3-wave3"

PORTABILITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS m3_historical_source_refs (
    world_id            TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    source_id           TEXT NOT NULL,
    original_source_id  TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK(kind IN ('chunk', 'entry')),
    title               TEXT NOT NULL DEFAULT '',
    document_filename   TEXT NOT NULL DEFAULT '',
    entry_type          TEXT NOT NULL DEFAULT '',
    deleted_at          TEXT NOT NULL DEFAULT '',
    content_sha256      TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'deleted_historical'
                        CHECK(status IN ('deleted_historical')),
    selectable_as_canon INTEGER NOT NULL DEFAULT 0 CHECK(selectable_as_canon = 0),
    searchable          INTEGER NOT NULL DEFAULT 0 CHECK(searchable = 0),
    created_at          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (world_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_m3_historical_source_refs_world
    ON m3_historical_source_refs(world_id, kind, source_id ASC);
"""


def ensure_portability_schema(conn: sqlite3.Connection) -> None:
    """幂等创建 `m3_historical_source_refs` 表及索引，不使用 `executescript` 以免隐式提交外层事务。"""
    for raw in PORTABILITY_SCHEMA.split(";"):
        stmt = raw.strip()
        if stmt:
            conn.execute(stmt)
