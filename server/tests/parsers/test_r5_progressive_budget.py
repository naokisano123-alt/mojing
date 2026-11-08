"""R5-L 解析器逐步预算与告警语义纠正单元测试。

测试覆盖（依据 R5_GEMINI_LI.md）：
1. PDF 逐步累计 UTF-16 预算：在超限页面立即中断，不提取后续页面。
2. DOCX 逐步累计 UTF-16 预算：在超限段落/表格立即中断，不解析后续正文块。
3. 修正 PDF 告警语义：
   - 彻底移除不实的 HEADERS_FOOTERS_SKIPPED 机器码；
   - 正确产出 COLUMN_ORDER_UNCERTAIN 与 EXTRACTION_WARNING 表达流顺序与无法分离页眉页脚；
   - 保持混合页 PARTIAL_TEXT 与 EMPTY_PAGES。
"""

from __future__ import annotations

import unittest

from app.parsers import (
    ExtractionLimitError,
    ParseLimits,
    WarningCode,
    parse_docx,
    parse_pdf,
    u16len,
)

from .sample_helpers import create_test_docx, create_test_pdf


class TestR5ProgressiveBudgetAndWarnings(unittest.TestCase):
    def test_pdf_progressive_budget_aborts_early(self) -> None:
        """测试 PDF 按页逐步累加 UTF-16 预算，超限时立即抛出 ExtractionLimitError 并在超限点截断。"""
        page1 = "第一页内容：青冥之界。"
        page2 = "第二页内容：渡云港商船出海。"
        page3 = "第三页内容：极北寒夜的大雪。"
        pdf_bytes = create_test_pdf([page1, page2, page3])

        u1 = u16len(page1)
        u2 = u16len(page2)
        expected_two_pages_units = u1 + 2 + u2

        limits = ParseLimits(max_text_units=expected_two_pages_units + 5)
        with self.assertRaises(ExtractionLimitError) as ctx:
            parse_pdf(pdf_bytes, "progressive.pdf", limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "EXTRACTION_LIMIT")
        self.assertEqual(ctx.exception.details["limit"], expected_two_pages_units + 5)
        self.assertEqual(ctx.exception.details["actual"], expected_two_pages_units + 2 + u16len(page3))

    def test_docx_progressive_budget_aborts_early(self) -> None:
        """测试 DOCX 按块逐步累加 UTF-16 预算并在超限时立即截断。"""
        p1 = "云山纪第一章：凡人不可登临之山峰。"
        p2 = "云山纪第二章：摘星崖上的千年剑痕。"
        p3 = "云山纪第三章：听涛阁记载的最后一次灵舟。"
        docx_bytes = create_test_docx([("p", p1), ("p", p2), ("p", p3)])

        u1 = u16len(p1)
        u2 = u16len(p2)
        u3 = u16len(p3)
        expected_two_blocks = u1 + 2 + u2

        limits = ParseLimits(max_text_units=expected_two_blocks + 2)
        with self.assertRaises(ExtractionLimitError) as ctx:
            parse_docx(docx_bytes, "progressive.docx", limits)

        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "EXTRACTION_LIMIT")
        self.assertEqual(ctx.exception.details["limit"], expected_two_blocks + 2)
        self.assertEqual(ctx.exception.details["actual"], expected_two_blocks + 2 + u3)

    def test_pdf_honest_warning_semantics(self) -> None:
        """测试 PDF 告警彻底纠正：不再宣称 HEADERS_FOOTERS_SKIPPED，改用诚实的限制说明。"""
        pdf_bytes = create_test_pdf(["正常单页正文"])
        doc = parse_pdf(pdf_bytes, "single.pdf", ParseLimits())

        codes = [r["code"] for r in doc.warning_records]
        self.assertNotIn(
            WarningCode.HEADERS_FOOTERS_SKIPPED,
            codes,
            "PDF 不支持分离页眉页脚，不能虚假声称 HEADERS_FOOTERS_SKIPPED",
        )
        self.assertIn(WarningCode.COLUMN_ORDER_UNCERTAIN, codes)
        self.assertIn(WarningCode.EXTRACTION_WARNING, codes)

    def test_pdf_mixed_keeps_partial_text_and_empty_pages(self) -> None:
        """测试混合型 PDF 真实保留 PARTIAL_TEXT 与 EMPTY_PAGES。"""
        pdf_bytes = create_test_pdf(["第一页有文字", "", "第三页有文字"])
        doc = parse_pdf(pdf_bytes, "mixed.pdf", ParseLimits())

        codes = [r["code"] for r in doc.warning_records]
        self.assertIn(WarningCode.PARTIAL_TEXT, codes)
        self.assertIn(WarningCode.EMPTY_PAGES, codes)
        self.assertNotIn(WarningCode.HEADERS_FOOTERS_SKIPPED, codes)


if __name__ == "__main__":
    unittest.main()
