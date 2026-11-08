"""PDF 文本提取器（R5-L 增强与语义修正）。

设计考量与 R5 硬化：
- 仅支持文本型 PDF，使用纯 Python 库 pypdf 提取页面文本；
- 严禁假成功与私自调用 OCR：纯图片扫描件或未提取到文字的 PDF 明确报错拒收；
- 混合型 PDF（部分页面为空白/无文字）：提取可用页面，必须明确产出 PARTIAL_TEXT 结构化告警；
- 诚实告警语义（R5-L 修正）：
  * 纠正原先“无法分离页眉页脚却输出 HEADERS_FOOTERS_SKIPPED”的不实语义；
    改用 COLUMN_ORDER_UNCERTAIN 与 EXTRACTION_WARNING 诚实表达提取限制；
  * 异常页或无文本页诚实描述为“可能为空白页、格式异常或未嵌入文字的图片”，不武断判定为扫描件；
- 逐步累计 UTF-16 预算（R5-L 核心）：
  * 按页累计 UTF-16 文本量（包含页面拼接使用的 "\n\n" 分隔符，各占 2 个码元）；
  * 一旦累积超过 effective_max_units 立即中断并抛出 ExtractionLimitError，不进行后续无谓页面提取与全量 join；
  * 说明：单页解析内部的瞬间 CPU / 内存开销不受此计数硬隔离，不夸大宣称绝对防御资源耗尽。
- 加密支持：允许空密码可读文档，真实密码加密文档诚实报 422 ENCRYPTED_DOCUMENT；
- 页面顺序：按自然页码顺序拼接正文（双换行分隔）。
"""

from __future__ import annotations

import io
from typing import Any

import pypdf
from pypdf.errors import PdfReadError

from .types import (
    DocumentWarningRecord,
    ExtractionLimitError,
    ParsedDocument,
    ParseError,
    ParseLimits,
    WarningCode,
    u16len,
)


def parse_pdf(
    data: bytes,
    filename: str,
    limits: ParseLimits,
) -> ParsedDocument:
    """从二进制数据中提取 PDF 文本并执行安全、逐步限额与格式检查。"""
    if not data:
        raise ParseError(422, "EMPTY_DOCUMENT", "文件是空的，没有可导入的文字。")

    if len(data) > limits.max_bytes:
        raise ParseError(
            413,
            "FILE_TOO_LARGE",
            f"文件超过 {limits.max_bytes // 1024 // 1024} MiB 上限。",
            {"max_bytes": limits.max_bytes, "received_bytes": len(data)},
        )

    # 基础文件头识别（允许前 1024 字节内存在空白或注释）
    header_chunk = data[:1024]
    if b"%PDF-" not in header_chunk:
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "文件缺少标准的 PDF 文件头标识（%PDF-），不是合法的 PDF 文档。",
            {"filename": filename},
        )

    bio = io.BytesIO(data)
    try:
        reader = pypdf.PdfReader(bio, strict=False)
    except (PdfReadError, Exception) as exc:
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "无法解析该 PDF 文件，文件结构可能已损坏或格式不兼容。",
            {"filename": filename, "error_detail": str(exc)},
        ) from exc

    # 加密文件处理（规范：允许空密码可读；若设真实密码则诚实拒绝，不误判为正常文件）
    if reader.is_encrypted:
        try:
            decrypt_result = reader.decrypt("")
            if decrypt_result == 0:
                raise ParseError(
                    422,
                    "ENCRYPTED_DOCUMENT",
                    "PDF 文件已设置访问密码，无法直接读取内容。请解密后重新上传。",
                    {"filename": filename},
                )
            # 确认解密后页面可访问
            _ = len(reader.pages)
        except Exception as exc:
            if isinstance(exc, ParseError):
                raise
            raise ParseError(
                422,
                "ENCRYPTED_DOCUMENT",
                "PDF 文件已加密且密码验证失败，无法读取内容。",
                {"filename": filename, "error_detail": str(exc)},
            ) from exc

    try:
        total_pages = len(reader.pages)
    except Exception as exc:
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "无法读取 PDF 页面列表，文档可能损坏。",
            {"filename": filename, "error_detail": str(exc)},
        ) from exc

    if total_pages == 0:
        raise ParseError(
            422,
            "EMPTY_DOCUMENT",
            "PDF 文档不包含任何可用页面。",
            {"filename": filename},
        )

    if total_pages > limits.max_pages:
        raise ParseError(
            413,
            "FILE_TOO_LARGE",
            f"PDF 页数超过上限（文档包含 {total_pages} 页，最大允许 {limits.max_pages} 页）。",
            {"filename": filename, "total_pages": total_pages, "max_pages": limits.max_pages},
        )

    pages_text: list[str] = []
    empty_pages: list[int] = []
    accumulated_units = 0
    effective_limit = limits.effective_max_units

    for page_idx in range(1, total_pages + 1):
        try:
            page = reader.pages[page_idx - 1]
            raw_text = page.extract_text() or ""
        except Exception:
            empty_pages.append(page_idx)
            continue

        normalized_page = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized_page:
            empty_pages.append(page_idx)
            continue

        # 逐步累计 UTF-16 文本量预算（段间以 "\n\n" 拼接，占 2 个 UTF-16 码元）
        page_units = u16len(normalized_page)
        delimiter_units = 2 if pages_text else 0
        candidate_units = accumulated_units + delimiter_units + page_units

        # 超限立即中断解析并报错，不执行后续无谓抽取
        if candidate_units > effective_limit:
            raise ExtractionLimitError(
                limit=effective_limit,
                actual=candidate_units,
                message=(
                    f"PDF 提取正文文本量超出限制（在第 {page_idx} 页累计约 {candidate_units} 码元，"
                    f"上限 {effective_limit} 码元）。"
                ),
            )

        accumulated_units = candidate_units
        pages_text.append(normalized_page)

    # 纯扫描件、异常或全空白页拒绝
    if not pages_text:
        raise ParseError(
            422,
            "SCANNED_OR_EMPTY_PDF",
            "PDF 中未检测到可提取的文本内容（可能是纯图片扫描件、空白页或提取异常）。"
            "系统暂不支持 OCR 识别，请导入包含可选中文本的文档。",
            {"filename": filename, "total_pages": total_pages},
        )

    warnings: list[str] = []
    warning_records: list[DocumentWarningRecord] = []

    if empty_pages:
        if len(empty_pages) <= 8:
            empty_desc = ", ".join(str(p) for p in empty_pages)
        else:
            empty_desc = ", ".join(str(p) for p in empty_pages[:8]) + f" 等共 {len(empty_pages)} 页"

        msg_partial = (
            f"PDF 共有 {total_pages} 页，其中第 {empty_desc} 页未提取到文本"
            "（可能为空白页、格式异常或未嵌入文字的图片）；已提取可用页面的文本（部分内容可能缺失）。"
        )
        warnings.append(msg_partial)

        # 结构化告警：保持 PARTIAL_TEXT 与 EMPTY_PAGES
        warning_records.append(
            {
                "code": WarningCode.PARTIAL_TEXT,
                "message": f"部分页面未提取到文字内容（共 {len(empty_pages)} 页缺失）",
                "count": len(empty_pages),
            }
        )
        warning_records.append(
            {
                "code": WarningCode.EMPTY_PAGES,
                "message": f"第 {empty_desc} 页未提取到文本（可能为空白页、异常或扫描件）",
                "count": len(empty_pages),
            }
        )

    # 诚实呈现版面限制：去除原先不实的 HEADERS_FOOTERS_SKIPPED，准确表达流顺序与无法分离页眉页脚
    msg_layout = (
        "PDF 文本提取受排版顺序影响（多栏可能按流顺序读取）；"
        "不支持页眉、页脚及复杂矢量标注与正文的结构化分离。"
    )
    warnings.append(msg_layout)
    warning_records.append(
        {
            "code": WarningCode.COLUMN_ORDER_UNCERTAIN,
            "message": "PDF 文本提取受排版顺序影响（多栏可能按流顺序读取）",
        }
    )
    warning_records.append(
        {
            "code": WarningCode.EXTRACTION_WARNING,
            "message": "不支持页眉、页脚及复杂矢量标注与正文的独立分离",
        }
    )

    full_text = "\n\n".join(pages_text)
    final_units = u16len(full_text)

    return ParsedDocument(
        text=full_text,
        media_type="application/pdf",
        encoding="utf-8",
        warnings=warnings,
        warning_records=warning_records,
        page_count=total_pages,
        extracted_chars=final_units,
        extracted_units=final_units,
    )
