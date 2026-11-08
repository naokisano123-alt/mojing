"""人工设定条目后端服务核心实现。

契约与规范依据（ROUND3.md、M1_R3_API.md §1、§4）：
1. 模块边界与事务：所有服务函数接收连接 conn: sqlite3.Connection，由调用方控制事务，本模块不 commit、不 rollback、不私建正式库；
2. 基础 CRUD：create_entry, list_entries, get_entry, update_entry, delete_entry；
3. 五类 type（character, location, faction, rule, other），两状态（draft, confirmed），origin='manual'；
4. 标题 1–80 字符，summary ≤ 500 字符；允许同名不同条目，绝不自动合并；
5. 乐观并发 revision：初值 1，每次成功更新 +1。update_entry 与 delete_entry 强校验 expected_revision，版本不一致返回 409 LORE_ENTRY_CONFLICT；
6. 软删除与幂等性：delete_entry 使用 expected_revision 进行软删除，已软删除的条目再次以相同 revision 删除保持幂等（204）；
7. 来源引用强校验：
   - document_id 属于同世界且未软删（SOURCE_CROSS_WORLD / SOURCE_DELETED）；
   - chunk_id 属于该文档（SOURCE_CHUNK_MISMATCH）；
   - 区间半开 [start, end) 必须落在块内，支持全文绝对坐标或块内偏移（SOURCE_OUT_OF_RANGE）；
   - UTF-16 边界合法，不切代理对（SOURCE_INVALID_OFFSET）；
   - 服务端提取 excerpt，不信任客户端上送内容；
8. 来源生命周期与派生状态：
   - 来源文档软删除后，条目保留，来源状态派生为 availability: 'deleted'；
   - 切片异常缺失派生为 availability: 'unavailable'；
   - 当且仅当 entry.status === 'confirmed' 且存在非 active 来源时，needs_source_review 为 True（仅作复核提示）；
   - 无来源的 manual 条目合法且 needs_source_review 为 False；
   - 已软删除的世界下所有条目不可读写，抛 404 WORLD_NOT_FOUND。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .coords import check_utf16_offset, generate_excerpt, u16len
from .errors import (
    LoreEntryConflictError,
    LoreEntryNotFoundError,
    LoreEntrySourceChunkMismatchError,
    LoreEntrySourceCrossWorldError,
    LoreEntrySourceDeletedError,
    LoreEntrySourceInvalidOffsetError,
    LoreEntrySourceOutOfRangeError,
    LoreEntryValidationError,
    LoreEntryWorldNotFoundError,
)
from .types import (
    VALID_ENTRY_STATUSES,
    VALID_ENTRY_TYPES,
    LoreEntry,
    LoreEntryListResult,
    LoreEntrySource,
    SourceAvailability,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_TITLE_LENGTH = 80
MAX_SUMMARY_LENGTH = 500


def _iso_now() -> str:
    """生成带 UTC 时区的标准 ISO 8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def _check_world_active(conn: sqlite3.Connection, world_id: str) -> None:
    """校验世界是否存在且未被软删除，已删除抛出 404 WORLD_NOT_FOUND。"""
    cur = conn.cursor()
    cur.execute("SELECT id, deleted_at FROM worlds WHERE id = ?", (world_id,))
    row = cur.fetchone()
    if not row:
        raise LoreEntryWorldNotFoundError(world_id)

    deleted_at = row["deleted_at"] if hasattr(row, "keys") else row[1]
    if deleted_at is not None:
        raise LoreEntryWorldNotFoundError(world_id)


def _validate_type(entry_type: Any) -> str:
    if not isinstance(entry_type, str) or entry_type not in VALID_ENTRY_TYPES:
        raise LoreEntryValidationError(
            "type",
            f"条目类型必须为以下五类之一：{', '.join(sorted(VALID_ENTRY_TYPES))}。",
            {"allowed": list(sorted(VALID_ENTRY_TYPES)), "actual": entry_type},
        )
    return entry_type


def _validate_title(title: Any) -> str:
    if not isinstance(title, str):
        raise LoreEntryValidationError("title", "条目标题必须为字符串。")
    stripped = title.strip()
    if not stripped or len(stripped) > MAX_TITLE_LENGTH:
        raise LoreEntryValidationError(
            "title",
            f"条目标题长度必须在 1 到 {MAX_TITLE_LENGTH} 个字符之间。",
            {"max_length": MAX_TITLE_LENGTH, "actual_length": len(stripped)},
        )
    return stripped


def _validate_summary(summary: Any) -> str:
    if summary is None:
        return ""
    if not isinstance(summary, str):
        raise LoreEntryValidationError("summary", "条目摘要必须为字符串。")
    stripped = summary.strip()
    if len(stripped) > MAX_SUMMARY_LENGTH:
        raise LoreEntryValidationError(
            "summary",
            f"条目摘要长度不能超过 {MAX_SUMMARY_LENGTH} 个字符。",
            {"max_length": MAX_SUMMARY_LENGTH, "actual_length": len(stripped)},
        )
    return stripped


def _validate_status(status: Any) -> str:
    if status is None:
        return "draft"
    if not isinstance(status, str) or status not in VALID_ENTRY_STATUSES:
        raise LoreEntryValidationError(
            "status",
            f"条目状态必须为以下之一：{', '.join(sorted(VALID_ENTRY_STATUSES))}。",
            {"allowed": list(sorted(VALID_ENTRY_STATUSES)), "actual": status},
        )
    return status


def _validate_expected_revision(expected_revision: Any) -> int:
    if expected_revision is None or not isinstance(expected_revision, int) or expected_revision < 1:
        raise LoreEntryValidationError(
            "expected_revision",
            "必须提供正整数 expected_revision 用于并发安全检查。",
            {"actual": expected_revision},
        )
    return expected_revision


def _validate_and_prepare_source(
    conn: sqlite3.Connection,
    world_id: str,
    source: dict[str, Any],
) -> tuple[str, str, int, int, str]:
    """严格校验单条来源引用，返回 (document_id, chunk_id, abs_start, abs_end, excerpt)。"""
    if not isinstance(source, dict):
        raise LoreEntryValidationError("source_links", "来源项必须为对象结构。")

    document_id = source.get("document_id")
    chunk_id = source.get("chunk_id")
    start_char = source.get("start_char")
    end_char = source.get("end_char")

    if not isinstance(document_id, str) or not document_id.strip():
        raise LoreEntryValidationError("source_links.document_id", "来源 document_id 不能为空。")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise LoreEntryValidationError("source_links.chunk_id", "来源 chunk_id 不能为空。")
    if not isinstance(start_char, int) or not isinstance(end_char, int):
        raise LoreEntryValidationError(
            "source_links.coordinates", "start_char 与 end_char 必须为整数。"
        )

    # 1. 检查文档：同世界、未软删除
    cur = conn.cursor()
    cur.execute(
        "SELECT id, world_id, deleted_at FROM documents WHERE id = ?",
        (document_id,),
    )
    doc_row = cur.fetchone()
    if not doc_row:
        raise LoreEntrySourceCrossWorldError(document_id)

    doc_world_id = doc_row["world_id"] if hasattr(doc_row, "keys") else doc_row[1]
    doc_deleted_at = doc_row["deleted_at"] if hasattr(doc_row, "keys") else doc_row[2]

    if doc_world_id != world_id:
        raise LoreEntrySourceCrossWorldError(document_id)
    if doc_deleted_at is not None:
        raise LoreEntrySourceDeletedError(document_id)

    # 2. 检查切片：属于该文档
    cur.execute(
        "SELECT id, document_id, start_char, end_char, text FROM chunks WHERE id = ?",
        (chunk_id,),
    )
    chunk_row = cur.fetchone()
    if not chunk_row:
        raise LoreEntrySourceChunkMismatchError(chunk_id)

    c_doc_id = chunk_row["document_id"] if hasattr(chunk_row, "keys") else chunk_row[1]
    c_start = chunk_row["start_char"] if hasattr(chunk_row, "keys") else chunk_row[2]
    c_end = chunk_row["end_char"] if hasattr(chunk_row, "keys") else chunk_row[3]
    c_text = chunk_row["text"] if hasattr(chunk_row, "keys") else chunk_row[4]

    if c_doc_id != document_id:
        raise LoreEntrySourceChunkMismatchError(chunk_id)

    chunk_u16_len = c_end - c_start

    # 3. 校验并换算区间：支持全文绝对坐标或块内相对坐标
    if start_char >= c_start and end_char <= c_end:
        abs_start = start_char
        abs_end = end_char
        rel_start = start_char - c_start
        rel_end = end_char - c_start
    elif 0 <= start_char < end_char <= chunk_u16_len:
        rel_start = start_char
        rel_end = end_char
        abs_start = c_start + start_char
        abs_end = c_start + end_char
    else:
        raise LoreEntrySourceOutOfRangeError(start_char, end_char, chunk_u16_len)

    if not (0 <= rel_start < rel_end <= chunk_u16_len):
        raise LoreEntrySourceOutOfRangeError(start_char, end_char, chunk_u16_len)

    # 4. 代理对边界合法性检查
    if not check_utf16_offset(c_text, rel_start) or not check_utf16_offset(c_text, rel_end):
        raise LoreEntrySourceInvalidOffsetError(start_char, end_char)

    # 5. 服务端生成真实摘录（不信任客户端）
    excerpt = generate_excerpt(c_text, rel_start, rel_end)

    return document_id, chunk_id, abs_start, abs_end, excerpt


def _fetch_sources_for_entry(
    conn: sqlite3.Connection,
    entry_id: str,
) -> list[LoreEntrySource]:
    """查询指定条目的所有来源引用，并动态派生 availability。"""
    query = """
    SELECT
        s.id AS source_id,
        s.document_id,
        s.chunk_id,
        s.start_char,
        s.end_char,
        s.excerpt,
        d.id AS doc_id,
        d.deleted_at AS doc_deleted_at,
        c.id AS chunk_exists_id
    FROM lore_entry_sources s
    LEFT JOIN documents d ON s.document_id = d.id
    LEFT JOIN chunks c ON s.chunk_id = c.id
    WHERE s.entry_id = ? AND s.deleted_at IS NULL
    ORDER BY s.created_at ASC, s.id ASC
    """
    cur = conn.cursor()
    cur.execute(query, (entry_id,))
    rows = cur.fetchall()

    results: list[LoreEntrySource] = []
    for r in rows:
        if hasattr(r, "keys"):
            doc_id = r["document_id"]
            chunk_id = r["chunk_id"]
            start_char = r["start_char"]
            end_char = r["end_char"]
            excerpt = r["excerpt"]
            doc_exists = r["doc_id"]
            doc_deleted_at = r["doc_deleted_at"]
            chunk_exists = r["chunk_exists_id"]
        else:
            (
                _sid,
                doc_id,
                chunk_id,
                start_char,
                end_char,
                excerpt,
                doc_exists,
                doc_deleted_at,
                chunk_exists,
            ) = r

        # 动态派生 availability
        if doc_exists is None or doc_deleted_at is not None:
            availability: SourceAvailability = "deleted"
        elif chunk_exists is None:
            availability = "unavailable"
        else:
            availability = "active"

        results.append(
            {
                "document_id": doc_id,
                "chunk_id": chunk_id,
                "start_char": start_char,
                "end_char": end_char,
                "excerpt": excerpt,
                "availability": availability,
            }
        )
    return results


def create_entry(
    conn: sqlite3.Connection,
    world_id: str,
    payload: dict[str, Any],
) -> LoreEntry:
    """在指定世界新建人工设定条目。

    参数:
        conn: sqlite3 连接，调用方控制事务。
        world_id: 所属世界 ID。
        payload: 包含 type, title, summary, status, source_links 等字段。

    返回:
        LoreEntry 完整模型字典（初始 revision=1，origin='manual'）。
    """
    if not isinstance(payload, dict):
        raise LoreEntryValidationError("payload", "请求体必须为 JSON 对象。")

    _check_world_active(conn, world_id)

    entry_type = _validate_type(payload.get("type"))
    title = _validate_title(payload.get("title"))
    summary = _validate_summary(payload.get("summary"))
    status = _validate_status(payload.get("status"))

    raw_sources = payload.get("source_links", [])
    if raw_sources is None:
        raw_sources = []
    if not isinstance(raw_sources, list):
        raise LoreEntryValidationError("source_links", "source_links 必须为数组。")

    # 预先逐条严格校验全部来源
    prepared_sources = [
        _validate_and_prepare_source(conn, world_id, s) for s in raw_sources
    ]

    entry_id = str(uuid.uuid4())
    now = _iso_now()

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO lore_entries (
            id, world_id, type, title, summary, status,
            revision, origin, created_at, updated_at, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry_id,
            world_id,
            entry_type,
            title,
            summary,
            status,
            1,
            "manual",
            now,
            now,
            None,
        ),
    )

    for doc_id, chunk_id, abs_start, abs_end, excerpt in prepared_sources:
        cur.execute(
            """INSERT INTO lore_entry_sources (
                id, entry_id, world_id, document_id, chunk_id,
                start_char, end_char, excerpt, created_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                entry_id,
                world_id,
                doc_id,
                chunk_id,
                abs_start,
                abs_end,
                excerpt,
                now,
                None,
            ),
        )

    return get_entry(conn, entry_id)


def get_entry(conn: sqlite3.Connection, entry_id: str) -> LoreEntry:
    """获取单个条目的详细信息。"""
    cur = conn.cursor()
    cur.execute(
        """SELECT id, world_id, type, title, summary, status,
                  revision, origin, created_at, updated_at, deleted_at
           FROM lore_entries
           WHERE id = ? AND deleted_at IS NULL""",
        (entry_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LoreEntryNotFoundError(entry_id)

    if hasattr(row, "keys"):
        e_id = row["id"]
        w_id = row["world_id"]
        e_type = row["type"]
        title = row["title"]
        summary = row["summary"]
        status = row["status"]
        revision = row["revision"]
        origin = row["origin"]
        created_at = row["created_at"]
        updated_at = row["updated_at"]
        deleted_at = row["deleted_at"]
    else:
        (
            e_id,
            w_id,
            e_type,
            title,
            summary,
            status,
            revision,
            origin,
            created_at,
            updated_at,
            deleted_at,
        ) = row

    # 世界已被删除时，条目不可读
    _check_world_active(conn, w_id)

    source_links = _fetch_sources_for_entry(conn, e_id)
    needs_review = (status == "confirmed") and any(
        s["availability"] != "active" for s in source_links
    )

    return {
        "id": e_id,
        "world_id": w_id,
        "type": e_type,
        "title": title,
        "summary": summary,
        "status": status,
        "revision": revision,
        "origin": origin,
        "source_links": source_links,
        "needs_source_review": needs_review,
        "created_at": created_at,
        "updated_at": updated_at,
        "deleted_at": deleted_at,
    }


def list_entries(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    type: str | None = None,
    status: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> LoreEntryListResult:
    """分页查询指定世界下的设定条目列表。"""
    _check_world_active(conn, world_id)

    if not isinstance(limit, int) or limit < 1 or limit > MAX_PAGE_SIZE:
        raise LoreEntryValidationError(
            "limit",
            f"分页条数 limit 必须在 1 到 {MAX_PAGE_SIZE} 之间。",
            {"min": 1, "max": MAX_PAGE_SIZE, "actual": limit},
        )
    if not isinstance(offset, int) or offset < 0:
        raise LoreEntryValidationError(
            "offset", "分页偏移 offset 不能小于 0。", {"actual": offset}
        )

    where_clauses = ["world_id = ?", "deleted_at IS NULL"]
    params: list[Any] = [world_id]

    if type is not None:
        _validate_type(type)
        where_clauses.append("type = ?")
        params.append(type)

    if status is not None:
        _validate_status(status)
        where_clauses.append("status = ?")
        params.append(status)

    where_sql = " AND ".join(where_clauses)

    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM lore_entries WHERE {where_sql}", params)
    total = cur.fetchone()[0]

    if total == 0:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    # 排序稳定：updated_at DESC, id ASC
    query_sql = f"""
    SELECT id FROM lore_entries
    WHERE {where_sql}
    ORDER BY updated_at DESC, id ASC
    LIMIT ? OFFSET ?
    """
    cur.execute(query_sql, params + [limit, offset])
    id_rows = cur.fetchall()

    items: list[LoreEntry] = [
        get_entry(conn, r["id"] if hasattr(r, "keys") else r[0]) for r in id_rows
    ]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def update_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    payload: dict[str, Any],
    expected_revision: int,
) -> LoreEntry:
    """部分更新设定条目（带乐观并发版本校验）。

    参数:
        conn: sqlite3 连接。
        entry_id: 条目 ID。
        payload: 欲更新字段子集（未传字段保留原值）。
        expected_revision: 客户端持有的期望版本号。
    """
    if not isinstance(payload, dict):
        raise LoreEntryValidationError("payload", "请求体必须为 JSON 对象。")

    exp_rev = _validate_expected_revision(expected_revision)

    cur = conn.cursor()
    cur.execute(
        """SELECT id, world_id, type, title, summary, status, revision, deleted_at
           FROM lore_entries
           WHERE id = ? AND deleted_at IS NULL""",
        (entry_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LoreEntryNotFoundError(entry_id)

    w_id = row["world_id"] if hasattr(row, "keys") else row[1]
    cur_type = row["type"] if hasattr(row, "keys") else row[2]
    cur_title = row["title"] if hasattr(row, "keys") else row[3]
    cur_summary = row["summary"] if hasattr(row, "keys") else row[4]
    cur_status = row["status"] if hasattr(row, "keys") else row[5]
    cur_revision = row["revision"] if hasattr(row, "keys") else row[6]

    _check_world_active(conn, w_id)

    # 乐观并发检查
    if cur_revision != exp_rev:
        raise LoreEntryConflictError(exp_rev, cur_revision)

    # 字段增量更新判断
    new_type = _validate_type(payload["type"]) if "type" in payload else cur_type
    new_title = _validate_title(payload["title"]) if "title" in payload else cur_title
    new_summary = (
        _validate_summary(payload["summary"]) if "summary" in payload else cur_summary
    )
    new_status = (
        _validate_status(payload["status"]) if "status" in payload else cur_status
    )

    now = _iso_now()
    new_revision = cur_revision + 1

    # 处理来源引用更新
    if "source_links" in payload:
        raw_sources = payload["source_links"]
        if raw_sources is None:
            raw_sources = []
        if not isinstance(raw_sources, list):
            raise LoreEntryValidationError("source_links", "source_links 必须为数组。")

        prepared_sources = [
            _validate_and_prepare_source(conn, w_id, s) for s in raw_sources
        ]

        # 软删除已有来源
        cur.execute(
            "UPDATE lore_entry_sources SET deleted_at = ? WHERE entry_id = ? AND deleted_at IS NULL",
            (now, entry_id),
        )

        # 插入新来源
        for doc_id, chunk_id, abs_start, abs_end, excerpt in prepared_sources:
            cur.execute(
                """INSERT INTO lore_entry_sources (
                    id, entry_id, world_id, document_id, chunk_id,
                    start_char, end_char, excerpt, created_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    entry_id,
                    w_id,
                    doc_id,
                    chunk_id,
                    abs_start,
                    abs_end,
                    excerpt,
                    now,
                    None,
                ),
            )

    cur.execute(
        """UPDATE lore_entries
           SET type = ?, title = ?, summary = ?, status = ?,
               revision = ?, updated_at = ?
           WHERE id = ?""",
        (new_type, new_title, new_summary, new_status, new_revision, now, entry_id),
    )

    return get_entry(conn, entry_id)


def delete_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    expected_revision: int,
) -> None:
    """软删除设定条目（带乐观并发版本校验）。

    规则依据（M1_R3_API.md §1.3）：
    - expected_revision 不匹配返回 409 LORE_ENTRY_CONFLICT；
    - 首次删除将 deleted_at 置为当前时间；
    - 针对已删除条目且 revision 匹配时保持幂等（返回 None，对应 204）。
    """
    exp_rev = _validate_expected_revision(expected_revision)

    cur = conn.cursor()
    cur.execute(
        "SELECT id, world_id, revision, deleted_at FROM lore_entries WHERE id = ?",
        (entry_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LoreEntryNotFoundError(entry_id)

    w_id = row["world_id"] if hasattr(row, "keys") else row[1]
    cur_rev = row["revision"] if hasattr(row, "keys") else row[2]
    deleted_at = row["deleted_at"] if hasattr(row, "keys") else row[3]

    _check_world_active(conn, w_id)

    if cur_rev != exp_rev:
        raise LoreEntryConflictError(exp_rev, cur_rev)

    if deleted_at is not None:
        # 重复删除保持幂等
        return None

    now = _iso_now()
    cur.execute(
        "UPDATE lore_entries SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (now, now, entry_id),
    )
    cur.execute(
        "UPDATE lore_entry_sources SET deleted_at = ? WHERE entry_id = ? AND deleted_at IS NULL",
        (now, entry_id),
    )
    return None
