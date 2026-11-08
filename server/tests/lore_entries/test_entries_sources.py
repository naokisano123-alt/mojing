"""人工设定条目来源引用校验、Emoji 边界与生命周期测试。

依据：
- docs/collaboration/ROUND3.md、docs/collaboration/R3_GEMINI_MA.md
- docs/contracts/M1_R3_API.md §1.2、§1.5
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timezone

try:
    from app.db import SCHEMA
    from app.lore_entries import (
        LoreEntryValidationError,
        LoreEntrySourceChunkMismatchError,
        LoreEntrySourceCrossWorldError,
        LoreEntrySourceDeletedError,
        LoreEntrySourceInvalidOffsetError,
        LoreEntrySourceOutOfRangeError,
        create_entry,
        ensure_lore_entries_schema,
        get_entry,
        u16len,
        update_entry,
    )
except ImportError:
    from server.app.db import SCHEMA
    from server.app.lore_entries import (
        LoreEntryValidationError,
        LoreEntrySourceChunkMismatchError,
        LoreEntrySourceCrossWorldError,
        LoreEntrySourceDeletedError,
        LoreEntrySourceInvalidOffsetError,
        LoreEntrySourceOutOfRangeError,
        create_entry,
        ensure_lore_entries_schema,
        get_entry,
        u16len,
        update_entry,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestLoreEntriesSources(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        ensure_lore_entries_schema(self.conn)

        self.world_a = "world_alpha"
        self.world_b = "world_beta"
        now = _iso_now()

        # 建立世界 A 和 B
        self.conn.execute(
            "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
            (self.world_a, "雾港世界", "描述A", now, now, None),
        )
        self.conn.execute(
            "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
            (self.world_b, "荒原世界", "描述B", now, now, None),
        )

        # 在世界 A 中导入文档 1（普通设定）
        self.doc_1_id = "doc_a_01"
        self.doc_1_text = (
            "第一幕 街角相逢\n"
            "陆平握着带鞘短刀，站在红枫酒馆前。\n"
            "酒馆老板娘苏晚推开木门，向外张望。"
        )
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.doc_1_id,
                self.world_a,
                "雾港风物志.txt",
                "text/plain",
                len(self.doc_1_text.encode("utf-8")),
                "utf-8",
                "sha_01",
                self.doc_1_text,
                u16len(self.doc_1_text),
                3,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.chunk_1_id = "c_a_01_01"
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.chunk_1_id,
                self.doc_1_id,
                self.world_a,
                0,
                json.dumps(["雾港", "第一幕"], ensure_ascii=False),
                0,
                u16len(self.doc_1_text),
                self.doc_1_text,
            ),
        )

        # 在世界 A 中导入文档 2（包含 4 字节 Emoji 与代理对）
        self.doc_emoji_id = "doc_a_emoji"
        self.doc_emoji_text = (
            "秘宝典籍：\n"
            "神龙吐息 🐉 在古堡 🏰 之中盘旋，闪耀着金色符文。"
        )
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.doc_emoji_id,
                self.world_a,
                "秘宝.txt",
                "text/plain",
                len(self.doc_emoji_text.encode("utf-8")),
                "utf-8",
                "sha_emoji",
                self.doc_emoji_text,
                u16len(self.doc_emoji_text),
                2,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.chunk_emoji_id = "c_a_emoji_01"
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.chunk_emoji_id,
                self.doc_emoji_id,
                self.world_a,
                0,
                json.dumps(["秘宝"], ensure_ascii=False),
                0,
                u16len(self.doc_emoji_text),
                self.doc_emoji_text,
            ),
        )

        # 在世界 B 中导入文档（用于跨世界隔离拦截）
        self.doc_b_id = "doc_b_01"
        self.doc_b_text = "荒原守护者索尔。"
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.doc_b_id,
                self.world_b,
                "荒原人物.txt",
                "text/plain",
                100,
                "utf-8",
                "sha_b",
                self.doc_b_text,
                u16len(self.doc_b_text),
                1,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.chunk_b_id = "c_b_01_01"
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.chunk_b_id,
                self.doc_b_id,
                self.world_b,
                0,
                json.dumps(["荒原"], ensure_ascii=False),
                0,
                u16len(self.doc_b_text),
                self.doc_b_text,
            ),
        )

        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_source_cross_world_rejected(self) -> None:
        """测试跨世界引用拦截：世界 A 的条目引用世界 B 的文档必报 422 SOURCE_CROSS_WORLD。"""
        with self.assertRaises(LoreEntrySourceCrossWorldError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "非法条目",
                    "source_links": [
                        {
                            "document_id": self.doc_b_id,
                            "chunk_id": self.chunk_b_id,
                            "start_char": 0,
                            "end_char": 5,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "SOURCE_CROSS_WORLD")

    def test_source_already_deleted_doc_rejected(self) -> None:
        """测试引用已软删除的文档必报 422 SOURCE_DELETED。"""
        self.conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (_iso_now(), self.doc_1_id),
        )
        self.conn.commit()

        with self.assertRaises(LoreEntrySourceDeletedError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "尝试引用已删文档",
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": 0,
                            "end_char": 5,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "SOURCE_DELETED")

    def test_source_chunk_mismatch_rejected(self) -> None:
        """测试切片 ID 与文档不匹配必报 422 SOURCE_CHUNK_MISMATCH。"""
        with self.assertRaises(LoreEntrySourceChunkMismatchError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "切片不属于文档",
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_emoji_id,  # 传入了另一篇文档的 chunk
                            "start_char": 0,
                            "end_char": 5,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "SOURCE_CHUNK_MISMATCH")

    def test_source_out_of_range_rejected(self) -> None:
        """测试区间非法（负数、反向、超出切片长度）必报 422 SOURCE_OUT_OF_RANGE。"""
        # 反向 start >= end
        with self.assertRaises(LoreEntrySourceOutOfRangeError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "区间反向",
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": 10,
                            "end_char": 5,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.code, "SOURCE_OUT_OF_RANGE")

        # 越界 (超出切片总 UTF-16 长度)
        c_len = u16len(self.doc_1_text)
        with self.assertRaises(LoreEntrySourceOutOfRangeError):
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "区间越界",
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": 0,
                            "end_char": c_len + 10,
                        }
                    ],
                },
            )

    def test_emoji_surrogate_pairs_boundary_validation(self) -> None:
        """测试包含 Emoji 时的 UTF-16 边界：合法则成功提取，切断代理对则报 422 SOURCE_INVALID_OFFSET。"""
        # text: "秘宝典籍：\n神龙吐息 🐉 在古堡 🏰 之中盘旋，闪耀着金色符文。"
        # 计算 '🐉' 的 UTF-16 起止坐标
        prefix = "秘宝典籍：\n神龙吐息 "
        dragon_start = u16len(prefix)  # 6 + 1 + 5 = 12
        dragon_end = dragon_start + 2  # '🐉' 占 2 个 UTF-16 units = 14

        # 1. 合法区间：包含整条神龙
        entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "character",
                "title": "神龙",
                "summary": "来自秘宝典籍",
                "source_links": [
                    {
                        "document_id": self.doc_emoji_id,
                        "chunk_id": self.chunk_emoji_id,
                        "start_char": dragon_start,
                        "end_char": dragon_end,
                    }
                ],
            },
        )
        self.assertEqual(len(entry["source_links"]), 1)
        self.assertEqual(entry["source_links"][0]["excerpt"], "🐉")
        self.assertEqual(entry["source_links"][0]["availability"], "active")

        # 2. 非法切断代理对：start_char 落在代理对中间 (dragon_start + 1)
        with self.assertRaises(LoreEntrySourceInvalidOffsetError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "切断代理对起点",
                    "source_links": [
                        {
                            "document_id": self.doc_emoji_id,
                            "chunk_id": self.chunk_emoji_id,
                            "start_char": dragon_start + 1,
                            "end_char": dragon_end + 3,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "SOURCE_INVALID_OFFSET")

        # 3. 非法切断代理对：end_char 落在代理对中间 (dragon_start + 1)
        with self.assertRaises(LoreEntrySourceInvalidOffsetError):
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "切断代理对终点",
                    "source_links": [
                        {
                            "document_id": self.doc_emoji_id,
                            "chunk_id": self.chunk_emoji_id,
                            "start_char": 0,
                            "end_char": dragon_start + 1,
                        }
                    ],
                },
            )

    def test_document_soft_delete_lifecycle_and_needs_source_review(self) -> None:
        """文档软删后的来源生命周期：
        - 条目本身保留，不物理删除用户手写条目；
        - source_links 派生 availability: 'deleted'；
        - confirmed 条目派生 needs_source_review: True；
        - draft 条目 needs_source_review 保持 False；
        - 无来源条目不受影响。
        """
        # 创建一个 confirmed 状态的条目
        confirmed_entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "character",
                "title": "陆平",
                "summary": "佩刀剑客",
                "status": "confirmed",
                "source_links": [
                    {
                        "document_id": self.doc_1_id,
                        "chunk_id": self.chunk_1_id,
                        "start_char": 0,
                        "end_char": 10,
                    }
                ],
            },
        )
        self.assertFalse(confirmed_entry["needs_source_review"])
        self.assertEqual(confirmed_entry["source_links"][0]["availability"], "active")

        # 创建一个 draft 状态的条目
        draft_entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "character",
                "title": "草稿陆平",
                "status": "draft",
                "source_links": [
                    {
                        "document_id": self.doc_1_id,
                        "chunk_id": self.chunk_1_id,
                        "start_char": 0,
                        "end_char": 10,
                    }
                ],
            },
        )

        # 创建一个无来源的 manual 条目
        manual_entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "rule",
                "title": "无引用规则",
                "status": "confirmed",
                "source_links": [],
            },
        )

        # 软删除来源文档 doc_1
        self.conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (_iso_now(), self.doc_1_id),
        )
        self.conn.commit()

        # 1. 验证 confirmed_entry：保留条目，availability 变为 'deleted'，needs_source_review 变为 True
        c_fetched = get_entry(self.conn, confirmed_entry["id"])
        self.assertIsNotNone(c_fetched)
        self.assertEqual(c_fetched["source_links"][0]["availability"], "deleted")
        self.assertTrue(c_fetched["needs_source_review"])

        # 2. 验证 draft_entry：availability 为 'deleted'，但 needs_source_review 依然为 False
        d_fetched = get_entry(self.conn, draft_entry["id"])
        self.assertEqual(d_fetched["source_links"][0]["availability"], "deleted")
        self.assertFalse(d_fetched["needs_source_review"])

        # 3. 验证无来源条目：完全不受影响，needs_source_review 为 False
        m_fetched = get_entry(self.conn, manual_entry["id"])
        self.assertFalse(m_fetched["needs_source_review"])

    def test_server_generated_excerpt_and_length_cap(self) -> None:
        """测试服务端摘录生成：严格由真实块文本截取（≤120 UTF-16 单位加 '…'），拒绝客户端伪造。"""
        # 构建超过 120 UTF-16 单位的文档
        long_para = "云深不知处，远客寻仙踪。" * 15  # 12 * 15 = 180 UTF-16 units
        doc_long_id = "doc_long"
        now = _iso_now()
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_long_id,
                self.world_a,
                "长文本.txt",
                "text/plain",
                len(long_para.encode("utf-8")),
                "utf-8",
                "sha_long",
                long_para,
                u16len(long_para),
                1,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        chunk_long_id = "c_long_01"
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_long_id,
                doc_long_id,
                self.world_a,
                0,
                json.dumps(["长卷"], ensure_ascii=False),
                0,
                u16len(long_para),
                long_para,
            ),
        )
        self.conn.commit()

        # 尝试传入虚假的 excerpt 字段
        entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "location",
                "title": "长文本地点",
                "source_links": [
                    {
                        "document_id": doc_long_id,
                        "chunk_id": chunk_long_id,
                        "start_char": 0,
                        "end_char": u16len(long_para),
                        "excerpt": "客户端伪造的摘要文本",
                    }
                ],
            },
        )

        real_excerpt = entry["source_links"][0]["excerpt"]
        self.assertNotEqual(real_excerpt, "客户端伪造的摘要文本")
        self.assertTrue(real_excerpt.endswith("…"))
        self.assertLessEqual(u16len(real_excerpt), 120)
        self.assertTrue(real_excerpt.startswith("云深不知处"))



    def test_non_first_chunk_absolute_coordinates_and_reject_relative_guess(self) -> None:
        """R4-03 核心回归测试：
        对于非首个切片（如起始坐标 1610），仅接受全文绝对坐标 [1610, 1620)；
        若传入相对坐标 [0, 10)，必须严格报错 422 SOURCE_OUT_OF_RANGE，
        绝不能隐式猜测相对坐标并改写为绝对坐标。
        """
        doc_multi_id = "doc_multi_chunk"
        chunk0_text = "A" * 1610
        chunk1_text = "B" * 1609  # 1610 + 1609 = 3219
        full_text = chunk0_text + chunk1_text
        now = _iso_now()

        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_multi_id,
                self.world_a,
                "多块文档.txt",
                "text/plain",
                len(full_text.encode("utf-8")),
                "utf-8",
                "sha_multi",
                full_text,
                u16len(full_text),
                2,
                2,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )

        c0_id = "c_multi_0"
        c1_id = "c_multi_1"
        self.conn.execute(
            """INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (c0_id, doc_multi_id, self.world_a, 0, json.dumps(["块0"]), 0, 1610, chunk0_text),
        )
        self.conn.execute(
            """INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (c1_id, doc_multi_id, self.world_a, 1, json.dumps(["块1"]), 1610, 3219, chunk1_text),
        )
        self.conn.commit()

        # 1. 传入第二块的合法全文绝对坐标 [1610, 1620) -> 必须成功
        entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "character",
                "title": "多块条目",
                "source_links": [
                    {
                        "document_id": doc_multi_id,
                        "chunk_id": c1_id,
                        "start_char": 1610,
                        "end_char": 1620,
                    }
                ],
            },
        )
        self.assertEqual(len(entry["source_links"]), 1)
        self.assertEqual(entry["source_links"][0]["start_char"], 1610)
        self.assertEqual(entry["source_links"][0]["end_char"], 1620)
        self.assertEqual(entry["source_links"][0]["excerpt"], "B" * 10)
        self.assertEqual(entry["source_links"][0]["chunk_index"], 1)

        # 2. 传入第二块的相对坐标 [0, 10) -> 必须被拒绝，绝不猜测改写为 [1610, 1620)
        with self.assertRaises(LoreEntrySourceOutOfRangeError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "错误相对坐标条目",
                    "source_links": [
                        {
                            "document_id": doc_multi_id,
                            "chunk_id": c1_id,
                            "start_char": 0,
                            "end_char": 10,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.code, "SOURCE_OUT_OF_RANGE")

    def test_source_coordinates_type_safety_and_reject_booleans(self) -> None:
        """测试来源坐标严格拒绝布尔值（bool 为 int 子类）与非整型。"""
        with self.assertRaises(LoreEntryValidationError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "布尔坐标拦截",
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": True,
                            "end_char": 10,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.details["field"], "source_links.coordinates")

        with self.assertRaises(LoreEntryValidationError):
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "字符串坐标拦截",
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": "0",
                            "end_char": 10,
                        }
                    ],
                },
            )

    def test_source_links_null_rejected(self) -> None:
        """契约守卫：source_links 为 null 必须被拒绝（422），不能当成 [] 静默清空。"""
        with self.assertRaises(LoreEntryValidationError) as ctx:
            create_entry(
                self.conn,
                self.world_a,
                {
                    "type": "character",
                    "title": "null来源项",
                    "source_links": None,
                },
            )
        self.assertEqual(ctx.exception.details["field"], "source_links")

        e = create_entry(
            self.conn,
            self.world_a,
            {"type": "character", "title": "正常条目", "source_links": []},
        )
        with self.assertRaises(LoreEntryValidationError):
            update_entry(
                self.conn,
                e["id"],
                {"source_links": None},
                expected_revision=1,
            )

    def test_preserve_historical_deleted_sources_on_update_title(self) -> None:
        """R4-07 核心业务闭环测试：
        来源文档软删除后，用户仅修改条目标题：
        1. PATCH 省略 source_links：保留原引用不变；
        2. PATCH 显式包含本条目已有且相同的历史引用：允许保留原引用及原摘录；
        3. PATCH 新增引用或修改引用到已删文档：必须严格报错 422 SOURCE_DELETED；
        4. 其他条目尝试借用失效引用：必须报错 422 SOURCE_DELETED。
        """
        # 创建带有 doc_1 引用的条目 1
        e1 = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "character",
                "title": "陆平原版",
                "source_links": [
                    {
                        "document_id": self.doc_1_id,
                        "chunk_id": self.chunk_1_id,
                        "start_char": 0,
                        "end_char": 10,
                    }
                ],
            },
        )
        old_excerpt = e1["source_links"][0]["excerpt"]

        # 软删除来源文档 doc_1
        self.conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (_iso_now(), self.doc_1_id),
        )
        self.conn.commit()

        # 1. PATCH 仅改标题，省略 source_links -> 成功且保留原引用
        upd1 = update_entry(
            self.conn,
            e1["id"],
            {"title": "陆平新版标题"},
            expected_revision=1,
        )
        self.assertEqual(upd1["title"], "陆平新版标题")
        self.assertEqual(len(upd1["source_links"]), 1)
        self.assertEqual(upd1["source_links"][0]["availability"], "deleted")
        self.assertEqual(upd1["source_links"][0]["excerpt"], old_excerpt)

        # 2. PATCH 显式包含本条目原有的已删引用 -> 成功保留原引用
        upd2 = update_entry(
            self.conn,
            e1["id"],
            {
                "title": "陆平第二版",
                "source_links": [
                    {
                        "document_id": self.doc_1_id,
                        "chunk_id": self.chunk_1_id,
                        "start_char": 0,
                        "end_char": 10,
                    }
                ],
            },
            expected_revision=2,
        )
        self.assertEqual(upd2["title"], "陆平第二版")
        self.assertEqual(len(upd2["source_links"]), 1)
        self.assertEqual(upd2["source_links"][0]["availability"], "deleted")
        self.assertEqual(upd2["source_links"][0]["excerpt"], old_excerpt)

        # 3. PATCH 修改了引用区间 [0, 15) -> 视作修改引用，已删文档拒绝建新引用
        with self.assertRaises(LoreEntrySourceDeletedError):
            update_entry(
                self.conn,
                e1["id"],
                {
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": 0,
                            "end_char": 15,
                        }
                    ],
                },
                expected_revision=3,
            )

        # 4. 另一条目 e2 尝试借用 doc_1 的失效引用 -> 必须被拒绝
        e2 = create_entry(
            self.conn,
            self.world_a,
            {"type": "character", "title": "新角色", "source_links": []},
        )
        with self.assertRaises(LoreEntrySourceDeletedError):
            update_entry(
                self.conn,
                e2["id"],
                {
                    "source_links": [
                        {
                            "document_id": self.doc_1_id,
                            "chunk_id": self.chunk_1_id,
                            "start_char": 0,
                            "end_char": 10,
                        }
                    ],
                },
                expected_revision=1,
            )

        # 5. 显式传 [] 成功清空来源
        upd_empty = update_entry(
            self.conn,
            e1["id"],
            {"source_links": []},
            expected_revision=3,
        )
        self.assertEqual(upd_empty["source_links"], [])

    def test_source_availability_and_additive_chunk_index(self) -> None:
        """验证 availability 准确区分及 additive chunk_index 字段。"""
        entry = create_entry(
            self.conn,
            self.world_a,
            {
                "type": "character",
                "title": "苏晚",
                "source_links": [
                    {
                        "document_id": self.doc_1_id,
                        "chunk_id": self.chunk_1_id,
                        "start_char": 0,
                        "end_char": 5,
                    }
                ],
            },
        )
        s0 = entry["source_links"][0]
        self.assertEqual(s0["availability"], "active")
        self.assertEqual(s0.get("chunk_index"), 0)

        # 临时关闭外键模拟切片物理丢失或数据异常 -> 派生为 'unavailable'
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("DELETE FROM chunks WHERE id = ?", (self.chunk_1_id,))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()

        fetched = get_entry(self.conn, entry["id"])
        s_unavail = fetched["source_links"][0]
        self.assertEqual(s_unavail["availability"], "unavailable")
        self.assertIsNone(s_unavail.get("chunk_index"))

if __name__ == "__main__":
    unittest.main()
