"""PDF 提取器单元测试。

测试覆盖：
- 中文单页/多页正常提取与页面顺序保持
- 空白页/扫描件识别与拒绝（不假装成功、不私自 OCR）
- 混合页提取与降级警告（部分页面含文字，部分空白）
- 加密文件拦截（ENCRYPTED_DOCUMENT）
- 损坏文件与非法文件头拦截（CORRUPT_DOCUMENT）
- 空文件与超出上限防护（字节限制、页数限制、字符数限制）
"""

from __future__ import annotations

import unittest

from server.app.parsers.pdf import parse_pdf
from server.app.parsers.types import ParseError, ParseLimits

from .sample_helpers import create_empty_pdf, create_test_pdf


class TestPdfParser(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = ParseLimits(
            max_bytes=2 * 1024 * 1024,
            max_pages=50,
            max_extracted_chars=100_000,
        )

    def test_pdf_single_page_chinese(self) -> None:
        content = "第一章 雾港来信\n旧街的杂货铺在晨雾中点亮了第一盏黄铜油灯。"
        pdf_bytes = create_test_pdf([content])

        parsed = parse_pdf(pdf_bytes, "lore.pdf", self.limits)

        self.assertEqual(parsed.media_type, "application/pdf")
        self.assertEqual(parsed.encoding, "utf-8")
        self.assertEqual(parsed.page_count, 1)
        self.assertIn("第一章 雾港来信", parsed.text)
        self.assertIn("杂货铺", parsed.text)
        self.assertGreater(parsed.extracted_chars, 0)
        self.assertTrue(any("排版顺序" in w for w in parsed.warnings))

    def test_pdf_multi_page_order(self) -> None:
        pages = [
            "第一页：北方群岛的晨雾与航线。",
            "第二页：黑水工坊与蒸汽动力机械。",
            "第三页：守夜人协会的隐秘信条。",
        ]
        pdf_bytes = create_test_pdf(pages)

        parsed = parse_pdf(pdf_bytes, "chapters.pdf", self.limits)

        self.assertEqual(parsed.page_count, 3)
        # 验证提取顺序严格按照页面序列
        pos1 = parsed.text.find("第一页")
        pos2 = parsed.text.find("第二页")
        pos3 = parsed.text.find("第三页")
        self.assertTrue(0 <= pos1 < pos2 < pos3)

    def test_pdf_empty_or_scanned_rejected(self) -> None:
        """纯空白页或无文字图层扫描件应被明确拒收，禁止假成功。"""
        empty_pdf = create_empty_pdf(pages_count=2)

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(empty_pdf, "scanned.pdf", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "SCANNED_OR_EMPTY_PDF")
        self.assertIn("OCR", ctx.exception.message)

    def test_pdf_mixed_text_and_empty_pages(self) -> None:
        """多页中部分为文本、部分为空白/扫描页，提取文本并出具明确警告。"""
        pages = [
            "第一页有效正文：帝国旧历三百年。",
            "",  # 第 2 页空白/纯图
            "第三页有效正文：海潮退去后的白骨滩涂。",
        ]
        pdf_bytes = create_test_pdf(pages)

        parsed = parse_pdf(pdf_bytes, "mixed.pdf", self.limits)

        self.assertEqual(parsed.page_count, 3)
        self.assertIn("第一页有效正文", parsed.text)
        self.assertIn("第三页有效正文", parsed.text)
        # 警告中应当明确提及第 2 页未提取到文本
        warning_found = any("第 2 未提取到文本" in w or "第 2" in w for w in parsed.warnings)
        self.assertTrue(warning_found, f"Warnings were: {parsed.warnings}")

    def test_pdf_encrypted_rejected(self) -> None:
        """加密文档直接拦截，提示用户解密后重新上传。"""
        encrypted_pdf = create_test_pdf(["机密档案正文"], encrypted_password="pass123")

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(encrypted_pdf, "secret.pdf", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "ENCRYPTED_DOCUMENT")

    def test_pdf_corrupt_header_rejected(self) -> None:
        """缺少 %PDF- 头标识的伪造或损坏文件被拒绝。"""
        bad_data = b"This is plain text with .pdf extension, not a real pdf"

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(bad_data, "fake.pdf", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "CORRUPT_DOCUMENT")

    def test_pdf_empty_bytes_rejected(self) -> None:
        with self.assertRaises(ParseError) as ctx:
            parse_pdf(b"", "empty.pdf", self.limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "EMPTY_DOCUMENT")

    def test_pdf_exceeds_byte_limit(self) -> None:
        strict_limits = ParseLimits(max_bytes=100)
        pdf_bytes = create_test_pdf(["测试正文"])

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(pdf_bytes, "oversize.pdf", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "FILE_TOO_LARGE")

    def test_pdf_exceeds_page_limit(self) -> None:
        strict_limits = ParseLimits(max_pages=2)
        pages = ["第一页", "第二页", "第三页"]
        pdf_bytes = create_test_pdf(pages)

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(pdf_bytes, "long.pdf", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "FILE_TOO_LARGE")
        self.assertIn("页数超过上限", ctx.exception.message)

    def test_pdf_exceeds_extracted_char_limit(self) -> None:
        strict_limits = ParseLimits(max_extracted_chars=10)
        pdf_bytes = create_test_pdf(["这段文字明显超过了十个字符的提取上限"])

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(pdf_bytes, "many_chars.pdf", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "FILE_TOO_LARGE")
        self.assertIn("文本量超出限制", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
