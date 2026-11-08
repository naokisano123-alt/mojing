"""DOCX 文本提取器。

设计考量与 R3 硬化：
- 支持标准 Microsoft Word (.docx) 及 Office Open XML 格式；
- 文档结构顺序：遍历文档 body 元素，严格保持段落与表格的自然穿插顺序；
- 标题分级保留：识别 Word 内置 Heading/标题样式（1-6 级）并转换为 Markdown ATX 标题（#）；
- 表格结构提取：将表格行转换为可读 Markdown 格式，保留行与列之间的语义关联；
- 文本量按 UTF-16 统计（B3）：使用 u16len，超限抛出 422 EXTRACTION_LIMIT；
- 结构化告警（B4）：输出 TABLES_FLATTENED, HEADERS_FOOTERS_SKIPPED, TEXTBOXES_SKIPPED 等机器码；
- 安全防护与资源限制：
  - ZIP 炸弹防御：严格检查压缩条目数（max_zip_entries）、解压总大小（max_uncompressed_bytes）与膨胀率；
  - 结构合法性校验：必须包含 word/document.xml，拦截伪造或损坏文件。
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import docx
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from .types import (
    DocumentWarningRecord,
    ExtractionLimitError,
    ParsedDocument,
    ParseError,
    ParseLimits,
    WarningCode,
    u16len,
)

_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _detect_heading_level(paragraph: Paragraph) -> int | None:
    """尝试从段落样式或大纲级别提取 1~6 级标题。"""
    try:
        if paragraph.style and paragraph.style.name:
            name = paragraph.style.name.strip()
            # 匹配英文 Heading 1 ~ Heading 6
            m_en = re.match(r"^Heading\s*([1-6])$", name, re.IGNORECASE)
            if m_en:
                return int(m_en.group(1))
            # 匹配中文 标题 1 ~ 标题 6
            m_cn = re.match(r"^标题\s*([1-6])$", name)
            if m_cn:
                return int(m_cn.group(1))
    except Exception:
        pass

    try:
        pPr = paragraph._element.pPr
        if pPr is not None and getattr(pPr, "outlineLvl", None) is not None:
            lvl = pPr.outlineLvl.val
            if 0 <= lvl <= 5:
                return lvl + 1
    except Exception:
        pass

    return None


def _format_table(table: Table) -> str:
    """将 Word 表格转换为 GitHub Flavored Markdown 兼容的纯文本表格。"""
    if not table.rows:
        return ""

    rows_data: list[list[str]] = []
    max_cols = 0

    for row in table.rows:
        row_cells = [
            re.sub(r"[\r\n\t]+", " ", cell.text).strip() for cell in row.cells
        ]
        max_cols = max(max_cols, len(row_cells))
        rows_data.append(row_cells)

    if max_cols == 0 or not any(any(cell for cell in row) for row in rows_data):
        return ""

    # 规整化列数
    normalized_rows: list[list[str]] = []
    for row in rows_data:
        if len(row) < max_cols:
            row = row + [""] * (max_cols - len(row))
        normalized_rows.append(row[:max_cols])

    lines: list[str] = []
    header = normalized_rows[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")

    for row in normalized_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def parse_docx(
    data: bytes,
    filename: str,
    limits: ParseLimits,
) -> ParsedDocument:
    """解析 DOCX 二进制数据并提取结构化正文与表格。"""
    if not data:
        raise ParseError(422, "EMPTY_DOCUMENT", "文件是空的，没有可导入的文字。")

    if len(data) > limits.max_bytes:
        raise ParseError(
            413,
            "FILE_TOO_LARGE",
            f"文件超过 {limits.max_bytes // 1024 // 1024} MiB 上限。",
            {"max_bytes": limits.max_bytes, "received_bytes": len(data)},
        )

    bio = io.BytesIO(data)

    if not zipfile.is_zipfile(bio):
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "文件不是有效的 DOCX 或 Office Open XML 压缩文档。",
            {"filename": filename},
        )

    # 安全检查：防御 ZIP 炸弹与检测必要文档核心文件
    try:
        with zipfile.ZipFile(bio, "r") as zf:
            infolist = zf.infolist()
            # 1. 检查条目数上限（R3 防护要求）
            if len(infolist) > limits.max_zip_entries:
                raise ParseError(
                    413,
                    "ZIP_BOMB_DETECTED",
                    f"DOCX 压缩包条目数异常 ({len(infolist)} 项，上限 {limits.max_zip_entries} 项)，已被拦截。",
                    {
                        "filename": filename,
                        "zip_entries": len(infolist),
                        "max_allowed": limits.max_zip_entries,
                    },
                )

            total_uncompressed = sum(info.file_size for info in infolist)

            # 2. 检查解包总大小
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise ParseError(
                    413,
                    "ZIP_BOMB_DETECTED",
                    f"DOCX 解压缩后大小 ({total_uncompressed // 1024 // 1024} MiB) 超出系统安全上限，已拦截。",
                    {
                        "filename": filename,
                        "uncompressed_bytes": total_uncompressed,
                        "max_allowed": limits.max_uncompressed_bytes,
                    },
                )

            # 3. 检查压缩膨胀比率
            ratio = total_uncompressed / max(1, len(data))
            if ratio > limits.max_uncompressed_ratio and total_uncompressed > (5 * 1024 * 1024):
                raise ParseError(
                    413,
                    "ZIP_BOMB_DETECTED",
                    f"DOCX 压缩膨胀比率异常 ({ratio:.1f}x)，已被安全防御机制拦截。",
                    {"filename": filename, "compression_ratio": ratio},
                )

            namelist = set(zf.namelist())
            if "word/document.xml" not in namelist:
                raise ParseError(
                    422,
                    "CORRUPT_DOCUMENT",
                    "DOCX 文档结构损坏：缺少核心正文定义文件 (word/document.xml)。",
                    {"filename": filename},
                )
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "DOCX 压缩包损坏或格式不合法。",
            {"filename": filename, "error_detail": str(exc)},
        ) from exc

    bio.seek(0)
    try:
        doc = docx.Document(bio)
    except Exception as exc:
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "无法以 DOCX 规范读取该文档的内容结构，文件可能损坏。",
            {"filename": filename, "error_detail": str(exc)},
        ) from exc

    blocks: list[str] = []
    table_count = 0

    # 保持正文段落与表格的原生交替顺序
    try:
        body_elements = doc.element.body
        for child in body_elements:
            if isinstance(child, CT_P):
                p = Paragraph(child, doc)
                raw_text = p.text.replace("\r\n", "\n").replace("\r", "\n").strip()
                if not raw_text:
                    continue

                level = _detect_heading_level(p)
                if level is not None and not raw_text.startswith("#"):
                    # 格式化为 Markdown 标题，便于后续分块系统识别
                    blocks.append(f"{'#' * level} {raw_text}")
                else:
                    blocks.append(raw_text)

            elif isinstance(child, CT_Tbl):
                tbl = Table(child, doc)
                tbl_markdown = _format_table(tbl)
                if tbl_markdown:
                    blocks.append(tbl_markdown)
                    table_count += 1
    except Exception as exc:
        raise ParseError(
            422,
            "CORRUPT_DOCUMENT",
            "遍历 DOCX 正文结构时发生错误。",
            {"filename": filename, "error_detail": str(exc)},
        ) from exc

    full_text = "\n\n".join(blocks).strip()

    if not full_text:
        raise ParseError(
            422,
            "EMPTY_DOCUMENT",
            "DOCX 文档未提取到任何正文或表格文字。",
            {"filename": filename},
        )

    # 按 UTF-16 code units 统计（B3 契约要求）
    text_units = u16len(full_text)
    effective_limit = limits.effective_max_units

    if text_units > effective_limit:
        raise ExtractionLimitError(
            limit=effective_limit,
            actual=text_units,
            message=f"DOCX 提取后的正文文本量超出限制（当前约 {text_units} 码元，上限 {effective_limit} 码元）。",
        )

    warnings: list[str] = [
        "DOCX 文档中的页眉、页脚、批注、形状与独立绘图文本框不在导入范围内，仅提取正文与表格内容。"
    ]
    warning_records: list[DocumentWarningRecord] = [
        {
            "code": WarningCode.HEADERS_FOOTERS_SKIPPED,
            "message": "DOCX 文档中的页眉页脚与批注不在导入范围内，已跳过",
        },
        {
            "code": WarningCode.TEXTBOXES_SKIPPED,
            "message": "DOCX 文档中的独立绘图文本框与浮动形状不在导入范围内，已跳过",
        },
    ]

    if table_count > 0:
        warning_records.append(
            {
                "code": WarningCode.TABLES_FLATTENED,
                "message": f"文档中的表格已转换为 Markdown 纯文本表示（共 {table_count} 个表格）",
                "count": table_count,
            }
        )

    return ParsedDocument(
        text=full_text,
        media_type=_DOCX_MEDIA_TYPE,
        encoding="utf-8",
        warnings=warnings,
        warning_records=warning_records,
        page_count=None,
        extracted_chars=text_units,
        extracted_units=text_units,
    )
