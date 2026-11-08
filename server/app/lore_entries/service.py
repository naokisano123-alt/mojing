"""人工设定条目后端服务核心实现。

契约与规范依据（ROUND3.md、ROUND5.md、M1_R3_API.md §1、§4、R5_GEMINI_MA.md）：
1. 模块边界与事务：所有服务函数接收外部注入连接 conn: sqlite3.Connection，由调用方控制事务，本模块不 commit、不 rollback、不私建正式库；
2. 事务原子性与嵌套回滚（SAVEPOINT）：条目和来源变更位于同一原子单元中，校验失败或写入异常时回滚该保存点，绝不遗留半份来源或已递增的 revision；真实数据库错误原样抛出，不吞为 409；
3. 原子并发（R4-02 修复）：update 与 delete 均通过原子 UPDATE ... WHERE id = ? AND revision = ? AND deleted_at IS NULL 执行并核对受影响行数；update/update 同版本竞争仅一个成功其余 409；update/delete 不复活；重复同版本删除维持幂等（204）；
4. 来源全文绝对坐标（R4-03 修复）：仅接受 chunk.start <= start < end <= chunk.end 的全文绝对 UTF-16 区间，彻底剔除相对坐标猜测，拒绝布尔值与代理对中间点；
5. source_links 契约守卫：null 绝不隐式当作 [] 清空，必须报错 422；无来源时显式传 []；
6. PATCH 来源语义闭环（R4-07 修复）：PATCH 若省略 source_links 则完整保留已有引用；显式数组允许保留当前条目原有的非 active 历史引用（保持原摘录用于提示复核），新增或修改的引用必须同世界且 active，禁止盗用其他条目的失效引用；
7. 可用性派生与 additive chunk_index：准确区分软删（'deleted'）与切片缺失/不从属（'unavailable'），并在切片有效时 additive 输出 chunk_index；
8. 软删除世界拦截：已软删除世界下的所有条目均不可读写，抛 404 WORLD_NOT_FOUND。
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
    """校验世界是否存在且未被软删除，不存在或已删除抛出 404 WORLD_NOT_FOUND。"""
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
    if expected_revision is None or isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
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
    existing_sources_cache: dict[tuple[str, str, int, int], str] | None = None,
) -> tuple[str, str, int, int, str]:
    """严格校验单条来源引用，返回 (document_id, chunk_id, abs_start, abs_end, excerpt)。

    规则（R5_GEMINI_MA §3、§4）：
    1. 坐标严格要求全文绝对 UTF-16：chunk.start <= start < end <= chunk.end；
    2. 绝不猜测相对坐标，拒绝布尔值与代理对中间点；
    3. 若该引用是当前条目原有的非 active 历史引用，允许保留原有摘录；
    4. 新增或修改的引用必须属于同世界且为 active 状态。
    """
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

    # 严格类型检查：拒绝布尔值（bool 是 int 的子类）与非整数
    if isinstance(start_char, bool) or isinstance(end_char, bool):
        raise LoreEntryValidationError("source_links.coordinates", "start_char 与 end_char 不能为布尔值。")
    if not isinstance(start_char, int) or not isinstance(end_char, int):
        raise LoreEntryValidationError(
            "source_links.coordinates", "start_char 与 end_char 必须为整数。"
        )

    # 1. 检查文档：是否存在、是否属于同世界
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

    source_key = (document_id, chunk_id, start_char, end_char)
    is_retained_historical = (
        existing_sources_cache is not None and source_key in existing_sources_cache
    )

    # 若文档已被软删除
    if doc_deleted_at is not None:
        if is_retained_historical:
            # 允许保留本条目已有且身份/区间完全相同的历史引用，保留原摘录
            return document_id, chunk_id, start_char, end_char, existing_sources_cache[source_key]
        raise LoreEntrySourceDeletedError(document_id)

    # 2. 检查切片：属于该文档且存在
    cur.execute(
        "SELECT id, document_id, start_char, end_char, text FROM chunks WHERE id = ?",
        (chunk_id,),
    )
    chunk_row = cur.fetchone()
    if not chunk_row:
        if is_retained_historical:
            # 切片缺失但为条目已有历史引用，保留原摘录
            return document_id, chunk_id, start_char, end_char, existing_sources_cache[source_key]
        raise LoreEntrySourceChunkMismatchError(chunk_id)

    c_doc_id = chunk_row["document_id"] if hasattr(chunk_row, "keys") else chunk_row[1]
    c_start = chunk_row["start_char"] if hasattr(chunk_row, "keys") else chunk_row[2]
    c_end = chunk_row["end_char"] if hasattr(chunk_row, "keys") else chunk_row[3]
    c_text = chunk_row["text"] if hasattr(chunk_row, "keys") else chunk_row[4]

    if c_doc_id != document_id:
        if is_retained_historical:
            return document_id, chunk_id, start_char, end_char, existing_sources_cache[source_key]
        raise LoreEntrySourceChunkMismatchError(chunk_id)

    chunk_u16_len = c_end - c_start

    # 3. 严格全文绝对坐标校验：chunk.start <= start_char < end_char <= chunk.end
    # 彻底杜绝猜测相对坐标 (R4-03 修复)
    if not (c_start <= start_char < end_char <= c_end):
        raise LoreEntrySourceOutOfRangeError(start_char, end_char, chunk_u16_len)

    abs_start = start_char
    abs_end = end_char
    rel_start = start_char - c_start
    rel_end = end_char - c_start

    # 4. UTF-16 代理对边界合法性检查
    if not check_utf16_offset(c_text, rel_start) or not check_utf16_offset(c_text, rel_end):
        raise LoreEntrySourceInvalidOffsetError(start_char, end_char)

    # 5. 服务端生成真实摘录（不信任客户端）
    excerpt = generate_excerpt(c_text, rel_start, rel_end)

    return document_id, chunk_id, abs_start, abs_end, excerpt


def _fetch_sources_for_entry(
    conn: sqlite3.Connection,
    entry_id: str,
) -> list[LoreEntrySource]:
    """查询指定条目的所有来源引用，并准确派生 availability 与 additive chunk_index。"""
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
        c.id AS chunk_exists_id,
        c.document_id AS chunk_doc_id,
        c.chunk_index AS chunk_index
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
            chunk_doc_id = r["chunk_doc_id"]
            chunk_idx = r["chunk_index"]
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
                chunk_doc_id,
                chunk_idx,
            ) = r

        # 准确区分软删与缺失，核对文档/块关系 (R5_GEMINI_MA §5)
        if doc_exists is None or doc_deleted_at is not None:
            availability: SourceAvailability = "deleted"
            res_chunk_idx = chunk_idx if (chunk_exists is not None and chunk_doc_id == doc_id) else None
        elif chunk_exists is None or chunk_doc_id != doc_id:
            availability = "unavailable"
            res_chunk_idx = None
        else:
            availability = "active"
            res_chunk_idx = chunk_idx

        source_dict: LoreEntrySource = {
            "document_id": doc_id,
            "chunk_id": chunk_id,
            "start_char": start_char,
            "end_char": end_char,
            "excerpt": excerpt,
            "availability": availability,
        }
        if res_chunk_idx is not None:
            source_dict["chunk_index"] = res_chunk_idx

        results.append(source_dict)
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

    # null 不能当 [] 静默清空
    if "source_links" in payload and payload["source_links"] is None:
        raise LoreEntryValidationError(
            "source_links", "source_links 不能为 null，无来源时应传空数组 []。"
        )

    raw_sources = payload.get("source_links")
    if raw_sources is None:
        raw_sources = []
    if not isinstance(raw_sources, list):
        raise LoreEntryValidationError("source_links", "source_links 必须为数组。")

    # 使用可嵌套 SAVEPOINT 保证原子性
    sp_name = f"sp_create_entry_{uuid.uuid4().hex}"
    cur = conn.cursor()
    cur.execute(f"SAVEPOINT {sp_name}")
    try:
        prepared_sources = [
            _validate_and_prepare_source(conn, world_id, s) for s in raw_sources
        ]

        entry_id = str(uuid.uuid4())
        now = _iso_now()

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

        cur.execute(f"RELEASE {sp_name}")
        return get_entry(conn, entry_id)
    except Exception:
        cur.execute(f"ROLLBACK TO {sp_name}")
        cur.execute(f"RELEASE {sp_name}")
        raise


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

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > MAX_PAGE_SIZE:
        raise LoreEntryValidationError(
            "limit",
            f"分页条数 limit 必须在 1 到 {MAX_PAGE_SIZE} 之间。",
            {"min": 1, "max": MAX_PAGE_SIZE, "actual": limit},
        )
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
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
    """部分更新设定条目（带原子乐观并发校验与保存点回滚）。

    规则依据（ROUND5.md、R5_GEMINI_MA.md）：
    1. 原子 UPDATE 必须包含 expected_revision 与 deleted_at IS NULL 条件；
    2. 并发同版本更新最多一个成功，其余 409；
    3. 已被软删除条目不可更新复活（404）；
    4. PATCH 若省略 source_links 保持原引用不变；显式数组若为 null 报错 422；
    5. 显式 source_links 可保留本条目已有且相同的非 active 历史引用，保留原摘录；
    6. 变更置于 SAVEPOINT 中，失败时原子回滚，不吞真实数据库异常。
    """
    if not isinstance(payload, dict):
        raise LoreEntryValidationError("payload", "请求体必须为 JSON 对象。")

    exp_rev = _validate_expected_revision(expected_revision)

    if "source_links" in payload and payload["source_links"] is None:
        raise LoreEntryValidationError(
            "source_links", "source_links 不能为 null，无来源时应传空数组 []。"
        )

    sp_name = f"sp_update_entry_{uuid.uuid4().hex}"
    cur = conn.cursor()
    cur.execute(f"SAVEPOINT {sp_name}")
    try:
        # 查询当前条目最新数据
        cur.execute(
            """SELECT id, world_id, type, title, summary, status, revision, deleted_at
               FROM lore_entries
               WHERE id = ?""",
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
        cur_deleted_at = row["deleted_at"] if hasattr(row, "keys") else row[7]

        # 软删除条目不可更新（不复活）
        if cur_deleted_at is not None:
            raise LoreEntryNotFoundError(entry_id)

        _check_world_active(conn, w_id)

        # 乐观并发版本检查
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

        # 原子 UPDATE：携带 expected_revision 与 deleted_at IS NULL
        cur.execute(
            """UPDATE lore_entries
               SET type = ?, title = ?, summary = ?, status = ?,
                   revision = ?, updated_at = ?
               WHERE id = ? AND revision = ? AND deleted_at IS NULL""",
            (new_type, new_title, new_summary, new_status, new_revision, now, entry_id, exp_rev),
        )
        if cur.rowcount == 0:
            # 存在并发冲突：重新检查最新状态
            cur.execute("SELECT revision, deleted_at FROM lore_entries WHERE id = ?", (entry_id,))
            chk = cur.fetchone()
            if not chk:
                raise LoreEntryNotFoundError(entry_id)
            chk_del = chk["deleted_at"] if hasattr(chk, "keys") else chk[1]
            chk_rev = chk["revision"] if hasattr(chk, "keys") else chk[0]
            if chk_del is not None:
                raise LoreEntryNotFoundError(entry_id)
            raise LoreEntryConflictError(exp_rev, chk_rev)

        # 处理来源引用更新：仅当显式传了 source_links 时才变更来源
        if "source_links" in payload:
            raw_sources = payload["source_links"]
            if not isinstance(raw_sources, list):
                raise LoreEntryValidationError("source_links", "source_links 必须为数组。")

            # 查询当前条目已有的全部未删除来源，支持保留失效历史引用
            cur.execute(
                """SELECT document_id, chunk_id, start_char, end_char, excerpt
                   FROM lore_entry_sources
                   WHERE entry_id = ? AND deleted_at IS NULL""",
                (entry_id,),
            )
            existing_rows = cur.fetchall()
            existing_sources_map: dict[tuple[str, str, int, int], str] = {}
            for er in existing_rows:
                if hasattr(er, "keys"):
                    existing_sources_map[
                        (er["document_id"], er["chunk_id"], er["start_char"], er["end_char"])
                    ] = er["excerpt"]
                else:
                    existing_sources_map[(er[0], er[1], er[2], er[3])] = er[4]

            prepared_sources = [
                _validate_and_prepare_source(
                    conn, w_id, s, existing_sources_cache=existing_sources_map
                )
                for s in raw_sources
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

        cur.execute(f"RELEASE {sp_name}")
        return get_entry(conn, entry_id)
    except Exception:
        cur.execute(f"ROLLBACK TO {sp_name}")
        cur.execute(f"RELEASE {sp_name}")
        raise


def delete_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    expected_revision: int,
) -> None:
    """软删除设定条目（带原子并发校验与幂等性保障）。

    规则依据（M1_R3_API.md §1.3、R5_GEMINI_MA.md）：
    1. 原子 UPDATE ... WHERE id = ? AND revision = ? AND deleted_at IS NULL；
    2. expected_revision 不匹配返回 409 LORE_ENTRY_CONFLICT；
    3. 针对已删除条目且 revision 匹配时保持幂等（返回 None，对应 204）；
    4. 竞争删除保持幂等或 409；
    5. 关联来源同步软删除。
    """
    exp_rev = _validate_expected_revision(expected_revision)

    sp_name = f"sp_delete_entry_{uuid.uuid4().hex}"
    cur = conn.cursor()
    cur.execute(f"SAVEPOINT {sp_name}")
    try:
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
            # 已经软删除且 revision 匹配，重复删除保持幂等（204）
            cur.execute(f"RELEASE {sp_name}")
            return None

        now = _iso_now()
        # 原子更新软删除
        cur.execute(
            """UPDATE lore_entries
               SET deleted_at = ?, updated_at = ?
               WHERE id = ? AND revision = ? AND deleted_at IS NULL""",
            (now, now, entry_id, exp_rev),
        )
        if cur.rowcount == 0:
            # 并发竞争检测
            cur.execute("SELECT revision, deleted_at FROM lore_entries WHERE id = ?", (entry_id,))
            chk = cur.fetchone()
            if not chk:
                raise LoreEntryNotFoundError(entry_id)
            chk_rev = chk["revision"] if hasattr(chk, "keys") else chk[0]
            chk_del = chk["deleted_at"] if hasattr(chk, "keys") else chk[1]
            if chk_del is not None and chk_rev == exp_rev:
                # 另一事务已以同一版本删除 -> 幂等成功
                cur.execute(f"RELEASE {sp_name}")
                return None
            raise LoreEntryConflictError(exp_rev, chk_rev)

        cur.execute(
            "UPDATE lore_entry_sources SET deleted_at = ? WHERE entry_id = ? AND deleted_at IS NULL",
            (now, entry_id),
        )
        cur.execute(f"RELEASE {sp_name}")
        return None
    except Exception:
        cur.execute(f"ROLLBACK TO {sp_name}")
        cur.execute(f"RELEASE {sp_name}")
        raise
