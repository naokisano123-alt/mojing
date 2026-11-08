"""中文资料检索服务单元测试套件。

覆盖要求：
1. 中文单字、双字、长词、人名、地名子串检索。
2. 跨世界严格隔离。
3. 世界与文档软删除排除。
4. SQL 注入安全、特殊字符与字面量（%、_、'、"、;、--、反斜杠）。
5. Emoji 与 UTF-16 偏移精确定位。
6. 分页稳定性、limit/offset 边界。
7. 各种非法输入的参数校验与错误码。
8. 标题路径与文件名命中降级。
9. 接近 20 文档规模的数据检索性能证据。
10. R3 回归：B6 杜绝 JSON 符号（'['、'\"'、','）伪命中；
11. R3 回归：B6 ASCII 大小写与 Unicode 坐标稳定性；
12. R3 回归：B7 标题与文件名命中时坐标严格为 None；
13. R3 回归：B8 排序优先级严格保证 text(1) > heading(2) > filename(3)。
"""

from __future__ import annotations

import json
import sqlite3
import time
import unittest
from datetime import datetime, timezone

try:
    from app.db import SCHEMA
    from app.lore_search import (
        SearchValidationError,
        SearchWorldNotFoundError,
        search_chunks,
        u16len,
    )
except ImportError:
    from server.app.db import SCHEMA
    from server.app.lore_search import (
        SearchValidationError,
        SearchWorldNotFoundError,
        search_chunks,
        u16len,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestLoreSearchService(unittest.TestCase):
    def setUp(self) -> None:
        """建立内存 SQLite 数据库并初始化 M1 表结构。"""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

        self.world_a = "world_alpha"
        self.world_b = "world_beta"
        now = _iso_now()

        # 插入世界 A 与 B
        self.conn.execute(
            "INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.world_a, "雾港世界", "阿尔法测试世界", now, now, None),
        )
        self.conn.execute(
            "INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.world_b, "沙漠世界", "贝塔测试世界", now, now, None),
        )

        # 插入世界 A 文档 1: 人物设定集
        self.doc_1_id = "doc_01"
        self.doc_1_text = (
            "第一章 雾中来客\n"
            "陆平身穿一件陈旧的黑色风衣，手里握着一柄带鞘的短刀。\n"
            "他在旧街的转角处停下脚步，向远处红枫酒馆张望。\n"
            "酒馆的老板娘名为苏晚，传闻知晓城中所有秘密。"
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
                "人物与势力设定.txt",
                "text/plain",
                len(self.doc_1_text.encode("utf-8")),
                "utf-8",
                "sha256_mock_1",
                self.doc_1_text,
                u16len(self.doc_1_text),
                4,
                2,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )

        # 切片 1-1
        self.chunk_1_1_text = (
            "第一章 雾中来客\n陆平身穿一件陈旧的黑色风衣，手里握着一柄带鞘的短刀。"
        )
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_01_01",
                self.doc_1_id,
                self.world_a,
                0,
                json.dumps(["设定集", "第一章 雾中来客"], ensure_ascii=False),
                0,
                u16len(self.chunk_1_1_text),
                self.chunk_1_1_text,
            ),
        )

        # 切片 1-2
        self.chunk_1_2_text = (
            "\n他在旧街的转角处停下脚步，向远处红枫酒馆张望。\n"
            "酒馆的老板娘名为苏晚，传闻知晓城中所有秘密。"
        )
        c1_1_len = u16len(self.chunk_1_1_text)
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_01_02",
                self.doc_1_id,
                self.world_a,
                1,
                json.dumps(["设定集", "第一章 雾中来客", "酒馆情报"], ensure_ascii=False),
                c1_1_len,
                c1_1_len + u16len(self.chunk_1_2_text),
                self.chunk_1_2_text,
            ),
        )

        # 插入世界 A 文档 2: 特殊符号与 Emoji 文档
        self.doc_2_id = "doc_02"
        self.doc_2_text = (
            "魔法道具记载：\n"
            "1. 苍龙符咒 🐉：蕴含龙息。\n"
            "2. 降价幅度高达 50%_off，注意此处的 % 和 _ 是普通文本。\n"
            "3. 注入指令示例：'; DROP TABLE chunks; --\n"
            "4. 城堡遗迹 🏰 的入口隐藏在迷宫深处。"
        )
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.doc_2_id,
                self.world_a,
                "特殊道具与符文.md",
                "text/markdown",
                len(self.doc_2_text.encode("utf-8")),
                "utf-8",
                "sha256_mock_2",
                self.doc_2_text,
                u16len(self.doc_2_text),
                5,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_02_01",
                self.doc_2_id,
                self.world_a,
                0,
                json.dumps(["符文秘典", "魔法道具"], ensure_ascii=False),
                0,
                u16len(self.doc_2_text),
                self.doc_2_text,
            ),
        )

        # 插入世界 B 的同名人物文档（测试跨世界隔离）
        self.doc_b_id = "doc_beta_01"
        self.doc_b_text = "沙海游侠陆平在绿洲饮水，身佩短刀。"
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.doc_b_id,
                self.world_b,
                "沙漠人物.txt",
                "text/plain",
                len(self.doc_b_text.encode("utf-8")),
                "utf-8",
                "sha256_mock_b",
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
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_beta_01",
                self.doc_b_id,
                self.world_b,
                0,
                json.dumps(["沙漠志"], ensure_ascii=False),
                0,
                u16len(self.doc_b_text),
                self.doc_b_text,
            ),
        )

        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_chinese_single_char_search(self) -> None:
        """测试中文单字短词匹配（如 '刀'、'陆'）。"""
        res = search_chunks(self.conn, self.world_a, "刀")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["items"][0]["chunk_id"], "c_01_01")
        self.assertIn("短刀", res["items"][0]["snippet"])

        res_lu = search_chunks(self.conn, self.world_a, "陆")
        self.assertEqual(res_lu["total"], 1)
        self.assertEqual(res_lu["items"][0]["document_id"], self.doc_1_id)

    def test_chinese_two_char_and_name_search(self) -> None:
        """测试中文双字人名/地名匹配（如 '陆平'、'红枫'、'苏晚'）。"""
        res_name = search_chunks(self.conn, self.world_a, "陆平")
        self.assertEqual(res_name["total"], 1)
        self.assertEqual(res_name["items"][0]["chunk_id"], "c_01_01")
        self.assertEqual(res_name["items"][0]["match_field"], "text")

        res_place = search_chunks(self.conn, self.world_a, "红枫")
        self.assertEqual(res_place["total"], 1)
        self.assertEqual(res_place["items"][0]["chunk_id"], "c_01_02")
        self.assertIn("红枫酒馆", res_place["items"][0]["snippet"])

    def test_cross_world_isolation(self) -> None:
        """测试跨世界严格隔离：搜索世界 A 中的 '陆平' 绝不返回世界 B 的切片。"""
        res_a = search_chunks(self.conn, self.world_a, "陆平")
        self.assertEqual(res_a["total"], 1)
        self.assertEqual(res_a["items"][0]["world_id"], self.world_a)
        self.assertEqual(res_a["items"][0]["document_id"], self.doc_1_id)

        res_b = search_chunks(self.conn, self.world_b, "陆平")
        self.assertEqual(res_b["total"], 1)
        self.assertEqual(res_b["items"][0]["world_id"], self.world_b)
        self.assertEqual(res_b["items"][0]["document_id"], self.doc_b_id)

    def test_soft_deleted_document_exclusion(self) -> None:
        """测试软删除文档的切片不会被检索出来。"""
        self.conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (_iso_now(), self.doc_1_id),
        )
        self.conn.commit()

        res = search_chunks(self.conn, self.world_a, "陆平")
        self.assertEqual(res["total"], 0)
        self.assertEqual(len(res["items"]), 0)

    def test_soft_deleted_world_raises_404(self) -> None:
        """测试世界被软删除时直接抛出 404 WORLD_NOT_FOUND。"""
        self.conn.execute(
            "UPDATE worlds SET deleted_at = ? WHERE id = ?",
            (_iso_now(), self.world_a),
        )
        self.conn.commit()

        with self.assertRaises(SearchWorldNotFoundError) as ctx:
            search_chunks(self.conn, self.world_a, "陆平")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "WORLD_NOT_FOUND")

    def test_sql_injection_and_special_characters(self) -> None:
        """测试 SQL 注入片段和特殊字符（%、_、'、"、--）作为字面量安全检索。"""
        res_percent = search_chunks(self.conn, self.world_a, "50%")
        self.assertEqual(res_percent["total"], 1)
        self.assertIn("50%_off", res_percent["items"][0]["snippet"])

        res_wildcard = search_chunks(self.conn, self.world_a, "%_off")
        self.assertEqual(res_wildcard["total"], 1)

        res_inject = search_chunks(self.conn, self.world_a, "DROP TABLE")
        self.assertEqual(res_inject["total"], 1)
        self.assertIn("DROP TABLE chunks", res_inject["items"][0]["snippet"])

        res_quote = search_chunks(self.conn, self.world_a, "'; DROP")
        self.assertEqual(res_quote["total"], 1)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM chunks")
        self.assertGreater(cur.fetchone()[0], 0)

    def test_emoji_and_utf16_coordinates(self) -> None:
        """测试包含 Emoji（占用 2 个 UTF-16 code unit 的代理对）情况下的精确定位。"""
        res = search_chunks(self.conn, self.world_a, "龙息")
        self.assertEqual(res["total"], 1)
        item = res["items"][0]
        self.assertEqual(item["match_field"], "text")

        start = item["match_start_char"]
        end = item["match_end_char"]
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)

        doc_u16_bytes = self.doc_2_text.encode("utf-16-le")
        extracted_bytes = doc_u16_bytes[start * 2 : end * 2]
        extracted_text = extracted_bytes.decode("utf-16-le")
        self.assertEqual(extracted_text, "龙息")

        res_castle = search_chunks(self.conn, self.world_a, "🏰")
        self.assertEqual(res_castle["total"], 1)
        castle_item = res_castle["items"][0]
        self.assertIsNotNone(castle_item["match_start_char"])
        self.assertIsNotNone(castle_item["match_end_char"])
        extracted_castle = doc_u16_bytes[
            castle_item["match_start_char"] * 2 : castle_item["match_end_char"] * 2
        ].decode("utf-16-le")
        self.assertEqual(extracted_castle, "🏰")

    def test_heading_path_match(self) -> None:
        """当关键词仅出现在标题路径中时，能够正确返回并标注 match_field='heading'。"""
        res = search_chunks(self.conn, self.world_a, "符文秘典")
        self.assertEqual(res["total"], 1)
        item = res["items"][0]
        self.assertEqual(item["match_field"], "heading")
        self.assertIn("【标题命中: 符文秘典 > 魔法道具】", item["snippet"])
        # R3 契约规范：heading 命中的坐标必须为 None
        self.assertIsNone(item["match_start_char"])
        self.assertIsNone(item["match_end_char"])
        self.assertIsNone(item["match_chunk_start_char"])
        self.assertIsNone(item["match_chunk_end_char"])

    def test_filename_match(self) -> None:
        """当关键词仅出现在文件名中时，能够正确返回并标注 match_field='filename'。"""
        res = search_chunks(self.conn, self.world_a, "特殊道具")
        self.assertEqual(res["total"], 1)
        item = res["items"][0]
        self.assertEqual(item["match_field"], "filename")
        self.assertIn("【文档命中: 特殊道具与符文.md】", item["snippet"])
        # R3 契约规范：filename 命中的坐标必须为 None
        self.assertIsNone(item["match_start_char"])
        self.assertIsNone(item["match_end_char"])
        self.assertIsNone(item["match_chunk_start_char"])
        self.assertIsNone(item["match_chunk_end_char"])

    def test_b6_no_json_syntax_pseudo_match(self) -> None:
        """[R3 回归 B6] 搜索 '['、']' 等 JSON 存储语法符号不应产生虚假命中。"""
        # doc_01 的 chunks heading_path 是 '["设定集", "第一章 雾中来客"]'
        # 正文、标题内容和文件名均不含 '['
        res_bracket = search_chunks(self.conn, self.world_a, "[")
        self.assertEqual(res_bracket["total"], 0)
        self.assertEqual(len(res_bracket["items"]), 0)

        res_close_bracket = search_chunks(self.conn, self.world_a, "]")
        self.assertEqual(res_close_bracket["total"], 0)

    def test_b6_ascii_case_insensitivity_and_coordinates(self) -> None:
        """[R3 回归 B6] ASCII 大小写不敏感且绝对不导致坐标偏移。"""
        # doc_02 包含 '50%_off'
        res_upper = search_chunks(self.conn, self.world_a, "OFF")
        self.assertEqual(res_upper["total"], 1)
        item = res_upper["items"][0]
        self.assertEqual(item["match_field"], "text")

        start = item["match_start_char"]
        end = item["match_end_char"]
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)

        doc_u16_bytes = self.doc_2_text.encode("utf-16-le")
        matched = doc_u16_bytes[start * 2 : end * 2].decode("utf-16-le")
        self.assertEqual(matched.lower(), "off")

    def test_b7_non_text_coords_are_strictly_none(self) -> None:
        """[R3 回归 B7] 标题/文件名命中时，坐标严格为 None，绝不填 0 或编造前 80 字。"""
        res_heading = search_chunks(self.conn, self.world_a, "酒馆情报")
        self.assertEqual(res_heading["total"], 1)
        item = res_heading["items"][0]
        self.assertEqual(item["match_field"], "heading")
        self.assertIsNone(item["match_start_char"])
        self.assertIsNone(item["match_end_char"])
        self.assertIsNone(item["match_chunk_start_char"])
        self.assertIsNone(item["match_chunk_end_char"])

        res_fn = search_chunks(self.conn, self.world_a, "人物与势力")
        self.assertGreaterEqual(res_fn["total"], 1)
        fn_item = res_fn["items"][0]
        self.assertEqual(fn_item["match_field"], "filename")
        self.assertIsNone(fn_item["match_start_char"])
        self.assertIsNone(fn_item["match_end_char"])

    def test_b8_sorting_priority_text_over_heading_over_filename(self) -> None:
        """[R3 回归 B8] 命中排序严格保证 text(1) > heading(2) > filename(3)。"""
        now = _iso_now()
        # 插入一个专门测试排序的文档：
        # doc_sort_1: 文件名含 '排序探针'，heading 为 ['普通标题']，text 为 '没有任何关键词'
        # doc_sort_2: 文件名含 '普通文档'，heading 为 ['排序探针卷']，text 为 '没有任何关键词'
        # doc_sort_3: 文件名含 '普通文档B'，heading 为 ['普通标题B']，text 为 '这里包含排序探针正文'
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "doc_s1",
                self.world_a,
                "排序探针档案.txt",
                "text/plain",
                100,
                "utf-8",
                "sha_s1",
                "没有任何关键词",
                7,
                1,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_s1",
                "doc_s1",
                self.world_a,
                0,
                json.dumps(["普通标题"], ensure_ascii=False),
                0,
                7,
                "没有任何关键词",
            ),
        )

        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "doc_s2",
                self.world_a,
                "普通文档A.txt",
                "text/plain",
                100,
                "utf-8",
                "sha_s2",
                "没有任何关键词",
                7,
                1,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_s2",
                "doc_s2",
                self.world_a,
                0,
                json.dumps(["排序探针卷"], ensure_ascii=False),
                0,
                7,
                "没有任何关键词",
            ),
        )

        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                source_kind, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "doc_s3",
                self.world_a,
                "普通文档B.txt",
                "text/plain",
                100,
                "utf-8",
                "sha_s3",
                "这里包含排序探针正文",
                10,
                1,
                1,
                "user_import",
                "ready",
                now,
                now,
                None,
            ),
        )
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "c_s3",
                "doc_s3",
                self.world_a,
                0,
                json.dumps(["普通标题B"], ensure_ascii=False),
                0,
                10,
                "这里包含排序探针正文",
            ),
        )
        self.conn.commit()

        res = search_chunks(self.conn, self.world_a, "排序探针")
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["items"][0]["match_field"], "text")
        self.assertEqual(res["items"][0]["chunk_id"], "c_s3")

        self.assertEqual(res["items"][1]["match_field"], "heading")
        self.assertEqual(res["items"][1]["chunk_id"], "c_s2")

        self.assertEqual(res["items"][2]["match_field"], "filename")
        self.assertEqual(res["items"][2]["chunk_id"], "c_s1")

    def test_pagination(self) -> None:
        """测试分页稳定性和 limit/offset。"""
        for i in range(15):
            self.conn.execute(
                """INSERT INTO chunks (
                    id, document_id, world_id, chunk_index, heading_path,
                    start_char, end_char, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"c_page_{i:02d}",
                    self.doc_1_id,
                    self.world_a,
                    10 + i,
                    json.dumps(["分页测试"], ensure_ascii=False),
                    1000 + i * 50,
                    1000 + (i + 1) * 50,
                    f"这是第 {i} 条用于测试分页稳定性的切片文本，标记词：页码标记。",
                ),
            )
        self.conn.commit()

        page1 = search_chunks(self.conn, self.world_a, "页码标记", limit=5, offset=0)
        self.assertEqual(page1["total"], 15)
        self.assertEqual(len(page1["items"]), 5)
        self.assertEqual(page1["limit"], 5)
        self.assertEqual(page1["offset"], 0)

        page2 = search_chunks(self.conn, self.world_a, "页码标记", limit=5, offset=5)
        self.assertEqual(len(page2["items"]), 5)
        self.assertEqual(page2["offset"], 5)

        ids_1 = {it["chunk_id"] for it in page1["items"]}
        ids_2 = {it["chunk_id"] for it in page2["items"]}
        self.assertEqual(len(ids_1.intersection(ids_2)), 0)

    def test_validation_errors(self) -> None:
        """测试参数非法时的错误码与错误信息。"""
        with self.assertRaises(SearchValidationError) as ctx:
            search_chunks(self.conn, self.world_a, "   ")
        self.assertEqual(ctx.exception.code, "SEARCH_QUERY_INVALID")

        with self.assertRaises(SearchValidationError) as ctx:
            search_chunks(self.conn, self.world_a, "测" * 101)
        self.assertEqual(ctx.exception.code, "SEARCH_QUERY_INVALID")

        with self.assertRaises(SearchValidationError) as ctx:
            search_chunks(self.conn, self.world_a, "测试", limit=0)
        self.assertEqual(ctx.exception.code, "SEARCH_LIMIT_INVALID")

        with self.assertRaises(SearchValidationError) as ctx:
            search_chunks(self.conn, self.world_a, "测试", limit=101)
        self.assertEqual(ctx.exception.code, "SEARCH_LIMIT_INVALID")

        with self.assertRaises(SearchValidationError) as ctx:
            search_chunks(self.conn, self.world_a, "测试", offset=-1)
        self.assertEqual(ctx.exception.code, "SEARCH_OFFSET_INVALID")

        with self.assertRaises(SearchWorldNotFoundError) as ctx:
            search_chunks(self.conn, "non_existent_world", "测试")
        self.assertEqual(ctx.exception.code, "WORLD_NOT_FOUND")

    def test_realistic_twenty_docs_performance(self) -> None:
        """性能样本证据：在达到 M1 上限约 20 个文档、约 200 个切片规模下测试检索耗时。"""
        sample_para = (
            "在旧时代的遗迹之中，旅者们寻觅着遗失的古籍与星图。\n"
            "每一枚古铜钱币上都镌刻着失落王朝的符印，记载着未曾公开的秘史。\n"
            "雾气从海湾深处泛起，潮汐推动着古老的船只缓缓进港。"
        )
        now = _iso_now()
        for d in range(20):
            doc_id = f"perf_doc_{d:02d}"
            self.conn.execute(
                """INSERT INTO documents (
                    id, world_id, filename, media_type, byte_size, encoding,
                    content_sha256, text, text_length, line_count, chunk_count,
                    source_kind, status, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    self.world_a,
                    f"设定文档_{d:02d}.txt",
                    "text/plain",
                    1000,
                    "utf-8",
                    f"sha_{d}",
                    sample_para,
                    u16len(sample_para),
                    3,
                    10,
                    "user_import",
                    "ready",
                    now,
                    now,
                    None,
                ),
            )
            for c in range(10):
                self.conn.execute(
                    """INSERT INTO chunks (
                        id, document_id, world_id, chunk_index, heading_path,
                        start_char, end_char, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"perf_c_{d:02d}_{c:02d}",
                        doc_id,
                        self.world_a,
                        c,
                        json.dumps([f"卷_{d}", f"章_{c}"], ensure_ascii=False),
                        c * 100,
                        (c + 1) * 100,
                        sample_para + (f" 目标人物名：云深第{d}号。" if c == 5 else ""),
                    ),
                )
        self.conn.commit()

        start_time = time.perf_counter()
        for _ in range(5):
            res = search_chunks(self.conn, self.world_a, "云深第7号", limit=20, offset=0)
            self.assertEqual(res["total"], 1)
        elapsed_ms = (time.perf_counter() - start_time) * 1000 / 5

        self.assertLess(
            elapsed_ms,
            100.0,
            f"单次检索耗时 {elapsed_ms:.2f}ms，超过了合理阈值",
        )


if __name__ == "__main__":
    unittest.main()
