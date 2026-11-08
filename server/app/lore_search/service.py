"""中文资料检索服务核心实现。

特性：
1. 参数化 SQL + SQLite instr 字面子串检索，支持中文 1–2 字短词、人名、标点、emoji及各种符号。
2. 严格排除软删除世界（worlds.deleted_at IS NOT NULL）与软删除文档（documents.deleted_at IS NOT NULL）。
3. 严格数据隔离：只查询参数中明确指定的 world_id，跨世界零泄漏。
4. 准确定位并计算 UTF-16 绝对起点/终点坐标（用于前端高亮与跳转）。
5. 明确生成摘要 snippet，不把摘要冒充为全文。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .types import (
    MatchField,
    SearchItem,
    SearchResult,
    SearchValidationError,
    SearchWorldNotFoundError,
)

MAX_QUERY_LENGTH = 100
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
SNIPPET_CONTEXT_CHARS = 45


def u16len(text: str) -> int:
    """计算字符串对应的 UTF-16 code unit 长度（与浏览器 JS 一致）。"""
    return len(text.encode("utf-16-le")) // 2


def _validate_inputs(
    world_id: str, query: str, limit: int, offset: int
) -> tuple[str, str, int, int]:
    """校验参数有效性，不合法抛出对应的 SearchValidationError。"""
    if not isinstance(world_id, str) or not world_id.strip():
        raise SearchValidationError(
            "SEARCH_WORLD_ID_INVALID",
            "世界 ID 不能为空。",
            {"field": "world_id"},
        )

    if not isinstance(query, str):
        raise SearchValidationError(
            "SEARCH_QUERY_INVALID",
            "搜索词必须为字符串。",
            {"field": "query"},
        )

    clean_query = query.strip()
    if not clean_query:
        raise SearchValidationError(
            "SEARCH_QUERY_INVALID",
            "搜索词不能为空。",
            {"field": "query"},
        )

    if len(clean_query) > MAX_QUERY_LENGTH:
        raise SearchValidationError(
            "SEARCH_QUERY_INVALID",
            f"搜索词长度不能超过 {MAX_QUERY_LENGTH} 个字符。",
            {
                "field": "query",
                "max_length": MAX_QUERY_LENGTH,
                "actual_length": len(clean_query),
            },
        )

    if not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT:
        raise SearchValidationError(
            "SEARCH_LIMIT_INVALID",
            f"每页条数 limit 必须在 1 到 {MAX_LIMIT} 之间。",
            {"field": "limit", "min": 1, "max": MAX_LIMIT, "actual": limit},
        )

    if not isinstance(offset, int) or offset < 0:
        raise SearchValidationError(
            "SEARCH_OFFSET_INVALID",
            "分页偏移 offset 不能小于 0。",
            {"field": "offset", "actual": offset},
        )

    return world_id.strip(), clean_query, limit, offset


def _check_world_exists(conn: sqlite3.Connection, world_id: str) -> None:
    """确认世界存在且未被软删除。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT id, deleted_at FROM worlds WHERE id = ?",
        (world_id,),
    )
    row = cur.fetchone()
    if not row:
        raise SearchWorldNotFoundError(world_id)

    deleted_at = row["deleted_at"] if hasattr(row, "keys") else row[1]
    if deleted_at is not None:
        raise SearchWorldNotFoundError(world_id)


def _build_snippet_and_coords(
    chunk_text: str,
    heading_path: list[str],
    filename: str,
    chunk_start_char: int,
    chunk_end_char: int,
    query: str,
) -> tuple[str, MatchField, int, int, int, int]:
    """生成摘要片段及 UTF-16 坐标。

    返回:
    (snippet, match_field, match_start_char, match_end_char, match_chunk_start_char, match_chunk_end_char)
    """
    lower_text = chunk_text.lower()
    lower_query = query.lower()
    pos = lower_text.find(lower_query)

    if pos != -1:
        # 正文命中
        match_len = len(query)
        start_idx = max(0, pos - SNIPPET_CONTEXT_CHARS)
        end_idx = min(len(chunk_text), pos + match_len + SNIPPET_CONTEXT_CHARS)

        prefix = "..." if start_idx > 0 else ""
        suffix = "..." if end_idx < len(chunk_text) else ""
        snippet = prefix + chunk_text[start_idx:end_idx] + suffix

        chunk_rel_start = u16len(chunk_text[:pos])
        chunk_rel_end = chunk_rel_start + u16len(chunk_text[pos : pos + match_len])
        doc_abs_start = chunk_start_char + chunk_rel_start
        doc_abs_end = chunk_start_char + chunk_rel_end

        return (
            snippet,
            "text",
            doc_abs_start,
            doc_abs_end,
            chunk_rel_start,
            chunk_rel_end,
        )

    # 检查标题路径是否命中
    heading_str = " > ".join(heading_path)
    if lower_query in heading_str.lower():
        body_preview = chunk_text[:80] + ("..." if len(chunk_text) > 80 else "")
        snippet = f"【标题命中: {heading_str}】 {body_preview}"
        rel_end = min(u16len(chunk_text), u16len(chunk_text[:80]))
        return (
            snippet,
            "heading",
            chunk_start_char,
            chunk_start_char + rel_end,
            0,
            rel_end,
        )

    # 文档名称命中
    body_preview = chunk_text[:80] + ("..." if len(chunk_text) > 80 else "")
    snippet = f"【文档命中: {filename}】 {body_preview}"
    rel_end = min(u16len(chunk_text), u16len(chunk_text[:80]))
    return (
        snippet,
        "filename",
        chunk_start_char,
        chunk_start_char + rel_end,
        0,
        rel_end,
    )


def search_chunks(
    conn: sqlite3.Connection,
    world_id: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> SearchResult:
    """在指定世界已导入且未删除的切片中检索。

    参数:
        conn: sqlite3 连接，由调用方提供，本函数不开启新事务也不提交事务。
        world_id: 目标世界 ID。
        query: 检索关键词（支持中文 1–2 字短词、人名、地名、符号、emoji）。
        limit: 每页条数（1–100，默认 20）。
        offset: 分页偏移量（>= 0，默认 0）。

    返回:
        SearchResult 结构，包含 items, total, limit, offset。
    """
    valid_world_id, clean_query, valid_limit, valid_offset = _validate_inputs(
        world_id, query, limit, offset
    )

    # 校验目标世界是否存在且未被软删除
    _check_world_exists(conn, valid_world_id)

    cur = conn.cursor()

    # 统计总数：软删除排除 + world_id 隔离
    count_sql = """
    SELECT COUNT(*) AS total_count
    FROM chunks c
    JOIN documents d ON c.document_id = d.id
    JOIN worlds w ON c.world_id = w.id
    WHERE c.world_id = ?
      AND w.deleted_at IS NULL
      AND d.deleted_at IS NULL
      AND (
          instr(lower(c.text), lower(?)) > 0
          OR instr(lower(c.heading_path), lower(?)) > 0
          OR instr(lower(d.filename), lower(?)) > 0
      )
    """
    cur.execute(count_sql, (valid_world_id, clean_query, clean_query, clean_query))
    count_row = cur.fetchone()
    total = count_row[0] if count_row else 0

    if total == 0:
        return {
            "items": [],
            "total": 0,
            "limit": valid_limit,
            "offset": valid_offset,
        }

    # 检索条目，根据正文匹配度优先排序，分页稳定
    query_sql = """
    SELECT
        c.id AS chunk_id,
        c.document_id,
        c.world_id,
        c.chunk_index,
        c.heading_path,
        c.start_char,
        c.end_char,
        c.text AS chunk_text,
        d.filename
    FROM chunks c
    JOIN documents d ON c.document_id = d.id
    JOIN worlds w ON c.world_id = w.id
    WHERE c.world_id = ?
      AND w.deleted_at IS NULL
      AND d.deleted_at IS NULL
      AND (
          instr(lower(c.text), lower(?)) > 0
          OR instr(lower(c.heading_path), lower(?)) > 0
          OR instr(lower(d.filename), lower(?)) > 0
      )
    ORDER BY
        CASE
            WHEN instr(lower(c.text), lower(?)) > 0 THEN 1
            WHEN instr(lower(c.heading_path), lower(?)) > 0 THEN 2
            ELSE 3
        END ASC,
        c.document_id ASC,
        c.chunk_index ASC
    LIMIT ? OFFSET ?
    """

    params = (
        valid_world_id,
        clean_query,
        clean_query,
        clean_query,
        clean_query,
        clean_query,
        valid_limit,
        valid_offset,
    )
    cur.execute(query_sql, params)
    rows = cur.fetchall()

    items: list[SearchItem] = []
    for row in rows:
        if hasattr(row, "keys"):
            chunk_id = row["chunk_id"]
            doc_id = row["document_id"]
            w_id = row["world_id"]
            chunk_index = row["chunk_index"]
            raw_headings = row["heading_path"]
            start_char = row["start_char"]
            end_char = row["end_char"]
            chunk_text = row["chunk_text"]
            filename = row["filename"]
        else:
            (
                chunk_id,
                doc_id,
                w_id,
                chunk_index,
                raw_headings,
                start_char,
                end_char,
                chunk_text,
                filename,
            ) = row

        heading_path: list[str] = (
            json.loads(raw_headings) if isinstance(raw_headings, str) else list(raw_headings)
        )

        (
            snippet,
            match_field,
            doc_abs_start,
            doc_abs_end,
            chunk_rel_start,
            chunk_rel_end,
        ) = _build_snippet_and_coords(
            chunk_text=chunk_text,
            heading_path=heading_path,
            filename=filename,
            chunk_start_char=start_char,
            chunk_end_char=end_char,
            query=clean_query,
        )

        items.append(
            {
                "world_id": w_id,
                "document_id": doc_id,
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "filename": filename,
                "heading_path": heading_path,
                "start_char": start_char,
                "end_char": end_char,
                "snippet": snippet,
                "match_field": match_field,
                "match_start_char": doc_abs_start,
                "match_end_char": doc_abs_end,
                "match_chunk_start_char": chunk_rel_start,
                "match_chunk_end_char": chunk_rel_end,
                "match_coords_scope": "document_and_chunk_utf16",
            }
        )

    return {
        "items": items,
        "total": total,
        "limit": valid_limit,
        "offset": valid_offset,
    }
