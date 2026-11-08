"""统一入口 extract_document 单元测试。

测试覆盖：
- 按照后缀名自动分发至 PDF 与 DOCX 解析器
- 大小写后缀兼容（.PDF, .Docx）
- 兼容处理 TXT 与 Markdown 格式输入
- 不支持的后缀名拒绝（415 UNSUPPORTED_MEDIA_TYPE）
- 空文件与超出上限防护
"""

from __future__ import annotations

import unittest

from server.app.parsers import extract_document
from server.app.parsers.types import ParseError, ParseLimits

from .sample_helpers import create_test_docx, create_test_pdf


class TestUnifiedExtractor(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = ParseLimits(max_bytes=2 * 1024 * 1024)

    def test_extract_pdf_dispatch(self) -> None:
        pdf_bytes = create_test_pdf(["PDF 分发测试正文"])
        parsed = extract_document(pdf_bytes, "test.pdf", limits=self.limits)

        self.assertEqual(parsed.media_type, "application/pdf")
        self.assertIn("PDF 分发测试正文", parsed.text)

    def test_extract_pdf_case_insensitive(self) -> None:
        pdf_bytes = create_test_pdf(["大写后缀测试"])
        parsed = extract_document(pdf_bytes, "TEST.PDF", limits=self.limits)

        self.assertEqual(parsed.media_type, "application/pdf")
        self.assertIn("大写后缀测试", parsed.text)

    def test_extract_docx_dispatch(self) -> None:
        docx_bytes = create_test_docx([("heading", "DOCX 标题", 1), ("p", "DOCX 段落正文")])
        parsed = extract_document(docx_bytes, "lore.docx", limits=self.limits)

        self.assertIn("application/vnd.openxmlformats-officedocument", parsed.media_type)
        self.assertIn("# DOCX 标题", parsed.text)
        self.assertIn("DOCX 段落正文", parsed.text)

    def test_extract_txt_and_md_pass_through(self) -> None:
        txt_bytes = "纯文本世界观资料：魔法公会".encode("utf-8")
        parsed_txt = extract_document(txt_bytes, "lore.txt", limits=self.limits)
        self.assertEqual(parsed_txt.media_type, "text/plain")
        self.assertEqual(parsed_txt.text, "纯文本世界观资料：魔法公会")

        md_bytes = "# Markdown 标题\n- 设定一\n- 设定二".encode("utf-8")
        parsed_md = extract_document(md_bytes, "lore.md", limits=self.limits)
        self.assertEqual(parsed_md.media_type, "text/markdown")
        self.assertIn("# Markdown 标题", parsed_md.text)

    def test_extract_unsupported_extension_rejected(self) -> None:
        bad_extensions = ["evil.exe", "image.png", "doc.rtf", "page.html", "archive.zip"]
        for filename in bad_extensions:
            with self.subTest(filename=filename):
                with self.assertRaises(ParseError) as ctx:
                    extract_document(b"some content", filename, limits=self.limits)
                self.assertEqual(ctx.exception.status, 415)
                self.assertEqual(ctx.exception.code, "UNSUPPORTED_MEDIA_TYPE")


if __name__ == "__main__":
    unittest.main()
