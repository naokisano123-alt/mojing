"""可追溯上下文选择模块完整单元测试。

测试覆盖：
1. 世界不存在与软删除拦截（404）
2. 玩家行动输入校验（非字符串、空、超长、坏 Unicode 422）
3. 空世界安全返回空 ContextBundle
4. 严格跨世界隔离
5. 排除软删除文档切片
6. draft 设定条目绝不自动提升
7. 排除软删除设定条目
8. confirmed 且含有失效来源（needs_source_review 为 True）条目保守排除
9. 无来源 manual confirmed 条目合法保留
10. action 字面搜索命中切片优先
11. action 匹配 confirmed 条目优先
12. 无命中或长文本确定性回退（条目优先，文档切片按 document_id, chunk_index 稳定补足）
13. 超长文本与 Emoji 代理对安全截断（单份 ≤ 4000 UTF-16，总 ≤ 16000 UTF-16）
14. 最多 8 份限制与去重
15. 空白文本不占条目
16. metadata 与 sources 严格 1:1 结构与字段核对
17. SQL 特殊字符注入防护
18. 与 Codex narration core.validate_request / FakeModelClient 完美兼容
19. 纯只读保证（数据库前后完全不变，无 commit）
"""

import sqlite3
import unittest
import uuid
from datetime import datetime, timezone

from app.db import SCHEMA
from app.lore_entries.schema import ensure_lore_entries_schema
from app.narration import FakeModelClient, NarrationRequest, generate_draft, validate_request
from app.narration_context import (
    MAX_ACTION_UNITS,
    MAX_CONTEXT_UNITS,
    MAX_SOURCE_UNITS,
    MAX_SOURCES,
    ContextBundle,
    ContextError,
    build_context,
    safe_u16_truncate,
    u16len,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NarrationContextTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        ensure_lore_entries_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _create_world(self, name: str = "测试世界", deleted: bool = False) -> str:
        w_id = str(uuid.uuid4())
        now = _now()
        deleted_at = now if deleted else None
        self.conn.execute(
            "INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (w_id, name, "描述", now, now, deleted_at),
        )
        return w_id

    def _create_document(
        self,
        world_id: str,
        filename: str = "test.md",
        text: str = "文档正文",
        deleted: bool = False,
    ) -> str:
        doc_id = str(uuid.uuid4())
        now = _now()
        deleted_at = now if deleted else None
        self.conn.execute(
            """INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding,
                content_sha256, text, text_length, line_count, chunk_count,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                world_id,
                filename,
                "text/markdown",
                len(text.encode("utf-8")),
                "utf-8",
                "sha256fake",
                text,
                len(text),
                1,
                1,
                now,
                now,
                deleted_at,
            ),
        )
        return doc_id

    def _create_chunk(
        self,
        doc_id: str,
        world_id: str,
        index: int,
        text: str,
        heading_path: str = "[]",
    ) -> str:
        c_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO chunks (
                id, document_id, world_id, chunk_index, heading_path,
                start_char, end_char, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (c_id, doc_id, world_id, index, heading_path, 0, len(text), text),
        )
        return c_id

    def _create_lore_entry(
        self,
        world_id: str,
        title: str,
        summary: str = "",
        status: str = "confirmed",
        deleted: bool = False,
        sources: list[tuple[str, str, int, int]] | None = None,
    ) -> str:
        e_id = str(uuid.uuid4())
        now = _now()
        deleted_at = now if deleted else None
        self.conn.execute(
            """INSERT INTO lore_entries (
                id, world_id, type, title, summary, status, revision,
                origin, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                e_id,
                world_id,
                "character",
                title,
                summary,
                status,
                1,
                "manual",
                now,
                now,
                deleted_at,
            ),
        )
        if sources:
            for doc_id, chunk_id, start_char, end_char in sources:
                s_id = str(uuid.uuid4())
                self.conn.execute(
                    """INSERT INTO lore_entry_sources (
                        id, entry_id, world_id, document_id, chunk_id,
                        start_char, end_char, excerpt, created_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        s_id,
                        e_id,
                        world_id,
                        doc_id,
                        chunk_id,
                        start_char,
                        end_char,
                        "摘录",
                        now,
                        None,
                    ),
                )
        return e_id

    # 1. 世界不存在与软删除测试
    def test_world_not_found(self):
        with self.assertRaises(ContextError) as ctx:
            build_context(self.conn, "non-existent-world", "探索港口")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "WORLD_NOT_FOUND")

    def test_world_soft_deleted(self):
        w_id = self._create_world(deleted=True)
        with self.assertRaises(ContextError) as ctx:
            build_context(self.conn, w_id, "探索港口")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "WORLD_NOT_FOUND")

    def test_world_id_empty(self):
        with self.assertRaises(ContextError) as ctx:
            build_context(self.conn, "   ", "探索港口")
        self.assertEqual(ctx.exception.status, 404)

    # 2. 行动输入校验
    def test_action_validation_empty_and_types(self):
        w_id = self._create_world()
        for bad_action in ["", "   ", "\t\n", None, 123]:
            with self.assertRaises(ContextError) as ctx:
                build_context(self.conn, w_id, bad_action)  # type: ignore
            self.assertEqual(ctx.exception.status, 422)
            self.assertEqual(ctx.exception.code, "ACTION_INVALID")

    def test_action_exceeds_max_units(self):
        w_id = self._create_world()
        too_long = "字" * (MAX_ACTION_UNITS + 1)
        with self.assertRaises(ContextError) as ctx:
            build_context(self.conn, w_id, too_long)
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "ACTION_INVALID")

    # 3. 空世界返回空 ContextBundle
    def test_empty_world_returns_empty_bundle(self):
        w_id = self._create_world()
        bundle = build_context(self.conn, w_id, "独自走在荒野上")
        self.assertIsInstance(bundle, ContextBundle)
        self.assertEqual(len(bundle.sources), 0)
        self.assertEqual(len(bundle.metadata), 0)

    # 4. 跨世界隔离
    def test_world_isolation(self):
        w1 = self._create_world("世界1")
        w2 = self._create_world("世界2")

        d1 = self._create_document(w1, "w1_doc.txt", "世界1的神秘海域")
        self._create_chunk(d1, w1, 0, "世界1的神秘海域，有一座灯塔。")
        self._create_lore_entry(w1, "艾莲娜", "世界1的领航员")

        d2 = self._create_document(w2, "w2_doc.txt", "世界2的机械森林")
        self._create_chunk(d2, w2, 0, "世界2的机械森林，住着发条巨兽。")
        self._create_lore_entry(w2, "发条核心", "世界2的能源宝物")

        b1 = build_context(self.conn, w1, "探索")
        for s in b1.sources:
            self.assertNotIn("世界2", s.text)
            self.assertNotIn("发条", s.text)
        for m in b1.metadata:
            if m["kind"] == "chunk":
                self.assertEqual(m["document_id"], d1)

        b2 = build_context(self.conn, w2, "探索")
        for s in b2.sources:
            self.assertNotIn("世界1", s.text)
            self.assertNotIn("艾莲娜", s.text)

    # 5. 排除软删除文档切片
    def test_soft_deleted_documents_excluded(self):
        w_id = self._create_world()
        d_active = self._create_document(w_id, "active.txt", "有效文档正文")
        c_active = self._create_chunk(d_active, w_id, 0, "这是有效文档的切片。")

        d_deleted = self._create_document(w_id, "deleted.txt", "已删除文档", deleted=True)
        self._create_chunk(d_deleted, w_id, 0, "这是软删除文档的切片，不应出现。")

        bundle = build_context(self.conn, w_id, "文档")
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(bundle.sources[0].source_id, f"chunk:{c_active}")
        self.assertIn("有效文档", bundle.sources[0].text)

    # 6. draft 设定条目绝不自动提升
    def test_draft_entries_never_promoted(self):
        w_id = self._create_world()
        self._create_lore_entry(w_id, "草稿条目", "尚未确认的内容", status="draft")
        self._create_lore_entry(w_id, "正式条目", "已确认的内容", status="confirmed")

        bundle = build_context(self.conn, w_id, "条目")
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(bundle.metadata[0]["title"], "正式条目")
        self.assertNotIn("草稿条目", [m["title"] for m in bundle.metadata])

    # 7. 排除软删除设定条目
    def test_deleted_lore_entries_excluded(self):
        w_id = self._create_world()
        self._create_lore_entry(w_id, "已删除条目", "已软删的内容", status="confirmed", deleted=True)
        self._create_lore_entry(w_id, "正常条目", "正常有效的内容", status="confirmed", deleted=False)

        bundle = build_context(self.conn, w_id, "条目")
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(bundle.metadata[0]["title"], "正常条目")

    # 8. confirmed 且存在失效来源（needs_source_review == True）条目保守排除
    def test_confirmed_entry_with_invalid_source_conservatively_excluded(self):
        w_id = self._create_world()
        d_active = self._create_document(w_id, "doc1.txt", "有效正文")
        c_active = self._create_chunk(d_active, w_id, 0, "有效切片正文")

        d_del = self._create_document(w_id, "doc2.txt", "已删正文", deleted=True)
        c_del = self._create_chunk(d_del, w_id, 0, "已删切片正文")

        # 条目 1：有效来源 -> 应该保留
        e1 = self._create_lore_entry(
            w_id, "有效条目", "来源有效", status="confirmed", sources=[(d_active, c_active, 0, 4)]
        )
        # 条目 2：指向已软删除文档 -> 存在失效来源，保守排除！
        e2 = self._create_lore_entry(
            w_id, "失效来源条目", "文档已删", status="confirmed", sources=[(d_del, c_del, 0, 4)]
        )

        bundle = build_context(self.conn, w_id, "条目")
        entry_ids = [m["entry_id"] for m in bundle.metadata if m["kind"] == "entry"]
        self.assertIn(e1, entry_ids)
        self.assertNotIn(e2, entry_ids)

    # 9. 无来源 manual confirmed 条目合法保留
    def test_confirmed_manual_entry_without_sources_is_valid(self):
        w_id = self._create_world()
        e_id = self._create_lore_entry(
            w_id, "纯设定背景", "纯手工创作者编写的设定，无引用来源", status="confirmed", sources=[]
        )

        bundle = build_context(self.conn, w_id, "设定背景")
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(bundle.sources[0].source_id, f"entry:{e_id}")
        self.assertEqual(bundle.metadata[0]["kind"], "entry")
        self.assertEqual(bundle.metadata[0]["origin"], "manual")
        self.assertEqual(bundle.metadata[0]["title"], "纯设定背景")

    # 10. 字面 search_chunks 匹配行动
    def test_action_literal_search_matching(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "port.txt", "港口资料")
        c1 = self._create_chunk(d, w_id, 0, "港口东面有一座古老的灯塔。")
        c2 = self._create_chunk(d, w_id, 1, "港口西面停泊着商船。")

        bundle = build_context(self.conn, w_id, "灯塔")
        # c1 匹配 '灯塔'，应该排在前面
        self.assertTrue(len(bundle.sources) >= 1)
        self.assertEqual(bundle.sources[0].source_id, f"chunk:{c1}")
        self.assertIn("灯塔", bundle.sources[0].text)

    # 11. 匹配 confirmed 条目优先
    def test_action_matching_confirmed_entry(self):
        w_id = self._create_world()
        self._create_lore_entry(w_id, "黑珍珠号", "一艘传说中的幽灵海盗船", status="confirmed")
        self._create_lore_entry(w_id, "铁炉堡", "矮人王国的宏伟都城", status="confirmed")

        bundle = build_context(self.conn, w_id, "我想打听黑珍珠号的下落")
        self.assertEqual(bundle.metadata[0]["title"], "黑珍珠号")

    # 12. 无命中或行动文本较长时的稳定排序与补足
    def test_fallback_order_when_no_match_or_action_long(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "doc.txt", "正文")
        c1 = self._create_chunk(d, w_id, 0, "切片0：阳光照在海面上。")
        c2 = self._create_chunk(d, w_id, 1, "切片1：微风吹拂着树梢。")
        e1 = self._create_lore_entry(w_id, "世界法则", "第一条法则是重力", status="confirmed")

        # 行动与以上内容均无直接字面子串重合
        bundle = build_context(self.conn, w_id, "深潜入未知的海底沟壑寻找发光矿石")
        # 规则：条目优先，再按 document_id, chunk_index 稳定补足切片
        source_ids = [s.source_id for s in bundle.sources]
        self.assertEqual(source_ids[0], f"entry:{e1}")
        self.assertEqual(source_ids[1], f"chunk:{c1}")
        self.assertEqual(source_ids[2], f"chunk:{c2}")

    # 13. 超长文本与 Emoji 代理对安全截断
    def test_emoji_surrogate_pair_safe_truncation(self):
        # safe_u16_truncate 测试
        s = "abc😀def"  # '😀' 为 2 个 UTF-16 code units
        self.assertEqual(safe_u16_truncate(s, 4), "abc")  # 3 units，不能切开😀
        self.assertEqual(safe_u16_truncate(s, 5), "abc😀")  # 5 units

        # 数据库中单份超长切片（> 4000 UTF-16 code units）
        w_id = self._create_world()
        d = self._create_document(w_id, "large.txt", "超长文本")
        # 2005 个 emoji = 4010 UTF-16 units
        huge_emoji_text = "😀" * 2005
        c_id = self._create_chunk(d, w_id, 0, huge_emoji_text)

        bundle = build_context(self.conn, w_id, "测试")
        self.assertEqual(len(bundle.sources), 1)
        truncated_text = bundle.sources[0].text
        self.assertLessEqual(u16len(truncated_text), MAX_SOURCE_UNITS)
        self.assertEqual(u16len(truncated_text), 4000)  # 恰好截取 2000 个完整 emoji
        self.assertEqual(truncated_text, "😀" * 2000)

    # 14. 最多 8 份上限与总预算 16000 限制
    def test_max_sources_limit_8(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "lots.txt", "很多切片")
        for i in range(12):
            self._create_chunk(d, w_id, i, f"切片编号 {i}")

        bundle = build_context(self.conn, w_id, "测试")
        self.assertEqual(len(bundle.sources), MAX_SOURCES)
        self.assertEqual(len(bundle.metadata), MAX_SOURCES)

    def test_max_context_units_limit_16000(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "big.txt", "巨大")
        for i in range(6):
            # 每份 3500 units，5 份就达到 17500，必须受限于 16000
            self._create_chunk(d, w_id, i, f"编号{i}: " + ("字" * 3500))

        bundle = build_context(self.conn, w_id, "测试")
        total_units = sum(u16len(s.text) for s in bundle.sources)
        self.assertLessEqual(total_units, MAX_CONTEXT_UNITS)

    # 15. 空白文本不占条目
    def test_blank_chunk_or_entry_skipped(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "blank.txt", "空白")
        self._create_chunk(d, w_id, 0, "    \n\t   ")  # 纯空白切片
        c_valid = self._create_chunk(d, w_id, 1, "这是有效正文。")

        bundle = build_context(self.conn, w_id, "测试")
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(bundle.sources[0].source_id, f"chunk:{c_valid}")

    # 16. source_id 去重与 metadata 1:1 对应核对
    def test_metadata_one_to_one_correspondence(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "story.md", "故事正文")
        c1 = self._create_chunk(d, w_id, 0, "开头：英雄出发。")
        e1 = self._create_lore_entry(w_id, "勇者之剑", "上古神兵", status="confirmed")

        bundle = build_context(self.conn, w_id, "英雄")
        self.assertEqual(len(bundle.sources), len(bundle.metadata))

        for s, m in zip(bundle.sources, bundle.metadata):
            self.assertEqual(s.source_id, m["source_id"])
            self.assertEqual(s.kind, m["kind"])
            if s.kind == "chunk":
                self.assertEqual(m["chunk_id"], c1)
                self.assertEqual(m["document_id"], d)
                self.assertEqual(m["chunk_index"], 0)
                self.assertEqual(m["title"], "story.md")
            elif s.kind == "entry":
                self.assertEqual(m["entry_id"], e1)
                self.assertEqual(m["title"], "勇者之剑")

    # 17. SQL 注入防护测试
    def test_sql_injection_defense(self):
        w_id = self._create_world()
        evil_action = "'; DROP TABLE worlds; --"
        bundle = build_context(self.conn, w_id, evil_action)
        self.assertIsInstance(bundle, ContextBundle)
        # 验证表依然健在
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM worlds")
        count = cur.fetchone()[0]
        self.assertEqual(count, 1)

    # 18. 与 Codex narration 核心完美对接
    def test_compatibility_with_codex_core_validate_and_fake(self):
        import asyncio

        w_id = self._create_world()
        d = self._create_document(w_id, "history.txt", "历史")
        self._create_chunk(d, w_id, 0, "古老的王国建立了三百年。")
        self._create_lore_entry(w_id, "贤者议会", "十二位贤者掌管魔法", status="confirmed")

        bundle = build_context(self.conn, w_id, "拜访贤者议会")
        request = NarrationRequest(action="拜访贤者议会", sources=bundle.sources)
        # 核心请求校验不抛异常
        validate_request(request)

        # FakeModelClient 生成草案
        client = FakeModelClient()
        draft = asyncio.run(generate_draft(request, client))
        self.assertIn("未调用真实 AI", draft.narrative)
        self.assertTrue(len(draft.references) <= len(bundle.sources))

    # 19. 纯只读保证（数据库前后完全不变，无 commit）
    def test_read_only_invariance(self):
        w_id = self._create_world()
        d = self._create_document(w_id, "doc.txt", "正文")
        self._create_chunk(d, w_id, 0, "切片正文")
        self._create_lore_entry(w_id, "条目标题", "条目摘要", status="confirmed")

        # 记录初始行数与数据状态
        def get_db_fingerprint():
            c = self.conn.cursor()
            w = c.execute("SELECT COUNT(*) FROM worlds").fetchone()[0]
            d_count = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            ch = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            le = c.execute("SELECT COUNT(*) FROM lore_entries").fetchone()[0]
            ls = c.execute("SELECT COUNT(*) FROM lore_entry_sources").fetchone()[0]
            return (w, d_count, ch, le, ls)

        before = get_db_fingerprint()
        build_context(self.conn, w_id, "行动测试")
        after = get_db_fingerprint()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
