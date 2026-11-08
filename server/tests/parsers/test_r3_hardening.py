"""R3-L 解析质量与安全硬化单元测试套件。

覆盖要求（依据 R3_GEMINI_LI.md 与 M1_R3_API.md）：
1. max_text_units UTF-16 统计（B3）：Emoji 代理对计为 2 个码元，验证按 UTF-16 准确拦截超限并返回 422 EXTRACTION_LIMIT。
2. 结构化告警记录 warning_records（B4）：
   - 混合型 PDF（部分页面空白/无文本）真产出 PARTIAL_TEXT 机器码与 count，同时产出 EMPTY_PAGES；
   - DOCX 表格产出 TABLES_FLATTENED 机器码与表格数量 count；
   - 保持旧版 warnings 字符串列表共存过渡。
3. DOCX ZIP 条目数上限与压缩炸弹防护：条目数超限时拦截并报 413 ZIP_BOMB_DETECTED。
4. PDF 真实加密保护：设置密码的 PDF 诚实返回 422 ENCRYPTED_DOCUMENT。
"""

from __future__ import annotations

import io
import unittest
import zipfile

import pypdf

from app.parsers import (
    ExtractionLimitError,
    ParseError,
    ParseLimits,
    WarningCode,
    extract_document,
    parse_docx,
    parse_pdf,
    u16len,
)

from .sample_helpers import create_test_docx, create_test_pdf


class TestR3Hardening(unittest.TestCase):
    def test_u16len_with_emojis(self) -> None:
        """测试 UTF-16 码元计算：BMP 字符为 1，Emoji 等代理对字符为 2。"""
        # 普通纯中文：每个字 1 个码元
        self.assertEqual(u16len("云山之巅"), 4)
        # 包含 2 个 Emoji：'🏰' 和 '🐉'，各占 2 个 UTF-16 码元
        text_with_emoji = "城堡🏰巨龙🐉"
        # Python len() 是 6，但 UTF-16 是 4 + 2*2 = 8
        self.assertEqual(len(text_with_emoji), 6)
        self.assertEqual(u16len(text_with_emoji), 8)

    def test_txt_max_text_units_counts_utf16(self) -> None:
        """B3: 纯文本提取器按 UTF-16 code units 计算上限，而不是 Python len()。"""
        # 5 个 Emoji: Python len 为 5，UTF-16 units 为 10
        emojis = "🏰🐉🗡️🛡️✨"
        emojis_bytes = emojis.encode("utf-8")
        actual_u16 = u16len(emojis)
        self.assertGreater(actual_u16, len(emojis))

        # 设置上限为 8（小于 10 个 UTF-16 码元，但大于 5）
        limits = ParseLimits(max_text_units=8)

        # 如果错误地使用 Python len()（5 <= 8），则不会报错；
        # 正确按 UTF-16 码元计（10 > 8），必须抛出 422 EXTRACTION_LIMIT。
        with self.assertRaises(ExtractionLimitError) as ctx:
            extract_document(emojis_bytes, "test.txt", limits=limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "EXTRACTION_LIMIT")
        self.assertEqual(ctx.exception.details["limit"], 8)
        self.assertEqual(ctx.exception.details["actual"], actual_u16)

    def test_pdf_max_text_units_counts_utf16(self) -> None:
        """B3: PDF 提取文本按 UTF-16 units 统计并拦截超限。"""
        # 生成包含 Emoji 的 PDF 内容
        content = "云山🏰秘境🐉"
        pdf_bytes = create_test_pdf([content])
        actual_u16 = u16len(content)

        # 设置上限小于 actual_u16
        limits = ParseLimits(max_text_units=actual_u16 - 1)
        with self.assertRaises(ExtractionLimitError) as ctx:
            parse_pdf(pdf_bytes, "emoji.pdf", limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "EXTRACTION_LIMIT")
        self.assertEqual(ctx.exception.details["limit"], actual_u16 - 1)
        self.assertEqual(ctx.exception.details["actual"], actual_u16)

    def test_pdf_partial_text_warning_records(self) -> None:
        """B4: 混合型 PDF（有文本页与空页）必须输出包含 PARTIAL_TEXT 的结构化告警记录。"""
        # 创建 2 页 PDF：第 1 页有文字，第 2 页为空白
        pdf_bytes = create_test_pdf(["第 1 页正文内容", "   "])

        doc = parse_pdf(pdf_bytes, "mixed.pdf", ParseLimits())
        self.assertEqual(doc.page_count, 2)
        self.assertEqual(doc.text, "第 1 页正文内容")

        # 验证结构化告警记录 warning_records
        records = doc.warning_records
        self.assertTrue(len(records) > 0)

        codes = [r["code"] for r in records]
        self.assertIn(WarningCode.PARTIAL_TEXT, codes, "混合 PDF 必须产出 PARTIAL_TEXT 机器告警")
        self.assertIn(WarningCode.EMPTY_PAGES, codes)

        partial_rec = next(r for r in records if r["code"] == WarningCode.PARTIAL_TEXT)
        self.assertEqual(partial_rec["count"], 1)

        # 验证旧版 warnings 字符串列表依然保留
        self.assertTrue(len(doc.warnings) > 0)
        self.assertTrue(any("未提取到文本" in w for w in doc.warnings))

    def test_docx_warning_records_for_tables(self) -> None:
        """B4: DOCX 包含表格时必须输出 TABLES_FLATTENED 结构化告警。"""
        docx_bytes = create_test_docx(
            [
                ("p", "表格说明段落"),
                ("table", [["属性", "数值"], ["生命值", "100"]]),
            ]
        )

        doc = parse_docx(docx_bytes, "table.docx", ParseLimits())
        codes = [r["code"] for r in doc.warning_records]

        self.assertIn(WarningCode.TABLES_FLATTENED, codes)
        self.assertIn(WarningCode.HEADERS_FOOTERS_SKIPPED, codes)

        tbl_rec = next(r for r in doc.warning_records if r["code"] == WarningCode.TABLES_FLATTENED)
        self.assertEqual(tbl_rec["count"], 1)

    def test_docx_max_zip_entries_protection(self) -> None:
        """DOCX ZIP 条目数过多时拦截（防御 zip-bomb DoS 攻击）。"""
        # 构造包含大量条目的合法 ZIP DOCX
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as zf:
            zf.writestr("word/document.xml", "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>")
            for i in range(15):
                zf.writestr(f"dummy_{i}.txt", "dummy")

        data = bio.getvalue()
        # 限制最大条目数为 10
        strict_limits = ParseLimits(max_zip_entries=10)

        with self.assertRaises(ParseError) as ctx:
            parse_docx(data, "too_many_entries.docx", strict_limits)

        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.code, "ZIP_BOMB_DETECTED")
        self.assertIn("条目数异常", ctx.exception.message)

    def test_pdf_real_encryption_rejected(self) -> None:
        """真实加密密码的 PDF 诚实返回 422 ENCRYPTED_DOCUMENT。"""
        # 创建带有实际密码加密的 PDF
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("secret_password_123")

        bio = io.BytesIO()
        writer.write(bio)
        encrypted_bytes = bio.getvalue()

        with self.assertRaises(ParseError) as ctx:
            parse_pdf(encrypted_bytes, "encrypted.pdf", ParseLimits())

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "ENCRYPTED_DOCUMENT")


if __name__ == "__main__":
    unittest.main()
