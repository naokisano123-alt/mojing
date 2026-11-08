"""PDF 文本提取器。

设计考量：
- 仅支持文本型 PDF，使用纯 Python 库 pypdf 提取页面文本；
- 严禁假成功与私自调用 OCR：纯图片扫描件或未提取到文字的 PDF 明确报错拒收；
- 混合型 PDF（部分页面为图片/空白，部分页面为文本）：提取可用页面并明确提供 warnings 提示；
- 安全防护：检测加密文件、损坏文件、超大页数与提取字符上限；
- 页面顺序：按自然页码顺序拼接正文（双换行分隔），方便后续分块与 UTF-16 偏移计算。
"""

from __future__ import annotations

import io
from typing import Any

import pypdf
from pypdf.errors import PdfReadError

from .types import ParsedDocument, ParseError, ParseLimits


def parse_pdf(
    data: bytes,
    filename: str,
    limits: ParseLimits,
) -> ParsedDocument:
    """从二进制数据中提取 PDF 文本并执行安全与格式检查。"""
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

    # 加密文件检测
    if reader.is_encrypted:
        try:
            # 尝试空密码解密（部分未设密码但标记了加密字典的文档）
            decrypt_result = reader.decrypt("")
            if reader.is_encrypted or decrypt_result == 0:
                raise ParseError(
                    422,
                    "ENCRYPTED_DOCUMENT",
                    "PDF 文件已加密，无法直接读取内容。请解密后重新上传。",
                    {"filename": filename},
                )
        except Exception as exc:
            if isinstance(exc, ParseError):
                raise
            raise ParseError(
                422,
                "ENCRYPTED_DOCUMENT",
                "PDF 文件已加密且密码验证失败，无法读取内容。",
                {"filename": filename},
            ) from exc

    total_pages = len(reader.pages)
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

    for page_idx, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception:
            empty_pages.append(page_idx)
            continue

        normalized_page = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if normalized_page:
            pages_text.append(normalized_page)
        else:
            empty_pages.append(page_idx)

    # 纯扫描件或全空白页拒绝
    if not pages_text:
        raise ParseError(
            422,
            "SCANNED_OR_EMPTY_PDF",
            "PDF 中未检测到可提取的文本内容（可能是纯图片扫描件或空白文件）。系统暂不支持 OCR 识别，请导入包含可选中文本的文档。",
            {"filename": filename, "total_pages": total_pages},
        )

    warnings: list[str] = []
    if empty_pages:
        if len(empty_pages) <= 8:
            empty_desc = ", ".join(str(p) for p in empty_pages)
        else:
            empty_desc = ", ".join(str(p) for p in empty_pages[:8]) + f" 等共 {len(empty_pages)} 页"
        warnings.append(
            f"PDF 共有 {total_pages} 页，其中第 {empty_desc} 未提取到文本（可能是图片或空白页）；已提取可用页面的文本（部分内容可能缺失）。"
        )

    warnings.append(
        "PDF 文本提取受排版顺序影响（如多栏排版可能按数据流顺序读取）；不支持页眉、页脚及复杂矢量标注的分离。"
    )

    full_text = "\n\n".join(pages_text)
    char_count = len(full_text)

    if char_count > limits.max_extracted_chars:
        raise ParseError(
            413,
            "FILE_TOO_LARGE",
            f"PDF 提取后的文本量超出限制（当前约 {char_count} 字符，上限 {limits.max_extracted_chars} 字符）。",
            {"filename": filename, "extracted_chars": char_count, "max_chars": limits.max_extracted_chars},
        )

    return ParsedDocument(
        text=full_text,
        media_type="application/pdf",
        encoding="utf-8",
        warnings=warnings,
        page_count=total_pages,
        extracted_chars=char_count,
    )
