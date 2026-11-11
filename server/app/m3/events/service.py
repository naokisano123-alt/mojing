"""M3.1 / M3.5 世界事件账本持久层服务（`read_snapshot`, `apply_proposal`, `snapshot`, `restore`, 到期分页读取等）。

契约要求（`docs/contracts/M3_WAVE1_API.md`, `docs/contracts/M3_WAVE2_API.md` & `docs/collaboration/M3_WAVE2_GEMINI_MARCIN.md`）：
1. 模块数据库写入口接收已打开的 `sqlite3.Connection`，模块不自行调用 `conn.commit()`。
2. 提供可供 `PulseWorker` 直接接入的只读快照回调 `read_snapshot(conn, instance_id, job)`，
   完成队列任务 `kind` 与事件包 `mode`、数据库 `world_instances.revision` 与对外 `world_revision` / `base_world_revision` 的映射。
3. Seed 按可解释的稳定顺序挑选有效 Canon 设定条目与文档分块节选，各自设条数与单条字符上限，整体受字符预算约束；无授权资料时返回可展示的阻塞原因（`PULSE_SOURCE_REQUIRED`）。
4. Advance 仅读取到期活跃事件（`status='active' AND next_update_minute <= world_minute`）及最小因果邻域、允许的 Canon ID 与节选；无到期事件或到期时间超过已确认分钟时返回跳过（`skip=True`）。
5. `apply_proposal(conn, instance_id, validated, job=None, ...)` 在 `SAVEPOINT` 保护下重新核对实际数据库状态（实例版本、已确认时间、事件版本、跨实例引用、因果环、临时事件上限与重大转折前置条件），任何一项失败则完整回滚。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
import json
import sqlite3
from typing import Any
from uuid import uuid4

from .errors import (
    CausalCycleError,
    CrossInstanceReferenceError,
    EventNotFoundError,
    EventRevisionConflictError,
    EventValidationError,
    InstanceNotFoundError,
    PulseSourceRequiredError,
    SnapshotRestoreError,
    SourceFabricatedError,
    WorldRevisionConflictError,
)
from .schema import ensure_events_schema
from .types import (
    CONSEQUENCE_DOMAINS,
    DEFAULT_ADVANCE_CHAR_BUDGET,
    DEFAULT_ADVANCE_MAX_CAUSAL_NEIGHBORS,
    DEFAULT_ADVANCE_MAX_CHUNK_CHARS,
    DEFAULT_ADVANCE_MAX_CHUNKS,
    DEFAULT_ADVANCE_MAX_DUE_EVENTS,
    DEFAULT_ADVANCE_MAX_ENTRIES,
    DEFAULT_ADVANCE_MAX_ENTRY_CHARS,
    DEFAULT_MAX_ACTIVE_INCIDENTS,
    DEFAULT_MAX_INITIAL_THREADS,
    DEFAULT_MIN_INITIAL_THREADS,
    DEFAULT_SEED_CHAR_BUDGET,
    DEFAULT_SEED_MAX_CHUNK_CHARS,
    DEFAULT_SEED_MAX_CHUNKS,
    DEFAULT_SEED_MAX_ENTRIES,
    DEFAULT_SEED_MAX_ENTRY_CHARS,
    EVENT_KINDS,
    EVENT_SCOPES,
    EVENT_STATUSES,
    IMPACT_TYPES,
    LORE_ENTRY_TYPE_PRIORITY,
    MAJOR_TURNPOINT_STAGES,
    MAJOR_TURNPOINT_TYPES,
    PROPOSAL_MODES,
    SNAPSHOT_FORMAT_VERSION,
    TERMINAL_EVENT_STATUSES,
    CanonSourceItem,
    ProposalMode,
    ProposalValidationContext,
    ValidatedEventOp,
    ValidatedEventProposal,
)
from .validator import (
    _detect_causal_cycles,
    _extract_minute_from_mapping,
    _extract_mode_from_mapping,
    _extract_revision_from_mapping,
    _require_non_empty_str,
    _require_strict_int,
    _safe_u16_truncate,
    _u16_units,
    validate_proposal,
)


def _default_event_id() -> str:
    return f"evt_{uuid4().hex[:12]}"


def _default_consequence_id() -> str:
    return f"csq_{uuid4().hex[:12]}"


def _default_precondition_id() -> str:
    return f"pre_{uuid4().hex[:12]}"


def ensure_instance_exists(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_id: str = "",
    adventure_id: str | None = None,
    world_minute: int = 0,
    revision: int = 0,
) -> dict[str, Any]:
    """确保 `world_instances` 中存在指定实例（供独立测试或初始化辅助使用）。"""
    ensure_events_schema(conn)
    instance_id = _require_non_empty_str(instance_id, "instance_id", 120)
    world_minute = _require_strict_int(world_minute, "world_minute", min_value=0)
    revision = _require_strict_int(revision, "revision", min_value=0)
    row = conn.execute(
        "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision)
            VALUES (?, ?, ?, ?, ?)
            """,
            (instance_id, world_id, adventure_id, world_minute, revision),
        )
        row = conn.execute(
            "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
    rev = int(row[4])
    return {
        "id": row[0],
        "world_id": row[1],
        "adventure_id": row[2],
        "world_minute": int(row[3]),
        "revision": rev,
        "world_revision": rev,
    }


def _get_instance_row(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    read_only: bool = False,
) -> dict[str, Any]:
    if not read_only:
        ensure_events_schema(conn)
    else:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "world_events" not in tables:
            ensure_events_schema(conn)
    instance_id = _require_non_empty_str(instance_id, "instance_id", 120)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(world_instances)").fetchall()}
    rev_col = "revision" if "revision" in cols else ("world_revision" if "world_revision" in cols else "0")
    row = conn.execute(
        f"SELECT id, world_id, adventure_id, world_minute, {rev_col} FROM world_instances WHERE id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise InstanceNotFoundError(instance_id)
    rev = int(row[4] or 0)
    return {
        "id": row[0],
        "world_id": row[1],
        "adventure_id": row[2],
        "world_minute": int(row[3] or 0),
        "revision": rev,
        "world_revision": rev,
    }


def _row_to_event_dict(
    row: sqlite3.Row | tuple[Any, ...],
    consequences_by_event: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    else:
        (
            instance_id,
            event_id,
            kind,
            title,
            description,
            location,
            scope,
            participants_json,
            stage,
            status,
            created_minute,
            updated_minute,
            next_update_minute,
            cause_event_ids_json,
            source_ids_json,
            turnpoint_type,
            precondition_ids_json,
            revision,
            public_label,
        ) = row
        data = {
            "instance_id": instance_id,
            "event_id": event_id,
            "kind": kind,
            "title": title,
            "description": description,
            "location": location,
            "scope": scope,
            "participants_json": participants_json,
            "stage": stage,
            "status": status,
            "created_minute": created_minute,
            "updated_minute": updated_minute,
            "next_update_minute": next_update_minute,
            "cause_event_ids_json": cause_event_ids_json,
            "source_ids_json": source_ids_json,
            "turnpoint_type": turnpoint_type,
            "precondition_ids_json": precondition_ids_json,
            "revision": revision,
            "public_label": public_label,
        }

    event_id = data["event_id"]
    record: dict[str, Any] = {
        "instance_id": data["instance_id"],
        "event_id": event_id,
        "kind": data["kind"],
        "title": data["title"],
        "description": data["description"],
        "location": data["location"],
        "scope": data["scope"],
        "participants": json.loads(data.get("participants_json") or "[]"),
        "stage": data["stage"],
        "status": data["status"],
        "created_minute": int(data["created_minute"]),
        "updated_minute": int(data["updated_minute"]),
        "next_update_minute": (
            int(data["next_update_minute"]) if data["next_update_minute"] is not None else None
        ),
        "cause_event_ids": json.loads(data.get("cause_event_ids_json") or "[]"),
        "source_ids": json.loads(data.get("source_ids_json") or "[]"),
        "turnpoint_type": data.get("turnpoint_type"),
        "precondition_ids": json.loads(data.get("precondition_ids_json") or "[]"),
        "revision": int(data["revision"]),
    }
    if data.get("public_label"):
        record["public_label"] = data["public_label"]
    if consequences_by_event is not None:
        record["consequences"] = list(consequences_by_event.get(event_id, ()))
    return record


def _load_consequences_map(
    conn: sqlite3.Connection, instance_id: str
) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT instance_id, consequence_id, source_event_id, source_event_revision,
               impact_type, domain, target, summary, effective_minute
        FROM world_event_consequences
        WHERE instance_id = ?
        ORDER BY effective_minute ASC, consequence_id ASC
        """,
        (instance_id,),
    ).fetchall()
    mapping: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = {
            "instance_id": row[0],
            "consequence_id": row[1],
            "source_event_id": row[2],
            "source_event_revision": int(row[3]),
            "impact_type": row[4],
            "domain": row[5],
            "target": row[6],
            "summary": row[7],
            "effective_minute": int(row[8]),
        }
        mapping.setdefault(item["source_event_id"], []).append(item)
    return mapping


def _discover_world_canon_sources(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    confirmed_entries_only: bool = True,
) -> frozenset[str]:
    """从数据库发现当前 `world_id` 下所有合法且未软删除的 `chunk:<id>` 与 `entry:<id>`。"""
    if not world_id:
        return frozenset()
    allowed: set[str] = set()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "chunks" in tables and "documents" in tables:
        doc_cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        status_clause = "AND (d.status IS NULL OR d.status = 'ready')" if "status" in doc_cols else ""
        rows = conn.execute(
            f"""
            SELECT c.id
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.world_id = ? AND d.deleted_at IS NULL {status_clause}
            """,
            (world_id,),
        ).fetchall()
        for r in rows:
            allowed.add(f"chunk:{r[0]}")
    if "lore_entries" in tables:
        entry_cols = {r[1] for r in conn.execute("PRAGMA table_info(lore_entries)").fetchall()}
        if confirmed_entries_only and "status" in entry_cols:
            rows = conn.execute(
                """
                SELECT id FROM lore_entries
                WHERE world_id = ? AND deleted_at IS NULL AND status = 'confirmed'
                """,
                (world_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id FROM lore_entries
                WHERE world_id = ? AND deleted_at IS NULL
                """,
                (world_id,),
            ).fetchall()
        for r in rows:
            allowed.add(f"entry:{r[0]}")
    return frozenset(allowed)


def _fetch_all_canon_candidates(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    fallback_sources: list[Mapping[str, Any]] | tuple[Any, ...] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按可解释的稳定顺序读取指定世界下所有有效设定条目与文档分块候选。

    稳定顺序规则：
    1. 设定条目（`lore_entries`）：仅取 `deleted_at IS NULL AND status = 'confirmed'` 且正文非空的条目，
       按 `(类型优先级 LORE_ENTRY_TYPE_PRIORITY, id ASC)` 排序；
    2. 文档分块（`chunks JOIN documents`）：仅取 `d.deleted_at IS NULL AND (d.status IS NULL OR d.status = 'ready')`
       且正文非空的分块，按 `(c.chunk_index ASC, d.created_at ASC, d.id ASC, c.id ASC)` 跨文档轮询排序（优先覆盖各份文档的首块与前序章节）。
    """
    entry_candidates: list[dict[str, Any]] = []
    chunk_candidates: list[dict[str, Any]] = []

    if world_id:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "lore_entries" in tables:
            entry_cols = {r[1] for r in conn.execute("PRAGMA table_info(lore_entries)").fetchall()}
            status_sql = "AND status = 'confirmed'" if "status" in entry_cols else ""
            type_col = "type" if "type" in entry_cols else "'other'"
            title_col = "title" if "title" in entry_cols else "''"
            summary_col = "summary" if "summary" in entry_cols else "''"
            rows = conn.execute(
                f"""
                SELECT id, {type_col}, {title_col}, {summary_col}
                FROM lore_entries
                WHERE world_id = ? AND deleted_at IS NULL {status_sql}
                ORDER BY id ASC
                """,
                (world_id,),
            ).fetchall()
            raw_entries: list[tuple[int, str, dict[str, Any]]] = []
            for r in rows:
                eid = str(r[0] or "").strip()
                etype = str(r[1] or "other").strip()
                etitle = str(r[2] or "").strip()
                esummary = str(r[3] or "").strip()
                body = esummary if esummary else etitle
                if not eid or not body:
                    continue
                priority = LORE_ENTRY_TYPE_PRIORITY.get(etype.lower(), 99)
                raw_entries.append(
                    (
                        priority,
                        eid,
                        {
                            "source_id": f"entry:{eid}",
                            "kind": "entry",
                            "title": etitle or f"设定条目 {eid}",
                            "text": f"【{etitle}】{esummary}" if (etitle and esummary and etitle not in esummary) else body,
                            "entry_type": etype,
                        },
                    )
                )
            raw_entries.sort(key=lambda item: (item[0], item[1]))
            entry_candidates = [item[2] for item in raw_entries]

        if "chunks" in tables and "documents" in tables:
            doc_cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
            status_sql = "AND (d.status IS NULL OR d.status = 'ready')" if "status" in doc_cols else ""
            created_col = "d.created_at" if "created_at" in doc_cols else "d.id"
            filename_col = "d.filename" if "filename" in doc_cols else "d.id"
            rows = conn.execute(
                f"""
                SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.text, {filename_col}, {created_col}
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.world_id = ? AND d.deleted_at IS NULL {status_sql}
                ORDER BY c.chunk_index ASC, {created_col} ASC, d.id ASC, c.id ASC
                """,
                (world_id,),
            ).fetchall()
            for r in rows:
                cid = str(r[0] or "").strip()
                doc_id = str(r[1] or "").strip()
                c_idx = int(r[2] or 0)
                heading = str(r[3] or "").strip()
                ctext = str(r[4] or "").strip()
                fname = str(r[5] or "").strip()
                if not cid or not ctext:
                    continue
                title_label = f"{fname} · {heading}" if (fname and heading) else (heading or fname or f"分块 {cid}")
                chunk_candidates.append(
                    {
                        "source_id": f"chunk:{cid}",
                        "kind": "chunk",
                        "title": title_label,
                        "text": ctext,
                        "document_id": doc_id,
                        "chunk_index": c_idx,
                    }
                )

    # 若数据库中无表或无记录，且测试任务显式注入了合成 canon_sources，则按相同确定性规则归一化
    if not entry_candidates and not chunk_candidates and fallback_sources:
        fb_entries: list[tuple[int, str, dict[str, Any]]] = []
        fb_chunks: list[tuple[int, str, str, dict[str, Any]]] = []
        for raw_item in fallback_sources:
            item_obj = (
                raw_item.to_dict()
                if isinstance(raw_item, CanonSourceItem)
                else (dict(raw_item) if isinstance(raw_item, Mapping) else None)
            )
            if not item_obj:
                continue
            sid = str(item_obj.get("source_id") or "").strip()
            text = str(item_obj.get("text") or "").strip()
            if not sid or not text:
                continue
            title = str(item_obj.get("title") or "").strip()
            if sid.startswith("entry:"):
                etype = str(item_obj.get("entry_type") or "other").strip()
                prio = LORE_ENTRY_TYPE_PRIORITY.get(etype.lower(), 99)
                fb_entries.append(
                    (
                        prio,
                        sid,
                        {
                            "source_id": sid,
                            "kind": "entry",
                            "title": title or sid,
                            "text": text,
                            "entry_type": etype,
                        },
                    )
                )
            elif sid.startswith("chunk:"):
                c_idx = int(item_obj.get("chunk_index") or 0)
                doc_id = str(item_obj.get("document_id") or "")
                fb_chunks.append(
                    (
                        c_idx,
                        doc_id,
                        sid,
                        {
                            "source_id": sid,
                            "kind": "chunk",
                            "title": title or sid,
                            "text": text,
                            "document_id": doc_id,
                            "chunk_index": c_idx,
                        },
                    )
                )
        fb_entries.sort(key=lambda x: (x[0], x[1]))
        fb_chunks.sort(key=lambda x: (x[0], x[1], x[2]))
        entry_candidates = [x[2] for x in fb_entries]
        chunk_candidates = [x[3] for x in fb_chunks]

    return entry_candidates, chunk_candidates


def select_canon_sources(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    max_entries: int = DEFAULT_SEED_MAX_ENTRIES,
    max_chunks: int = DEFAULT_SEED_MAX_CHUNKS,
    max_entry_chars: int = DEFAULT_SEED_MAX_ENTRY_CHARS,
    max_chunk_chars: int = DEFAULT_SEED_MAX_CHUNK_CHARS,
    char_budget: int = DEFAULT_SEED_CHAR_BUDGET,
    preferred_source_ids: tuple[str, ...] | list[str] | frozenset[str] = (),
    fallback_sources: list[Mapping[str, Any]] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """按可解释的稳定顺序挑选有效 Canon 设定条目与分块节选，严格执行单类数量上限、单条字符上限与总字符预算。

    挑选顺序：
    1. 若提供了 `preferred_source_ids`（例如 Advance 中到期事件及因果邻域已绑定的来源 ID），
       先按 `preferred_source_ids` 出现顺序挑选对应的有效条目或分块；
    2. 再按稳定顺序挑選设定条目（`entry:<id>`，上限 `max_entries` 条，每条最多 `max_entry_chars` UTF-16 单位）；
    3. 随后按跨文档分块轮询顺序挑选文档分块（`chunk:<id>`，上限 `max_chunks` 条，每条最多 `max_chunk_chars` UTF-16 单位）；
    4. 全程累计字符数不超过 `char_budget`。
    """
    max_entries = _require_strict_int(max_entries, "max_entries", min_value=0)
    max_chunks = _require_strict_int(max_chunks, "max_chunks", min_value=0)
    max_entry_chars = _require_strict_int(max_entry_chars, "max_entry_chars", min_value=32)
    max_chunk_chars = _require_strict_int(max_chunk_chars, "max_chunk_chars", min_value=32)
    char_budget = _require_strict_int(char_budget, "char_budget", min_value=64)

    entry_candidates, chunk_candidates = _fetch_all_canon_candidates(
        conn, world_id, fallback_sources=fallback_sources
    )
    all_available_ids: set[str] = {
        c["source_id"] for c in entry_candidates
    } | {
        c["source_id"] for c in chunk_candidates
    }

    by_id: dict[str, dict[str, Any]] = {
        c["source_id"]: c for c in (*entry_candidates, *chunk_candidates)
    }

    ordered_queue: list[dict[str, Any]] = []
    queued_ids: set[str] = set()

    for pref_id in preferred_source_ids:
        if pref_id in by_id and pref_id not in queued_ids:
            ordered_queue.append(by_id[pref_id])
            queued_ids.add(pref_id)

    for item in entry_candidates:
        if item["source_id"] not in queued_ids:
            ordered_queue.append(item)
            queued_ids.add(item["source_id"])

    for item in chunk_candidates:
        if item["source_id"] not in queued_ids:
            ordered_queue.append(item)
            queued_ids.add(item["source_id"])

    selected_items: list[CanonSourceItem] = []
    used_entries = 0
    used_chunks = 0
    used_chars = 0

    for cand in ordered_queue:
        if used_chars >= char_budget:
            break
        kind = cand["kind"]
        if kind == "entry":
            if used_entries >= max_entries:
                continue
            per_item_cap = max_entry_chars
        else:
            if used_chunks >= max_chunks:
                continue
            per_item_cap = max_chunk_chars

        remaining_budget = char_budget - used_chars
        if remaining_budget <= 0:
            break

        effective_cap = min(per_item_cap, remaining_budget)
        raw_text = cand["text"].strip()
        raw_units = _u16_units(raw_text, "canon.text")
        if raw_units > effective_cap:
            excerpt = _safe_u16_truncate(raw_text, effective_cap).strip()
            truncated = True
        else:
            excerpt = raw_text
            truncated = False

        if not excerpt:
            continue

        excerpt_units = _u16_units(excerpt, "canon.excerpt")
        src_obj = CanonSourceItem(
            source_id=cand["source_id"],
            kind=kind,
            title=cand.get("title", ""),
            text=excerpt,
            entry_type=cand.get("entry_type", ""),
            document_id=cand.get("document_id", ""),
            chunk_index=cand.get("chunk_index"),
            char_count=excerpt_units,
            truncated=truncated,
        )
        selected_items.append(src_obj)
        used_chars += excerpt_units
        if kind == "entry":
            used_entries += 1
        else:
            used_chunks += 1

    return {
        "sources": tuple(selected_items),
        "canon_sources": [s.to_dict() for s in selected_items],
        "selected_source_ids": [s.source_id for s in selected_items],
        "all_valid_source_ids": sorted(all_available_ids),
        "budget": {
            "selection_order": "preferred_event_sources -> confirmed_lore_entries(type_priority,id) -> ready_document_chunks(chunk_index,doc_created_at,doc_id,chunk_id)",
            "max_entries": max_entries,
            "max_chunks": max_chunks,
            "max_entry_chars": max_entry_chars,
            "max_chunk_chars": max_chunk_chars,
            "char_budget": char_budget,
            "used_chars": used_chars,
            "selected_entry_count": used_entries,
            "selected_chunk_count": used_chunks,
            "total_available_entries": len(entry_candidates),
            "total_available_chunks": len(chunk_candidates),
        },
    }


def _select_minimal_causal_neighborhood(
    conn: sqlite3.Connection,
    instance_id: str,
    due_events: list[dict[str, Any]],
    consequences_map: dict[str, list[dict[str, Any]]],
    *,
    max_neighbors: int = DEFAULT_ADVANCE_MAX_CAUSAL_NEIGHBORS,
) -> list[dict[str, Any]]:
    """为到期活跃事件抽取最小因果邻域（同一实例内的直接父事件 + 1 跳直接衍生事件，不含其它无关事件）。"""
    max_neighbors = _require_strict_int(max_neighbors, "max_neighbors", min_value=0)
    if not due_events or max_neighbors == 0:
        return []

    due_ids = {ev["event_id"] for ev in due_events}
    parent_ids: list[str] = []
    seen_candidates: set[str] = set(due_ids)

    # 1. 直接因果父事件（按到期事件顺序收集）
    for ev in due_events:
        for cid in ev.get("cause_event_ids", ()):
            if isinstance(cid, str) and cid not in seen_candidates:
                seen_candidates.add(cid)
                parent_ids.append(cid)

    neighbors: list[dict[str, Any]] = []
    if parent_ids:
        placeholders = ",".join("?" for _ in parent_ids)
        rows = conn.execute(
            f"""
            SELECT instance_id, event_id, kind, title, description, location, scope,
                   participants_json, stage, status, created_minute, updated_minute,
                   next_update_minute, cause_event_ids_json, source_ids_json,
                   turnpoint_type, precondition_ids_json, revision, public_label
            FROM world_events
            WHERE instance_id = ? AND event_id IN ({placeholders})
            ORDER BY updated_minute DESC, created_minute ASC, event_id ASC
            """,
            (instance_id, *parent_ids),
        ).fetchall()
        by_eid = {r[1]: _row_to_event_dict(r, consequences_map) for r in rows}
        for pid in parent_ids:
            if pid in by_eid and len(neighbors) < max_neighbors:
                neighbors.append(by_eid[pid])

    # 2. 若仍有配额，补充 1 跳直接子事件及本实例近期已发生演进（updated_minute > created_minute 或已产生后果）的潜在上游事件
    if len(neighbors) < max_neighbors:
        all_rows = conn.execute(
            """
            SELECT instance_id, event_id, kind, title, description, location, scope,
                   participants_json, stage, status, created_minute, updated_minute,
                   next_update_minute, cause_event_ids_json, source_ids_json,
                   turnpoint_type, precondition_ids_json, revision, public_label
            FROM world_events
            WHERE instance_id = ?
            ORDER BY updated_minute DESC, revision DESC, created_minute ASC, event_id ASC
            """,
            (instance_id,),
        ).fetchall()
        for r in all_rows:
            eid = r[1]
            if eid in seen_candidates:
                continue
            causes = json.loads(r[13] or "[]")
            if any(c in due_ids for c in causes):
                seen_candidates.add(eid)
                neighbors.append(_row_to_event_dict(r, consequences_map))
                if len(neighbors) >= max_neighbors:
                    break
        if len(neighbors) < max_neighbors:
            for r in all_rows:
                eid = r[1]
                if eid in seen_candidates:
                    continue
                status_val = r[9]
                created_min = int(r[10] or 0)
                updated_min = int(r[11] or 0)
                rev_val = int(r[17] or 1)
                if status_val in ("active", "completed") and (
                    updated_min > created_min or rev_val > 1 or bool(consequences_map.get(eid))
                ):
                    seen_candidates.add(eid)
                    neighbors.append(_row_to_event_dict(r, consequences_map))
                    if len(neighbors) >= max_neighbors:
                        break

    return neighbors


def _read_world_intro(conn: sqlite3.Connection, world_id: str) -> str:
    if not world_id:
        return ""
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "worlds" not in tables:
        return ""
    row = conn.execute(
        "SELECT name, description FROM worlds WHERE id = ? AND deleted_at IS NULL",
        (world_id,),
    ).fetchone()
    if row is None:
        return ""
    name = str(row[0] or "").strip()
    desc = str(row[1] or "").strip()
    if name and desc:
        return _safe_u16_truncate(f"{name}：{desc}", 600)
    return _safe_u16_truncate(name or desc, 600)


def read_snapshot(
    conn: sqlite3.Connection,
    instance_id: str,
    job: Mapping[str, Any] | None = None,
    *,
    max_seed_entries: int = DEFAULT_SEED_MAX_ENTRIES,
    max_seed_chunks: int = DEFAULT_SEED_MAX_CHUNKS,
    max_seed_entry_chars: int = DEFAULT_SEED_MAX_ENTRY_CHARS,
    max_seed_chunk_chars: int = DEFAULT_SEED_MAX_CHUNK_CHARS,
    seed_char_budget: int = DEFAULT_SEED_CHAR_BUDGET,
    max_due_events: int = DEFAULT_ADVANCE_MAX_DUE_EVENTS,
    max_causal_neighbors: int = DEFAULT_ADVANCE_MAX_CAUSAL_NEIGHBORS,
    max_advance_entries: int = DEFAULT_ADVANCE_MAX_ENTRIES,
    max_advance_chunks: int = DEFAULT_ADVANCE_MAX_CHUNKS,
    max_advance_entry_chars: int = DEFAULT_ADVANCE_MAX_ENTRY_CHARS,
    max_advance_chunk_chars: int = DEFAULT_ADVANCE_MAX_CHUNK_CHARS,
    advance_char_budget: int = DEFAULT_ADVANCE_CHAR_BUDGET,
    max_active_incidents: int = DEFAULT_MAX_ACTIVE_INCIDENTS,
) -> dict[str, Any]:
    """可供 `PulseWorker` 直接挂载的只读快照读取回调 `read_snapshot(conn, instance_id, job) -> dict`。

    严格遵守 `docs/contracts/M3_WAVE2_API.md` 与 `M3_WAVE2_GEMINI_MARCIN.md`：
    - 只读查询，不修改数据库状态；
    - 世界分钟（`world_minute` / `target_world_minute`）与版本（`world_revision` / `base_world_revision` / `revision`）
      均取自已确认实例根 `world_instances`；
    - 同时写入 `kind` 与 `mode` 映射；
    - Seed：按稳定顺序从当前世界有效 Canon 挑选设定条目与分块节选，受条数与字符预算约束；
      无授权资料时返回 `status='blocked', blocked=True, skip=True, reason_code='PULSE_SOURCE_REQUIRED'` 及可展示中文原因；
    - Advance：仅传到期的活跃事件（`status='active' AND next_update_minute <= world_minute`）及最小因果邻域、
      允许的 Canon ID 与节选；无到期事件或到期时间超过已确认分钟时返回 `status='skipped', skip=True` 及明确跳过原因。
    """
    inst = _get_instance_row(conn, instance_id, read_only=True)
    world_id = str(inst["world_id"] or "")
    confirmed_minute = int(inst["world_minute"])
    confirmed_revision = int(inst["revision"])

    job_map: Mapping[str, Any] = job if isinstance(job, Mapping) else {}
    if job_map.get("instance_id") is not None:
        job_inst_id = _require_non_empty_str(job_map.get("instance_id"), "job.instance_id", 120)
        if job_inst_id != instance_id:
            raise CrossInstanceReferenceError(instance_id, job_inst_id, job_inst_id)

    resolved_mode, _ = _extract_mode_from_mapping(job_map, "job")
    kind: ProposalMode = (resolved_mode or "advance")  # type: ignore[assignment]

    payload_raw = job_map.get("payload")
    job_payload: Mapping[str, Any] = payload_raw if isinstance(payload_raw, Mapping) else {}
    fallback_canon = job_payload.get("canon_sources")
    explicit_allowed_ids = job_payload.get("allowed_source_ids")

    other_rows = conn.execute(
        "SELECT event_id, instance_id FROM world_events WHERE instance_id != ?",
        (instance_id,),
    ).fetchall()
    other_instance_event_ids = {str(r[0]): str(r[1]) for r in other_rows}

    base_envelope: dict[str, Any] = {
        "instance_id": instance_id,
        "world_id": world_id,
        "world_minute": confirmed_minute,
        "target_world_minute": confirmed_minute,
        "world_revision": confirmed_revision,
        "base_world_revision": confirmed_revision,
        "revision": confirmed_revision,
        "kind": kind,
        "mode": kind,
        "job_kind": kind,
    }

    if kind == "seed":
        selection = select_canon_sources(
            conn,
            world_id,
            max_entries=max_seed_entries,
            max_chunks=max_seed_chunks,
            max_entry_chars=max_seed_entry_chars,
            max_chunk_chars=max_seed_chunk_chars,
            char_budget=seed_char_budget,
            fallback_sources=fallback_canon if isinstance(fallback_canon, (list, tuple)) else None,
        )
        canon_sources = selection["canon_sources"]
        selected_ids = list(selection["selected_source_ids"])
        if not selected_ids and isinstance(explicit_allowed_ids, (list, tuple)):
            for raw_sid in explicit_allowed_ids:
                if isinstance(raw_sid, str) and (raw_sid.startswith("chunk:") or raw_sid.startswith("entry:")):
                    selected_ids.append(raw_sid.strip())

        if not canon_sources and not selected_ids:
            reason_msg = "当前世界尚无已确认的设定条目或有效导入资料分块，无法生成初始事件线。请先导入一份世界资料或确认至少一条人工设定。"
            return {
                **base_envelope,
                "status": "blocked",
                "blocked": True,
                "skip": True,
                "reason_code": "PULSE_SOURCE_REQUIRED",
                "reason_message": reason_msg,
                "reason": reason_msg,
                "world_intro": _read_world_intro(conn, world_id),
                "canon_sources": [],
                "allowed_source_ids": [],
                "allowed_event_ids": [],
                "min_initial_threads": DEFAULT_MIN_INITIAL_THREADS,
                "max_initial_threads": DEFAULT_MAX_INITIAL_THREADS,
                "budget": selection["budget"],
                "other_instance_event_ids": other_instance_event_ids,
            }

        allowed_ids = sorted(set(selected_ids) | set(selection["all_valid_source_ids"]))
        return {
            **base_envelope,
            "status": "ready",
            "blocked": False,
            "skip": False,
            "reason_code": None,
            "reason_message": None,
            "world_intro": _read_world_intro(conn, world_id),
            "canon_sources": canon_sources,
            "allowed_source_ids": allowed_ids,
            "selected_source_ids": selected_ids,
            "allowed_event_ids": [],
            "min_initial_threads": int(job_payload.get("min_initial_threads", DEFAULT_MIN_INITIAL_THREADS)),
            "max_initial_threads": int(job_payload.get("max_initial_threads", DEFAULT_MAX_INITIAL_THREADS)),
            "budget": selection["budget"],
            "other_instance_event_ids": other_instance_event_ids,
        }

    # ==================== kind == "advance" ====================
    # 检查请求的到期时间是否超过已确认分钟
    requested_due_minute = (
        job_map.get("due_minute")
        if job_map.get("due_minute") is not None
        else job_map.get("target_world_minute")
    )
    if requested_due_minute is not None:
        req_min = _require_strict_int(requested_due_minute, "job.target_world_minute", min_value=0)
        if req_min > confirmed_minute:
            msg = f"任务请求的到期时间 ({req_min}) 超过实例已确认分钟 ({confirmed_minute})，本次跳过推进。"
            return {
                **base_envelope,
                "status": "skipped",
                "skip": True,
                "blocked": False,
                "reason_code": "DUE_TIME_EXCEEDS_CONFIRMED_MINUTE",
                "reason_message": msg,
                "reason": msg,
                "requested_due_minute": req_min,
                "due_events": [],
                "causal_neighborhood": [],
                "canon_sources": [],
                "allowed_source_ids": [],
                "allowed_event_ids": [],
                "active_incident_count": 0,
                "max_active_incidents": max_active_incidents,
                "registered_precondition_ids": [],
                "other_instance_event_ids": other_instance_event_ids,
            }

    consequences_map = _load_consequences_map(conn, instance_id)
    due_rows = conn.execute(
        """
        SELECT instance_id, event_id, kind, title, description, location, scope,
               participants_json, stage, status, created_minute, updated_minute,
               next_update_minute, cause_event_ids_json, source_ids_json,
               turnpoint_type, precondition_ids_json, revision, public_label
        FROM world_events
        WHERE instance_id = ?
          AND status = 'active'
          AND next_update_minute IS NOT NULL
          AND next_update_minute <= ?
        ORDER BY next_update_minute ASC, created_minute ASC, event_id ASC
        LIMIT ?
        """,
        (instance_id, confirmed_minute, max_due_events),
    ).fetchall()
    due_events = [_row_to_event_dict(r, consequences_map) for r in due_rows]

    active_incident_count = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM world_events
            WHERE instance_id = ? AND kind = 'incident' AND status IN ('active', 'stalled')
            """,
            (instance_id,),
        ).fetchone()[0]
    )

    if not due_events:
        earliest_row = conn.execute(
            """
            SELECT MIN(next_update_minute)
            FROM world_events
            WHERE instance_id = ? AND status = 'active' AND next_update_minute IS NOT NULL
            """,
            (instance_id,),
        ).fetchone()
        earliest_next = int(earliest_row[0]) if (earliest_row and earliest_row[0] is not None) else None
        if earliest_next is not None and earliest_next > confirmed_minute:
            reason_code = "DUE_TIME_EXCEEDS_CONFIRMED_MINUTE"
            reason_msg = (
                f"最早活跃事件到期时间 ({earliest_next}) 超过当前已确认世界分钟 ({confirmed_minute})，本次跳过推进。"
            )
        else:
            reason_code = "NO_DUE_EVENTS"
            reason_msg = f"当前已确认世界分钟 ({confirmed_minute}) 下没有待处理的到期活跃事件，本次跳过推进。"
        return {
            **base_envelope,
            "status": "skipped",
            "skip": True,
            "blocked": False,
            "reason_code": reason_code,
            "reason_message": reason_msg,
            "reason": reason_msg,
            "earliest_next_update_minute": earliest_next,
            "due_events": [],
            "causal_neighborhood": [],
            "canon_sources": [],
            "allowed_source_ids": [],
            "allowed_event_ids": [],
            "active_incident_count": active_incident_count,
            "max_active_incidents": max_active_incidents,
            "registered_precondition_ids": [],
            "other_instance_event_ids": other_instance_event_ids,
        }

    causal_neighborhood = _select_minimal_causal_neighborhood(
        conn,
        instance_id,
        due_events,
        consequences_map,
        max_neighbors=max_causal_neighbors,
    )

    pre_rows = conn.execute(
        """
        SELECT precondition_id, source_ids_json
        FROM world_event_preconditions
        WHERE instance_id = ? AND status = 'satisfied'
        ORDER BY registered_minute ASC, precondition_id ASC
        """,
        (instance_id,),
    ).fetchall()
    registered_precondition_ids = [str(r[0]) for r in pre_rows]

    # 收集到期事件、最小因果邻域和已满足前置条件关联的优先 source_ids
    preferred_sources: list[str] = []
    seen_pref: set[str] = set()
    for ev in (*due_events, *causal_neighborhood):
        for sid in ev.get("source_ids", ()):
            if isinstance(sid, str) and sid not in seen_pref:
                seen_pref.add(sid)
                preferred_sources.append(sid)
    for r in pre_rows:
        for sid in json.loads(r[1] or "[]"):
            if isinstance(sid, str) and sid not in seen_pref:
                seen_pref.add(sid)
                preferred_sources.append(sid)

    selection = select_canon_sources(
        conn,
        world_id,
        max_entries=max_advance_entries,
        max_chunks=max_advance_chunks,
        max_entry_chars=max_advance_entry_chars,
        max_chunk_chars=max_advance_chunk_chars,
        char_budget=advance_char_budget,
        preferred_source_ids=tuple(preferred_sources),
        fallback_sources=fallback_canon if isinstance(fallback_canon, (list, tuple)) else None,
    )

    allowed_source_set: set[str] = (
        set(selection["selected_source_ids"])
        | set(selection["all_valid_source_ids"])
        | set(preferred_sources)
    )
    if isinstance(explicit_allowed_ids, (list, tuple)):
        for raw_sid in explicit_allowed_ids:
            if isinstance(raw_sid, str) and (raw_sid.startswith("chunk:") or raw_sid.startswith("entry:")):
                allowed_source_set.add(raw_sid.strip())

    allowed_event_ids = sorted(
        {ev["event_id"] for ev in due_events} | {ev["event_id"] for ev in causal_neighborhood}
    )

    return {
        **base_envelope,
        "status": "ready",
        "skip": False,
        "blocked": False,
        "reason_code": None,
        "reason_message": None,
        "due_events": due_events,
        "causal_neighborhood": causal_neighborhood,
        "canon_sources": selection["canon_sources"],
        "allowed_source_ids": sorted(allowed_source_set),
        "selected_source_ids": list(selection["selected_source_ids"]),
        "allowed_event_ids": allowed_event_ids,
        "active_incident_count": active_incident_count,
        "max_active_incidents": max_active_incidents,
        "registered_precondition_ids": registered_precondition_ids,
        "budget": selection["budget"],
        "other_instance_event_ids": other_instance_event_ids,
    }


def _load_validation_context_from_db(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_minute: int | None = None,
    mode: ProposalMode = "advance",
    allowed_source_ids: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
    min_initial_threads: int = DEFAULT_MIN_INITIAL_THREADS,
    max_initial_threads: int = DEFAULT_MAX_INITIAL_THREADS,
    max_active_incidents: int = DEFAULT_MAX_ACTIVE_INCIDENTS,
    expected_world_revision: int | None = None,
    strict_mode_match: bool = False,
) -> ProposalValidationContext:
    inst = _get_instance_row(conn, instance_id)
    effective_minute = inst["world_minute"] if world_minute is None else _require_strict_int(
        world_minute, "world_minute", min_value=0
    )

    consequences_map = _load_consequences_map(conn, instance_id)
    rows = conn.execute(
        """
        SELECT instance_id, event_id, kind, title, description, location, scope,
               participants_json, stage, status, created_minute, updated_minute,
               next_update_minute, cause_event_ids_json, source_ids_json,
               turnpoint_type, precondition_ids_json, revision, public_label
        FROM world_events
        WHERE instance_id = ?
        """,
        (instance_id,),
    ).fetchall()
    existing_events = {
        row[1]: _row_to_event_dict(row, consequences_map) for row in rows
    }

    # 查找其他实例的事件 ID，用于精确识别跨实例引用并报 CROSS_INSTANCE_REFERENCE
    other_rows = conn.execute(
        """
        SELECT event_id, instance_id
        FROM world_events
        WHERE instance_id != ?
        """,
        (instance_id,),
    ).fetchall()
    other_instance_event_ids: dict[str, str] = {}
    for o_eid, o_inst in other_rows:
        if o_eid not in existing_events:
            other_instance_event_ids[o_eid] = o_inst

    pre_rows = conn.execute(
        """
        SELECT precondition_id
        FROM world_event_preconditions
        WHERE instance_id = ? AND status = 'satisfied'
        """,
        (instance_id,),
    ).fetchall()
    registered_preconditions = frozenset(r[0] for r in pre_rows)

    if allowed_source_ids is not None:
        effective_sources = frozenset(str(s).strip() for s in allowed_source_ids if str(s).strip())
    else:
        effective_sources = _discover_world_canon_sources(conn, inst["world_id"], confirmed_entries_only=True)
        # 同时将实例内既有事件已验证的 source_ids 纳入允许集
        for ev in existing_events.values():
            effective_sources = effective_sources | frozenset(ev.get("source_ids", ()))

    active_inc_count = sum(
        1
        for ev in existing_events.values()
        if ev.get("kind") == "incident" and ev.get("status") not in TERMINAL_EVENT_STATUSES
    )

    return ProposalValidationContext(
        instance_id=instance_id,
        world_minute=effective_minute,
        mode=mode,
        allowed_source_ids=effective_sources,
        existing_events=existing_events,
        other_instance_event_ids=other_instance_event_ids,
        registered_precondition_ids=registered_preconditions,
        min_initial_threads=min_initial_threads,
        max_initial_threads=max_initial_threads,
        max_active_incidents=max_active_incidents,
        base_world_revision=expected_world_revision,
        active_incident_count=active_inc_count,
        strict_mode_match=strict_mode_match,
        allowed_event_ids=frozenset(existing_events.keys()),
    )


def _proposal_to_raw_dict(proposal: ValidatedEventProposal) -> dict[str, Any]:
    ops: list[dict[str, Any]] = []
    for op in proposal.operations:
        item: dict[str, Any] = {"op": op.op}
        if op.op == "update":
            item["event_id"] = op.event_id
            item["expected_event_revision"] = op.expected_event_revision
        if op.kind is not None:
            item["kind"] = op.kind
        if op.title is not None:
            item["title"] = op.title
        if op.public_label is not None:
            item["public_label"] = op.public_label
        if op.description is not None:
            item["description"] = op.description
        if op.location is not None:
            item["location"] = op.location
        if op.scope is not None:
            item["scope"] = op.scope
        if op.participants is not None:
            item["participants"] = list(op.participants)
        if op.stage is not None:
            item["stage"] = op.stage
        if op.status is not None:
            item["status"] = op.status
        if op.next_update_minute_provided:
            item["next_update_minute"] = op.next_update_minute
        if op.cause_event_ids is not None:
            item["cause_event_ids"] = list(op.cause_event_ids)
        if op.source_ids is not None:
            item["source_ids"] = list(op.source_ids)
        if op.consequences:
            item["consequences"] = [c.to_dict() for c in op.consequences]
        if op.turnpoint_type is not None:
            item["turnpoint_type"] = op.turnpoint_type
        if op.precondition_ids is not None:
            item["precondition_ids"] = list(op.precondition_ids)
        if op.reason:
            item["reason"] = op.reason
        ops.append(item)
    result: dict[str, Any] = {
        "instance_id": proposal.instance_id,
        "mode": proposal.mode,
        "kind": proposal.mode,
        "world_minute": proposal.world_minute,
        "operations": ops,
    }
    if proposal.base_world_revision is not None:
        result["base_world_revision"] = proposal.base_world_revision
    if proposal.summary:
        result["summary"] = proposal.summary
    if proposal.transmissions:
        result["transmissions"] = [dict(t) for t in proposal.transmissions]
    return result


def apply_proposal(
    conn: sqlite3.Connection,
    instance_id: str,
    proposal: ValidatedEventProposal | Mapping[str, Any],
    job: Mapping[str, Any] | None = None,
    *,
    world_minute: int | None = None,
    expected_world_revision: int | None = None,
    allowed_source_ids: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
    max_active_incidents: int = DEFAULT_MAX_ACTIVE_INCIDENTS,
    id_factory: Callable[[], str] | None = None,
    consequence_id_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """在 `SAVEPOINT` 保护下将事件提议原子应用到指定世界实例（兼容直接调用与 `PulseWorker` 4 参数回调）。

    - 模块不自行调用 `conn.commit()`，保留调用方外层事务边界。
    - 应用时实时复核数据库内当前实例版本（`world_instances.revision`）、已确认世界时间、
      事件版本（`expected_event_revision`）、跨实例引用、因果环与临时事件上限；
      任意单个操作失败均回滚整个 `SAVEPOINT`，绝不产生半笔状态。
    """
    ensure_events_schema(conn)
    instance_id = _require_non_empty_str(instance_id, "instance_id", 120)
    event_id_gen = id_factory or _default_event_id
    csq_id_gen = consequence_id_factory or _default_consequence_id

    savepoint_name = f"sp_m3_events_apply_{uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        inst_row = _get_instance_row(conn, instance_id)
        db_revision = int(inst_row["revision"])
        db_world_minute = int(inst_row["world_minute"])

        job_map: Mapping[str, Any] = job if isinstance(job, Mapping) else {}
        if job_map.get("instance_id") is not None:
            j_inst = _require_non_empty_str(job_map.get("instance_id"), "job.instance_id", 120)
            if j_inst != instance_id:
                raise CrossInstanceReferenceError(instance_id, j_inst, j_inst)

        job_mode, job_has_mode = _extract_mode_from_mapping(job_map, "job")
        job_minute = _extract_minute_from_mapping(job_map, "job")
        job_rev = _extract_revision_from_mapping(job_map, "job", instance_id)

        # 核对期望的世界实例版本（expected_world_revision / job.base_world_revision / proposal.base_world_revision）
        target_world_rev = expected_world_revision if expected_world_revision is not None else job_rev
        if isinstance(proposal, ValidatedEventProposal):
            if proposal.instance_id != instance_id:
                raise CrossInstanceReferenceError(
                    instance_id, proposal.instance_id, proposal.instance_id
                )
            if proposal.base_world_revision is not None:
                if target_world_rev is not None and proposal.base_world_revision != target_world_rev:
                    raise WorldRevisionConflictError(
                        instance_id, target_world_rev, proposal.base_world_revision
                    )
                target_world_rev = proposal.base_world_revision

        if target_world_rev is not None and db_revision != target_world_rev:
            raise WorldRevisionConflictError(instance_id, target_world_rev, db_revision)

        if isinstance(proposal, ValidatedEventProposal):
            if job_has_mode and job_mode != proposal.mode:
                raise EventValidationError(
                    "MODE_KIND_MISMATCH",
                    f"提议模式 ({proposal.mode!r}) 与队列任务类型 ({job_mode!r}) 不一致。",
                    {"proposal_mode": proposal.mode, "job_kind": job_mode},
                )
            if job_minute is not None and world_minute is None and proposal.world_minute != job_minute:
                raise EventValidationError(
                    "TIMESTAMP_OVERRIDE_FORBIDDEN",
                    f"提议时间 ({proposal.world_minute}) 与队列任务时间 ({job_minute}) 不一致。",
                    {"proposal_minute": proposal.world_minute, "job_minute": job_minute},
                )
            if job is not None and proposal.world_minute > db_world_minute and world_minute is None:
                raise EventValidationError(
                    "TIMESTAMP_OVERRIDE_FORBIDDEN",
                    f"提议时间 ({proposal.world_minute}) 超过实例已确认时间 ({db_world_minute})。",
                    {"proposal_minute": proposal.world_minute, "confirmed_minute": db_world_minute},
                )
            effective_minute = proposal.world_minute if world_minute is None else world_minute
            raw_payload = _proposal_to_raw_dict(proposal)
            if world_minute is not None:
                raw_payload["world_minute"] = world_minute
            inferred_mode = proposal.mode
            strict_mode = True
            if allowed_source_ids is None:
                db_sources = _discover_world_canon_sources(conn, inst_row["world_id"], confirmed_entries_only=True)
                prop_sources = frozenset(
                    sid for op in proposal.operations for sid in (op.source_ids or ())
                )
                effective_allowed_sources: frozenset[str] | None = db_sources | prop_sources
            else:
                effective_allowed_sources = frozenset(allowed_source_ids)
        elif isinstance(proposal, Mapping):
            raw_payload = dict(proposal)
            prop_mode, prop_has_mode = _extract_mode_from_mapping(raw_payload, "proposal")
            if job_has_mode and prop_has_mode and job_mode != prop_mode:
                raise EventValidationError(
                    "MODE_KIND_MISMATCH",
                    f"提议模式 ({prop_mode!r}) 与队列任务类型 ({job_mode!r}) 不一致。",
                    {"proposal_mode": prop_mode, "job_kind": job_mode},
                )
            inferred_mode = (job_mode or prop_mode or "advance")  # type: ignore[assignment]
            strict_mode = bool(job_has_mode or prop_has_mode)

            if world_minute is not None:
                effective_minute = world_minute
            elif job_minute is not None:
                if job_minute > db_world_minute:
                    raise EventValidationError(
                        "TIMESTAMP_OVERRIDE_FORBIDDEN",
                        f"任务目标时间 ({job_minute}) 超过数据库已确认分钟 ({db_world_minute})。",
                        {"job_minute": job_minute, "confirmed_minute": db_world_minute},
                    )
                effective_minute = job_minute
            else:
                effective_minute = db_world_minute

            payload_Strategy_sources = job_map.get("payload", {}).get("allowed_source_ids") if isinstance(job_map.get("payload"), Mapping) else None
            if allowed_source_ids is not None:
                effective_allowed_sources = frozenset(allowed_source_ids)
            elif isinstance(payload_Strategy_sources, (list, tuple, set, frozenset)):
                effective_allowed_sources = frozenset(str(x).strip() for x in payload_Strategy_sources if str(x).strip())
            else:
                effective_allowed_sources = None
        else:
            raise EventValidationError("PROPOSAL_INVALID", "proposal 必须为 ValidatedEventProposal 或字典。")

        db_context = _load_validation_context_from_db(
            conn,
            instance_id,
            world_minute=effective_minute,
            mode=inferred_mode,
            allowed_source_ids=effective_allowed_sources,
            max_active_incidents=max_active_incidents,
            expected_world_revision=target_world_rev,
            strict_mode_match=strict_mode,
        )
        validated = validate_proposal(raw_payload, db_context)
        commit_minute = validated.world_minute

        applied_events: list[dict[str, Any]] = []
        applied_consequences: list[dict[str, Any]] = []
        applied_revisions: list[dict[str, Any]] = []
        created_event_ids: list[str] = []
        updated_event_ids: list[str] = []

        for op in validated.operations:
            if op.op == "create":
                new_event_id = _require_non_empty_str(event_id_gen(), "generated_event_id", 120)
                exists = conn.execute(
                    "SELECT 1 FROM world_events WHERE instance_id = ? AND event_id = ?",
                    (instance_id, new_event_id),
                ).fetchone()
                if exists is not None:
                    raise EventValidationError(
                        "EVENT_ID_COLLISION",
                        f"生成的事件 ID 在实例内发生冲突: {new_event_id}。",
                        {"event_id": new_event_id},
                    )

                assert op.kind is not None
                assert op.title is not None
                assert op.description is not None
                assert op.location is not None
                assert op.scope is not None
                assert op.stage is not None
                assert op.status is not None

                participants_list = list(op.participants or ())
                causes_list = list(op.cause_event_ids or ())
                sources_list = list(op.source_ids or ())
                preconditions_list = list(op.precondition_ids or ())
                revision = 1

                conn.execute(
                    """
                    INSERT INTO world_events (
                        instance_id, event_id, kind, title, description, location, scope,
                        participants_json, stage, status, created_minute, updated_minute,
                        next_update_minute, cause_event_ids_json, source_ids_json,
                        turnpoint_type, precondition_ids_json, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_id,
                        new_event_id,
                        op.kind,
                        op.title,
                        op.description,
                        op.location,
                        op.scope,
                        json.dumps(participants_list, ensure_ascii=False),
                        op.stage,
                        op.status,
                        commit_minute,
                        commit_minute,
                        op.next_update_minute,
                        json.dumps(causes_list, ensure_ascii=False),
                        json.dumps(sources_list, ensure_ascii=False),
                        op.turnpoint_type,
                        json.dumps(preconditions_list, ensure_ascii=False),
                        revision,
                    ),
                )

                op_consequences: list[dict[str, Any]] = []
                for csq in op.consequences:
                    cid = _require_non_empty_str(csq_id_gen(), "generated_consequence_id", 120)
                    conn.execute(
                        """
                        INSERT INTO world_event_consequences (
                            instance_id, consequence_id, source_event_id, source_event_revision,
                            impact_type, domain, target, summary, effective_minute
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            instance_id,
                            cid,
                            new_event_id,
                            revision,
                            csq.impact_type,
                            csq.domain,
                            csq.target,
                            csq.summary,
                            commit_minute,
                        ),
                    )
                    csq_record = {
                        "instance_id": instance_id,
                        "consequence_id": cid,
                        "source_event_id": new_event_id,
                        "source_event_revision": revision,
                        "impact_type": csq.impact_type,
                        "domain": csq.domain,
                        "target": csq.target,
                        "summary": csq.summary,
                        "effective_minute": commit_minute,
                    }
                    op_consequences.append(csq_record)
                    applied_consequences.append(csq_record)

                event_record: dict[str, Any] = {
                    "instance_id": instance_id,
                    "event_id": new_event_id,
                    "kind": op.kind,
                    "title": op.title,
                    "description": op.description,
                    "location": op.location,
                    "scope": op.scope,
                    "participants": participants_list,
                    "stage": op.stage,
                    "status": op.status,
                    "created_minute": commit_minute,
                    "updated_minute": commit_minute,
                    "next_update_minute": op.next_update_minute,
                    "cause_event_ids": causes_list,
                    "source_ids": sources_list,
                    "turnpoint_type": op.turnpoint_type,
                    "precondition_ids": preconditions_list,
                    "consequences": op_consequences,
                    "revision": revision,
                }
                if op.public_label:
                    conn.execute("UPDATE world_events SET public_label = ? WHERE instance_id = ? AND event_id = ?",
                                 (op.public_label, instance_id, event_record["event_id"]))
                    event_record["public_label"] = op.public_label
                conn.execute(
                    """
                    INSERT INTO world_event_revisions (
                        instance_id, event_id, revision, operation, world_minute, reason, snapshot_json
                    ) VALUES (?, ?, ?, 'create', ?, ?, ?)
                    """,
                    (
                        instance_id,
                        new_event_id,
                        revision,
                        commit_minute,
                        op.reason,
                        json.dumps(event_record, ensure_ascii=False),
                    ),
                )
                applied_events.append(event_record)
                created_event_ids.append(new_event_id)
                applied_revisions.append(
                    {
                        "instance_id": instance_id,
                        "event_id": new_event_id,
                        "revision": revision,
                        "operation": "create",
                        "world_minute": commit_minute,
                        "reason": op.reason,
                        "snapshot": copy.deepcopy(event_record),
                    }
                )

            else:  # op.op == "update"
                assert op.event_id is not None
                assert op.expected_event_revision is not None
                target_id = op.event_id
                existing_ev = db_context.existing_events[target_id]
                new_revision = op.expected_event_revision + 1

                participants_list = list(op.participants or ())
                causes_list = list(op.cause_event_ids or ())
                sources_list = list(op.source_ids or ())
                preconditions_list = list(op.precondition_ids or ())

                cur = conn.execute(
                    """
                    UPDATE world_events
                    SET title = ?,
                        description = ?,
                        location = ?,
                        scope = ?,
                        participants_json = ?,
                        stage = ?,
                        status = ?,
                        updated_minute = ?,
                        next_update_minute = ?,
                        cause_event_ids_json = ?,
                        source_ids_json = ?,
                        turnpoint_type = ?,
                        precondition_ids_json = ?,
                        revision = ?
                    WHERE instance_id = ? AND event_id = ? AND revision = ?
                    """,
                    (
                        op.title,
                        op.description,
                        op.location,
                        op.scope,
                        json.dumps(participants_list, ensure_ascii=False),
                        op.stage,
                        op.status,
                        commit_minute,
                        op.next_update_minute,
                        json.dumps(causes_list, ensure_ascii=False),
                        json.dumps(sources_list, ensure_ascii=False),
                        op.turnpoint_type,
                        json.dumps(preconditions_list, ensure_ascii=False),
                        new_revision,
                        instance_id,
                        target_id,
                        op.expected_event_revision,
                    ),
                )
                if cur.rowcount != 1:
                    row = conn.execute(
                        "SELECT revision FROM world_events WHERE instance_id = ? AND event_id = ?",
                        (instance_id, target_id),
                    ).fetchone()
                    actual_rev = int(row[0]) if row else -1
                    raise EventRevisionConflictError(
                        instance_id, target_id, op.expected_event_revision, actual_rev
                    )

                op_consequences = list(existing_ev.get("consequences", ()))
                for csq in op.consequences:
                    cid = _require_non_empty_str(csq_id_gen(), "generated_consequence_id", 120)
                    conn.execute(
                        """
                        INSERT INTO world_event_consequences (
                            instance_id, consequence_id, source_event_id, source_event_revision,
                            impact_type, domain, target, summary, effective_minute
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            instance_id,
                            cid,
                            target_id,
                            new_revision,
                            csq.impact_type,
                            csq.domain,
                            csq.target,
                            csq.summary,
                            commit_minute,
                        ),
                    )
                    csq_record = {
                        "instance_id": instance_id,
                        "consequence_id": cid,
                        "source_event_id": target_id,
                        "source_event_revision": new_revision,
                        "impact_type": csq.impact_type,
                        "domain": csq.domain,
                        "target": csq.target,
                        "summary": csq.summary,
                        "effective_minute": commit_minute,
                    }
                    op_consequences.append(csq_record)
                    applied_consequences.append(csq_record)

                event_record = {
                    "instance_id": instance_id,
                    "event_id": target_id,
                    "kind": existing_ev["kind"],
                    "title": op.title,
                    "description": op.description,
                    "location": op.location,
                    "scope": op.scope,
                    "participants": participants_list,
                    "stage": op.stage,
                    "status": op.status,
                    "created_minute": int(existing_ev["created_minute"]),
                    "updated_minute": commit_minute,
                    "next_update_minute": op.next_update_minute,
                    "cause_event_ids": causes_list,
                    "source_ids": sources_list,
                    "turnpoint_type": op.turnpoint_type,
                    "precondition_ids": preconditions_list,
                    "consequences": op_consequences,
                    "revision": new_revision,
                }
                if op.public_label:
                    conn.execute("UPDATE world_events SET public_label = ? WHERE instance_id = ? AND event_id = ?",
                                 (op.public_label, instance_id, event_record["event_id"]))
                    event_record["public_label"] = op.public_label
                conn.execute(
                    """
                    INSERT INTO world_event_revisions (
                        instance_id, event_id, revision, operation, world_minute, reason, snapshot_json
                    ) VALUES (?, ?, ?, 'update', ?, ?, ?)
                    """,
                    (
                        instance_id,
                        target_id,
                        new_revision,
                        commit_minute,
                        op.reason,
                        json.dumps(event_record, ensure_ascii=False),
                    ),
                )
                applied_events.append(event_record)
                updated_event_ids.append(target_id)
                applied_revisions.append(
                    {
                        "instance_id": instance_id,
                        "event_id": target_id,
                        "revision": new_revision,
                        "operation": "update",
                        "world_minute": commit_minute,
                        "reason": op.reason,
                        "snapshot": copy.deepcopy(event_record),
                    }
                )

        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return {
            "instance_id": instance_id,
            "mode": validated.mode,
            "kind": validated.mode,
            "world_minute": commit_minute,
            "target_world_minute": commit_minute,
            "world_revision": db_revision,
            "base_world_revision": db_revision,
            "applied_operations": len(applied_events),
            "created_event_ids": created_event_ids,
            "updated_event_ids": updated_event_ids,
            "events": applied_events,
            "consequences": applied_consequences,
            "revisions": applied_revisions,
        }
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise


def get_event(conn: sqlite3.Connection, instance_id: str, event_id: str) -> dict[str, Any]:
    """读取指定实例下的单条权威事件记录（含已生效后果）。"""
    _get_instance_row(conn, instance_id)
    event_id = _require_non_empty_str(event_id, "event_id", 120)
    row = conn.execute(
        """
        SELECT instance_id, event_id, kind, title, description, location, scope,
               participants_json, stage, status, created_minute, updated_minute,
               next_update_minute, cause_event_ids_json, source_ids_json,
               turnpoint_type, precondition_ids_json, revision, public_label
        FROM world_events
        WHERE instance_id = ? AND event_id = ?
        """,
        (instance_id, event_id),
    ).fetchone()
    if row is None:
        other = conn.execute(
            "SELECT instance_id FROM world_events WHERE event_id = ? AND instance_id != ?",
            (event_id, instance_id),
        ).fetchone()
        if other is not None:
            raise CrossInstanceReferenceError(instance_id, event_id, other[0])
        raise EventNotFoundError(instance_id, event_id)
    consequences_map = _load_consequences_map(conn, instance_id)
    return _row_to_event_dict(row, consequences_map)


def list_events(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """按实例分页列出事件记录。"""
    _get_instance_row(conn, instance_id)
    limit = _require_strict_int(limit, "limit", min_value=1)
    if limit > 200:
        raise EventValidationError("VALIDATION_ERROR", "limit 不能超过 200。", {"field": "limit"})
    offset = _require_strict_int(offset, "offset", min_value=0)

    clauses = ["instance_id = ?"]
    params: list[Any] = [instance_id]
    if kind is not None:
        if kind not in EVENT_KINDS:
            raise EventValidationError("VALIDATION_ERROR", f"无效的 kind: {kind}。")
        clauses.append("kind = ?")
        params.append(kind)
    if status is not None:
        if status not in EVENT_STATUSES:
            raise EventValidationError("VALIDATION_ERROR", f"无效的 status: {status}。")
        clauses.append("status = ?")
        params.append(status)

    where_sql = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) FROM world_events WHERE {where_sql}",
        tuple(params),
    ).fetchone()[0]

    rows = conn.execute(
        f"""
        SELECT instance_id, event_id, kind, title, description, location, scope,
               participants_json, stage, status, created_minute, updated_minute,
               next_update_minute, cause_event_ids_json, source_ids_json,
               turnpoint_type, precondition_ids_json, revision, public_label
        FROM world_events
        WHERE {where_sql}
        ORDER BY created_minute ASC, event_id ASC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    consequences_map = _load_consequences_map(conn, instance_id)
    items = [_row_to_event_dict(r, consequences_map) for r in rows]
    return {
        "instance_id": instance_id,
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < int(total),
    }


def list_due_events(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_minute: int | None = None,
    kind: str | None = None,
    include_stalled: bool = True,
    limit: int = 20,
    offset: int = 0,
    cursor: str | None = None,
) -> dict[str, Any]:
    """到期事件分页读取（支持 `offset` 与不透明游标 `cursor` 稳定分页）。

    只读取属于 `instance_id`、处于非终态（`active` 或可选 `stalled`）且
    `next_update_minute <= world_minute` 的到期事件。
    """
    inst = _get_instance_row(conn, instance_id)
    target_minute = (
        inst["world_minute"]
        if world_minute is None
        else _require_strict_int(world_minute, "world_minute", min_value=0)
    )
    limit = _require_strict_int(limit, "limit", min_value=1)
    if limit > 100:
        raise EventValidationError("VALIDATION_ERROR", "limit 不能超过 100。", {"field": "limit"})
    offset = _require_strict_int(offset, "offset", min_value=0)

    statuses = ("active", "stalled") if include_stalled else ("active",)
    placeholders = ",".join("?" for _ in statuses)
    clauses = [
        "instance_id = ?",
        f"status IN ({placeholders})",
        "next_update_minute IS NOT NULL",
        "next_update_minute <= ?",
    ]
    params: list[Any] = [instance_id, *statuses, target_minute]

    if kind is not None:
        if kind not in EVENT_KINDS:
            raise EventValidationError("VALIDATION_ERROR", f"无效的 kind: {kind}。")
        clauses.append("kind = ?")
        params.append(kind)

    total_where = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) FROM world_events WHERE {total_where}",
        tuple(params),
    ).fetchone()[0]

    if cursor is not None:
        try:
            parts = cursor.split(":", 2)
            cur_next = int(parts[0])
            cur_created = int(parts[1])
            cur_eid = parts[2]
            if not cur_eid:
                raise ValueError("empty event_id")
        except (ValueError, IndexError) as exc:
            raise EventValidationError(
                "INVALID_CURSOR",
                "到期事件分页游标格式无效。",
                {"cursor": cursor},
            ) from exc
        clauses.append(
            """(
                next_update_minute > ?
                OR (next_update_minute = ? AND created_minute > ?)
                OR (next_update_minute = ? AND created_minute = ? AND event_id > ?)
            )"""
        )
        params.extend([cur_next, cur_next, cur_created, cur_next, cur_created, cur_eid])
        effective_offset = 0
    else:
        effective_offset = offset

    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT instance_id, event_id, kind, title, description, location, scope,
               participants_json, stage, status, created_minute, updated_minute,
               next_update_minute, cause_event_ids_json, source_ids_json,
               turnpoint_type, precondition_ids_json, revision, public_label
        FROM world_events
        WHERE {where_sql}
        ORDER BY next_update_minute ASC, created_minute ASC, event_id ASC
        LIMIT ? OFFSET ?
        """,
        (*params, limit + 1, effective_offset),
    ).fetchall()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    consequences_map = _load_consequences_map(conn, instance_id)
    items = [_row_to_event_dict(r, consequences_map) for r in page_rows]

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last['next_update_minute']}:{last['created_minute']}:{last['event_id']}"

    return {
        "instance_id": instance_id,
        "world_minute": target_minute,
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": effective_offset,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def list_event_revisions(
    conn: sqlite3.Connection, instance_id: str, event_id: str
) -> list[dict[str, Any]]:
    """读取指定事件的完整追加式修订历史（按 `revision ASC` 排序）。"""
    get_event(conn, instance_id, event_id)
    rows = conn.execute(
        """
        SELECT instance_id, event_id, revision, operation, world_minute, reason, snapshot_json
        FROM world_event_revisions
        WHERE instance_id = ? AND event_id = ?
        ORDER BY revision ASC
        """,
        (instance_id, event_id),
    ).fetchall()
    return [
        {
            "instance_id": r[0],
            "event_id": r[1],
            "revision": int(r[2]),
            "operation": r[3],
            "world_minute": int(r[4]),
            "reason": r[5],
            "snapshot": json.loads(r[6]),
        }
        for r in rows
    ]


def list_consequences(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    source_event_id: str | None = None,
    domain: str | None = None,
    target: str | None = None,
) -> list[dict[str, Any]]:
    """查询指定世界实例下的可追溯事件后果记录。"""
    _get_instance_row(conn, instance_id)
    clauses = ["instance_id = ?"]
    params: list[Any] = [instance_id]
    if source_event_id is not None:
        clauses.append("source_event_id = ?")
        params.append(_require_non_empty_str(source_event_id, "source_event_id", 120))
    if domain is not None:
        clauses.append("domain = ?")
        params.append(_require_non_empty_str(domain, "domain", 60))
    if target is not None:
        clauses.append("target = ?")
        params.append(_require_non_empty_str(target, "target", 120))

    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT instance_id, consequence_id, source_event_id, source_event_revision,
               impact_type, domain, target, summary, effective_minute
        FROM world_event_consequences
        WHERE {where_sql}
        ORDER BY effective_minute ASC, consequence_id ASC
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "instance_id": r[0],
            "consequence_id": r[1],
            "source_event_id": r[2],
            "source_event_revision": int(r[3]),
            "impact_type": r[4],
            "domain": r[5],
            "target": r[6],
            "summary": r[7],
            "effective_minute": int(r[8]),
        }
        for r in rows
    ]


def trace_causality(
    conn: sqlite3.Connection, instance_id: str, event_id: str
) -> dict[str, Any]:
    """追溯指定事件的完整上游起因链（ancestors）、下游衍生事件（descendants）与全部关联后果。"""
    target_event = get_event(conn, instance_id, event_id)
    all_events = {
        ev["event_id"]: ev
        for ev in list_events(conn, instance_id, limit=200)["items"]
    }

    # 向上追溯祖先事件（按时间从早到晚排序）
    ancestors_ids: list[str] = []
    visited_up: set[str] = {event_id}
    queue_up = list(target_event["cause_event_ids"])
    while queue_up:
        curr_id = queue_up.pop(0)
        if curr_id in visited_up or curr_id not in all_events:
            continue
        visited_up.add(curr_id)
        ancestors_ids.append(curr_id)
        for parent_id in all_events[curr_id]["cause_event_ids"]:
            if parent_id not in visited_up:
                queue_up.append(parent_id)

    ancestors = sorted(
        (all_events[eid] for eid in ancestors_ids),
        key=lambda e: (e["created_minute"], e["event_id"]),
    )

    # 向下追溯衍生事件
    children_map: dict[str, list[str]] = {}
    for eid, ev in all_events.items():
        for cid in ev["cause_event_ids"]:
            children_map.setdefault(cid, []).append(eid)

    descendant_ids: list[str] = []
    visited_down: set[str] = {event_id}
    queue_down = list(children_map.get(event_id, ()))
    while queue_down:
        curr_id = queue_down.pop(0)
        if curr_id in visited_down or curr_id not in all_events:
            continue
        visited_down.add(curr_id)
        descendant_ids.append(curr_id)
        for child_id in children_map.get(curr_id, ()):
            if child_id not in visited_down:
                queue_down.append(child_id)

    descendants = sorted(
        (all_events[eid] for eid in descendant_ids),
        key=lambda e: (e["created_minute"], e["event_id"]),
    )

    chain_ids = [e["event_id"] for e in ancestors] + [event_id] + [e["event_id"] for e in descendants]
    chain_consequences: list[dict[str, Any]] = []
    for cid in chain_ids:
        chain_consequences.extend(all_events[cid].get("consequences", ()))

    return {
        "instance_id": instance_id,
        "event": target_event,
        "ancestors": ancestors,
        "descendants": descendants,
        "chain_consequences": chain_consequences,
    }


def register_precondition(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    kind: str,
    title: str,
    participants: list[str] | tuple[str, ...] = (),
    source_ids: list[str] | tuple[str, ...] = (),
    cause_event_ids: list[str] | tuple[str, ...] = (),
    registered_minute: int | None = None,
    precondition_id: str | None = None,
    status: str = "satisfied",
) -> dict[str, Any]:
    """为指定实例登记重大历史转折的前置条件。"""
    inst = _get_instance_row(conn, instance_id)
    pid = (
        _require_non_empty_str(precondition_id, "precondition_id", 120)
        if precondition_id is not None
        else _default_precondition_id()
    )
    kind = _require_non_empty_str(kind, "kind", 60)
    title = _require_non_empty_str(title, "title", 120)
    if status not in ("satisfied", "revoked"):
        raise EventValidationError("VALIDATION_ERROR", "precondition status 仅支持 'satisfied' 或 'revoked'。")
    effective_minute = (
        inst["world_minute"]
        if registered_minute is None
        else _require_strict_int(registered_minute, "registered_minute", min_value=0)
    )

    cleaned_participants = [
        _require_non_empty_str(p, "participants", 80) for p in participants
    ]
    cleaned_sources = [
        _require_non_empty_str(s, "source_ids", 160) for s in source_ids
    ]
    cleaned_causes = [
        _require_non_empty_str(c, "cause_event_ids", 120) for c in cause_event_ids
    ]
    for cid in cleaned_causes:
        get_event(conn, instance_id, cid)

    conn.execute(
        """
        INSERT OR REPLACE INTO world_event_preconditions (
            instance_id, precondition_id, kind, title,
            participants_json, source_ids_json, cause_event_ids_json,
            registered_minute, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instance_id,
            pid,
            kind,
            title,
            json.dumps(cleaned_participants, ensure_ascii=False),
            json.dumps(cleaned_sources, ensure_ascii=False),
            json.dumps(cleaned_causes, ensure_ascii=False),
            effective_minute,
            status,
        ),
    )
    return {
        "instance_id": instance_id,
        "precondition_id": pid,
        "kind": kind,
        "title": title,
        "participants": cleaned_participants,
        "source_ids": cleaned_sources,
        "cause_event_ids": cleaned_causes,
        "registered_minute": effective_minute,
        "status": status,
    }


def list_preconditions(conn: sqlite3.Connection, instance_id: str) -> list[dict[str, Any]]:
    """列出指定实例已登记的前置条件。"""
    _get_instance_row(conn, instance_id)
    rows = conn.execute(
        """
        SELECT instance_id, precondition_id, kind, title,
               participants_json, source_ids_json, cause_event_ids_json,
               registered_minute, status
        FROM world_event_preconditions
        WHERE instance_id = ?
        ORDER BY registered_minute ASC, precondition_id ASC
        """,
        (instance_id,),
    ).fetchall()
    return [
        {
            "instance_id": r[0],
            "precondition_id": r[1],
            "kind": r[2],
            "title": r[3],
            "participants": json.loads(r[4] or "[]"),
            "source_ids": json.loads(r[5] or "[]"),
            "cause_event_ids": json.loads(r[6] or "[]"),
            "registered_minute": int(r[7]),
            "status": r[8],
        }
        for r in rows
    ]


def snapshot(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    """生成指定世界实例的可移植、JSON 可序列化事件快照。

    符合 `M3_WAVE1_API.md` §5：
    - 包含内部格式版本 `format_version: "m3.events.v1"`；
    - 包含事件权威记录、版本修订历史、因果引用、来源引用、可追溯后果与前置条件；
    - 不含任何活跃任务、模型凭据或进程租约；
    - 返回深拷贝纯数据字典，后续在原实例上的推进不会污染已捕获的快照。
    """
    inst = _get_instance_row(conn, instance_id)
    consequences_map = _load_consequences_map(conn, instance_id)

    event_rows = conn.execute(
        """
        SELECT instance_id, event_id, kind, title, description, location, scope,
               participants_json, stage, status, created_minute, updated_minute,
               next_update_minute, cause_event_ids_json, source_ids_json,
               turnpoint_type, precondition_ids_json, revision, public_label
        FROM world_events
        WHERE instance_id = ?
        ORDER BY created_minute ASC, event_id ASC
        """,
        (instance_id,),
    ).fetchall()
    events = [_row_to_event_dict(r, consequences_map) for r in event_rows]

    rev_rows = conn.execute(
        """
        SELECT instance_id, event_id, revision, operation, world_minute, reason, snapshot_json
        FROM world_event_revisions
        WHERE instance_id = ?
        ORDER BY event_id ASC, revision ASC
        """,
        (instance_id,),
    ).fetchall()
    revisions = [
        {
            "instance_id": r[0],
            "event_id": r[1],
            "revision": int(r[2]),
            "operation": r[3],
            "world_minute": int(r[4]),
            "reason": r[5],
            "snapshot": json.loads(r[6]),
        }
        for r in rev_rows
    ]

    consequences = list_consequences(conn, instance_id)
    preconditions = list_preconditions(conn, instance_id)

    return copy.deepcopy(
        {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "instance_id": instance_id,
            "world_minute": inst["world_minute"],
            "world_revision": inst["revision"],
            "events": events,
            "revisions": revisions,
            "consequences": consequences,
            "preconditions": preconditions,
        }
    )


def restore(
    conn: sqlite3.Connection,
    target_instance_id: str,
    snapshot_data: Mapping[str, Any],
    *,
    source_id_map: Mapping[str, str] | None = None,
    allowed_source_ids: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """在单个写事务（`SAVEPOINT`）内将事件快照原子恢复到目标世界实例 `target_instance_id`。"""
    ensure_events_schema(conn)
    target_instance_id = _require_non_empty_str(target_instance_id, "target_instance_id", 120)

    if not isinstance(snapshot_data, Mapping):
        raise SnapshotRestoreError("SNAPSHOT_INVALID", "快照数据必须为 JSON 对象。")

    if snapshot_data.get("format_version") != SNAPSHOT_FORMAT_VERSION:
        raise SnapshotRestoreError(
            "SNAPSHOT_VERSION_UNSUPPORTED",
            f"不支持的事件快照格式版本: {snapshot_data.get('format_version')!r}。",
            {
                "expected_version": SNAPSHOT_FORMAT_VERSION,
                "actual_version": snapshot_data.get("format_version"),
            },
        )

    snap_minute = _require_strict_int(
        snapshot_data.get("world_minute", 0), "snapshot.world_minute", min_value=0
    )
    raw_events = snapshot_data.get("events")
    raw_revisions = snapshot_data.get("revisions")
    raw_consequences = snapshot_data.get("consequences")
    raw_preconditions = snapshot_data.get("preconditions", [])

    if (
        not isinstance(raw_events, list)
        or not isinstance(raw_revisions, list)
        or not isinstance(raw_consequences, list)
        or not isinstance(raw_preconditions, list)
    ):
        raise SnapshotRestoreError(
            "SNAPSHOT_INVALID",
            "快照中的 events、revisions、consequences、preconditions 必须为数组。",
        )

    savepoint_name = f"sp_m3_events_restore_{uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        # 确保目标实例根记录存在（若尚未创建则以快照 world_minute 初始化）
        target_row = conn.execute(
            "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE id = ?",
            (target_instance_id,),
        ).fetchone()
        if target_row is None:
            conn.execute(
                """
                INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision)
                VALUES (?, '', NULL, ?, 0)
                """,
                (target_instance_id, snap_minute),
            )
            target_world_id = ""
        else:
            target_world_id = target_row[1]

        # 若目标实例已有旧事件数据，先清理目标实例自身的事件子表（绝不影响其他实例）
        conn.execute("DELETE FROM world_event_consequences WHERE instance_id = ?", (target_instance_id,))
        conn.execute("DELETE FROM world_event_revisions WHERE instance_id = ?", (target_instance_id,))
        conn.execute("DELETE FROM world_events WHERE instance_id = ?", (target_instance_id,))
        conn.execute("DELETE FROM world_event_preconditions WHERE instance_id = ?", (target_instance_id,))

        if allowed_source_ids is not None:
            permitted_sources: frozenset[str] | None = frozenset(
                str(s).strip() for s in allowed_source_ids if str(s).strip()
            )
        else:
            discovered = _discover_world_canon_sources(conn, target_world_id, confirmed_entries_only=False)
            permitted_sources = discovered if discovered else None

        def remap_source_id(sid: str) -> str:
            sid = _require_non_empty_str(sid, "source_id", 160)
            if source_id_map and sid in source_id_map:
                sid = _require_non_empty_str(source_id_map[sid], "mapped_source_id", 160)
            if not (sid.startswith("chunk:") or sid.startswith("entry:")):
                raise SnapshotRestoreError(
                    "SNAPSHOT_SOURCE_INVALID",
                    f"快照包含格式非法的来源引用: {sid}。",
                    {"source_id": sid},
                )
            if permitted_sources is not None and sid not in permitted_sources:
                raise SnapshotRestoreError(
                    "SNAPSHOT_SOURCE_UNRESOLVED",
                    f"快照引用的外部来源在目标世界中不存在: {sid}。",
                    {"source_id": sid},
                )
            return sid

        # 1. 校验并恢复前置条件
        restored_precondition_ids: set[str] = set()
        for idx, pre in enumerate(raw_preconditions):
            if not isinstance(pre, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"preconditions[{idx}] 必须为对象。")
            pid = _require_non_empty_str(pre.get("precondition_id"), f"preconditions[{idx}].precondition_id", 120)
            if pid in restored_precondition_ids:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"重复的 precondition_id: {pid}。")
            restored_precondition_ids.add(pid)
            p_kind = _require_non_empty_str(pre.get("kind"), f"preconditions[{idx}].kind", 60)
            p_title = _require_non_empty_str(pre.get("title"), f"preconditions[{idx}].title", 120)
            p_status = pre.get("status", "satisfied")
            if p_status not in ("satisfied", "revoked"):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"非法的 precondition status: {p_status}。")
            p_min = _require_strict_int(pre.get("registered_minute", 0), f"preconditions[{idx}].registered_minute", min_value=0)
            if p_min > snap_minute:
                raise SnapshotRestoreError("SNAPSHOT_TIME_INVALID", "前置条件登记时间晚于快照时间。")
            p_parts = [_require_non_empty_str(x, "participant", 80) for x in pre.get("participants", [])]
            p_sources = [remap_source_id(x) for x in pre.get("source_ids", [])]
            p_causes = [_require_non_empty_str(x, "cause_event_ids", 120) for x in pre.get("cause_event_ids", [])]

            conn.execute(
                """
                INSERT INTO world_event_preconditions (
                    instance_id, precondition_id, kind, title,
                    participants_json, source_ids_json, cause_event_ids_json,
                    registered_minute, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_instance_id,
                    pid,
                    p_kind,
                    p_title,
                    json.dumps(p_parts, ensure_ascii=False),
                    json.dumps(p_sources, ensure_ascii=False),
                    json.dumps(p_causes, ensure_ascii=False),
                    p_min,
                    p_status,
                ),
            )

        # 2. 校验并恢复事件主表
        snapshot_event_ids: set[str] = set()
        for idx, ev in enumerate(raw_events):
            if not isinstance(ev, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"events[{idx}] 必须为对象。")
            eid = _require_non_empty_str(ev.get("event_id"), f"events[{idx}].event_id", 120)
            if eid in snapshot_event_ids:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"快照包含重复的 event_id: {eid}。")
            snapshot_event_ids.add(eid)

        cause_graph: dict[str, tuple[str, ...]] = {}
        event_revisions_expected: dict[str, int] = {}

        for idx, ev in enumerate(raw_events):
            eid = ev["event_id"].strip()
            kind = ev.get("kind")
            if kind not in EVENT_KINDS:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"事件 {eid} kind 非法。")
            title = _require_non_empty_str(ev.get("title"), f"events[{idx}].title", 120)
            description = _require_non_empty_str(ev.get("description"), f"events[{idx}].description", 2000)
            location = _require_non_empty_str(ev.get("location"), f"events[{idx}].location", 120)
            scope = ev.get("scope")
            if scope not in EVENT_SCOPES:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"事件 {eid} scope 非法。")
            stage = _require_non_empty_str(ev.get("stage"), f"events[{idx}].stage", 80)
            status = ev.get("status")
            if status not in EVENT_STATUSES:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"事件 {eid} status 非法。")
            created_min = _require_strict_int(ev.get("created_minute"), f"events[{idx}].created_minute", min_value=0)
            updated_min = _require_strict_int(ev.get("updated_minute"), f"events[{idx}].updated_minute", min_value=created_min)
            if updated_min > snap_minute:
                raise SnapshotRestoreError(
                    "SNAPSHOT_TIME_INVALID",
                    f"事件 {eid} 的 updated_minute ({updated_min}) 晚于快照世界分钟 ({snap_minute})。",
                )
            next_min_raw = ev.get("next_update_minute")
            if status in TERMINAL_EVENT_STATUSES:
                if next_min_raw is not None:
                    raise SnapshotRestoreError("SNAPSHOT_INVALID", f"终态事件 {eid} 的 next_update_minute 必须为 None。")
                next_min = None
            else:
                next_min = _require_strict_int(next_min_raw, f"events[{idx}].next_update_minute", min_value=0)

            rev = _require_strict_int(ev.get("revision"), f"events[{idx}].revision", min_value=1)
            event_revisions_expected[eid] = rev

            parts = [_require_non_empty_str(p, "participant", 80) for p in ev.get("participants", [])]
            causes: list[str] = []
            for raw_cid in ev.get("cause_event_ids", []):
                cid = _require_non_empty_str(raw_cid, "cause_event_id", 120)
                if cid == eid:
                    raise SnapshotRestoreError("CAUSAL_SELF_REFERENCE", f"快照事件 {eid} 存在自引用。")
                if cid not in snapshot_event_ids:
                    raise SnapshotRestoreError(
                        "SNAPSHOT_CAUSE_MISSING",
                        f"快照事件 {eid} 引用了不在本快照内的外部或跨实例父事件: {cid}。",
                        {"event_id": eid, "cause_event_id": cid},
                    )
                causes.append(cid)
            cause_graph[eid] = tuple(causes)

            sources = [remap_source_id(s) for s in ev.get("source_ids", [])]
            tp_type = ev.get("turnpoint_type")
            if tp_type is not None and tp_type not in MAJOR_TURNPOINT_TYPES:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"未知的 turnpoint_type: {tp_type}。")
            pre_ids = [_require_non_empty_str(p, "precondition_id", 120) for p in ev.get("precondition_ids", [])]
            for pid in pre_ids:
                if pid not in restored_precondition_ids:
                    raise SnapshotRestoreError(
                        "SNAPSHOT_PRECONDITION_MISSING",
                        f"快照事件 {eid} 引用了未包含在快照中的前置条件: {pid}。",
                    )

            conn.execute(
                """
                INSERT INTO world_events (
                    instance_id, event_id, kind, title, description, location, scope,
                    participants_json, stage, status, created_minute, updated_minute,
                    next_update_minute, cause_event_ids_json, source_ids_json,
                    turnpoint_type, precondition_ids_json, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_instance_id,
                    eid,
                    kind,
                    title,
                    description,
                    location,
                    scope,
                    json.dumps(parts, ensure_ascii=False),
                    stage,
                    status,
                    created_min,
                    updated_min,
                    next_min,
                    json.dumps(causes, ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    tp_type,
                    json.dumps(pre_ids, ensure_ascii=False),
                    rev,
                ),
            )

            if ev.get("public_label") is not None:
                public_label = _require_non_empty_str(ev["public_label"], f"events[{idx}].public_label", 24)
                conn.execute("UPDATE world_events SET public_label = ? WHERE instance_id = ? AND event_id = ?",
                             (public_label, target_instance_id, eid))

        _detect_causal_cycles(cause_graph)

        # 3. 校验并恢复后果表
        seen_csq_ids: set[str] = set()
        for idx, csq in enumerate(raw_consequences):
            if not isinstance(csq, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"consequences[{idx}] 必须为对象。")
            cid = _require_non_empty_str(csq.get("consequence_id"), f"consequences[{idx}].consequence_id", 120)
            if cid in seen_csq_ids:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"重复的 consequence_id: {cid}。")
            seen_csq_ids.add(cid)

            src_eid = _require_non_empty_str(csq.get("source_event_id"), f"consequences[{idx}].source_event_id", 120)
            if src_eid not in snapshot_event_ids:
                raise SnapshotRestoreError(
                    "SNAPSHOT_CONSEQUENCE_ORPHAN",
                    f"后果 {cid} 引用的来源事件 {src_eid} 不在快照内。",
                )
            src_rev = _require_strict_int(
                csq.get("source_event_revision", 1),
                f"consequences[{idx}].source_event_revision",
                min_value=1,
            )
            if src_rev > event_revisions_expected[src_eid]:
                raise SnapshotRestoreError(
                    "SNAPSHOT_CONSEQUENCE_REVISION_INVALID",
                    f"后果 {cid} 的版本 ({src_rev}) 超出事件 {src_eid} 的当前版本 ({event_revisions_expected[src_eid]})。",
                )
            impact_type = csq.get("impact_type", "direct")
            if impact_type not in IMPACT_TYPES:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"非法的 impact_type: {impact_type}。")
            domain = _require_non_empty_str(csq.get("domain"), f"consequences[{idx}].domain", 60)
            if domain not in CONSEQUENCE_DOMAINS:
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"非法的后果域: {domain}。")
            target = _require_non_empty_str(csq.get("target"), f"consequences[{idx}].target", 120)
            summary = _require_non_empty_str(csq.get("summary"), f"consequences[{idx}].summary", 500)
            eff_min = _require_strict_int(csq.get("effective_minute", 0), f"consequences[{idx}].effective_minute", min_value=0)
            if eff_min > snap_minute:
                raise SnapshotRestoreError("SNAPSHOT_TIME_INVALID", f"后果 {cid} 生效时间晚于快照时间。")

            conn.execute(
                """
                INSERT INTO world_event_consequences (
                    instance_id, consequence_id, source_event_id, source_event_revision,
                    impact_type, domain, target, summary, effective_minute
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_instance_id,
                    cid,
                    src_eid,
                    src_rev,
                    impact_type,
                    domain,
                    target,
                    summary,
                    eff_min,
                ),
            )

        # 4. 校验并恢复修订历史表
        revisions_by_event: dict[str, list[int]] = {}
        for idx, rev_item in enumerate(raw_revisions):
            if not isinstance(rev_item, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"revisions[{idx}] 必须为对象。")
            eid = _require_non_empty_str(rev_item.get("event_id"), f"revisions[{idx}].event_id", 120)
            if eid not in snapshot_event_ids:
                raise SnapshotRestoreError(
                    "SNAPSHOT_REVISION_ORPHAN",
                    f"修订记录引用的事件 {eid} 不在快照中。",
                )
            rev_num = _require_strict_int(rev_item.get("revision"), f"revisions[{idx}].revision", min_value=1)
            op_name = rev_item.get("operation", "create")
            if op_name not in ("create", "update", "restore"):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"非法的修订操作类型: {op_name}。")
            w_min = _require_strict_int(rev_item.get("world_minute", 0), f"revisions[{idx}].world_minute", min_value=0)
            if w_min > snap_minute:
                raise SnapshotRestoreError("SNAPSHOT_TIME_INVALID", f"修订记录时间 ({w_min}) 晚于快照时间 ({snap_minute})。")
            reason = str(rev_item.get("reason") or "")
            raw_snap = rev_item.get("snapshot")
            if not isinstance(raw_snap, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_INVALID", f"revisions[{idx}].snapshot 必须为对象。")
            remapped_snap = dict(raw_snap)
            remapped_snap["instance_id"] = target_instance_id
            if "source_ids" in remapped_snap and isinstance(remapped_snap["source_ids"], list):
                remapped_snap["source_ids"] = [remap_source_id(s) for s in remapped_snap["source_ids"]]

            revisions_by_event.setdefault(eid, []).append(rev_num)
            conn.execute(
                """
                INSERT INTO world_event_revisions (
                    instance_id, event_id, revision, operation, world_minute, reason, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_instance_id,
                    eid,
                    rev_num,
                    op_name,
                    w_min,
                    reason,
                    json.dumps(remapped_snap, ensure_ascii=False),
                ),
            )

        for eid, expected_max_rev in event_revisions_expected.items():
            actual_revs = sorted(revisions_by_event.get(eid, ()))
            if len(actual_revs) != expected_max_rev or any(
                revision != expected for expected, revision in enumerate(actual_revs, 1)
            ):
                raise SnapshotRestoreError(
                    "SNAPSHOT_REVISION_GAP",
                    f"事件 {eid} 的修订历史不连续或与当前版本 ({expected_max_rev}) 不一致: {actual_revs}。",
                    {"event_id": eid, "expected_revision": expected_max_rev, "revisions": actual_revs},
                )

        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return snapshot(conn, target_instance_id)
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise


# 别名导出，方便 PulseWorker 接线
read_pulse_snapshot = read_snapshot
apply_pulse_proposal = apply_proposal
