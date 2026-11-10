"""M3 Wave 3 可续玩迁移、已删除资料历史引用表示与小型快照适配器。

契约要求（`docs/collaboration/M3_WAVE3_GEMINI_LI.md`）：
1. 修复 v4 存档引用已删除资料后无法迁移的边界：
   - 保留事件和知识历史，为缺失（已软删除）来源设计可审计的历史引用表示（`HistoricalSourceRef` 及 `m3_historical_source_refs` 墓碑表）；
   - 历史引用不可被误用为当前 Canon（`selectable_as_canon=False`），绝不向 `documents`、`chunks`、`lore_entries` 插入已删除记录，禁止把删除资料恢复为可搜索条目；
   - 禁止跨世界借用同名/同 ID 资料（检测 `other_world_sources` 并抛出 `CrossWorldSourceBorrowError`）；
   - 严厉拦截从未在该世界存在过的伪造引用（抛出 `ForgedHistoricalReferenceError`）。
2. 以小型适配器（`PortabilitySnapshotAdapter`）处理新旧存档（v1–v4 全兼容，无需破坏 v4 顶层结构），支持：
   - 同一世界二次导入（每次分配完全独立的新 `world_id`、`adventure_id`、`instance_id`、`checkpoint_id`）；
   - 新格式回导（导入后的世界再次导出并导入到第三台设备，历史墓碑引用完整延续）；
   - 旧分支仍可续玩、同分钟不同版本的检查点精确恢复；
   - 错误原子回滚（任何一步校验失败不留半个世界或残留行）与 ZIP 安全限制（大小上限、路径穿越、`..`、加密位、条目数量限制）。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
from shutil import copyfileobj
import sqlite3
from tempfile import TemporaryFile
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.adventures import scenario
from app.adventures.memory import validate_event
from app.adventures.state import character
from app.errors import ApiError
from app.m3 import events as world_events, knowledge as world_knowledge
from app.m3.core import adapters as core_adapters, service as instance_service
from app.world_export import _write_json

from .errors import (
    PortabilityError,
    CrossWorldSourceBorrowError,
    ForgedHistoricalReferenceError,
    HistoricalSourceNotCanonError,
    TransferInvalidError,
    TransferLimitExceededError,
    TransferMismatchError,
    TransferSettingsRequiredError,
)
from .schema import ensure_portability_schema
from .types import (
    HISTORICAL_REF_FORMAT,
    SAVE_FILE,
    SAVES_FORMAT,
    SAVES_INSTANCES_VERSION,
    SAVES_OPENINGS_VERSION,
    SAVES_VERSION,
    SETTINGS_FORMAT,
    SETTINGS_OPENINGS_VERSION,
    TRANSFER_BYTE_LIMIT,
    VERSION,
    WORLD_FILE,
    HistoricalSourceRef,
)

WORLD_TABLES = {
    "documents": "id filename media_type byte_size encoding content_sha256 text text_length line_count chunk_count source_kind encoding_semantics warnings status created_at updated_at",
    "chunks": "id document_id chunk_index heading_path start_char end_char",
    "lore_entries": "id type title summary status revision origin created_at updated_at",
    "lore_entry_sources": "id entry_id document_id chunk_id start_char end_char excerpt created_at",
    "item_descriptions": "name description",
    "world_openings": "id opening_json",
}

SAVE_TABLES = {
    "adventures": "id name mode revision created_at character_json origin_checkpoint_id opening_json",
    "adventure_turns": "adventure_id sequence draft_id action result created_at",
    "adventure_checkpoints": "id adventure_id name revision character_json mode created_at memory_cursor",
    "adventure_memory_events": "adventure_id sequence turn origin change_json",
}

WORLD_COLUMNS = "id name description created_at updated_at"
ID_FIELDS = {
    "document_id": "documents",
    "chunk_id": "chunks",
    "entry_id": "lore_entries",
    "adventure_id": "adventures",
    "origin_checkpoint_id": "adventure_checkpoints",
}
INTEGER_FIELDS = {
    "byte_size",
    "text_length",
    "line_count",
    "chunk_count",
    "chunk_index",
    "start_char",
    "end_char",
    "revision",
    "sequence",
    "turn",
    "memory_cursor",
}


def _extract_event_snapshot_source_ids(events_snap: Mapping[str, Any] | None) -> list[str]:
    """从单个事件域快照中提取所有被引用的 `source_ids`（按出现顺序去重）。"""
    if not isinstance(events_snap, Mapping):
        return []
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw_sid: Any) -> None:
        if isinstance(raw_sid, str) and raw_sid.strip():
            s = raw_sid.strip()
            if s not in seen:
                seen.add(s)
                found.append(s)

    for pre in events_snap.get("preconditions") or ():
        if isinstance(pre, Mapping):
            for sid in pre.get("source_ids") or ():
                _add(sid)
    for ev in events_snap.get("events") or ():
        if isinstance(ev, Mapping):
            for sid in ev.get("source_ids") or ():
                _add(sid)
    for rev in events_snap.get("revisions") or ():
        if isinstance(rev, Mapping):
            snap_inner = rev.get("snapshot")
            if isinstance(snap_inner, Mapping):
                for sid in snap_inner.get("source_ids") or ():
                    _add(sid)
    return found


def discover_world_source_catalog(
    conn: sqlite3.Connection,
    world_id: str,
) -> dict[str, Any]:
    """扫描数据库，严格区分指定世界的：
    1. `active_sources`：当前未删除的活跃 `chunk:<id>` 与 `entry:<id>`；
    2. `deleted_historical_sources`：属于该 `world_id` 但已被软删除（`deleted_at IS NOT NULL`）或此前已归档在 `m3_historical_source_refs` 墓碑表中的历史来源；
    3. `other_world_sources`：属于其他世界（`world_id != target`）的来源 ID -> 所属 `world_id`（用于拦截跨世界借用同名 ID）。
    """
    ensure_portability_schema(conn)
    active_sources: set[str] = set()
    deleted_historical: dict[str, HistoricalSourceRef] = {}
    other_world_sources: dict[str, str] = {}

    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    # 1. 检查 chunks JOIN documents
    if "chunks" in tables and "documents" in tables:
        for r in conn.execute(
            """
            SELECT c.id, c.world_id, c.heading_path, d.filename, d.deleted_at, d.content_sha256
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            """
        ).fetchall():
            cid = f"chunk:{r[0]}"
            w_id = str(r[1] or "")
            heading = str(r[2] or "")
            fname = str(r[3] or "")
            del_at = r[4]
            sha = str(r[5] or "")
            if w_id == world_id:
                if del_at is None:
                    active_sources.add(cid)
                else:
                    title_label = f"{fname} · {heading}" if (fname and heading) else (fname or heading or cid)
                    deleted_historical[cid] = HistoricalSourceRef(
                        source_id=cid,
                        original_source_id=cid,
                        kind="chunk",
                        title=title_label,
                        document_filename=fname,
                        deleted_at=str(del_at),
                        content_sha256=sha,
                    )
            else:
                other_world_sources[cid] = w_id

    # 2. 检查 lore_entries
    if "lore_entries" in tables:
        for r in conn.execute(
            """
            SELECT id, world_id, type, title, summary, deleted_at
            FROM lore_entries
            """
        ).fetchall():
            eid = f"entry:{r[0]}"
            w_id = str(r[1] or "")
            etype = str(r[2] or "")
            etitle = str(r[3] or "")
            summary = str(r[4] or "")
            del_at = r[5]
            if w_id == world_id:
                if del_at is None:
                    active_sources.add(eid)
                else:
                    sha = hashlib.sha256(f"{etitle}\n{summary}".encode("utf-8")).hexdigest()[:16]
                    deleted_historical[eid] = HistoricalSourceRef(
                        source_id=eid,
                        original_source_id=eid,
                        kind="entry",
                        title=etitle or eid,
                        entry_type=etype,
                        deleted_at=str(del_at),
                        content_sha256=sha,
                    )
            else:
                other_world_sources[eid] = w_id

    # 3. 检查已存在于 m3_historical_source_refs 的继承历史墓碑（支持二次回导）
    if "m3_historical_source_refs" in tables:
        for r in conn.execute(
            """
            SELECT source_id, original_source_id, kind, title, document_filename,
                   entry_type, deleted_at, content_sha256, world_id
            FROM m3_historical_source_refs
            """
        ).fetchall():
            sid = str(r[0])
            w_id = str(r[8] or "")
            if w_id == world_id:
                if sid not in active_sources and sid not in deleted_historical:
                    deleted_historical[sid] = HistoricalSourceRef(
                        source_id=sid,
                        original_source_id=str(r[1] or sid),
                        kind=str(r[2]),  # type: ignore[arg-type]
                        title=str(r[3] or sid),
                        document_filename=str(r[4] or ""),
                        entry_type=str(r[5] or ""),
                        deleted_at=str(r[6] or ""),
                        content_sha256=str(r[7] or ""),
                    )
            else:
                other_world_sources.setdefault(sid, w_id)

    return {
        "world_id": world_id,
        "active_sources": frozenset(active_sources),
        "deleted_historical_sources": deleted_historical,
        "other_world_sources": other_world_sources,
    }


def list_historical_source_refs(
    conn: sqlite3.Connection,
    world_id: str,
) -> list[dict[str, Any]]:
    """查询目标世界下已登记的所有历史墓碑引用（只供历史审计，绝不作为当前 Canon）。"""
    ensure_portability_schema(conn)
    rows = conn.execute(
        """
        SELECT source_id, original_source_id, kind, title, document_filename,
               entry_type, deleted_at, content_sha256, status, selectable_as_canon, searchable
        FROM m3_historical_source_refs
        WHERE world_id = ?
        ORDER BY source_id ASC
        """,
        (world_id,),
    ).fetchall()
    return [
        {
            "format": HISTORICAL_REF_FORMAT,
            "world_id": world_id,
            "source_id": str(r[0]),
            "original_source_id": str(r[1]),
            "kind": str(r[2]),
            "title": str(r[3]),
            "document_filename": str(r[4] or ""),
            "entry_type": str(r[5] or ""),
            "deleted_at": str(r[6] or ""),
            "content_sha256": str(r[7] or ""),
            "status": str(r[8]),
            "selectable_as_canon": bool(r[9]),
            "searchable": bool(r[10]),
        }
        for r in rows
    ]


def assert_source_usable_as_current_canon(
    conn: sqlite3.Connection,
    world_id: str,
    source_id: str,
) -> None:
    """校验指定 `source_id` 是否可被用作当前活跃 Canon。

    - 若该 `source_id` 位于 `m3_historical_source_refs` 墓碑表中或已在源表中软删除，
      立即抛出 `HistoricalSourceNotCanonError`（409 `HISTORICAL_SOURCE_NOT_CANON`）。
    """
    ensure_portability_schema(conn)
    tomb = conn.execute(
        "SELECT 1 FROM m3_historical_source_refs WHERE world_id = ? AND source_id = ? LIMIT 1",
        (world_id, source_id),
    ).fetchone()
    if tomb is not None:
        raise HistoricalSourceNotCanonError(source_id)

    catalog = discover_world_source_catalog(conn, world_id)
    if source_id in catalog["deleted_historical_sources"]:
        raise HistoricalSourceNotCanonError(source_id)
    if source_id in catalog["other_world_sources"] and source_id not in catalog["active_sources"]:
        raise CrossWorldSourceBorrowError(source_id, catalog["other_world_sources"][source_id])
    if source_id not in catalog["active_sources"]:
        raise ForgedHistoricalReferenceError(source_id)


class PortabilitySnapshotAdapter:
    """处理新旧存档（v1–v4）实例快照、动态领域模块载荷与已删除历史来源墓碑的小型适配器。"""

    @staticmethod
    def build_instance_snapshot(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
        """构建实例完整快照，并在 `events` 域内附带所有被引用但已软删除的可审计历史来源墓碑 `historical_sources`。"""
        snap = instance_service.snapshot(conn, instance_id)
        world_id = str(snap["instance"]["world_id"])
        catalog = discover_world_source_catalog(conn, world_id)
        deleted_map: dict[str, HistoricalSourceRef] = catalog["deleted_historical_sources"]

        ev_snap = world_events.snapshot(conn, instance_id)
        referenced_sids = _extract_event_snapshot_source_ids(ev_snap)
        hist_refs: list[dict[str, Any]] = []
        for sid in referenced_sids:
            if sid in deleted_map:
                hist_refs.append(deleted_map[sid].to_dict())

        if hist_refs:
            ev_snap = dict(ev_snap)
            ev_snap["historical_sources"] = hist_refs

        domains: dict[str, Any] = {
            "events": ev_snap,
            "knowledge": world_knowledge.snapshot(conn, instance_id),
        }
        # 若注册表挂载了额外模块适配器（如 Marcin 的 npcs），自动纳入快照
        for adapter in core_adapters.adapters():
            name = getattr(adapter, "name", "")
            if name and name not in domains:
                domains[name] = adapter.snapshot(conn, instance_id)

        snap["domains"] = domains
        return snap

    @staticmethod
    def validate_and_prepare_historical_sources(
        conn: sqlite3.Connection,
        *,
        events_snap: Mapping[str, Any],
        target_world_id: str,
        source_world_id: str,
        active_source_map: Mapping[str, str],
        historical_id_registry: dict[str, str],
        inherited_tombstones: Mapping[str, HistoricalSourceRef] | None = None,
    ) -> tuple[dict[str, str], set[str], list[HistoricalSourceRef]]:
        """校验事件快照中引用的全部 `source_ids`：

        1. 若属于 `active_source_map`（当前未删除的活跃资料），映射为新世界的活跃 ID；
        2. 若属于快照自带的 `historical_sources` 墓碑或源库同世界的软删除资料（`inherited_tombstones`）：
           - 严查该原始 ID 是否实际上属于其他世界（若是且不在本世界历史记录中，拒绝跨世界借用）；
           - 严查墓碑结构完整性（必须具备合法 `chunk:`/`entry:` 前缀、非空 `deleted_at` 与标题）；
           - 分配新世界内一致的墓碑 ID（存入 `historical_id_registry`，同一存档内多个实例/检查点共享同一重映射 ID），
             并生成待写入 `m3_historical_source_refs` 表的 `HistoricalSourceRef`（绝不写入 `documents`/`chunks`/`lore_entries`）；
        3. 若既不在 `active_source_map` 中，也没有合法的同世界历史删除墓碑证明，判定为伪造引用（`ForgedHistoricalReferenceError`）或跨世界借用（`CrossWorldSourceBorrowError`）并立即拒绝。
        """
        ensure_portability_schema(conn)

        # 检查当前数据库中其他世界已占用的所有原始 source_id（防止伪造墓碑借用其他世界 ID）
        source_catalog = discover_world_source_catalog(conn, source_world_id)
        other_world_lookup = source_catalog["other_world_sources"]
        source_world_deleted = source_catalog["deleted_historical_sources"]

        declared_tombstones: dict[str, HistoricalSourceRef] = dict(inherited_tombstones or {})
        for sid, ref_obj in source_world_deleted.items():
            declared_tombstones.setdefault(sid, ref_obj)

        raw_hist_list = events_snap.get("historical_sources")
        if raw_hist_list is not None:
            if not isinstance(raw_hist_list, list):
                raise TransferInvalidError("historical_sources 必须是数组。")
            for item in raw_hist_list:
                if not isinstance(item, Mapping):
                    raise TransferInvalidError("historical_sources 项必须是对象。")
                sid = str(item.get("source_id") or "").strip()
                kind = str(item.get("kind") or "").strip()
                del_at = str(item.get("deleted_at") or "").strip()
                title = str(item.get("title") or "").strip()
                if (
                    not sid
                    or kind not in ("chunk", "entry")
                    or not sid.startswith(f"{kind}:")
                    or not del_at
                    or not title
                ):
                    raise ForgedHistoricalReferenceError(sid or "<empty>")
                if bool(item.get("selectable_as_canon")) or bool(item.get("searchable")):
                    raise TransferInvalidError(
                        "历史来源墓碑严禁标记为 selectable_as_canon 或 searchable。",
                        reason_code="HISTORICAL_CANON_FLAG_FORBIDDEN",
                    )
                # 若该 ID 属于其他世界且从未在源世界存在，拦截跨世界借用
                if sid in other_world_lookup and sid not in source_world_deleted:
                    raise CrossWorldSourceBorrowError(sid, other_world_lookup[sid])
                declared_tombstones[sid] = HistoricalSourceRef.from_mapping(item)

        combined_map: dict[str, str] = dict(active_source_map)
        allowed_for_restore: set[str] = set(active_source_map.values())
        tombstones_to_persist: list[HistoricalSourceRef] = []

        referenced_sids = _extract_event_snapshot_source_ids(events_snap)
        for sid in referenced_sids:
            if not (sid.startswith("chunk:") or sid.startswith("entry:")):
                raise ForgedHistoricalReferenceError(sid)
            if sid in active_source_map:
                continue
            # 检查是否试图跨世界借用其他世界的活跃/历史 ID
            if sid in other_world_lookup and sid not in declared_tombstones:
                raise CrossWorldSourceBorrowError(sid, other_world_lookup[sid])
            if sid not in declared_tombstones:
                raise ForgedHistoricalReferenceError(sid)

            tomb = declared_tombstones[sid]
            prefix = f"{tomb.kind}:"
            if sid not in historical_id_registry:
                historical_id_registry[sid] = f"{prefix}{uuid4()}"
            mapped_sid = historical_id_registry[sid]
            combined_map[sid] = mapped_sid
            allowed_for_restore.add(mapped_sid)
            tombstones_to_persist.append(
                HistoricalSourceRef(
                    source_id=mapped_sid,
                    original_source_id=tomb.original_source_id or sid,
                    kind=tomb.kind,
                    title=tomb.title,
                    document_filename=tomb.document_filename,
                    entry_type=tomb.entry_type,
                    deleted_at=tomb.deleted_at,
                    content_sha256=tomb.content_sha256,
                )
            )

        return combined_map, allowed_for_restore, tombstones_to_persist

    @staticmethod
    def persist_historical_tombstones(
        conn: sqlite3.Connection,
        world_id: str,
        tombstones: list[HistoricalSourceRef],
    ) -> None:
        """将验证通过的历史墓碑写入 `m3_historical_source_refs`（不触碰 Canon 表）。"""
        ensure_portability_schema(conn)
        now_iso = datetime.now(timezone.utc).isoformat()
        for t in tombstones:
            conn.execute(
                """
                INSERT INTO m3_historical_source_refs (
                    world_id, source_id, original_source_id, kind, title,
                    document_filename, entry_type, deleted_at, content_sha256,
                    status, selectable_as_canon, searchable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'deleted_historical', 0, 0, ?)
                ON CONFLICT(world_id, source_id) DO UPDATE SET
                    title = excluded.title,
                    deleted_at = excluded.deleted_at
                """,
                (
                    world_id,
                    t.source_id,
                    t.original_source_id,
                    t.kind,
                    t.title,
                    t.document_filename,
                    t.entry_type,
                    t.deleted_at,
                    t.content_sha256,
                    now_iso,
                ),
            )




class HistoricalAwareEventsAdapter:
    """支持已删除资料历史墓碑校验与恢复的 Events 领域快照适配器。

    可通过 `install_portability_adapters()` 注册到 `app.m3.core.adapters`，
    使本地检查点创建（`checkpoints.create_checkpoint`）与分支恢复（`checkpoints.restore`）
    在资料被软删除后仍能精确保存与恢复历史事件因果引用。
    """

    name = "events"

    def snapshot(self, conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
        inst = instance_service.get(conn, instance_id)
        world_id = str(inst["world_id"])
        catalog = discover_world_source_catalog(conn, world_id)
        deleted_map: dict[str, HistoricalSourceRef] = catalog["deleted_historical_sources"]
        ev_snap = world_events.snapshot(conn, instance_id)
        referenced_sids = _extract_event_snapshot_source_ids(ev_snap)
        hist_refs = [deleted_map[sid].to_dict() for sid in referenced_sids if sid in deleted_map]
        if hist_refs:
            ev_snap = dict(ev_snap)
            ev_snap["historical_sources"] = hist_refs
        return ev_snap

    def restore(
        self,
        conn: sqlite3.Connection,
        snapshot: dict[str, Any],
        *,
        new_instance_id: str,
        world_id: str,
    ) -> None:
        catalog = discover_world_source_catalog(conn, world_id)
        active_sids = set(catalog["active_sources"])
        deleted_sids = set(catalog["deleted_historical_sources"].keys())
        other_world_sids = catalog["other_world_sources"]

        declared_hist = {
            str(item.get("source_id")): HistoricalSourceRef.from_mapping(item)
            for item in (snapshot.get("historical_sources") or ())
            if isinstance(item, Mapping) and item.get("source_id")
        }
        for sid, ref_obj in catalog["deleted_historical_sources"].items():
            declared_hist.setdefault(sid, ref_obj)

        referenced_sids = _extract_event_snapshot_source_ids(snapshot)
        tombstones_to_save: list[HistoricalSourceRef] = []
        for sid in referenced_sids:
            if sid in active_sids:
                continue
            if sid in other_world_sids and sid not in declared_hist:
                raise CrossWorldSourceBorrowError(sid, other_world_sids[sid])
            if sid not in declared_hist and sid not in deleted_sids:
                raise ForgedHistoricalReferenceError(sid)
            if sid in declared_hist:
                tombstones_to_save.append(declared_hist[sid])

        allowed_sids = active_sids | deleted_sids | set(declared_hist.keys())
        world_events.restore(
            conn,
            new_instance_id,
            snapshot,
            allowed_source_ids=allowed_sids,
        )
        if tombstones_to_save:
            PortabilitySnapshotAdapter.persist_historical_tombstones(conn, world_id, tombstones_to_save)


def install_portability_adapters() -> None:
    """向 `app.m3.core.adapters` 注册支持已删除历史来源墓碑的 EventsAdapter。"""
    core_adapters.load_builtin_adapters()
    core_adapters.register_adapter(HistoricalAwareEventsAdapter())


# ==================== 兼容 v1–v4 及历史已删除来源的完整导出/导入流水线 ====================


def _rows(conn: sqlite3.Connection, table: str, columns: str, world_id: str) -> Iterator[dict[str, Any]]:
    select = ", ".join(f"t.{column}" for column in columns.split())
    joins = ""
    where = "t.world_id = ?"
    if table in {"documents", "lore_entries", "lore_entry_sources"}:
        where += " AND t.deleted_at IS NULL"
    if table in {"chunks", "lore_entry_sources"}:
        joins += " JOIN documents d ON d.id=t.document_id AND d.world_id=t.world_id"
        where += " AND d.deleted_at IS NULL"
    if table == "lore_entry_sources":
        joins += " JOIN lore_entries e ON e.id=t.entry_id AND e.world_id=t.world_id"
        where += " AND e.deleted_at IS NULL"
    if table in {"adventure_turns", "adventure_checkpoints", "adventure_memory_events"}:
        joins += " JOIN adventures a ON a.id=t.adventure_id"
        where = "a.world_id = ?"
    return (
        dict(row)
        for row in conn.execute(
            f"SELECT {select} FROM {table} t{joins} WHERE {where} ORDER BY t.rowid",
            (world_id,),
        )
    )


def _world_payload(conn: sqlite3.Connection, world: Mapping[str, Any]) -> dict[str, Any]:
    has_openings = conn.execute(
        "SELECT 1 FROM world_openings WHERE world_id=? LIMIT 1", (world["id"],)
    ).fetchone()
    return {
        "format": SETTINGS_FORMAT,
        "version": SETTINGS_OPENINGS_VERSION if has_openings else VERSION,
        "world": {key: world[key] for key in WORLD_COLUMNS.split()},
        **{
            table: _rows(conn, table, columns, world["id"])
            for table, columns in WORLD_TABLES.items()
            if table != "world_openings" or has_openings
        },
    }


def _digest(file: Any) -> str:
    file.seek(0)
    sha = hashlib.sha256()
    for block in iter(lambda: file.read(65536), b""):
        sha.update(block)
    file.seek(0)
    return sha.hexdigest()


def _saved_instances(conn: sqlite3.Connection, world_id: str) -> Any:
    return conn.execute(
        """
        SELECT i.id, i.adventure_id FROM world_instances i JOIN adventures a ON a.id=i.adventure_id
        WHERE a.world_id=? AND (
            i.revision<>a.revision
            OR EXISTS (SELECT 1 FROM world_events e WHERE e.instance_id=i.id)
            OR EXISTS (SELECT 1 FROM m3_knowledge_facts k WHERE k.instance_id=i.id)
        )
        ORDER BY a.rowid
        """,
        (world_id,),
    )


def _enrich_checkpoint_snapshot_with_historical_sources(
    conn: sqlite3.Connection,
    world_id: str,
    raw_checkpoint_snap: str | None,
) -> str | None:
    """导出检查点时，若检查点内的事件快照引用了后来被软删除的资料，自动注入可审计的 `historical_sources` 墓碑。"""
    if not raw_checkpoint_snap:
        return raw_checkpoint_snap
    try:
        snap = json.loads(raw_checkpoint_snap)
    except Exception:
        return raw_checkpoint_snap
    if not isinstance(snap, dict):
        return raw_checkpoint_snap
    domains = snap.get("domains")
    if not isinstance(domains, dict) or not isinstance(domains.get("events"), dict):
        return raw_checkpoint_snap

    catalog = discover_world_source_catalog(conn, world_id)
    deleted_map: dict[str, HistoricalSourceRef] = catalog["deleted_historical_sources"]
    if not deleted_map:
        return raw_checkpoint_snap

    ev_snap = dict(domains["events"])
    existing_hist = {
        str(item.get("source_id")): item
        for item in (ev_snap.get("historical_sources") or ())
        if isinstance(item, Mapping) and item.get("source_id")
    }
    for sid in _extract_event_snapshot_source_ids(ev_snap):
        if sid in deleted_map and sid not in existing_hist:
            existing_hist[sid] = deleted_map[sid].to_dict()

    if existing_hist:
        ev_snap["historical_sources"] = list(existing_hist.values())
        snap = dict(snap)
        snap["domains"] = {**domains, "events": ev_snap}
        return json.dumps(snap, ensure_ascii=False)
    return raw_checkpoint_snap


def _checkpoint_rows_for_export(
    conn: sqlite3.Connection,
    columns: str,
    world_id: str,
) -> Iterator[dict[str, Any]]:
    for row in _rows(conn, "adventure_checkpoints", columns, world_id):
        if "instance_snapshot" in row and row["instance_snapshot"]:
            row["instance_snapshot"] = _enrich_checkpoint_snapshot_with_historical_sources(
                conn, world_id, row["instance_snapshot"]
            )
        yield row


def _save_payload(
    conn: sqlite3.Connection,
    world: Mapping[str, Any],
    settings_digest: str,
) -> dict[str, Any]:
    instances = _saved_instances(conn, world["id"]).fetchall()
    has_openings = conn.execute(
        "SELECT 1 FROM adventures WHERE world_id=? AND opening_json IS NOT NULL LIMIT 1",
        (world["id"],),
    ).fetchone()
    has_checkpoint_snapshots = conn.execute(
        """
        SELECT 1 FROM adventure_checkpoints c
        JOIN adventures a ON a.id = c.adventure_id
        WHERE a.world_id = ? AND c.instance_snapshot IS NOT NULL
        LIMIT 1
        """,
        (world["id"],),
    ).fetchone()
    version = SAVES_OPENINGS_VERSION if has_openings else SAVES_VERSION
    tables = SAVE_TABLES
    if instances or has_checkpoint_snapshots:
        version = SAVES_INSTANCES_VERSION
        tables = {
            **SAVE_TABLES,
            "adventure_checkpoints": SAVE_TABLES["adventure_checkpoints"] + " instance_snapshot",
        }

    table_iterators: dict[str, Any] = {}
    for table, columns in tables.items():
        cols = columns if version >= SAVES_OPENINGS_VERSION else columns.replace(" opening_json", "")
        if table == "adventure_checkpoints" and version == SAVES_INSTANCES_VERSION:
            table_iterators[table] = _checkpoint_rows_for_export(conn, cols, world["id"])
        else:
            table_iterators[table] = _rows(conn, table, cols, world["id"])

    payload: dict[str, Any] = {
        "format": SAVES_FORMAT,
        "version": version,
        "world_id": world["id"],
        "settings_sha256": settings_digest,
        **table_iterators,
    }
    if version == SAVES_INSTANCES_VERSION:
        payload["world_instances"] = (
            {
                "adventure_id": row["adventure_id"],
                "snapshot": PortabilitySnapshotAdapter.build_instance_snapshot(conn, row["id"]),
            }
            for row in instances
        )
    return payload


def build_export_payloads(
    conn: sqlite3.Connection,
    world_id: str,
) -> dict[str, Any]:
    """直接构建内存中的 `world_payload`、`saves_payload` 与 `settings_sha256`（供测试与性能基准直接调用）。"""
    ensure_portability_schema(conn)
    world_row = conn.execute(
        "SELECT * FROM worlds WHERE id = ? AND deleted_at IS NULL", (world_id,)
    ).fetchone()
    if world_row is None:
        raise ApiError(404, "WORLD_NOT_FOUND", "世界不存在。")
    world = dict(world_row)
    with TemporaryFile() as settings_file, TemporaryFile() as saves_file:
        _write_json(settings_file, _world_payload(conn, world))
        digest = _digest(settings_file)
        settings_bytes = settings_file.read()
        _write_json(saves_file, _save_payload(conn, world, digest))
        saves_file.seek(0)
        saves_bytes = saves_file.read()
    return {
        "world_id": world_id,
        "settings_sha256": digest,
        "world_bytes": settings_bytes,
        "saves_bytes": saves_bytes,
        "world_json": json.loads(settings_bytes.decode("utf-8")),
        "saves_json": json.loads(saves_bytes.decode("utf-8")),
    }


def transfer_export(
    conn: sqlite3.Connection,
    world: Mapping[str, Any],
    part: str,
    scratch_dir: Any = None,
) -> StreamingResponse:
    """兼容公共路由 `GET /api/v1/worlds/{world_id}/transfer` 的流式导出实现。"""
    ensure_portability_schema(conn)
    with ExitStack() as stack:
        settings = stack.enter_context(TemporaryFile(dir=scratch_dir))
        _write_json(settings, _world_payload(conn, world))
        size = settings.tell()
        if size > TRANSFER_BYTE_LIMIT:
            raise TransferLimitExceededError()
        archive = settings
        if part != "world":
            saves = stack.enter_context(TemporaryFile(dir=scratch_dir))
            _write_json(saves, _save_payload(conn, world, _digest(settings)))
            if size + saves.tell() > TRANSFER_BYTE_LIMIT:
                raise TransferLimitExceededError()
            archive = saves
            if part == "zip":
                archive = stack.enter_context(TemporaryFile(dir=scratch_dir))
                with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
                    for name, file in ((WORLD_FILE, settings), (SAVE_FILE, saves)):
                        file.seek(0)
                        with zipped.open(name, "w") as member:
                            copyfileobj(file, member)
                if archive.tell() > TRANSFER_BYTE_LIMIT:
                    raise TransferLimitExceededError()

        archive.seek(0, 2)
        size = archive.tell()
        archive.seek(0)
        cleanup = stack.pop_all()

    suffix = {"world": "世界观.json", "saves": "存档.json", "zip": "世界迁移.zip"}[part]
    stem = "".join(c if c.isalnum() or c in " _-." else "_" for c in world["name"]).strip(" .")
    filename = quote(f"{stem or '墨境'}-{suffix}", safe="")
    return StreamingResponse(
        iter(lambda: archive.read(65536), b""),
        media_type="application/zip" if part == "zip" else "application/json",
        background=BackgroundTask(cleanup.close),
        headers={
            "Content-Disposition": f"attachment; filename=\"mojing-transfer.{suffix.split('.')[-1]}\"; filename*=UTF-8''{filename}",
            "Content-Length": str(size),
            "Cache-Control": "no-store",
        },
    )


def _read_part(file: Any, parts: dict[str, Any], remaining: int) -> int:
    raw = file.read(remaining + 1)
    if len(raw) > remaining:
        raise TransferLimitExceededError()
    payload = json.loads(raw.decode("utf-8"), parse_constant=lambda _: None)
    if not isinstance(payload, dict):
        raise TransferInvalidError()
    kind = payload.get("format")
    version = payload.get("version")
    versions = (
        (VERSION, SAVES_VERSION, SAVES_OPENINGS_VERSION, SAVES_INSTANCES_VERSION)
        if kind == SAVES_FORMAT
        else (VERSION, SETTINGS_OPENINGS_VERSION)
    )
    if (
        kind not in (SETTINGS_FORMAT, SAVES_FORMAT)
        or kind in parts
        or type(version) is not int
        or version not in versions
    ):
        raise TransferInvalidError()
    parts[kind] = (payload, hashlib.sha256(raw).hexdigest())
    return len(raw)


def _read_files(files: list[Any]) -> dict[str, Any]:
    parts: dict[str, Any] = {}
    remaining = TRANSFER_BYTE_LIMIT
    for file in files:
        file.seek(0)
        if file.read(4) == b"PK\x03\x04":
            file.seek(0)
            if len(files) != 1:
                raise TransferInvalidError(reason_code="ZIP_MUST_BE_SINGLE_UPLOAD")
            with ZipFile(file) as zipped:
                entries = zipped.infolist()
                if len(entries) != 2 or {entry.filename for entry in entries} != {WORLD_FILE, SAVE_FILE}:
                    raise TransferInvalidError(reason_code="ZIP_ENTRIES_INVALID")
                if sum(entry.file_size for entry in entries) > TRANSFER_BYTE_LIMIT:
                    raise TransferLimitExceededError()
                for entry in entries:
                    if entry.flag_bits & 1:
                        raise TransferInvalidError(reason_code="ZIP_ENCRYPTED_FORBIDDEN")
                    with zipped.open(entry) as member:
                        remaining -= _read_part(member, parts, remaining)
        else:
            file.seek(0)
            remaining -= _read_part(file, parts, remaining)
    return parts


def _validated(row: Any, table: str, columns: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TransferInvalidError()
    values = {key: row[key] for key in columns.split()}
    for key, value in values.items():
        if value is None and key in {
            "character_json",
            "origin_checkpoint_id",
            "opening_json",
            "instance_snapshot",
        }:
            continue
        if key in INTEGER_FIELDS:
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                raise TransferInvalidError()
        elif key == "created_at" and table in SAVE_TABLES:
            if type(value) not in (float, int) or not math.isfinite(value):
                raise TransferInvalidError()
        elif not isinstance(value, str):
            raise TransferInvalidError()
    return values


def _records(payload: Mapping[str, Any], tables: Mapping[str, str]) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    for table, columns in tables.items():
        if not isinstance(payload[table], list):
            raise TransferInvalidError()
        records[table] = [_validated(row, table, columns) for row in payload[table]]
    return records


def _insert(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
    conn.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        tuple(row.values()),
    )


def _remap(row: Mapping[str, Any], table: str, ids: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    return {
        key: ids[table][value]
        if key == "id"
        else ids[ID_FIELDS[key]][value]
        if key in ID_FIELDS and value is not None
        else value
        for key, value in row.items()
    }


def _copy_settings(
    conn: sqlite3.Connection,
    settings: Mapping[str, Any],
    world_id: str,
    records: Mapping[str, list[dict[str, Any]]],
    ids: Mapping[str, Mapping[str, str]],
) -> None:
    world = _validated(settings["world"], "worlds", WORLD_COLUMNS)
    if not 1 <= len(world["name"].strip()) <= 40:
        raise TransferInvalidError()
    _insert(conn, "worlds", {**world, "id": world_id})
    texts = {row["id"]: row["text"].encode("utf-16-le") for row in records["documents"]}
    chunks = {row["id"]: row for row in records["chunks"]}
    for table, rows in records.items():
        for original in rows:
            row = _remap(original, table, ids)
            if table == "world_openings":
                row["opening_json"] = json.dumps(
                    scenario.normalize(json.loads(row["opening_json"])),
                    ensure_ascii=False,
                )
            if table == "documents" and not isinstance(json.loads(row["warnings"] or "[]"), list):
                raise TransferInvalidError()
            if table == "chunks":
                headings = json.loads(row["heading_path"])
                if not isinstance(headings, list) or not all(isinstance(h, str) for h in headings):
                    raise TransferInvalidError()
                start, end = row["start_char"] * 2, row["end_char"] * 2
                text = texts[original["document_id"]]
                if not 0 <= start < end <= len(text):
                    raise TransferInvalidError()
                row["text"] = text[start:end].decode("utf-16-le")
            if table == "lore_entry_sources" and chunks[original["chunk_id"]]["document_id"] != original["document_id"]:
                raise TransferInvalidError()
            _insert(conn, table, {**row, "world_id": world_id})


def _remap_result(result: str, ids: dict[str, dict[str, str]], opening: Any) -> str:
    def reference(kind: str, value: Any) -> str:
        if not isinstance(value, str):
            raise TransferInvalidError()
        return ids[kind].setdefault(value, str(uuid4()))

    def source_id(value: str) -> str:
        kind, _, original = value.partition(":")
        table = {"entry": "lore_entries", "chunk": "chunks"}[kind]
        return f"{kind}:{reference(table, original)}"

    parsed = json.loads(result)
    if (
        not isinstance(parsed["narrative"], str)
        or not isinstance(parsed["sources"], list)
        or not isinstance(parsed["references"], list)
    ):
        raise TransferInvalidError()
    suggestions = parsed.get("suggestions", [])
    if not isinstance(suggestions, list) or not all(isinstance(item, str) for item in suggestions):
        raise TransferInvalidError()
    for key in ("state_before", "state_after", "projected_state"):
        if parsed.get(key) is not None:
            character(parsed[key])
    if "clock" in parsed:
        clock = parsed["clock"]
        before = parsed.get("state_before")
        after = parsed.get("state_after")
        if opening is None or not before or not after or not isinstance(clock, dict):
            raise TransferInvalidError()
        minutes = clock.get("requested_minutes")
        if type(minutes) is not int or not 1 <= minutes <= scenario.MAX_ADVANCE:
            raise TransferInvalidError()
        try:
            expected = scenario.plan(opening, before["elapsed_minutes"], minutes)
        except ApiError:
            raise TransferInvalidError() from None
        if expected != clock or before["elapsed_minutes"] + clock["elapsed_minutes"] != after["elapsed_minutes"]:
            raise TransferInvalidError()
    for source in parsed["sources"]:
        source["source_id"] = source_id(source["source_id"])
        for field in ("entry_id", "document_id", "chunk_id"):
            if source.get(field) is not None:
                source[field] = reference(ID_FIELDS[field], source[field])
    parsed["references"] = [source_id(value) for value in parsed["references"]]
    return json.dumps(parsed, ensure_ascii=False, allow_nan=False)


def _restore_instance_with_historical_support(
    conn: sqlite3.Connection,
    snapshot: Mapping[str, Any],
    world_id: str,
    source_world_id: str,
    active_source_map: Mapping[str, str],
    historical_id_registry: dict[str, str],
    inherited_tombstones: Mapping[str, HistoricalSourceRef] | None = None,
    adventure_id: str | None = None,
) -> tuple[str, list[HistoricalSourceRef]]:
    root = snapshot["instance"]
    minute, revision, canon = root["world_minute"], root["world_revision"], root["canon_version"]
    if (
        type(snapshot["version"]) is not int
        or snapshot["version"] != instance_service.SNAPSHOT_VERSION
        or root["world_id"] != source_world_id
        or any(type(value) is not int or not 0 <= value < 2**53 for value in (minute, revision))
        or (canon is not None and not isinstance(canon, str))
    ):
        raise TransferInvalidError()

    domains = snapshot["domains"]
    registered_domain_names = {getattr(a, "name", "") for a in core_adapters.adapters()} | {"events", "knowledge", "npcs"}
    if not isinstance(domains, dict) or set(domains) - registered_domain_names:
        raise TransferInvalidError()

    target = instance_service.create_instance(
        conn,
        world_id,
        adventure_id=adventure_id,
        world_minute=minute,
        world_revision=revision,
        canon_version=canon,
    )
    instance_id = target["id"]
    persisted_tombstones: list[HistoricalSourceRef] = []

    try:
        combined_map = dict(active_source_map)
        if "events" in domains:
            ev_domain = domains["events"]
            if ev_domain["world_minute"] != minute:
                raise TransferInvalidError()
            combined_map, allowed_for_restore, persisted_tombstones = (
                PortabilitySnapshotAdapter.validate_and_prepare_historical_sources(
                    conn,
                    events_snap=ev_domain,
                    target_world_id=world_id,
                    source_world_id=source_world_id,
                    active_source_map=active_source_map,
                    historical_id_registry=historical_id_registry,
                    inherited_tombstones=inherited_tombstones,
                )
            )
            world_events.restore(
                conn,
                instance_id,
                ev_domain,
                source_id_map=combined_map,
                allowed_source_ids=allowed_for_restore,
            )
            if persisted_tombstones:
                PortabilitySnapshotAdapter.persist_historical_tombstones(
                    conn, world_id, persisted_tombstones
                )

        if "knowledge" in domains:
            world_knowledge.restore(conn, instance_id, domains["knowledge"])

        # 支持其他已注册模块适配器（如 Marcin 的 npcs）
        for domain_name, domain_payload in sorted(domains.items()):
            if domain_name in ("events", "knowledge"):
                continue
            adapter = core_adapters.get_adapter(domain_name)
            if adapter is not None:
                adapter.restore(
                    conn,
                    domain_payload,
                    new_instance_id=instance_id,
                    world_id=world_id,
                )
    except PortabilityError:
        raise
    except ApiError as exc:
        raise TransferInvalidError() from exc

    return instance_id, persisted_tombstones


def _restore_checkpoint_snapshot(
    conn: sqlite3.Connection,
    raw: str,
    world_id: str,
    source_world_id: str,
    active_source_map: Mapping[str, str],
    historical_id_registry: dict[str, str],
    inherited_tombstones: Mapping[str, HistoricalSourceRef] | None = None,
) -> tuple[str, list[HistoricalSourceRef]]:
    conn.execute("SAVEPOINT validate_m3_checkpoint")
    try:
        instance_id, tombstones = _restore_instance_with_historical_support(
            conn,
            json.loads(raw),
            world_id,
            source_world_id,
            active_source_map,
            historical_id_registry,
            inherited_tombstones,
        )
        remapped_json = json.dumps(
            PortabilitySnapshotAdapter.build_instance_snapshot(conn, instance_id),
            ensure_ascii=False,
        )
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT validate_m3_checkpoint")
        conn.execute("RELEASE SAVEPOINT validate_m3_checkpoint")

    # SAVEPOINT 回滚了临时实例表记录，现将验证通过的历史墓碑持久化到目标世界的墓碑表中
    if tombstones:
        PortabilitySnapshotAdapter.persist_historical_tombstones(conn, world_id, tombstones)
    return remapped_json, tombstones


def _copy_saves(
    conn: sqlite3.Connection,
    world_id: str,
    records: Mapping[str, list[dict[str, Any]]],
    ids: dict[str, dict[str, str]],
    source_world_id: str,
    active_source_map: Mapping[str, str],
    historical_id_registry: dict[str, str],
    inherited_tombstones: Mapping[str, HistoricalSourceRef] | None = None,
) -> None:
    runs = {run["id"]: run for run in records["adventures"]}
    openings = {
        run_id: scenario.normalize(json.loads(run["opening_json"])) if run["opening_json"] else None
        for run_id, run in runs.items()
    }
    histories: dict[str, list[int]] = {run_id: [] for run_id in runs}
    for row in records["adventure_turns"]:
        histories[row["adventure_id"]].append(row["sequence"])
    for run_id, run in runs.items():
        sequence = sorted(histories[run_id])
        if len(sequence) != run["revision"] or any(n != i for i, n in enumerate(sequence, 1)):
            raise TransferInvalidError()
    memory: dict[str, list[dict[str, Any]]] = {run_id: [] for run_id in runs}
    for event in records["adventure_memory_events"]:
        memory[event["adventure_id"]].append(event)
    for run_id, events in memory.items():
        last_turn = 0
        for sequence, event in enumerate(sorted(events, key=lambda item: item["sequence"]), 1):
            if (
                event["sequence"] != sequence
                or not last_turn <= event["turn"] <= runs[run_id]["revision"]
                or event["origin"] not in ("turn", "correction")
                or (event["origin"] == "turn" and event["turn"] == 0)
            ):
                raise TransferInvalidError()
            validate_event(json.loads(event["change_json"]))
            last_turn = event["turn"]
        events.sort(key=lambda item: item["sequence"])

    for table, rows in records.items():
        for original in rows:
            row = _remap(original, table, ids)
            if table == "adventures":
                row["world_id"] = world_id
                if openings[original["id"]]:
                    if not row["character_json"] or json.loads(row["character_json"]) is None:
                        raise TransferInvalidError()
                    row["opening_json"] = json.dumps(openings[original["id"]], ensure_ascii=False)
            if table in ("adventures", "adventure_checkpoints"):
                state = json.loads(row["character_json"] or "null")
                if state is not None:
                    character(state)
                if row["mode"] not in {"fake", "live"}:
                    raise TransferInvalidError()
            if table == "adventure_turns":
                row["result"] = _remap_result(
                    row["result"],
                    ids,
                    openings[original["adventure_id"]],
                )
            if table == "adventure_checkpoints":
                run_id = original["adventure_id"]
                cursor = row["memory_cursor"]
                if (
                    row["revision"] > runs[run_id]["revision"]
                    or cursor > len(memory[run_id])
                    or (cursor and memory[run_id][cursor - 1]["turn"] > row["revision"])
                ):
                    raise TransferInvalidError()
                row["request_id"] = row["id"]
                if raw := row.get("instance_snapshot"):
                    row["instance_snapshot"], _ = _restore_checkpoint_snapshot(
                        conn,
                        raw,
                        world_id,
                        source_world_id,
                        active_source_map,
                        historical_id_registry,
                        inherited_tombstones,
                    )
            _insert(conn, table, row)


def _local_digest(conn: sqlite3.Connection, world: Mapping[str, Any]) -> str:
    with TemporaryFile() as file:
        _write_json(file, _world_payload(conn, world))
        return _digest(file)


def _existing_settings(
    conn: sqlite3.Connection,
    source_id: str,
    digest: str,
) -> tuple[dict[str, Any], dict[str, dict[str, str]] | None, str]:
    candidates = conn.execute(
        """
        SELECT w.*, NULL AS local_digest, NULL AS id_map FROM worlds w
        WHERE w.id=? AND w.deleted_at IS NULL
        UNION ALL
        SELECT w.*, p.local_digest, p.id_map FROM worlds w
        JOIN world_transfer_sources p ON p.world_id=w.id
        WHERE p.source_id=? AND p.source_digest=? AND w.deleted_at IS NULL
        """,
        (source_id, source_id, digest),
    )
    for world in candidates:
        if _local_digest(conn, world) == (world["local_digest"] or digest):
            settings = _world_payload(conn, world)
            for table in WORLD_TABLES:
                settings[table] = list(settings.get(table, ()))
            return (
                settings,
                json.loads(world["id_map"]) if world["id_map"] else None,
                str(world["id"]),
            )
    raise TransferSettingsRequiredError()


def import_transfer(
    conn: sqlite3.Connection,
    uploads: list[Any],
    max_documents: int = 50,
) -> str:
    """兼容公共路由 `POST /api/v1/world-transfers` 的完整导入实现。

    - 支持 v1–v4 世界观与存档读取、缺失历史来源（已删除资料）墓碑迁移、伪造引用与跨世界引用拦截、
      同一世界二次导入、新格式回导、检查点分支续玩与异常整体回滚。
    """
    ensure_portability_schema(conn)
    if not 1 <= len(uploads) <= 2:
        raise TransferInvalidError()
    if sum(getattr(file, "size", 0) or 0 for file in uploads) > TRANSFER_BYTE_LIMIT:
        raise TransferLimitExceededError()

    try:
        parts = _read_files([file.file if hasattr(file, "file") else file for file in uploads])
        settings_part = parts.get(SETTINGS_FORMAT)
        save_part = parts.get(SAVES_FORMAT)
        saves = save_part[0] if save_part else None
        previous_ids: dict[str, dict[str, str]] | None = None
        matched_local_world_id: str | None = None

        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

        savepoint_name = f"sp_m3_portability_import_{uuid4().hex[:8]}"
        conn.execute(f"SAVEPOINT {savepoint_name}")
        try:
            if settings_part:
                settings, digest = settings_part
                source_id = settings["world"]["id"]
                if saves is not None and (
                    saves["settings_sha256"] != digest or saves["world_id"] != source_id
                ):
                    raise TransferMismatchError()
            else:
                if saves is None:
                    raise TransferInvalidError()
                source_id, digest = saves["world_id"], saves["settings_sha256"]
                settings, previous_ids, matched_local_world_id = _existing_settings(
                    conn, source_id, digest
                )

            if settings["version"] == VERSION:
                settings["world_openings"] = []
            world_records = _records(settings, WORLD_TABLES)
            if len(world_records["world_openings"]) > scenario.MAX_OPENINGS:
                raise TransferInvalidError()
            if len(world_records["documents"]) > max_documents:
                raise ApiError(
                    422,
                    "WORLD_DOCUMENT_LIMIT",
                    "这份世界观的文档数超出当前部署上限。",
                    {"limit": max_documents},
                )

            if saves is not None and saves["version"] == VERSION:
                saves["adventure_memory_events"] = []
                for checkpoint in saves["adventure_checkpoints"]:
                    checkpoint["memory_cursor"] = 0
            if saves is not None and saves["version"] < SAVES_OPENINGS_VERSION:
                for run in saves["adventures"]:
                    run["opening_json"] = None

            save_tables = SAVE_TABLES
            portable_instances: dict[str, Any] = {}
            if saves is not None and saves["version"] == SAVES_INSTANCES_VERSION:
                save_tables = {
                    **SAVE_TABLES,
                    "adventure_checkpoints": SAVE_TABLES["adventure_checkpoints"] + " instance_snapshot",
                }
                if not isinstance(saves.get("world_instances", []), list):
                    raise TransferInvalidError()
                for item in saves.get("world_instances", []):
                    adventure_id = item["adventure_id"]
                    if not isinstance(adventure_id, str) or adventure_id in portable_instances:
                        raise TransferInvalidError()
                    portable_instances[adventure_id] = item["snapshot"]

            save_records = _records(saves, save_tables) if saves is not None else {}
            ids: dict[str, dict[str, str]] = {
                table: {row["id"]: str(uuid4()) for row in rows if "id" in row}
                for table, rows in {**world_records, **save_records}.items()
            }
            world_id = str(uuid4())
            _copy_settings(conn, settings, world_id, world_records, ids)

            source_ids: dict[str, dict[str, str]] = {
                table: ids[table].copy() for table in ("documents", "chunks", "lore_entries")
            }
            inherited_tombstones: dict[str, HistoricalSourceRef] = {}
            if previous_ids is not None:
                source_ids = {
                    table: {
                        original: ids[table][local]
                        for original, local in mapping.items()
                        if local in ids.get(table, {})
                    }
                    for table, mapping in previous_ids.items()
                    if table in ("documents", "chunks", "lore_entries")
                }
                # 若此前单独导入过世界或存档，继承已登记的历史墓碑映射
                if "historical_tombstones" in previous_ids and isinstance(
                    previous_ids["historical_tombstones"], Mapping
                ):
                    for orig_sid, t_data in previous_ids["historical_tombstones"].items():
                        if isinstance(t_data, Mapping):
                            inherited_tombstones[str(orig_sid)] = HistoricalSourceRef.from_mapping(t_data)
                if matched_local_world_id:
                    local_cat = discover_world_source_catalog(conn, matched_local_world_id)
                    for sid, ref_obj in local_cat["deleted_historical_sources"].items():
                        inherited_tombstones.setdefault(ref_obj.original_source_id, ref_obj)
                        inherited_tombstones.setdefault(sid, ref_obj)

            historical_id_registry: dict[str, str] = {}
            if saves is not None:
                if set(portable_instances) - ids["adventures"].keys():
                    raise TransferInvalidError()
                active_source_map = {
                    f"{prefix}:{old}": f"{prefix}:{new}"
                    for table, prefix in (("chunks", "chunk"), ("lore_entries", "entry"))
                    for old, new in source_ids[table].items()
                }
                # 为历史回合叙事 sources/references 重映射预热已声明的墓碑 UUID，使回合引用与事件引用保持一致
                for inst_snap in portable_instances.values():
                    ev_dom = (inst_snap.get("domains") or {}).get("events") or {}
                    for h_item in ev_dom.get("historical_sources") or ():
                        if isinstance(h_item, Mapping) and h_item.get("source_id"):
                            h_sid = str(h_item["source_id"])
                            h_kind, _, h_raw = h_sid.partition(":")
                            h_table = "chunks" if h_kind == "chunk" else "lore_entries"
                            mapped_uuid = source_ids[h_table].setdefault(h_raw, str(uuid4()))
                            historical_id_registry[h_sid] = f"{h_kind}:{mapped_uuid}"

                _copy_saves(
                    conn,
                    world_id,
                    save_records,
                    {**ids, **source_ids},
                    source_id,
                    active_source_map,
                    historical_id_registry,
                    inherited_tombstones,
                )
                for run in save_records["adventures"]:
                    original = run["id"]
                    new_id = ids["adventures"][original]
                    if original in portable_instances:
                        _restore_instance_with_historical_support(
                            conn,
                            portable_instances[original],
                            world_id,
                            source_id,
                            active_source_map,
                            historical_id_registry,
                            inherited_tombstones,
                            new_id,
                        )
                    else:
                        instance_service.ensure_for_adventure(conn, new_id)

            world_row = conn.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
            persisted_tombstones_map = {
                item["original_source_id"]: item
                for item in list_historical_source_refs(conn, world_id)
            }
            stored_id_map = {
                **source_ids,
                **(
                    {"historical_tombstones": persisted_tombstones_map}
                    if persisted_tombstones_map
                    else {}
                ),
            }
            _insert(
                conn,
                "world_transfer_sources",
                {
                    "world_id": world_id,
                    "source_id": source_id,
                    "source_digest": digest,
                    "local_digest": _local_digest(conn, world_row),
                    "id_map": json.dumps(stored_id_map, ensure_ascii=False),
                },
            )
            conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            return world_id
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            raise
    except ApiError:
        raise
    except (
        BadZipFile,
        zlib.error,
        ValueError,
        sqlite3.IntegrityError,
        RuntimeError,
        TypeError,
        KeyError,
        AttributeError,
        OverflowError,
    ) as exc:
        raise TransferInvalidError() from exc
