"""统一文档提取入口。

契约接口：
`extract_document(data: bytes, filename: str, *, limits: ParseLimits | None = None) -> ParsedDocument`
根据文件后缀自动分发到 PDF / DOCX 独立提取器；同时兼容 TXT / Markdown 格式，
确保接口统一可复用。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .pdf import parse_pdf
from .types import (
    ExtractionLimitError,
    ParsedDocument,
    ParseError,
    ParseLimits,
    u16len,
)

_SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


@lru_cache(maxsize=1)
def _load_docx_parser():
    try:
        from .docx import parse_docx as parser
    except (ImportError, OSError):
        return None
    return parser


def docx_available() -> bool:
    return _load_docx_parser() is not None


def parse_docx(data: bytes, filename: str, limits: ParseLimits) -> ParsedDocument:
    parser = _load_docx_parser()
    if parser is None:
        raise ParseError(503, "DOCX_UNAVAILABLE", "当前设备无法加载 Word 解析组件。请另存为 TXT 或 Markdown 后导入。")
    return parser(data, filename, limits)


def extract_document(
    data: bytes,
    filename: str,
    *,
    limits: ParseLimits | None = None,
) -> ParsedDocument:
    """从二进制数据中提取规范化文本并返回标准元数据。

    Args:
        data: 原始文件字节数据。
        filename: 原始文件名（用于判断扩展名）。
        limits: 解析资源与安全限制，为空时使用默认配置。

    Returns:
        ParsedDocument 包含提取正文、格式媒体类型、编码与安全警告。

    Raises:
        ParseError: 当格式不支持、空内容、文件损坏、加密、扫描件无文本或超出安全上限时抛出。
    """
    effective_limits = limits if limits is not None else ParseLimits()

    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_EXTENSIONS:
        raise ParseError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            f"暂不支持的文件格式: '{suffix}'。支持的格式包括: {', '.join(_SUPPORTED_EXTENSIONS.keys())}。",
            {"filename": filename, "suffix": suffix, "allowed": list(_SUPPORTED_EXTENSIONS.keys())},
        )

    if suffix == ".pdf":
        return parse_pdf(data, filename, effective_limits)

    if suffix == ".docx":
        return parse_docx(data, filename, effective_limits)

    # 针对 .txt 与 .md 的兼容通道
    if not data:
        raise ParseError(422, "EMPTY_DOCUMENT", "文件是空的，没有可导入的文字。")

    if len(data) > effective_limits.max_bytes:
        raise ParseError(
            413,
            "FILE_TOO_LARGE",
            f"文件超过 {effective_limits.max_bytes // 1024 // 1024} MiB 上限。",
            {"max_bytes": effective_limits.max_bytes, "received_bytes": len(data)},
        )

    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            text = data.decode("gb18030")
            encoding = "gb18030"
        except UnicodeDecodeError as exc:
            raise ParseError(
                422,
                "ENCODING_UNSUPPORTED",
                "文本文件编码无法识别，请保存为 UTF-8 编码后重试。",
                {"filename": filename},
            ) from exc

    cleaned_text = text.replace("\r\n", "\n").replace("\r", "\n")
    if cleaned_text.startswith("\ufeff"):
        cleaned_text = cleaned_text[1:]

    if not cleaned_text.strip():
        raise ParseError(422, "EMPTY_DOCUMENT", "文本文件没有可提取的文字内容。")

    text_units = u16len(cleaned_text)
    effective_limit = effective_limits.effective_max_units

    if text_units > effective_limit:
        raise ExtractionLimitError(
            limit=effective_limit,
            actual=text_units,
            message=f"纯文本提取后的文本量超出限制（当前约 {text_units} 码元，上限 {effective_limit} 码元）。",
        )

    media_type = _SUPPORTED_EXTENSIONS[suffix]
    return ParsedDocument(
        text=cleaned_text,
        media_type=media_type,
        encoding=encoding,
        warnings=[],
        warning_records=[],
        page_count=None,
        extracted_chars=text_units,
        extracted_units=text_units,
    )
