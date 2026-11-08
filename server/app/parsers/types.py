"""文档解析器通用数据结构、限制配置与异常定义。

契约约定：
- ParsedDocument: 统一的文档解析输出载体。
  - 对于二进制提取格式 (PDF/DOCX)，encoding 标为 "utf-8"，表示提取出的纯文本表示形式；
  - warnings: 记录非致命降级提示（如混合扫描页、跳过的页眉页脚与文本框）。
- ParseLimits: 解析安全边界限制（文件字节、页数、提取字符数、解压缩炸弹阈值）。
- ParseError: 兼容 ApiError 的解析层专用异常，提供规范化的 HTTP 状态码、机器错误码与详情。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import ApiError


@dataclass(frozen=True)
class ParseLimits:
    """文档解析防护与资源限制配置。"""

    max_bytes: int = 2 * 1024 * 1024  # 默认 2 MiB 原始文件大小
    max_pages: int = 500  # PDF 最大允许页数
    max_extracted_chars: int = 2_000_000  # 提取后最大字符数（防止内存膨胀）
    max_uncompressed_bytes: int = 50 * 1024 * 1024  # DOCX 解包总大小上限（50 MiB 防压缩炸弹）
    max_uncompressed_ratio: float = 100.0  # DOCX 解压膨胀率上限（超过则拦截）


@dataclass(frozen=True)
class ParsedDocument:
    """提取器返回的纯文本与元数据载体。"""

    text: str
    media_type: str
    encoding: str = "utf-8"
    warnings: list[str] = field(default_factory=list)
    page_count: int | None = None
    extracted_chars: int = 0


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
