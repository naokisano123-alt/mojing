"""文档提取模块公共导出。"""

from __future__ import annotations

from .docx import parse_docx
from .extractor import extract_document
from .pdf import parse_pdf
from .types import (
    DocumentWarningRecord,
    ExtractionLimitError,
    ParsedDocument,
    ParseError,
    ParseLimits,
    WarningCode,
    u16len,
)

__all__ = [
    "extract_document",
    "parse_pdf",
    "parse_docx",
    "ParsedDocument",
    "ParseError",
    "ParseLimits",
    "ExtractionLimitError",
    "DocumentWarningRecord",
    "WarningCode",
    "u16len",
]
