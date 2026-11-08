"""人工设定条目数据库迁移与幂等性测试。

依据：
- docs/collaboration/ROUND3.md、docs/collaboration/R3_GEMINI_MA.md
- docs/contracts/M1_R3_API.md §3
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timezone

try:
    from app.db import SCHEMA
    from app.lore_entries import (
        create_entry,
        ensure_lore_entries_schema,
        get_entry,
        list_entries,
        update_entry,
    )
except ImportError:
    from server.app.db import SCHEMA
    from server.app.lore_entries import (
        create_entry,
        ensure_lore_entries_schema,
        get_entry,
        list_entries,
        update_entry,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestLoreEntriesMigration(unittest.TestCase):
    def test_idempotent_migration_preserves_old_data(self) -> None:
        """测试在已有旧版世界、文档与切片的库上无损执行迁移，且重复运行幂等。"""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # 1. 模拟旧版 R2 数据库结构（仅 worlds, documents, chunks）
        conn.executescript(SCHEMA)

        now = _iso_now()
        world_id = "legacy_world_01"
        conn.execute(
            "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
            (world_id, "旧世界", "历史遗留数据", now, now, None),
        )

        doc_id = "legacy_doc_01"
        doc_text = "旧世界的重要历史文献正文。"
        conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                world_id,
                "历史文献.txt",
                "text/plain",
                len(doc_text.encode("utf-8")),
                "utf-8",
                "sha_legacy",
                doc_text,
                len(doc_text),
                1,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )

        chunk_id = "legacy_c_01"
        conn.execute(
            """INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                doc_id,
                world_id,
                0,
                json.dumps(["历史卷"], ensure_ascii=False),
                0,
                len(doc_text),
                doc_text,
            ),
        )
        conn.commit()

        # 2. 第一次执行迁移
        ensure_lore_entries_schema(conn)

        # 验证旧数据毫发无损
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM worlds WHERE id = ?", (world_id,))
        self.assertEqual(cur.fetchone()["name"], "旧世界")

        cur.execute("SELECT id, filename FROM documents WHERE id = ?", (doc_id,))
        self.assertEqual(cur.fetchone()["filename"], "历史文献.txt")

        cur.execute("SELECT id FROM chunks WHERE id = ?", (chunk_id,))
        self.assertEqual(cur.fetchone()["id"], chunk_id)

        # 3. 验证新业务在迁移后能正常工作
        entry = create_entry(
            conn,
            world_id,
            {
                "type": "faction",
                "title": "旧日遗民",
                "summary": "来自历史文献",
                "source_links": [
                    {
                        "document_id": doc_id,
                        "chunk_id": chunk_id,
                        "start_char": 0,
                        "end_char": 5,
                    }
                ],
            },
        )
        self.assertEqual(entry["title"], "旧日遗民")
        self.assertEqual(entry["revision"], 1)

        # 4. 第二次重复执行迁移（幂等性验证）
        ensure_lore_entries_schema(conn)

        # 验证已写入的条目依然完整可读
        entry_fetched = get_entry(conn, entry["id"])
        self.assertEqual(entry_fetched["id"], entry["id"])
        self.assertEqual(entry_fetched["title"], "旧日遗民")

        # 5. 第三次重复执行迁移（多重幂等性）
        ensure_lore_entries_schema(conn)

        entries_list = list_entries(conn, world_id)
        self.assertEqual(entries_list["total"], 1)

        conn.close()


if __name__ == "__main__":
    unittest.main()
