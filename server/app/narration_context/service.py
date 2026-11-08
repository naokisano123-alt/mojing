"""可追溯上下文选择服务核心实现。

职责与规范依据：
- docs/contracts/M2_A_API.md §核心接口与模块拼装 (Ma 职责)
- docs/collaboration/ROUND6.md、R6_GEMINI_MA.md
- 仅读现有 SQLite 数据库，不修改 schema，不 commit/rollback，不发起网络请求
- 严格世界隔离与软删除过滤（文档/条目 deleted_at IS NULL）
- draft 条目不自动提升，confirmed 且有失效来源（needs_source_review 为 True）保守排除
- 预算上限：最多 8 份、单份 4000 UTF-16、总 16000 UTF-16；安全前缀截断不切开代理对；空白不占条目
- source_id 带 chunk:/entry: 命名空间并去重，metadata 与 sources 严格 1:1 对应
- 确定性筛选与补足顺序：
  1. action 字面命中（通过已有的 search_chunks）提取有效切片；
  2. 本世界有效 confirmed 人工条目（命中优先，随后按 updated_at DESC, id ASC 排序）；
  3. 本世界有效文档切片（按 document_id ASC, chunk_index ASC 稳定补足）。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from app.lore_search import SearchError, search_chunks
from app.narration import ContextSource

from .types import (
    MAX_ACTION_UNITS,
    MAX_CONTEXT_UNITS,
    MAX_SOURCE_UNITS,
    MAX_SOURCES,
    ContextBundle,
    ContextError,
    safe_u16_truncate,
    u16len,
)

def _check_world_active(conn: sqlite3.Connection, world_id: str) -> None:
    """校验世界存在且未被删除。"""
    if not isinstance(world_id, str) or not world_id.strip():
        raise ContextError(
            "WORLD_NOT_FOUND",
            "世界不存在或已被删除。",
            status=404,
            details={"world_id": world_id},
        )
    cur = conn.cursor()
    cur.execute(
        "SELECT id, deleted_at FROM worlds WHERE id = ?",
        (world_id.strip(),),
    )
    row = cur.fetchone()
    if not row:
        raise ContextError(
            "WORLD_NOT_FOUND",
            "世界不存在或已被删除。",
            status=404,
            details={"world_id": world_id},
        )
    deleted_at = row["deleted_at"] if hasattr(row, "keys") else row[1]
    if deleted_at is not None:
        raise ContextError(
            "WORLD_NOT_FOUND",
            "世界不存在或已被删除。",
            status=404,
            details={"world_id": world_id},
        )


def _validate_action(action: str) -> str:
    """校验玩家行动输入有效性。"""
    if not isinstance(action, str):
        raise ContextError(
            "ACTION_INVALID",
            "请先输入行动。",
            status=422,
            details={"field": "action"},
        )
    action_stripped = action.strip()
    if not action_stripped:
        raise ContextError(
            "ACTION_INVALID",
            "请先输入行动。",
            status=422,
            details={"field": "action"},
        )
    action_units = u16len(action)
    if action_units > MAX_ACTION_UNITS:
        raise ContextError(
            "ACTION_INVALID",
            f"行动超过 {MAX_ACTION_UNITS} 个 UTF-16 单位。",
            status=422,
            details={"max": MAX_ACTION_UNITS, "actual": action_units},
        )
    return action_stripped


def build_context(
    conn: sqlite3.Connection,
    world_id: str,
    action: str,
) -> ContextBundle:
    """为创作者叙事实验构建可追溯上下文包。

    Args:
        conn: 外部传入的 SQLite 连接（只读使用，不提交）
        world_id: 目标世界 ID
        action: 玩家输入的行动描述文本

    Returns:
        ContextBundle(sources, metadata)
    """
    _check_world_active(conn, world_id)
    action_stripped = _validate_action(action)
    clean_world_id = world_id.strip()

    sources: list[ContextSource] = []
    metadata: list[dict[str, Any]] = []
    added_ids: set[str] = set()
    total_units = 0

    def try_add_source(
        source_id: str,
        kind: Literal["chunk", "entry"],
        raw_text: str,
        meta_dict: dict[str, Any],
        reason: str = "document_fallback",
    ) -> bool:
        """尝试将资料加入上下文，遵循严格预算、代理对保护和去重。"""
        nonlocal total_units

        if len(sources) >= MAX_SOURCES:
            return False
        if total_units >= MAX_CONTEXT_UNITS:
            return False
        if source_id in added_ids:
            return False

        text = raw_text.strip()
        if not text:
            # 空白不占条目
            return False

        remaining_total = MAX_CONTEXT_UNITS - total_units
        if remaining_total <= 0:
            return False

        max_allowed = min(MAX_SOURCE_UNITS, remaining_total)
        current_units = u16len(text)
        if current_units > max_allowed:
            text = safe_u16_truncate(text, max_allowed).strip()
            if not text:
                return False
            current_units = u16len(text)

        meta_dict = {**meta_dict, "selection_reason": reason, "truncated": current_units < u16len(raw_text.strip())}
        sources.append(ContextSource(source_id=source_id, kind=kind, text=text))
        metadata.append(meta_dict)
        added_ids.add(source_id)
        total_units += current_units
        return True

    cur = conn.cursor()

    # 阶段 1：复用字面 search_chunks 匹配行动
    if 1 <= len(action_stripped) <= 100:
        try:
            search_res = search_chunks(
                conn,
                world_id=clean_world_id,
                query=action_stripped,
                limit=MAX_SOURCES,
            )
            if search_res and search_res.get("items"):
                for item in search_res["items"]:
                    chunk_id = item["chunk_id"]
                    sid = f"chunk:{chunk_id}"
                    if sid in added_ids:
                        continue
                    # 查询切片完整文本
                    cur.execute(
                        """SELECT c.text, d.filename
                           FROM chunks c
                           JOIN documents d ON c.document_id = d.id
                           WHERE c.id = ? AND c.world_id = ? AND d.world_id = c.world_id AND d.deleted_at IS NULL""",
                        (chunk_id, clean_world_id),
                    )
                    c_row = cur.fetchone()
                    if not c_row:
                        continue
                    c_text = c_row["text"] if hasattr(c_row, "keys") else c_row[0]
                    d_filename = c_row["filename"] if hasattr(c_row, "keys") else c_row[1]

                    meta = {
                        "source_id": sid,
                        "kind": "chunk",
                        "title": d_filename,
                        "document_id": item["document_id"],
                        "chunk_id": chunk_id,
                        "chunk_index": item["chunk_index"],
                    }
                    if not try_add_source(sid, "chunk", c_text, meta, "literal_match"):
                        break
        except SearchError:
            # 搜索异常时平滑降级到后续确定性规则
            pass

    # 阶段 2：取本世界 confirmed 人工条目
    # 规则：仅选本世界、未软删除、status='confirmed' 的条目；draft 绝不自动晋升；
    # 存在失效来源（needs_source_review == True）保守排除；无来源 manual confirmed 合法保留。
    if len(sources) < MAX_SOURCES and total_units < MAX_CONTEXT_UNITS:
        # Filter invalid references in SQL rather than one service query per entry.
        # Iterate rows lazily; the source budget stops reading as soon as it is full.
        cur.execute(
            """SELECT e.id, e.title, e.summary, e.origin, e.updated_at,
                      CASE WHEN instr(lower(?), lower(e.title)) > 0
                        OR instr(lower(e.title), lower(?)) > 0
                        OR instr(lower(e.summary), lower(?)) > 0 THEN 0 ELSE 1 END AS priority
               FROM lore_entries e
               WHERE e.world_id = ? AND e.deleted_at IS NULL AND e.status = 'confirmed'
                 AND NOT EXISTS (
                   SELECT 1 FROM lore_entry_sources s
                   LEFT JOIN documents d ON d.id = s.document_id
                   LEFT JOIN chunks c ON c.id = s.chunk_id
                   WHERE s.entry_id = e.id AND s.deleted_at IS NULL AND (
                     d.id IS NULL OR d.deleted_at IS NOT NULL OR c.id IS NULL
                     OR c.document_id != s.document_id OR d.world_id != e.world_id
                     OR c.world_id != e.world_id))
               ORDER BY priority ASC, e.updated_at DESC, e.id ASC""",
            (action_stripped, action_stripped, action_stripped, clean_world_id),
        )

        for erow in cur:
            if len(sources) >= MAX_SOURCES or total_units >= MAX_CONTEXT_UNITS:
                break
            eid = erow["id"] if hasattr(erow, "keys") else erow[0]
            title = erow["title"] if hasattr(erow, "keys") else erow[1]
            summary = erow["summary"] if hasattr(erow, "keys") else erow[2]
            origin = erow["origin"] if hasattr(erow, "keys") else erow[3]
            sid = f"entry:{eid}"
            if sid in added_ids:
                continue

            entry_text = (
                f"{title.strip()}\n{summary.strip()}"
                if summary and summary.strip()
                else title.strip()
            )
            meta = {
                "source_id": sid,
                "kind": "entry",
                "title": title.strip(),
                "entry_id": eid,
                "origin": origin or "manual",
            }
            priority = erow["priority"] if hasattr(erow, "keys") else erow[5]
            try_add_source(sid, "entry", entry_text, meta, "literal_match" if priority == 0 else "confirmed_fallback")

    # 阶段 3：按 document_id ASC, chunk_index ASC 稳定补足有效文档切片
    if len(sources) < MAX_SOURCES and total_units < MAX_CONTEXT_UNITS:
        cur.execute(
            """SELECT c.id, c.document_id, c.chunk_index, c.text, d.filename
               FROM chunks c
               JOIN documents d ON c.document_id = d.id
               WHERE c.world_id = ? AND d.world_id = c.world_id AND d.deleted_at IS NULL
               ORDER BY c.document_id ASC, c.chunk_index ASC""",
            (clean_world_id,),
        )
        for crow in cur:
            if len(sources) >= MAX_SOURCES or total_units >= MAX_CONTEXT_UNITS:
                break
            cid = crow["id"] if hasattr(crow, "keys") else crow[0]
            doc_id = crow["document_id"] if hasattr(crow, "keys") else crow[1]
            c_idx = crow["chunk_index"] if hasattr(crow, "keys") else crow[2]
            c_text = crow["text"] if hasattr(crow, "keys") else crow[3]
            d_filename = crow["filename"] if hasattr(crow, "keys") else crow[4]
            sid = f"chunk:{cid}"
            if sid in added_ids:
                continue

            meta = {
                "source_id": sid,
                "kind": "chunk",
                "title": d_filename,
                "document_id": doc_id,
                "chunk_id": cid,
                "chunk_index": c_idx,
            }
            try_add_source(sid, "chunk", c_text, meta)

    return ContextBundle(sources=tuple(sources), metadata=tuple(metadata))
