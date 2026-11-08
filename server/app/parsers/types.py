"""文档解析器通用数据结构、限制配置与异常定义。

契约约定与 R3 硬化：
- docs/contracts/M1_R3_API.md §2.2 (UTF-16 计数) & §2.4 (结构化告警)
- ParsedDocument: 统一文档解析载体。
  - warning_records: 结构化告警记录（code/message/count），便于机器直接消费；
  - warnings: 旧版字符串列表，保留用于平滑过渡；
  - extracted_units: 规范化正文的 UTF-16 code units 数量。
- ParseLimits: 解析安全边界限制（文件字节、页数、UTF-16文本上限、DOCX Zip 条目与解压缩炸弹阈值）。
- ParseError: 兼容 ApiError 的解析层专用异常，提供规范化的 HTTP 状态码、机器错误码与详情。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from ..errors import ApiError


def u16len(text: str) -> int:
    """计算字符串对应的 UTF-16 code unit 长度（与浏览器 JS 一致）。"""
    return len(text.encode("utf-16-le")) // 2


class WarningCode:
    """标准结构化告警代码常量（依据 M1_R3_API.md §2.4）。"""

    PARTIAL_TEXT = "PARTIAL_TEXT"
    EMPTY_PAGES = "EMPTY_PAGES"
    TABLES_FLATTENED = "TABLES_FLATTENED"
    HEADERS_FOOTERS_SKIPPED = "HEADERS_FOOTERS_SKIPPED"
    TEXTBOXES_SKIPPED = "TEXTBOXES_SKIPPED"
    COLUMN_ORDER_UNCERTAIN = "COLUMN_ORDER_UNCERTAIN"
    EXTRACTION_WARNING = "EXTRACTION_WARNING"


class DocumentWarningRecord(TypedDict, total=False):
    """结构化文档告警记录。"""

    code: str
    message: str
    count: int


@dataclass(frozen=True)
class ParseLimits:
    """文档解析防护与资源限制配置。"""

    max_bytes: int = 2 * 1024 * 1024  # 默认原始文件大小上限
    max_pages: int = 500  # PDF 最大允许页数
    max_text_units: int = 2_000_000  # 提取后正文的 UTF-16 文本量上限（契约 §2.2）
    max_extracted_chars: int = 2_000_000  # 旧字段兼容别名
    max_zip_entries: int = 1000  # DOCX ZIP 压缩包条目数上限（防解包 DoS）
    max_uncompressed_bytes: int = 50 * 1024 * 1024  # DOCX 解包总大小上限（50 MiB 防压缩炸弹）
    max_uncompressed_ratio: float = 100.0  # DOCX 解压膨胀率上限

    @property
    def effective_max_units(self) -> int:
        """获取生效的 UTF-16 文本量上限。"""
        return min(self.max_text_units, self.max_extracted_chars)


@dataclass(frozen=True)
class ParsedDocument:
    """提取器返回的纯文本与结构化元数据载体。"""

    text: str
    media_type: str
    encoding: str = "utf-8"
    warnings: list[str] = field(default_factory=list)  # 旧字符串列表过渡兼容
    warning_records: list[DocumentWarningRecord] = field(default_factory=list)  # 结构化记录
    page_count: int | None = None
    extracted_chars: int = 0  # 兼容旧代码，数值与 extracted_units 一致
    extracted_units: int = 0  # 真实 UTF-16 code units 数量


class ParseError(ApiError):
    """解析层业务与安全异常，继承自 ApiError，便于主路由无缝捕获并返回统一错误体。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status=status, code=code, message=message, details=details)


class ExtractionLimitError(ParseError):
    """文本量超出 UTF-16 限制时抛出的专用异常（契约 §2.2: 422 EXTRACTION_LIMIT）。"""

    def __init__(
        self,
        limit: int,
        actual: int,
        message: str | None = None,
    ) -> None:
        msg = (
            message
            or f"文档提取后的正文文本量超出限制（当前 {actual} 码元，上限 {limit} 码元）。"
        )
        super().__init__(
            status=422,
            code="EXTRACTION_LIMIT",
            message=msg,
            details={"limit": limit, "actual": actual},
        )
