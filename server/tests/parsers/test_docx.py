"""DOCX 提取器单元测试。

测试覆盖：
- 标题级别识别（Heading 1-3，转换为 Markdown #, ##, ###）
- 段落与表格穿插的原始文档顺序保持
- 表格转换为结构化 Markdown 表格
- 结构警告（明确说明跳过页眉/页脚/文本框等）
- 空文档拒收（EMPTY_DOCUMENT）
- 损坏/伪造 DOCX 拦截（非 ZIP 文件、缺少 document.xml）
- ZIP 炸弹攻击防御（解压后体积超限或异常膨胀率拦截）
- 大小与字符数配额超限测试
"""

from __future__ import annotations

import unittest
import zipfile

from server.app.parsers.docx import parse_docx
from server.app.parsers.types import ParseError, ParseLimits

from .sample_helpers import create_corrupt_zip, create_test_docx


class TestDocxParser(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = ParseLimits(
            max_bytes=2 * 1024 * 1024,
            max_extracted_chars=100_000,
            max_uncompressed_bytes=10 * 1024 * 1024,
            max_uncompressed_ratio=100.0,
        )

    def test_docx_headings_and_paragraphs(self) -> None:
        """测试标题转换为 Markdown # 语法，以便后续分块引擎自动识别层级。"""
        elements = [
            ("heading", "古神纪元总纲", 1),
            ("p", "这是总纲第一段的说明文字。"),
            ("heading", "三大古老宗派", 2),
            ("p", "宗派分为守秘者、持灯者与破晓会。"),
        ]
        docx_bytes = create_test_docx(elements)

        parsed = parse_docx(docx_bytes, "world.docx", self.limits)

        self.assertIn("application/vnd.openxmlformats-officedocument", parsed.media_type)
        self.assertEqual(parsed.encoding, "utf-8")
        self.assertIn("# 古神纪元总纲", parsed.text)
        self.assertIn("## 三大古老宗派", parsed.text)
        self.assertIn("这是总纲第一段的说明文字。", parsed.text)
        self.assertTrue(any("页眉、页脚" in w for w in parsed.warnings))

    def test_docx_interleaved_paragraphs_and_tables(self) -> None:
        """测试段落与表格按照原生文档流顺序交错排列，不发生位置颠倒。"""
        elements = [
            ("heading", "第一章 势力调查", 1),
            ("p", "下表列出了北境所有活跃势力的基本情况："),
            ("table", [["势力名", "领地", "危险度"], ["黑鸦军团", "铁砧堡", "极高"], ["灰原商会", "自由港", "中等"]]),
            ("p", "上述调查报告由密探于帝国历前夕采集。"),
        ]
        docx_bytes = create_test_docx(elements)

        parsed = parse_docx(docx_bytes, "factions.docx", self.limits)

        # 验证文本出现顺序
        pos_p1 = parsed.text.find("下表列出了北境")
        pos_table = parsed.text.find("黑鸦军团")
        pos_p2 = parsed.text.find("上述调查报告由密探")
        self.assertTrue(0 <= pos_p1 < pos_table < pos_p2)

        # 验证表格格式化为 Markdown 表格
        self.assertIn("| 势力名 | 领地 | 危险度 |", parsed.text)
        self.assertIn("| --- | --- | --- |", parsed.text)
        self.assertIn("| 黑鸦军团 | 铁砧堡 | 极高 |", parsed.text)

    def test_docx_empty_document_rejected(self) -> None:
        """无任何文字段落或表格的 DOCX 明确拒绝。"""
        docx_bytes = create_test_docx([])

        with self.assertRaises(ParseError) as ctx:
            parse_docx(docx_bytes, "empty.docx", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "EMPTY_DOCUMENT")

    def test_docx_corrupt_non_zip_rejected(self) -> None:
        bad_data = b"Plain non-zip binary text data pretending to be docx"

        with self.assertRaises(ParseError) as ctx:
            parse_docx(bad_data, "bad.docx", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "CORRUPT_DOCUMENT")

    def test_docx_missing_document_xml_rejected(self) -> None:
        """合法的 ZIP 但缺失 word/document.xml，拦截并识别为损坏文档。"""
        fake_zip = create_corrupt_zip({"[Content_Types].xml": b"<Types/>"})

        with self.assertRaises(ParseError) as ctx:
            parse_docx(fake_zip, "missing_xml.docx", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "CORRUPT_DOCUMENT")
        self.assertIn("word/document.xml", ctx.exception.message)

    def test_docx_zip_bomb_uncompressed_limit_rejected(self) -> None:
        """测试解压体积超过安全上限时触发防御拦截。"""
        strict_limits = ParseLimits(max_uncompressed_bytes=1000)
        elements = [("heading", "正文", 1), ("p", "内容" * 200)]
        docx_bytes = create_test_docx(elements)

        with self.assertRaises(ParseError) as ctx:
            parse_docx(docx_bytes, "bomb.docx", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "ZIP_BOMB_DETECTED")

    def test_docx_exceeds_byte_limit(self) -> None:
        strict_limits = ParseLimits(max_bytes=100)
        docx_bytes = create_test_docx([("p", "正常文字")])

        with self.assertRaises(ParseError) as ctx:
            parse_docx(docx_bytes, "large.docx", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "FILE_TOO_LARGE")

    def test_docx_exceeds_char_limit(self) -> None:
        strict_limits = ParseLimits(max_extracted_chars=10)
        docx_bytes = create_test_docx([("p", "这段文字已经远超十个字符了")])

        with self.assertRaises(ParseError) as ctx:
            parse_docx(docx_bytes, "long.docx", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "FILE_TOO_LARGE")
        self.assertIn("文本量超出限制", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
