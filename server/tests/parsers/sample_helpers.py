"""为解析器测试构建内存中可重现的 PDF 和 DOCX 样本文件。

所有样本均在内存中纯动态生成，无任何外部文件依赖、无隐私数据。
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import docx
import pypdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

# 注册 reportlab 自带的中文 CIDFont，确保中文可在 PDF 中渲染且无系统本地字体依赖
try:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _HAS_CHINESE_FONT = True
except Exception:
    _HAS_CHINESE_FONT = False


def create_test_pdf(
    pages: list[str],
    *,
    encrypted_password: str | None = None,
) -> bytes:
    """生成包含多页文本的内存 PDF 数据。"""
    bio = io.BytesIO()
    c = canvas.Canvas(bio)

    for page_text in pages:
        if _HAS_CHINESE_FONT:
            c.setFont("STSong-Light", 12)
        else:
            c.setFont("Helvetica", 12)

        # 逐行绘制
        y = 750
        for line in page_text.splitlines():
            if line.strip():
                c.drawString(72, y, line.strip())
                y -= 20
        c.showPage()

    c.save()
    raw_pdf = bio.getvalue()

    if encrypted_password:
        reader = pypdf.PdfReader(io.BytesIO(raw_pdf))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(encrypted_password)
        out_bio = io.BytesIO()
        writer.write(out_bio)
        return out_bio.getvalue()

    return raw_pdf


def create_empty_pdf(pages_count: int = 1) -> bytes:
    """生成仅包含空白页的纯白 PDF（模拟无 OCR 文字的扫描件或纯空白件）。"""
    writer = pypdf.PdfWriter()
    for _ in range(pages_count):
        writer.add_blank_page(width=595, height=842)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


def create_test_docx(
    elements: list[tuple[str, Any]],
) -> bytes:
    """按顺序生成 DOCX 文档。

    elements 格式：
    - ("heading", text, level: int)
    - ("p", text)
    - ("table", rows: list[list[str]])
    """
    doc = docx.Document()
    for item in elements:
        kind = item[0]
        if kind == "heading":
            text, level = item[1], item[2]
            doc.add_heading(text, level=level)
        elif kind == "p":
            text = item[1]
            doc.add_paragraph(text)
        elif kind == "table":
            rows = item[1]
            if rows:
                num_rows = len(rows)
                num_cols = len(rows[0])
                table = doc.add_table(rows=num_rows, cols=num_cols)
                for r_idx, row in enumerate(rows):
                    for c_idx, val in enumerate(row):
                        table.cell(r_idx, c_idx).text = str(val)

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def create_corrupt_zip(inner_files: dict[str, bytes]) -> bytes:
    """生成自定义内容的 zip 数据，用于模拟缺失关键结构或恶意结构的 docx。"""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in inner_files.items():
            zf.writestr(name, content)
    return bio.getvalue()
