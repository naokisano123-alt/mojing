"""单元测试：告警记录规范化与兼容转换 (normalize_warning_records)。

依据：
- docs/collaboration/ROUND5.md
- docs/collaboration/R5_GEMINI_LI.md
- docs/collaboration/R4_GEMINI_LI.md
"""

from __future__ import annotations

import json
import unittest

from app.import_policy.warnings import (
    DEFAULT_LEGACY_WARNING_CODE,
    normalize_warning_records,
)


class TestNormalizeWarningRecords(unittest.TestCase):
    def test_standard_records_with_and_without_count(self) -> None:
        """测试正常提取的结构化记录：count 可有可无，均能正确序列化。"""
        records = [
            {"code": "PARTIAL_TEXT", "message": "部分页面缺失", "count": 2},
            {"code": "COLUMN_ORDER_UNCERTAIN", "message": "多栏流顺序未定"},
        ]
        result = normalize_warning_records(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {"code": "PARTIAL_TEXT", "message": "部分页面缺失", "count": 2})
        self.assertEqual(result[1], {"code": "COLUMN_ORDER_UNCERTAIN", "message": "多栏流顺序未定"})
        self.assertNotIn("count", result[1])
        dumped = json.dumps(result)
        self.assertIn("PARTIAL_TEXT", dumped)

    def test_preserve_unknown_valid_code(self) -> None:
        """测试未知但合法的机器码得到保留，不被强行过滤或丢弃。"""
        records = [
            {"code": "FUTURE_OCR_SKIPPED", "message": "未来可能新增的告警码", "count": 5},
            {"code": "CUSTOM_RULE_TRIGGERED", "message": "自定义规则触发"},
        ]
        result = normalize_warning_records(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["code"], "FUTURE_OCR_SKIPPED")
        self.assertEqual(result[0]["count"], 5)
        self.assertEqual(result[1]["code"], "CUSTOM_RULE_TRIGGERED")

    def test_filter_bad_shapes_and_invalid_codes(self) -> None:
        """过滤损坏的形状、非字典项、空白 code、非字符串 code 等。"""
        records = [
            None,
            "not a dict",
            123,
            {"bad_key": "no code"},
            {"code": "", "message": "empty code"},
            {"code": "   ", "message": "blank code"},
            {"code": 12345, "message": "numeric code"},
            {"code": "VALID_CODE", "message": None},
            {"code": "VALID_CODE_2"},
        ]
        result = normalize_warning_records(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["code"], "VALID_CODE")
        self.assertEqual(result[0]["message"], "VALID_CODE")
        self.assertEqual(result[1]["code"], "VALID_CODE_2")
        self.assertEqual(result[1]["message"], "VALID_CODE_2")

    def test_deduplication(self) -> None:
        """测试相同 (code, message) 的去重机制与 count 补齐。"""
        records = [
            {"code": "TEXTBOXES_SKIPPED", "message": "独立文本框已跳过"},
            {"code": "TEXTBOXES_SKIPPED", "message": "独立文本框已跳过"},
            {"code": "TEXTBOXES_SKIPPED", "message": "独立文本框已跳过", "count": 3},
        ]
        result = normalize_warning_records(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "TEXTBOXES_SKIPPED")
        self.assertEqual(result[0]["count"], 3)

    def test_legacy_fallback_when_records_empty(self) -> None:
        """当 records 为空或无有效项时，回退读取 legacy_warnings。"""
        legacy = [
            "PDF 文本提取受排版顺序影响；不支持页眉页脚分离。",
            "检测到 3 个表格已扁平化处理。",
        ]
        result = normalize_warning_records(records=[], legacy_warnings=legacy)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["code"], DEFAULT_LEGACY_WARNING_CODE)
        self.assertEqual(result[0]["message"], "PDF 文本提取受排版顺序影响；不支持页眉页脚分离。")
        self.assertEqual(result[1]["code"], DEFAULT_LEGACY_WARNING_CODE)
        self.assertEqual(result[1]["message"], "检测到 3 个表格已扁平化处理。")

    def test_legacy_fallback_does_not_guess_code_from_chinese(self) -> None:
        """回退模式下严禁根据中文子串去瞎猜机器码（如发现'页眉'就猜 HEADERS_FOOTERS_SKIPPED）。"""
        legacy = [
            "跳过了页眉页脚与图表标注",
            "部分页面由于扫描无法提取文字",
        ]
        result = normalize_warning_records(records=None, legacy_warnings=legacy)
        for item in result:
            self.assertEqual(item["code"], DEFAULT_LEGACY_WARNING_CODE)
            self.assertNotEqual(item["code"], "HEADERS_FOOTERS_SKIPPED")
            self.assertNotEqual(item["code"], "PARTIAL_TEXT")

    def test_structured_records_prevent_legacy_fallback(self) -> None:
        """只要存在有效的结构化记录，就不消费 legacy_warnings，避免混杂重复。"""
        records = [{"code": "PARTIAL_TEXT", "message": "部分文字缺失", "count": 1}]
        legacy = ["旧版警告文本：部分文字缺失"]
        result = normalize_warning_records(records=records, legacy_warnings=legacy)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "PARTIAL_TEXT")

    def test_empty_inputs_return_empty_list(self) -> None:
        """全部为空或无意义输入时返回空列表，不抛异常。"""
        self.assertEqual(normalize_warning_records(None, None), [])
        self.assertEqual(normalize_warning_records([], []), [])
        self.assertEqual(normalize_warning_records([{}], ["   "]), [])


if __name__ == "__main__":
    unittest.main()
