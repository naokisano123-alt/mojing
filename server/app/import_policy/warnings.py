"""文档导入告警记录规范化与兼容转换模块（R5-L 交付）。

契约与协作规约依据：
- docs/collaboration/ROUND5.md (R5-L 核心交付项 1)
- docs/collaboration/R5_GEMINI_LI.md
- docs/contracts/M1_R3_API.md §2.4 (结构化告警记录格式)

设计原则：
1. 健壮提取：正确处理 TypedDict (DocumentWarningRecord) 与通用 dict，容忍 count 字段缺省。
2. 保留未知合法机器码：不预设封闭白名单，对符合字符串格式的自定义或新引入 code 予以原样保留。
3. JSON 安全性：输出纯净的 dict 列表，只包含 Python 原生基础类型（str, int），无异常对象或复杂引用。
4. 告警去重：按 (code, message) 维度去除完全重复的告警项，避免同一告警多次落库。
5. 诚实回退：仅当 records 完全无有效结构化项时，才回退消费 legacy_warnings 纯字符串列表；
   回退时统一使用 EXTRACTION_WARNING 通用机器码，绝不通过中文关键词模糊猜测特定机器码。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

DEFAULT_LEGACY_WARNING_CODE = "EXTRACTION_WARNING"


def normalize_warning_records(
    records: Any = None,
    legacy_warnings: Any = (),
) -> list[dict[str, Any]]:
    """将提取器返回的告警项规范化为 JSON 安全的结构化字典列表。

    参数:
        records: 结构化告警记录列表/可迭代对象（通常为 DocumentWarningRecord 或 dict）。
        legacy_warnings: 旧版纯文本告警字符串列表（仅在 records 无有效项时作为 fallback 使用）。

    返回:
        规范化的字典列表，每项格式为:
            {"code": str, "message": str} 或 {"code": str, "message": str, "count": int}
    """
    normalized: list[dict[str, Any]] = []
    seen_keys: dict[tuple[str, str], int] = {}  # (code, message) -> index in normalized

    # 1. 尝试从结构化 records 中提取
    if records is not None:
        iterable_records: Iterable[Any]
        if isinstance(records, (Mapping, dict)):
            iterable_records = [records]
        elif isinstance(records, (list, tuple, set)) or hasattr(records, "__iter__"):
            iterable_records = records
        else:
            iterable_records = ()

        for item in iterable_records:
            if not isinstance(item, (Mapping, dict)):
                continue

            raw_code = item.get("code")
            if not isinstance(raw_code, str):
                continue
            clean_code = raw_code.strip()
            if not clean_code:
                continue

            raw_message = item.get("message")
            if raw_message is None:
                clean_message = clean_code
            else:
                clean_message = str(raw_message).strip()

            entry: dict[str, Any] = {
                "code": clean_code,
                "message": clean_message,
            }

            raw_count = item.get("count")
            if raw_count is not None:
                try:
                    count_val = int(raw_count)
                    if count_val >= 0:
                        entry["count"] = count_val
                except (ValueError, TypeError):
                    pass

            dedup_key = (clean_code, clean_message)
            if dedup_key in seen_keys:
                prev_idx = seen_keys[dedup_key]
                if "count" in entry and "count" not in normalized[prev_idx]:
                    normalized[prev_idx]["count"] = entry["count"]
            else:
                seen_keys[dedup_key] = len(normalized)
                normalized.append(entry)

    # 2. 若存在有效结构化项，直接返回（不混入 legacy 字符串，避免语义混乱）
    if normalized:
        return normalized

    # 3. 仅在 records 无有效项时，回退读取 legacy_warnings
    if legacy_warnings:
        iterable_legacy: Iterable[Any]
        if isinstance(legacy_warnings, str):
            iterable_legacy = [legacy_warnings]
        elif isinstance(legacy_warnings, (list, tuple, set)) or hasattr(legacy_warnings, "__iter__"):
            iterable_legacy = legacy_warnings
        else:
            iterable_legacy = ()

        for w in iterable_legacy:
            if not w:
                continue
            clean_text = str(w).strip()
            if not clean_text:
                continue

            dedup_key = (DEFAULT_LEGACY_WARNING_CODE, clean_text)
            if dedup_key not in seen_keys:
                seen_keys[dedup_key] = len(normalized)
                normalized.append({
                    "code": DEFAULT_LEGACY_WARNING_CODE,
                    "message": clean_text,
                })

    return normalized
