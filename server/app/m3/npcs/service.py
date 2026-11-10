"""M3.4 NPC 自主行动、行程、动态模拟深度与快照/恢复持久层服务（M3 Wave 3）。

契约要求（`docs/collaboration/M3_WAVE3.md` & `docs/collaboration/M3_WAVE3_GEMINI_MARCIN.md`）：
1. 四个核心接口：
   - 只读选取：`read_snapshot(conn, instance_id, job=None, ...)` 与 `select_npcs_for_pulse(conn, instance_id, ...)`
   - 纯校验：`validate_proposal(proposal, context)`（由 `validator.py` 提供）
   - 调用方事务内应用：`apply_proposal(conn, instance_id, validated, job=None, ...)`（`SAVEPOINT` 保护，不自行 `commit`）
   - 快照与恢复：`snapshot(conn, instance_id)` / `restore(conn, target_instance_id, snapshot, ...)` 及 `NpcsAdapter`
2. 时间来自已确认世界分钟（`world_instances.world_minute`），事件影响必须引用实例内已存在的原因。
3. 复用现有事件、角色、知识的边界，禁止直接记玩家金钱、背包、属性和知情事实。
4. 提供与脉动（`PulseWorker`）及 Ma 互动包（`check_npc_accessibility` / `list_contactable_npcs`）的最小接线契约。
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import sqlite3
from typing import Any
from uuid import uuid4

from .errors import (
    CrossInstanceReferenceError,
    DuplicateNpcTaskError,
    InstanceNotFoundError,
    InvalidCausalReferenceError,
    NpcNotDueError,
    NpcNotFoundError,
    NpcRevisionConflictError,
    NpcValidationError,
    SnapshotRestoreError,
    SourceFabricatedError,
    WorldRevisionConflictError,
)
from .schema import ensure_npcs_schema
from .types import (
    DepthBudget,
    NPC_ACTION_KINDS,
    NPC_AVAILABILITY_STATES,
    NPC_DEPTH_TIER_ALIASES,
    NPC_DEPTH_TIER_LABELS_ZH,
    NPC_STATUSES,
    SNAPSHOT_FORMAT_NAME,
    SNAPSHOT_FORMAT_VERSION,
    TASK_STATUSES,
    NpcValidationContext,
    ValidatedNpcActionOp,
    ValidatedNpcProposal,
)
from .validator import (
    _require_non_empty_str,
    _require_strict_int,
    evaluate_npc_depths,
    normalize_depth_tier,
    validate_proposal,
)


def _default_action_id() -> str:
    return f"nact_{uuid4().hex[:12]}"


def ensure_instance_exists(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_id: str = "",
    adventure_id: str | None = None,
    world_minute: int = 0,
    revision: int = 0,
) -> dict[str, Any]:
    """确保 `world_instances` 中存在指定世界实例（供独立测试或 Fixture 初始化使用）。"""
    ensure_npcs_schema(conn)
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
    rev = int(row[4] or 0)
    return {
        "id": str(row[0]),
        "world_id": str(row[1] or ""),
        "adventure_id": row[2],
        "world_minute": int(row[3] or 0),
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
        ensure_npcs_schema(conn)
    else:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "m3_npcs" not in tables:
            ensure_npcs_schema(conn)
    instance_id = _require_non_empty_str(instance_id, "instance_id", 120)
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(world_instances)").fetchall()}
    rev_col = "revision" if "revision" in cols else ("world_revision" if "world_revision" in cols else "0")
    row = conn.execute(
        f"SELECT id, world_id, adventure_id, world_minute, {rev_col} FROM world_instances WHERE id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise InstanceNotFoundError(instance_id)
    rev = int(row[4] or 0)
    return {
        "id": str(row[0]),
        "world_id": str(row[1] or ""),
        "adventure_id": row[2],
        "world_minute": int(row[3] or 0),
        "revision": rev,
        "world_revision": rev,
    }


def _row_to_npc_dict(
    row: sqlite3.Row | tuple[Any, ...],
    tasks_by_npc: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        d = dict(row)
    else:
        (
            instance_id,
            npc_id,
            name,
            role,
            faction,
            home_location,
            location,
            origin_location,
            destination_location,
            current_activity,
            current_action_kind,
            motivation,
            goal,
            depth_tier,
            depth_reason,
            shift_name,
            availability,
            counterpart_npc_id,
            conversation_topic,
            current_task_id,
            cause_event_ids_json,
            cause_fact_ids_json,
            source_ids_json,
            action_meta_json,
            status,
            created_minute,
            updated_minute,
            last_action_minute,
            next_due_minute,
            revision,
        ) = row
        d = {
            "instance_id": instance_id,
            "npc_id": npc_id,
            "name": name,
            "role": role,
            "faction": faction,
            "home_location": home_location,
            "location": location,
            "origin_location": origin_location,
            "destination_location": destination_location,
            "current_activity": current_activity,
            "current_action_kind": current_action_kind,
            "motivation": motivation,
            "goal": goal,
            "depth_tier": depth_tier,
            "depth_reason": depth_reason,
            "shift_name": shift_name,
            "availability": availability,
            "counterpart_npc_id": counterpart_npc_id,
            "conversation_topic": conversation_topic,
            "current_task_id": current_task_id,
            "cause_event_ids_json": cause_event_ids_json,
            "cause_fact_ids_json": cause_fact_ids_json,
            "source_ids_json": source_ids_json,
            "action_meta_json": action_meta_json,
            "status": status,
            "created_minute": created_minute,
            "updated_minute": updated_minute,
            "last_action_minute": last_action_minute,
            "next_due_minute": next_due_minute,
            "revision": revision,
        }

    next_due = int(d["next_due_minute"]) if d["next_due_minute"] is not None else None
    tier = str(d["depth_tier"] or "background")
    npc_id = str(d["npc_id"])
    record: dict[str, Any] = {
        "instance_id": str(d["instance_id"]),
        "npc_id": npc_id,
        "name": str(d["name"]),
        "role": str(d["role"]),
        "identity": str(d["role"]),
        "faction": str(d.get("faction") or ""),
        "home_location": str(d.get("home_location") or d["location"]),
        "location": str(d["location"]),
        "origin_location": str(d.get("origin_location") or ""),
        "destination_location": str(d.get("destination_location") or ""),
        "current_activity": str(d["current_activity"]),
        "current_action_kind": str(d["current_action_kind"]),
        "action_kind": str(d["current_action_kind"]),
        "motivation": str(d["motivation"]),
        "goal": str(d.get("goal") or ""),
        "depth_tier": tier,
        "tier_label_zh": NPC_DEPTH_TIER_LABELS_ZH.get(tier, "背景"),
        "depth_reason": str(d.get("depth_reason") or ""),
        "shift_name": str(d.get("shift_name") or ""),
        "availability": str(d.get("availability") or "available"),
        "counterpart_npc_id": str(d.get("counterpart_npc_id") or ""),
        "conversation_topic": str(d.get("conversation_topic") or ""),
        "current_task_id": str(d.get("current_task_id") or ""),
        "cause_event_ids": json.loads(d.get("cause_event_ids_json") or "[]"),
        "cause_fact_ids": json.loads(d.get("cause_fact_ids_json") or "[]"),
        "source_ids": json.loads(d.get("source_ids_json") or "[]"),
        "action_meta": json.loads(d.get("action_meta_json") or "{}"),
        "status": str(d.get("status") or "active"),
        "created_minute": int(d["created_minute"]),
        "updated_minute": int(d["updated_minute"]),
        "last_action_minute": int(d.get("last_action_minute") or d["updated_minute"]),
        "next_due_minute": next_due,
        "next_update_minute": next_due,
        "revision": int(d["revision"]),
    }
    if tasks_by_npc is not None:
        record["tasks"] = list(tasks_by_npc.get(npc_id, ()))
    return record


def _load_tasks_map(
    conn: sqlite3.Connection, instance_id: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str], dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT instance_id, npc_id, task_id, title, stage, progress_percent, status,
               motivation, cause_event_ids_json, cause_fact_ids_json, source_ids_json,
               created_minute, updated_minute, revision
        FROM m3_npc_tasks
        WHERE instance_id = ?
        ORDER BY npc_id ASC, created_minute ASC, task_id ASC
        """,
        (instance_id,),
    ).fetchall()
    by_npc: dict[str, list[dict[str, Any]]] = {}
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        item = {
            "instance_id": str(r[0]),
            "npc_id": str(r[1]),
            "task_id": str(r[2]),
            "title": str(r[3]),
            "stage": str(r[4]),
            "progress_percent": int(r[5]),
            "status": str(r[6]),
            "motivation": str(r[7] or ""),
            "cause_event_ids": json.loads(r[8] or "[]"),
            "cause_fact_ids": json.loads(r[9] or "[]"),
            "source_ids": json.loads(r[10] or "[]"),
            "created_minute": int(r[11]),
            "updated_minute": int(r[12]),
            "revision": int(r[13]),
        }
        by_npc.setdefault(item["npc_id"], []).append(item)
        by_key[(item["npc_id"], item["task_id"])] = item
    return by_npc, by_key


def _discover_instance_events(
    conn: sqlite3.Connection,
    instance_id: str,
    world_minute: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], frozenset[str]]:
    """从同库 `world_events`、`m3_knowledge_event_stubs` 或 `m3_npc_event_stubs` 读取本实例已存在事件。"""
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    events_by_id: dict[str, dict[str, Any]] = {}

    if "world_events" in tables:
        rows = conn.execute(
            """
            SELECT event_id, kind, title, description, location, scope,
                   participants_json, stage, status, created_minute, updated_minute,
                   next_update_minute, revision
            FROM world_events
            WHERE instance_id = ?
            ORDER BY created_minute ASC, event_id ASC
            """,
            (instance_id,),
        ).fetchall()
        for r in rows:
            eid = str(r[0])
            events_by_id[eid] = {
                "event_id": eid,
                "kind": str(r[1]),
                "title": str(r[2]),
                "description": str(r[3]),
                "location": str(r[4]),
                "scope": str(r[5]),
                "participants": json.loads(r[6] or "[]"),
                "stage": str(r[7]),
                "status": str(r[8]),
                "created_minute": int(r[9]),
                "updated_minute": int(r[10]),
                "next_update_minute": int(r[11]) if r[11] is not None else None,
                "revision": int(r[12]),
            }

    for stub_table in ("m3_npc_event_stubs", "m3_knowledge_event_stubs"):
        if stub_table in tables:
            rows = conn.execute(
                f"""
                SELECT event_id, kind, title, description, location, scope,
                       participants_json, stage, status, created_minute, updated_minute,
                       next_update_minute, revision
                FROM {stub_table}
                WHERE instance_id = ?
                ORDER BY created_minute ASC, event_id ASC
                """,
                (instance_id,),
            ).fetchall()
            for r in rows:
                eid = str(r[0])
                events_by_id.setdefault(
                    eid,
                    {
                        "event_id": eid,
                        "kind": str(r[1]),
                        "title": str(r[2]),
                        "description": str(r[3]),
                        "location": str(r[4]),
                        "scope": str(r[5]),
                        "participants": json.loads(r[6] or "[]"),
                        "stage": str(r[7]),
                        "status": str(r[8]),
                        "created_minute": int(r[9]),
                        "updated_minute": int(r[10]),
                        "next_update_minute": int(r[11]) if r[11] is not None else None,
                        "revision": int(r[12]),
                    },
                )

    all_events = list(events_by_id.values())
    active_events = [ev for ev in all_events if ev.get("status") == "active"]
    due_events = [
        ev
        for ev in active_events
        if ev.get("next_update_minute") is not None and int(ev["next_update_minute"]) <= world_minute
    ]
    return due_events, active_events, frozenset(events_by_id.keys())


def _discover_instance_facts(
    conn: sqlite3.Connection,
    instance_id: str,
) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """读取本实例内各 NPC 已获知的事实 ID（兼容 `m3_knowledge_facts` 与独立 `m3_npc_knowledge_stubs`）。"""
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    by_holder: dict[str, set[str]] = {}
    all_facts: set[str] = set()

    for tbl in ("m3_knowledge_facts", "m3_npc_knowledge_stubs"):
        if tbl in tables:
            rows = conn.execute(
                f"""
                SELECT fact_id, holder_id
                FROM {tbl}
                WHERE instance_id = ? AND status = 'active'
                """,
                (instance_id,),
            ).fetchall()
            for r in rows:
                fid = str(r[0]).strip()
                hid = str(r[1]).strip()
                if fid and hid and hid.lower() != "player":
                    by_holder.setdefault(hid, set()).add(fid)
                    all_facts.add(fid)

    return (
        {k: frozenset(v) for k, v in by_holder.items()},
        frozenset(all_facts),
    )


def _discover_canon_source_ids(conn: sqlite3.Connection, world_id: str) -> frozenset[str]:
    if not world_id:
        return frozenset()
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    allowed: set[str] = set()
    if "chunks" in tables and "documents" in tables:
        doc_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        status_sql = "AND (d.status IS NULL OR d.status = 'ready')" if "status" in doc_cols else ""
        rows = conn.execute(
            f"""
            SELECT c.id
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.world_id = ? AND d.deleted_at IS NULL {status_sql}
            """,
            (world_id,),
        ).fetchall()
        for r in rows:
            allowed.add(f"chunk:{r[0]}")
    if "lore_entries" in tables:
        entry_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(lore_entries)").fetchall()}
        status_sql = "AND status = 'confirmed'" if "status" in entry_cols else ""
        rows = conn.execute(
            f"""
            SELECT id FROM lore_entries
            WHERE world_id = ? AND deleted_at IS NULL {status_sql}
            """,
            (world_id,),
        ).fetchall()
        for r in rows:
            allowed.add(f"entry:{r[0]}")
    return frozenset(allowed)


def _discover_other_instance_ids(conn: sqlite3.Connection, instance_id: str) -> frozenset[str]:
    rows = conn.execute(
        "SELECT id FROM world_instances WHERE id != ?",
        (instance_id,),
    ).fetchall()
    return frozenset(str(r[0]) for r in rows if r[0])


def register_event_stub(
    conn: sqlite3.Connection,
    instance_id: str,
    event_id: str,
    *,
    title: str,
    description: str = "",
    location: str = "霜木镇",
    scope: str = "local",
    kind: str = "thread",
    participants: list[str] | tuple[str, ...] = (),
    stage: str = "active",
    status: str = "active",
    created_minute: int = 0,
    updated_minute: int = 0,
    next_update_minute: int | None = None,
    revision: int = 1,
) -> dict[str, Any]:
    """登记独立测试用的事件桩（不直接依赖 `m3/events` 内部实现）。"""
    ensure_npcs_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO m3_npc_event_stubs (
            instance_id, event_id, kind, title, description, location, scope,
            participants_json, stage, status, created_minute, updated_minute,
            next_update_minute, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instance_id,
            event_id,
            kind,
            title,
            description,
            location,
            scope,
            json.dumps(list(participants), ensure_ascii=False),
            stage,
            status,
            created_minute,
            updated_minute,
            next_update_minute,
            revision,
        ),
    )
    return {
        "instance_id": instance_id,
        "event_id": event_id,
        "title": title,
        "location": location,
        "participants": list(participants),
        "status": status,
        "revision": revision,
    }


def register_knowledge_stub(
    conn: sqlite3.Connection,
    instance_id: str,
    fact_id: str,
    *,
    holder_id: str,
    title: str = "",
    content: str = "",
    source_event_id: str = "",
    knowledge_type: str = "confirmed",
    learned_minute: int = 0,
    status: str = "active",
) -> dict[str, Any]:
    """登记独立测试用的 NPC 知情记录桩（禁止登记 player 知情事实）。"""
    if str(holder_id).strip().lower() == "player":
        raise NpcValidationError("PLAYER_KNOWLEDGE_PROTECTED", "NPC 模块严禁直接登记 player 知情记录。")
    ensure_npcs_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO m3_npc_knowledge_stubs (
            instance_id, fact_id, holder_id, knowledge_type, title, content,
            source_event_id, learned_minute, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instance_id,
            fact_id,
            holder_id,
            knowledge_type,
            title,
            content,
            source_event_id,
            learned_minute,
            status,
        ),
    )
    return {
        "instance_id": instance_id,
        "fact_id": fact_id,
        "holder_id": holder_id,
        "source_event_id": source_event_id,
        "learned_minute": learned_minute,
    }


def get_npc(
    conn: sqlite3.Connection,
    instance_id: str,
    npc_id: str,
) -> dict[str, Any]:
    """查询指定世界实例内的单个 NPC 完整持久状态。"""
    _get_instance_row(conn, instance_id, read_only=True)
    npc_id = _require_non_empty_str(npc_id, "npc_id", 100)
    row = conn.execute(
        """
        SELECT instance_id, npc_id, name, role, faction, home_location, location,
               origin_location, destination_location, current_activity, current_action_kind,
               motivation, goal, depth_tier, depth_reason, shift_name, availability,
               counterpart_npc_id, conversation_topic, current_task_id,
               cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
               status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
        FROM m3_npcs
        WHERE instance_id = ? AND npc_id = ?
        """,
        (instance_id, npc_id),
    ).fetchone()
    if row is None:
        raise NpcNotFoundError(instance_id, npc_id)
    tasks_by_npc, _ = _load_tasks_map(conn, instance_id)
    return _row_to_npc_dict(row, tasks_by_npc)


def list_npcs(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    location: str | None = None,
    depth_tier: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """按实例查询 NPC 列表（支持地点、模拟深度档位、状态过滤）。"""
    _get_instance_row(conn, instance_id, read_only=True)
    limit = _require_strict_int(limit, "limit", min_value=1, max_value=200)
    offset = _require_strict_int(offset, "offset", min_value=0)

    clauses = ["instance_id = ?"]
    params: list[Any] = [instance_id]
    if location is not None:
        clauses.append("location = ?")
        params.append(location.strip())
    if depth_tier is not None:
        norm_tier = normalize_depth_tier(depth_tier)
        clauses.append("depth_tier = ?")
        params.append(norm_tier)
    if status is not None:
        clauses.append("status = ?")
        params.append(status.strip())

    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT instance_id, npc_id, name, role, faction, home_location, location,
               origin_location, destination_location, current_activity, current_action_kind,
               motivation, goal, depth_tier, depth_reason, shift_name, availability,
               counterpart_npc_id, conversation_topic, current_task_id,
               cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
               status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
        FROM m3_npcs
        WHERE {where_sql}
        ORDER BY CASE depth_tier
            WHEN 'foreground' THEN 0
            WHEN 'associated' THEN 1
            ELSE 2
        END ASC, npc_id ASC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    tasks_by_npc, _ = _load_tasks_map(conn, instance_id)
    items = [_row_to_npc_dict(r, tasks_by_npc) for r in rows]
    return {"instance_id": instance_id, "items": items, "total": len(items)}


def list_due_npcs(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_minute: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """只读列出当前已确认分钟下已到期（`next_due_minute <= world_minute`）的活跃 NPC。"""
    inst = _get_instance_row(conn, instance_id, read_only=True)
    confirmed_min = int(inst["world_minute"]) if world_minute is None else _require_strict_int(world_minute, "world_minute", min_value=0)
    limit = _require_strict_int(limit, "limit", min_value=1, max_value=100)
    rows = conn.execute(
        """
        SELECT instance_id, npc_id, name, role, faction, home_location, location,
               origin_location, destination_location, current_activity, current_action_kind,
               motivation, goal, depth_tier, depth_reason, shift_name, availability,
               counterpart_npc_id, conversation_topic, current_task_id,
               cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
               status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
        FROM m3_npcs
        WHERE instance_id = ?
          AND status = 'active'
          AND next_due_minute IS NOT NULL
          AND next_due_minute <= ?
        ORDER BY next_due_minute ASC,
                 CASE depth_tier WHEN 'foreground' THEN 0 WHEN 'associated' THEN 1 ELSE 2 END ASC,
                 npc_id ASC
        LIMIT ?
        """,
        (instance_id, confirmed_min, limit),
    ).fetchall()
    tasks_by_npc, _ = _load_tasks_map(conn, instance_id)
    items = [_row_to_npc_dict(r, tasks_by_npc) for r in rows]
    return {
        "instance_id": instance_id,
        "world_minute": confirmed_min,
        "items": items,
    }


def list_npc_tasks(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    npc_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """查询实例内 NPC 任务进度记录。"""
    _get_instance_row(conn, instance_id, read_only=True)
    _, by_key = _load_tasks_map(conn, instance_id)
    items = list(by_key.values())
    if npc_id is not None:
        items = [t for t in items if t["npc_id"] == npc_id]
    if status is not None:
        items = [t for t in items if t["status"] == status]
    return items


def list_npc_action_log(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    npc_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """查询实例内追加式 NPC 行动与升降档历史记录。"""
    _get_instance_row(conn, instance_id, read_only=True)
    limit = _require_strict_int(limit, "limit", min_value=1, max_value=500)
    if npc_id is not None:
        rows = conn.execute(
            """
            SELECT action_seq, action_id, npc_id, npc_revision, op, action_kind,
                   from_location, to_location, current_activity, motivation,
                   depth_tier, depth_reason, world_minute, next_due_minute,
                   cause_event_ids_json, cause_fact_ids_json, source_ids_json, details_json
            FROM m3_npc_action_log
            WHERE instance_id = ? AND npc_id = ?
            ORDER BY action_seq ASC
            LIMIT ?
            """,
            (instance_id, npc_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT action_seq, action_id, npc_id, npc_revision, op, action_kind,
                   from_location, to_location, current_activity, motivation,
                   depth_tier, depth_reason, world_minute, next_due_minute,
                   cause_event_ids_json, cause_fact_ids_json, source_ids_json, details_json
            FROM m3_npc_action_log
            WHERE instance_id = ?
            ORDER BY action_seq ASC
            LIMIT ?
            """,
            (instance_id, limit),
        ).fetchall()
    return [
        {
            "instance_id": instance_id,
            "action_seq": int(r[0]),
            "action_id": str(r[1]),
            "npc_id": str(r[2]),
            "npc_revision": int(r[3]),
            "op": str(r[4]),
            "action_kind": str(r[5]),
            "from_location": str(r[6]),
            "to_location": str(r[7]),
            "current_activity": str(r[8]),
            "motivation": str(r[9]),
            "depth_tier": str(r[10]),
            "depth_reason": str(r[11]),
            "world_minute": int(r[12]),
            "next_due_minute": int(r[13]) if r[13] is not None else None,
            "cause_event_ids": json.loads(r[14] or "[]"),
            "cause_fact_ids": json.loads(r[15] or "[]"),
            "source_ids": json.loads(r[16] or "[]"),
            "details": json.loads(r[17] or "{}"),
        }
        for r in rows
    ]


def check_npc_accessibility(
    conn: sqlite3.Connection,
    instance_id: str,
    npc_id: str,
    *,
    player_location: str,
    world_minute: int | None = None,
) -> dict[str, Any]:
    """供 Ma 玩家主动查证/交谈模块（`encounters`）调用的只读 NPC 可接触性核验边界。

    核对实例、地点、行程状态（是否途中离开 `in_transit`、是否已前往其它地点、是否下班 `off_duty`）与已确认世界时间，
    返回结构化可接触性结果（不含隐藏事件或未获知事实）。
    """
    inst = _get_instance_row(conn, instance_id, read_only=True)
    confirmed_min = int(inst["world_minute"])
    if world_minute is not None:
        req_min = _require_strict_int(world_minute, "world_minute", min_value=0)
        if req_min > confirmed_min:
            raise NpcValidationError(
                "UNCONFIRMED_WORLD_MINUTE",
                f"请求的交谈时间 ({req_min}) 超过当前实例已确认世界分钟 ({confirmed_min})。",
                {"confirmed_world_minute": confirmed_min, "requested_world_minute": req_min},
            )
    else:
        req_min = confirmed_min

    clean_player_loc = _require_non_empty_str(player_location, "player_location", 100)
    rows = conn.execute(
        """
        SELECT instance_id, npc_id, name, role, faction, home_location, location,
               origin_location, destination_location, current_activity, current_action_kind,
               motivation, goal, depth_tier, depth_reason, shift_name, availability,
               counterpart_npc_id, conversation_topic, current_task_id,
               cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
               status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
        FROM m3_npcs
        WHERE instance_id = ? AND npc_id = ?
        """,
        (instance_id, npc_id.strip()),
    ).fetchone()

    if rows is None:
        return {
            "instance_id": instance_id,
            "npc_id": npc_id,
            "player_location": clean_player_loc,
            "world_minute": req_min,
            "contactable": False,
            "accessible": False,
            "availability": "unavailable",
            "reason_code": "NPC_NOT_FOUND",
            "reason_message": "该人物在当前世界实例中不存在。",
        }

    npc = _row_to_npc_dict(rows)
    npc_loc = npc["location"]
    avail = npc["availability"]
    status = npc["status"]

    if status != "active":
        contactable = False
        reason_code = "NPC_INACTIVE"
        reason_msg = f"{npc['name']}当前不在活跃状态。"
    elif avail == "in_transit" and npc["origin_location"] == clean_player_loc:
        contactable = False
        reason_code = "NPC_DEPARTED_IN_TRANSIT"
        reason_msg = f"{npc['name']}已离开{clean_player_loc}，正在前往{npc['destination_location'] or npc_loc}途中。"
    elif npc_loc != clean_player_loc:
        contactable = False
        reason_code = "NPC_AT_DIFFERENT_LOCATION"
        reason_msg = f"{npc['name']}当前位于{npc_loc}，不在{clean_player_loc}。"
    elif avail == "in_transit":
        contactable = False
        reason_code = "NPC_IN_TRANSIT"
        reason_msg = f"{npc['name']}尚在旅途之中，预计第 {npc['next_due_minute']} 分钟后抵达{npc_loc}。"
    elif avail == "off_duty":
        contactable = False
        reason_code = "NPC_OFF_DUTY"
        reason_msg = f"{npc['name']}当前已换班歇业（{npc['shift_name'] or '休息中'}），暂不便接待交谈。"
    else:
        contactable = True
        reason_code = "OK"
        reason_msg = f"{npc['name']}当前在{npc_loc}（{npc['current_activity']}），可以发起交谈。"

    return {
        "instance_id": instance_id,
        "npc_id": npc["npc_id"],
        "name": npc["name"],
        "role": npc["role"],
        "identity": npc["identity"],
        "location": npc_loc,
        "origin_location": npc["origin_location"],
        "destination_location": npc["destination_location"],
        "player_location": clean_player_loc,
        "world_minute": req_min,
        "contactable": contactable,
        "accessible": contactable,
        "availability": avail,
        "shift_name": npc["shift_name"],
        "current_activity": npc["current_activity"],
        "current_action_kind": npc["current_action_kind"],
        "next_due_minute": npc["next_due_minute"],
        "revision": npc["revision"],
        "reason_code": reason_code,
        "reason_message": reason_msg,
    }


def list_contactable_npcs(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    player_location: str,
    world_minute: int | None = None,
    include_unavailable_at_location: bool = False,
) -> dict[str, Any]:
    """供 Ma 玩家主动查证/交谈模块列出当前地点可接触的 NPC 列表。"""
    inst = _get_instance_row(conn, instance_id, read_only=True)
    confirmed_min = int(inst["world_minute"]) if world_minute is None else _require_strict_int(world_minute, "world_minute", min_value=0)
    clean_loc = _require_non_empty_str(player_location, "player_location", 100)
    all_npcs = list_npcs(conn, instance_id, status="active", limit=100)["items"]

    contactable_items: list[dict[str, Any]] = []
    departed_or_unavailable: list[dict[str, Any]] = []
    for npc in all_npcs:
        check = check_npc_accessibility(
            conn,
            instance_id,
            npc["npc_id"],
            player_location=clean_loc,
            world_minute=confirmed_min,
        )
        if check["contactable"]:
            contactable_items.append(check)
        elif (
            include_unavailable_at_location
            and (
                npc["location"] == clean_loc
                or npc["origin_location"] == clean_loc
                or npc["home_location"] == clean_loc
            )
        ):
            departed_or_unavailable.append(check)

    return {
        "instance_id": instance_id,
        "player_location": clean_loc,
        "world_minute": confirmed_min,
        "items": contactable_items,
        "unavailable_items": departed_or_unavailable,
    }


def read_snapshot(
    conn: sqlite3.Connection,
    instance_id: str,
    job: Mapping[str, Any] | None = None,
    *,
    mode: str | None = None,
    player_location: str = "霜木镇",
    focus_npc_ids: list[str] | tuple[str, ...] = (),
    budget: DepthBudget | None = None,
    fallback_sources: list[Mapping[str, Any]] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """只读选取接口：读取已确认实例时间与版本、按三档模拟深度评估 NPC 并按预算挑选到期 NPC。

    - 若 `job` 指定了超过已确认 `world_minute` 的 `target_world_minute`，返回 `status='skipped', skip=True, reason_code='DUE_TIME_EXCEEDS_CONFIRMED_MINUTE'`；
    - 若在 `advance` 模式下当前无任何到期活跃 NPC（`due_npcs` 为空），返回 `status='skipped', skip=True, reason_code='NO_DUE_NPCS'`（无到期不变更）；
    - 绝不修改数据库状态或推进时间。
    """
    inst = _get_instance_row(conn, instance_id, read_only=True)
    confirmed_minute = int(inst["world_minute"])
    world_revision = int(inst["revision"])
    world_id = str(inst["world_id"] or "")

    job_map = job if isinstance(job, Mapping) else {}
    resolved_mode = str(
        mode
        or job_map.get("mode")
        or job_map.get("kind")
        or job_map.get("job_kind")
        or "advance"
    ).strip()
    if resolved_mode not in ("seed", "advance"):
        raise NpcValidationError("INVALID_MODE", f"read_snapshot 仅支持 'seed' 或 'advance'，实际为 {resolved_mode!r}。")

    job_target_min = job_map.get("target_world_minute")
    if job_target_min is not None:
        target_min = _require_strict_int(job_target_min, "job.target_world_minute", min_value=0)
        if target_min > confirmed_minute:
            return {
                "instance_id": instance_id,
                "world_id": world_id,
                "mode": resolved_mode,
                "kind": resolved_mode,
                "world_minute": confirmed_minute,
                "target_world_minute": confirmed_minute,
                "world_revision": world_revision,
                "base_world_revision": world_revision,
                "revision": world_revision,
                "status": "skipped",
                "skip": True,
                "blocked": False,
                "reason_code": "DUE_TIME_EXCEEDS_CONFIRMED_MINUTE",
                "reason_message": f"任务目标分钟 ({target_min}) 超过实例已确认世界分钟 ({confirmed_minute})，跳过本次 NPC 模拟。",
                "due_npcs": [],
                "tier_evaluations": [],
                "budget": (budget or DepthBudget()).to_dict(),
            }

    if isinstance(job_map.get("player_location"), str) and job_map["player_location"].strip():
        player_location = job_map["player_location"].strip()
    if isinstance(job_map.get("focus_npc_ids"), (list, tuple)):
        focus_npc_ids = list(job_map["focus_npc_ids"])

    tasks_by_npc, tasks_by_key = _load_tasks_map(conn, instance_id)
    npc_rows = conn.execute(
        """
        SELECT instance_id, npc_id, name, role, faction, home_location, location,
               origin_location, destination_location, current_activity, current_action_kind,
               motivation, goal, depth_tier, depth_reason, shift_name, availability,
               counterpart_npc_id, conversation_topic, current_task_id,
               cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
               status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
        FROM m3_npcs
        WHERE instance_id = ?
        ORDER BY npc_id ASC
        """,
        (instance_id,),
    ).fetchall()
    all_npcs = [_row_to_npc_dict(r, tasks_by_npc) for r in npc_rows]
    existing_npcs_map = {n["npc_id"]: n for n in all_npcs}

    due_events, active_events, allowed_event_ids = _discover_instance_events(
        conn, instance_id, confirmed_minute
    )
    allowed_fact_ids_by_npc, allowed_fact_ids = _discover_instance_facts(conn, instance_id)
    allowed_source_ids = set(_discover_canon_source_ids(conn, world_id))
    if fallback_sources:
        for src in fallback_sources:
            sid = getattr(src, "source_id", None) or (src.get("source_id") if isinstance(src, Mapping) else None)
            if isinstance(sid, str) and sid.strip():
                allowed_source_ids.add(sid.strip())
    for n in all_npcs:
        for sid in n.get("source_ids", ()):
            if isinstance(sid, str) and sid.strip():
                allowed_source_ids.add(sid.strip())
    for ev in active_events:
        for sid in ev.get("source_ids", ()) or ():
            if isinstance(sid, str) and sid.strip():
                allowed_source_ids.add(sid.strip())

    other_instance_ids = _discover_other_instance_ids(conn, instance_id)

    depth_result = evaluate_npc_depths(
        all_npcs,
        world_minute=confirmed_minute,
        player_location=player_location,
        due_events=due_events,
        active_events=active_events,
        focus_npc_ids=focus_npc_ids,
        budget=budget,
    )
    selected_due_npcs = depth_result["selected_due_npcs"]

    if resolved_mode == "advance" and not selected_due_npcs:
        earliest_due = min(
            (
                int(n["next_due_minute"])
                for n in all_npcs
                if n.get("status") == "active" and n.get("next_due_minute") is not None
            ),
            default=None,
        )
        return {
            "instance_id": instance_id,
            "world_id": world_id,
            "mode": resolved_mode,
            "kind": resolved_mode,
            "world_minute": confirmed_minute,
            "target_world_minute": confirmed_minute,
            "world_revision": world_revision,
            "base_world_revision": world_revision,
            "revision": world_revision,
            "player_location": player_location,
            "status": "skipped",
            "skip": True,
            "blocked": False,
            "reason_code": "NO_DUE_NPCS",
            "reason_message": "当前已确认世界分钟下没有到期的 NPC 自主行动，跳过模拟。",
            "earliest_next_due_minute": earliest_due,
            "due_npcs": [],
            "existing_npcs": existing_npcs_map,
            "existing_tasks": {f"{k[0]}:{k[1]}": v for k, v in tasks_by_key.items()},
            "tier_evaluations": depth_result["tier_evaluations"],
            "budget": depth_result["budget"],
            "due_events": due_events,
            "active_events": active_events,
            "allowed_event_ids": sorted(allowed_event_ids),
            "allowed_fact_ids_by_npc": {k: sorted(v) for k, v in allowed_fact_ids_by_npc.items()},
            "allowed_fact_ids": sorted(allowed_fact_ids),
            "allowed_source_ids": sorted(allowed_source_ids),
            "other_instance_ids": sorted(other_instance_ids),
        }

    return {
        "instance_id": instance_id,
        "world_id": world_id,
        "mode": resolved_mode,
        "kind": resolved_mode,
        "world_minute": confirmed_minute,
        "target_world_minute": confirmed_minute,
        "world_revision": world_revision,
        "base_world_revision": world_revision,
        "revision": world_revision,
        "player_location": player_location,
        "status": "ready",
        "skip": False,
        "blocked": False,
        "reason_code": "OK",
        "reason_message": "NPC 模拟快照就绪。",
        "due_npcs": selected_due_npcs,
        "selected_npc_ids": depth_result["selected_npc_ids"],
        "existing_npcs": existing_npcs_map,
        "existing_tasks": {f"{k[0]}:{k[1]}": v for k, v in tasks_by_key.items()},
        "tier_evaluations": depth_result["tier_evaluations"],
        "budget": depth_result["budget"],
        "due_events": due_events,
        "active_events": active_events,
        "allowed_event_ids": sorted(allowed_event_ids),
        "allowed_fact_ids_by_npc": {k: sorted(v) for k, v in allowed_fact_ids_by_npc.items()},
        "allowed_fact_ids": sorted(allowed_fact_ids),
        "allowed_source_ids": sorted(allowed_source_ids),
        "other_instance_ids": sorted(other_instance_ids),
    }


select_npcs_for_pulse = read_snapshot
read_pulse_snapshot = read_snapshot


def apply_proposal(
    conn: sqlite3.Connection,
    instance_id: str,
    proposal: ValidatedNpcProposal | Mapping[str, Any],
    job: Mapping[str, Any] | None = None,
    *,
    player_location: str = "霜木镇",
    allowed_source_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    enforce_tier_interval: bool = True,
) -> dict[str, Any]:
    """在调用方事务内（使用 `SAVEPOINT`）原子应用 NPC 提议。

    - 绝不主动调用 `conn.commit()`；
    - 在 `SAVEPOINT` 内实时复核数据库当前 `world_instances.revision`、已确认 `world_minute`、
      NPC `revision`、到期时间、实例内因果引用与重复任务；
    - 任何一步校验失败立即 `ROLLBACK TO SAVEPOINT`，不产生半笔状态；
    - 支持基于 `job_id` / `idempotency_key` 的幂等重放保护。
    """
    ensure_npcs_schema(conn)
    savepoint_name = f"sp_m3_npcs_apply_{uuid4().hex[:10]}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        inst = _get_instance_row(conn, instance_id)
        confirmed_minute = int(inst["world_minute"])
        current_world_rev = int(inst["revision"])
        world_id = str(inst["world_id"] or "")

        job_map = job if isinstance(job, Mapping) else {}
        if isinstance(job_map.get("player_location"), str) and job_map["player_location"].strip():
            player_location = job_map["player_location"].strip()

        # 核对 job 的 base_world_revision 与 target_world_minute
        job_base_rev = job_map.get("base_world_revision")
        if job_base_rev is not None:
            expected_world_rev = _require_strict_int(job_base_rev, "job.base_world_revision", min_value=0)
            if expected_world_rev != current_world_rev:
                raise WorldRevisionConflictError(instance_id, expected_world_rev, current_world_rev)

        job_target_min = job_map.get("target_world_minute")
        if job_target_min is not None:
            expected_min = _require_strict_int(job_target_min, "job.target_world_minute", min_value=0)
            if expected_min != confirmed_minute:
                raise NpcValidationError(
                    "TIMESTAMP_OVERRIDE_FORBIDDEN",
                    f"job.target_world_minute ({expected_min}) 与当前实例已确认世界分钟 ({confirmed_minute}) 不一致。",
                    {"expected_world_minute": confirmed_minute, "job_target_world_minute": expected_min},
                )

        # 检查任务幂等重放（重复任务 / 重放保护）
        raw_idem_key = ""
        if isinstance(proposal, ValidatedNpcProposal):
            raw_idem_key = proposal.idempotency_key
        elif isinstance(proposal, Mapping):
            raw_idem_key = str(proposal.get("idempotency_key") or proposal.get("job_id") or "").strip()
        if not raw_idem_key and job_map.get("job_id"):
            raw_idem_key = str(job_map["job_id"]).strip()

        if raw_idem_key:
            applied_row = conn.execute(
                """
                SELECT result_json FROM m3_npc_applied_jobs
                WHERE instance_id = ? AND idempotency_key = ?
                """,
                (instance_id, raw_idem_key),
            ).fetchone()
            if applied_row is not None:
                cached = json.loads(applied_row[0])
                cached["idempotent_replay"] = True
                conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                return cached

        # 从数据库实时加载最新状态构造校验上下文，执行二次完整核验
        tasks_by_npc, tasks_by_key = _load_tasks_map(conn, instance_id)
        npc_rows = conn.execute(
            """
            SELECT instance_id, npc_id, name, role, faction, home_location, location,
                   origin_location, destination_location, current_activity, current_action_kind,
                   motivation, goal, depth_tier, depth_reason, shift_name, availability,
                   counterpart_npc_id, conversation_topic, current_task_id,
                   cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
                   status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
            FROM m3_npcs
            WHERE instance_id = ?
            ORDER BY npc_id ASC
            """,
            (instance_id,),
        ).fetchall()
        all_npcs = [_row_to_npc_dict(r, tasks_by_npc) for r in npc_rows]
        existing_npcs_map = {n["npc_id"]: n for n in all_npcs}

        due_events, active_events, allowed_event_ids = _discover_instance_events(
            conn, instance_id, confirmed_minute
        )
        allowed_fact_ids_by_npc, allowed_fact_ids = _discover_instance_facts(conn, instance_id)
        db_sources = set(_discover_canon_source_ids(conn, world_id))
        if allowed_source_ids is not None:
            db_sources.update(str(s).strip() for s in allowed_source_ids if str(s).strip())
        for n in all_npcs:
            db_sources.update(n.get("source_ids", ()))
        for ev in active_events:
            db_sources.update(ev.get("source_ids", ()) or ())

        other_instance_ids = _discover_other_instance_ids(conn, instance_id)

        depth_eval = evaluate_npc_depths(
            all_npcs,
            world_minute=confirmed_minute,
            player_location=player_location,
            due_events=due_events,
            active_events=active_events,
        )
        eval_by_npc = {e.npc_id: e for e in depth_eval["evaluations"]}
        for nid, ev_obj in eval_by_npc.items():
            if nid in existing_npcs_map:
                existing_npcs_map[nid]["depth_tier"] = ev_obj.current_tier
                existing_npcs_map[nid]["depth_reason"] = ev_obj.reason_summary

        resolved_mode = (
            proposal.mode
            if isinstance(proposal, ValidatedNpcProposal)
            else str(
                proposal.get("mode")
                or proposal.get("kind")
                or job_map.get("kind")
                or job_map.get("mode")
                or "advance"
            ).strip()
        )

        db_ctx = NpcValidationContext(
            instance_id=instance_id,
            world_minute=confirmed_minute,
            mode=resolved_mode,  # type: ignore[arg-type]
            base_world_revision=current_world_rev,
            existing_npcs=existing_npcs_map,
            existing_tasks=tasks_by_key,
            allowed_event_ids=allowed_event_ids,
            allowed_fact_ids_by_npc=allowed_fact_ids_by_npc,
            allowed_fact_ids=allowed_fact_ids,
            allowed_source_ids=frozenset(db_sources),
            other_instance_ids=other_instance_ids,
            enforce_tier_interval=enforce_tier_interval,
        )

        if isinstance(proposal, ValidatedNpcProposal):
            # 将已校验对象转为字典在最新 DB 上下文下二次校验，确保事务外到事务内期间无状态漂移
            raw_recheck: dict[str, Any] = {
                "instance_id": proposal.instance_id,
                "mode": proposal.mode,
                "world_minute": proposal.world_minute,
                "summary": proposal.summary,
                "idempotency_key": proposal.idempotency_key,
                "operations": [
                    {
                        "op": op.op,
                        "npc_id": op.npc_id,
                        "expected_npc_revision": op.expected_npc_revision,
                        "name": op.name,
                        "role": op.role,
                        "faction": op.faction,
                        "home_location": op.home_location,
                        "location": op.location,
                        "from_location": op.from_location,
                        "to_location": op.to_location,
                        "current_activity": op.current_activity,
                        "action_kind": op.action_kind,
                        "motivation": op.motivation,
                        "goal": op.goal,
                        "depth_tier": op.depth_tier,
                        "depth_reason": op.depth_reason,
                        "status": op.status,
                        "availability": op.availability,
                        "duration_minutes": op.duration_minutes,
                        "next_due_minute": op.next_due_minute,
                        "target_npc_id": op.target_npc_id,
                        "meeting_location": op.meeting_location,
                        "conversation_topic": op.conversation_topic,
                        "from_shift": op.from_shift,
                        "to_shift": op.to_shift,
                        "shift_name": op.shift_name,
                        "task_id": op.task_id,
                        "task_title": op.task_title,
                        "task_stage": op.task_stage,
                        "task_progress_percent": op.task_progress_percent,
                        "task_status": op.task_status,
                        "cause_event_ids": list(op.cause_event_ids),
                        "cause_fact_ids": list(op.cause_fact_ids),
                        "source_ids": list(op.source_ids),
                        "reason": op.reason,
                    }
                    for op in proposal.operations
                ],
            }
            if proposal.base_world_revision is not None:
                raw_recheck["base_world_revision"] = proposal.base_world_revision
            validated = validate_proposal(raw_recheck, db_ctx)
        else:
            validated = validate_proposal(proposal, db_ctx)

        # 若 Advance 模式下无操作（例如无到期 NPC），保持零写入直接返回
        if not validated.operations:
            empty_res = {
                "instance_id": instance_id,
                "world_minute": confirmed_minute,
                "world_revision": current_world_rev,
                "applied_operations": 0,
                "created_npc_ids": [],
                "updated_npc_ids": [],
                "updated_task_ids": [],
                "npcs": [],
                "idempotent_replay": False,
            }
            conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            return empty_res

        seq_row = conn.execute(
            "SELECT COALESCE(MAX(action_seq), 0) FROM m3_npc_action_log WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        next_seq = int(seq_row[0] or 0)

        created_npc_ids: list[str] = []
        updated_npc_ids: list[str] = []
        updated_task_ids: list[str] = []

        for op in validated.operations:
            eval_item = eval_by_npc.get(op.npc_id)
            effective_tier = (
                eval_item.current_tier
                if eval_item is not None
                else (op.depth_tier or "background")
            )
            effective_tier_reason = (
                eval_item.reason_summary
                if eval_item is not None
                else (op.depth_reason or "初始化分级")
            )

            action_meta: dict[str, Any] = {
                "duration_minutes": op.duration_minutes,
            }
            if op.action_kind == "travel":
                action_meta.update(
                    {
                        "from_location": op.from_location,
                        "to_location": op.to_location,
                    }
                )
            elif op.action_kind == "conversation_plan":
                action_meta.update(
                    {
                        "target_npc_id": op.target_npc_id,
                        "meeting_location": op.meeting_location,
                        "conversation_topic": op.conversation_topic,
                    }
                )
            elif op.action_kind == "shift_change":
                action_meta.update(
                    {
                        "from_shift": op.from_shift,
                        "to_shift": op.to_shift,
                    }
                )
            elif op.action_kind == "task_progress":
                action_meta.update(
                    {
                        "task_id": op.task_id,
                        "task_title": op.task_title,
                        "task_stage": op.task_stage,
                        "task_progress_percent": op.task_progress_percent,
                        "task_status": op.task_status,
                    }
                )

            if op.op == "seed_npc":
                if op.npc_id in existing_npcs_map:
                    raise DuplicateNpcTaskError(
                        op.npc_id,
                        code="NPC_ALREADY_EXISTS",
                        message=f"NPC {op.npc_id} 在当前实例中已存在，不能重复初始化。",
                    )
                new_rev = 1
                origin_loc = op.from_location if op.action_kind == "travel" else op.location
                dest_loc = op.to_location if op.action_kind == "travel" else ""
                conn.execute(
                    """
                    INSERT INTO m3_npcs (
                        instance_id, npc_id, name, role, faction, home_location, location,
                        origin_location, destination_location, current_activity, current_action_kind,
                        motivation, goal, depth_tier, depth_reason, shift_name, availability,
                        counterpart_npc_id, conversation_topic, current_task_id,
                        cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
                        status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_id,
                        op.npc_id,
                        op.name or op.npc_id,
                        op.role or "",
                        op.faction or "",
                        op.home_location or op.location,
                        op.location,
                        origin_loc,
                        dest_loc,
                        op.current_activity,
                        op.action_kind,
                        op.motivation,
                        op.goal or "",
                        effective_tier,
                        effective_tier_reason,
                        op.shift_name,
                        op.availability,
                        op.target_npc_id,
                        op.conversation_topic,
                        op.task_id,
                        json.dumps(list(op.cause_event_ids), ensure_ascii=False),
                        json.dumps(list(op.cause_fact_ids), ensure_ascii=False),
                        json.dumps(list(op.source_ids), ensure_ascii=False),
                        json.dumps(action_meta, ensure_ascii=False),
                        op.status,
                        confirmed_minute,
                        confirmed_minute,
                        confirmed_minute,
                        op.next_due_minute,
                        new_rev,
                    ),
                )
                created_npc_ids.append(op.npc_id)
            else:
                prev_npc = existing_npcs_map[op.npc_id]
                new_rev = int(prev_npc["revision"]) + 1
                origin_loc = op.from_location if op.action_kind == "travel" else prev_npc["location"]
                dest_loc = op.to_location if op.action_kind == "travel" else ""
                cur = conn.execute(
                    """
                    UPDATE m3_npcs
                    SET name = ?,
                        role = ?,
                        faction = ?,
                        home_location = ?,
                        location = ?,
                        origin_location = ?,
                        destination_location = ?,
                        current_activity = ?,
                        current_action_kind = ?,
                        motivation = ?,
                        goal = ?,
                        depth_tier = ?,
                        depth_reason = ?,
                        shift_name = ?,
                        availability = ?,
                        counterpart_npc_id = ?,
                        conversation_topic = ?,
                        current_task_id = ?,
                        cause_event_ids_json = ?,
                        cause_fact_ids_json = ?,
                        source_ids_json = ?,
                        action_meta_json = ?,
                        status = ?,
                        updated_minute = ?,
                        last_action_minute = ?,
                        next_due_minute = ?,
                        revision = ?
                    WHERE instance_id = ? AND npc_id = ? AND revision = ?
                    """,
                    (
                        op.name or prev_npc["name"],
                        op.role or prev_npc["role"],
                        op.faction if op.faction is not None else prev_npc["faction"],
                        op.home_location or prev_npc["home_location"],
                        op.location,
                        origin_loc,
                        dest_loc,
                        op.current_activity,
                        op.action_kind,
                        op.motivation,
                        op.goal if op.goal is not None else prev_npc["goal"],
                        effective_tier,
                        effective_tier_reason,
                        op.shift_name if op.shift_name else prev_npc["shift_name"],
                        op.availability,
                        op.target_npc_id,
                        op.conversation_topic,
                        op.task_id if op.task_id else prev_npc["current_task_id"],
                        json.dumps(list(op.cause_event_ids), ensure_ascii=False),
                        json.dumps(list(op.cause_fact_ids), ensure_ascii=False),
                        json.dumps(list(op.source_ids), ensure_ascii=False),
                        json.dumps(action_meta, ensure_ascii=False),
                        op.status,
                        confirmed_minute,
                        confirmed_minute,
                        op.next_due_minute,
                        new_rev,
                        instance_id,
                        op.npc_id,
                        int(op.expected_npc_revision or prev_npc["revision"]),
                    ),
                )
                if cur.rowcount != 1:
                    raise NpcRevisionConflictError(
                        instance_id,
                        op.npc_id,
                        int(op.expected_npc_revision or 0),
                        int(prev_npc["revision"]),
                    )
                updated_npc_ids.append(op.npc_id)

            # 若为 task_progress，更新或插入 m3_npc_tasks
            if op.action_kind == "task_progress" and op.task_id:
                existing_task = tasks_by_key.get((op.npc_id, op.task_id))
                if existing_task is None:
                    conn.execute(
                        """
                        INSERT INTO m3_npc_tasks (
                            instance_id, npc_id, task_id, title, stage, progress_percent,
                            status, motivation, cause_event_ids_json, cause_fact_ids_json,
                            source_ids_json, created_minute, updated_minute, revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            instance_id,
                            op.npc_id,
                            op.task_id,
                            op.task_title or op.task_id,
                            op.task_stage,
                            int(op.task_progress_percent or 0),
                            op.task_status or "active",
                            op.motivation,
                            json.dumps(list(op.cause_event_ids), ensure_ascii=False),
                            json.dumps(list(op.cause_fact_ids), ensure_ascii=False),
                            json.dumps(list(op.source_ids), ensure_ascii=False),
                            confirmed_minute,
                            confirmed_minute,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE m3_npc_tasks
                        SET title = ?,
                            stage = ?,
                            progress_percent = ?,
                            status = ?,
                            motivation = ?,
                            cause_event_ids_json = ?,
                            cause_fact_ids_json = ?,
                            source_ids_json = ?,
                            updated_minute = ?,
                            revision = revision + 1
                        WHERE instance_id = ? AND npc_id = ? AND task_id = ?
                        """,
                        (
                            op.task_title or existing_task["title"],
                            op.task_stage,
                            int(op.task_progress_percent or 0),
                            op.task_status or "active",
                            op.motivation,
                            json.dumps(list(op.cause_event_ids), ensure_ascii=False),
                            json.dumps(list(op.cause_fact_ids), ensure_ascii=False),
                            json.dumps(list(op.source_ids), ensure_ascii=False),
                            confirmed_minute,
                            instance_id,
                            op.npc_id,
                            op.task_id,
                        ),
                    )
                updated_task_ids.append(op.task_id)

            next_seq += 1
            conn.execute(
                """
                INSERT INTO m3_npc_action_log (
                    instance_id, action_seq, action_id, npc_id, npc_revision,
                    op, action_kind, from_location, to_location, current_activity,
                    motivation, depth_tier, depth_reason, world_minute, next_due_minute,
                    cause_event_ids_json, cause_fact_ids_json, source_ids_json, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    next_seq,
                    _default_action_id(),
                    op.npc_id,
                    new_rev,
                    op.op,
                    op.action_kind,
                    op.from_location,
                    op.to_location or op.location,
                    op.current_activity,
                    op.motivation,
                    effective_tier,
                    effective_tier_reason,
                    confirmed_minute,
                    op.next_due_minute,
                    json.dumps(list(op.cause_event_ids), ensure_ascii=False),
                    json.dumps(list(op.cause_fact_ids), ensure_ascii=False),
                    json.dumps(list(op.source_ids), ensure_ascii=False),
                    json.dumps(action_meta, ensure_ascii=False),
                ),
            )

        # 刷新尚未在本次操作列表中但模拟深度发生变化的活跃 NPC 的 depth_tier 与 depth_reason
        touched_ids = set(created_npc_ids) | set(updated_npc_ids)
        for ev_item in depth_eval["evaluations"]:
            if ev_item.npc_id not in touched_ids and ev_item.transition != "unchanged":
                conn.execute(
                    """
                    UPDATE m3_npcs
                    SET depth_tier = ?, depth_reason = ?
                    WHERE instance_id = ? AND npc_id = ?
                    """,
                    (ev_item.current_tier, ev_item.reason_summary, instance_id, ev_item.npc_id),
                )

        # 推进实例世界版本（world_instances.revision）
        conn.execute(
            "UPDATE world_instances SET revision = revision + 1 WHERE id = ?",
            (instance_id,),
        )
        new_world_rev = current_world_rev + 1

        updated_npcs = [
            get_npc(conn, instance_id, nid)
            for nid in (*created_npc_ids, *updated_npc_ids)
        ]

        result_payload: dict[str, Any] = {
            "instance_id": instance_id,
            "world_minute": confirmed_minute,
            "world_revision": new_world_rev,
            "applied_operations": len(validated.operations),
            "created_npc_ids": created_npc_ids,
            "updated_npc_ids": updated_npc_ids,
            "updated_task_ids": updated_task_ids,
            "npcs": updated_npcs,
            "idempotent_replay": False,
        }

        if raw_idem_key:
            conn.execute(
                """
                INSERT INTO m3_npc_applied_jobs (
                    instance_id, idempotency_key, world_minute, world_revision, result_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    raw_idem_key,
                    confirmed_minute,
                    new_world_rev,
                    json.dumps(result_payload, ensure_ascii=False),
                ),
            )

        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return result_payload
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise


apply_pulse_proposal = apply_proposal


def snapshot(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    """导出指定世界实例的 NPC 状态、任务与行动历史快照（JSON 可序列化，不含临时租约或凭据）。"""
    inst = _get_instance_row(conn, instance_id, read_only=True)
    tasks_by_npc, _ = _load_tasks_map(conn, instance_id)
    npc_rows = conn.execute(
        """
        SELECT instance_id, npc_id, name, role, faction, home_location, location,
               origin_location, destination_location, current_activity, current_action_kind,
               motivation, goal, depth_tier, depth_reason, shift_name, availability,
               counterpart_npc_id, conversation_topic, current_task_id,
               cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
               status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
        FROM m3_npcs
        WHERE instance_id = ?
        ORDER BY npc_id ASC
        """,
        (instance_id,),
    ).fetchall()
    npcs_data = []
    for r in npc_rows:
        item = _row_to_npc_dict(r, tasks_by_npc)
        item.pop("instance_id", None)
        for t in item.get("tasks", ()):
            t.pop("instance_id", None)
        npcs_data.append(item)

    action_logs = list_npc_action_log(conn, instance_id, limit=500)
    for log_item in action_logs:
        log_item.pop("instance_id", None)

    return {
        "format": SNAPSHOT_FORMAT_NAME,
        "version": SNAPSHOT_FORMAT_VERSION,
        "world_id": inst["world_id"],
        "source_instance_id": instance_id,
        "world_minute": inst["world_minute"],
        "world_revision": inst["world_revision"],
        "npcs": copy.deepcopy(npcs_data),
        "action_log": copy.deepcopy(action_logs),
    }


def _remap_source_ids(
    raw_ids: list[Any] | tuple[Any, ...],
    source_id_map: Mapping[str, str] | None,
) -> list[str]:
    result: list[str] = []
    for raw in raw_ids:
        sid = _require_non_empty_str(raw, "snapshot.source_id", 120)
        mapped = source_id_map.get(sid, sid) if source_id_map else sid
        if not (mapped.startswith("chunk:") or mapped.startswith("entry:")):
            raise SnapshotRestoreError(
                "SNAPSHOT_SOURCE_INVALID",
                f"快照包含非法的来源引用格式: {mapped!r}。",
                {"source_id": mapped},
            )
        result.append(mapped)
    return result


def restore(
    conn: sqlite3.Connection,
    target_instance_id_or_snapshot: str | Mapping[str, Any],
    snapshot_payload: Mapping[str, Any] | None = None,
    *,
    new_instance_id: str | None = None,
    world_id: str | None = None,
    source_id_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """将 NPC 快照原子恢复到目标世界实例（同时兼容位置参数 `(conn, target_instance_id, snapshot)`
    与 `DomainAdapter` 关键字参数 `(conn, snapshot, *, new_instance_id=..., world_id=...)`）。

    全程运行在 `SAVEPOINT` 内，任何损坏或非法数据均完整回滚，不留半个实例。
    """
    ensure_npcs_schema(conn)
    if isinstance(target_instance_id_or_snapshot, str):
        target_id = target_instance_id_or_snapshot
        payload = snapshot_payload
    else:
        payload = target_instance_id_or_snapshot
        target_id = new_instance_id or ""

    if not isinstance(target_id, str) or not target_id.strip():
        raise SnapshotRestoreError("SNAPSHOT_TARGET_INVALID", "恢复快照必须提供有效的目标实例 ID。")
    target_id = target_id.strip()

    if not isinstance(payload, Mapping):
        raise SnapshotRestoreError("SNAPSHOT_INVALID", "NPC 快照载荷必须为 JSON 对象。")

    fmt = payload.get("format", SNAPSHOT_FORMAT_NAME)
    ver = payload.get("version")
    if fmt != SNAPSHOT_FORMAT_NAME or ver != SNAPSHOT_FORMAT_VERSION:
        raise SnapshotRestoreError(
            "SNAPSHOT_VERSION_UNSUPPORTED",
            f"不支持的 NPC 快照格式或版本: format={fmt!r}, version={ver!r}。",
        )

    raw_npcs = payload.get("npcs")
    raw_logs = payload.get("action_log", [])
    if not isinstance(raw_npcs, list) or not isinstance(raw_logs, list):
        raise SnapshotRestoreError("SNAPSHOT_CORRUPTED", "NPC 快照中的 npcs 与 action_log 必须为列表。")

    snap_min = _require_strict_int(payload.get("world_minute", 0), "snapshot.world_minute", min_value=0)
    snap_rev = _require_strict_int(payload.get("world_revision", 0), "snapshot.world_revision", min_value=0)
    snap_world_id = str(payload.get("world_id") or world_id or "")

    savepoint_name = f"sp_m3_npcs_restore_{uuid4().hex[:10]}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        inst_row = conn.execute(
            "SELECT id, world_id, world_minute, revision FROM world_instances WHERE id = ?",
            (target_id,),
        ).fetchone()
        if inst_row is None:
            conn.execute(
                """
                INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (target_id, snap_world_id, snap_min, snap_rev),
            )
        else:
            existing_world_id = str(inst_row[1] or "")
            if world_id and existing_world_id and existing_world_id != world_id:
                raise SnapshotRestoreError(
                    "INSTANCE_WORLD_MISMATCH",
                    "目标实例所属世界与恢复请求不一致。",
                )

        # 清空目标实例下可能残留的旧 NPC 数据
        conn.execute("DELETE FROM m3_npc_action_log WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_npc_tasks WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_npcs WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_npc_applied_jobs WHERE instance_id = ?", (target_id,))

        restored_npc_ids: list[str] = []
        seen_ids: set[str] = set()

        for idx, item in enumerate(raw_npcs):
            if not isinstance(item, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_CORRUPTED", f"npcs[{idx}] 必须为对象。")
            npc_id = _require_non_empty_str(item.get("npc_id"), f"npcs[{idx}].npc_id", 100)
            if npc_id in seen_ids:
                raise SnapshotRestoreError("SNAPSHOT_DUPLICATE_NPC", f"快照包含重复的 npc_id: {npc_id}。")
            seen_ids.add(npc_id)

            name = _require_non_empty_str(item.get("name"), f"npcs[{idx}].name", 80)
            role = _require_non_empty_str(item.get("role") or item.get("identity"), f"npcs[{idx}].role", 160)
            location = _require_non_empty_str(item.get("location"), f"npcs[{idx}].location", 100)
            activity = _require_non_empty_str(item.get("current_activity"), f"npcs[{idx}].current_activity", 300)
            action_kind = _require_non_empty_str(
                item.get("current_action_kind") or item.get("action_kind"),
                f"npcs[{idx}].current_action_kind",
                40,
            )
            if action_kind not in NPC_ACTION_KINDS:
                raise SnapshotRestoreError("SNAPSHOT_INVALID_ACTION_KIND", f"非法动作类型: {action_kind!r}。")
            motivation = _require_non_empty_str(item.get("motivation"), f"npcs[{idx}].motivation", 360)
            tier = normalize_depth_tier(item.get("depth_tier", "background"), f"npcs[{idx}].depth_tier")
            avail = str(item.get("availability") or "available")
            if avail not in NPC_AVAILABILITY_STATES:
                raise SnapshotRestoreError("SNAPSHOT_INVALID_AVAILABILITY", f"非法可接触状态: {avail!r}。")
            status = str(item.get("status") or "active")
            if status not in NPC_STATUSES:
                raise SnapshotRestoreError("SNAPSHOT_INVALID_STATUS", f"非法 NPC 状态: {status!r}。")

            created_min = _require_strict_int(item.get("created_minute", 0), f"npcs[{idx}].created_minute", min_value=0)
            updated_min = _require_strict_int(item.get("updated_minute", created_min), f"npcs[{idx}].updated_minute", min_value=0)
            last_act_min = _require_strict_int(item.get("last_action_minute", updated_min), f"npcs[{idx}].last_action_minute", min_value=0)
            next_due = item.get("next_due_minute")
            if next_due is not None:
                next_due = _require_strict_int(next_due, f"npcs[{idx}].next_due_minute", min_value=0)
            rev = _require_strict_int(item.get("revision", 1), f"npcs[{idx}].revision", min_value=1)

            remapped_sources = _remap_source_ids(item.get("source_ids") or [], source_id_map)
            cause_events = [str(e) for e in (item.get("cause_event_ids") or []) if str(e).strip()]
            cause_facts = [str(f) for f in (item.get("cause_fact_ids") or []) if str(f).strip()]
            action_meta = item.get("action_meta") if isinstance(item.get("action_meta"), Mapping) else {}

            conn.execute(
                """
                INSERT INTO m3_npcs (
                    instance_id, npc_id, name, role, faction, home_location, location,
                    origin_location, destination_location, current_activity, current_action_kind,
                    motivation, goal, depth_tier, depth_reason, shift_name, availability,
                    counterpart_npc_id, conversation_topic, current_task_id,
                    cause_event_ids_json, cause_fact_ids_json, source_ids_json, action_meta_json,
                    status, created_minute, updated_minute, last_action_minute, next_due_minute, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    npc_id,
                    name,
                    role,
                    str(item.get("faction") or ""),
                    str(item.get("home_location") or location),
                    location,
                    str(item.get("origin_location") or ""),
                    str(item.get("destination_location") or ""),
                    activity,
                    action_kind,
                    motivation,
                    str(item.get("goal") or ""),
                    tier,
                    str(item.get("depth_reason") or ""),
                    str(item.get("shift_name") or ""),
                    avail,
                    str(item.get("counterpart_npc_id") or ""),
                    str(item.get("conversation_topic") or ""),
                    str(item.get("current_task_id") or ""),
                    json.dumps(cause_events, ensure_ascii=False),
                    json.dumps(cause_facts, ensure_ascii=False),
                    json.dumps(remapped_sources, ensure_ascii=False),
                    json.dumps(dict(action_meta), ensure_ascii=False),
                    status,
                    created_min,
                    updated_min,
                    last_act_min,
                    next_due,
                    rev,
                ),
            )

            raw_tasks = item.get("tasks") or []
            if not isinstance(raw_tasks, list):
                raise SnapshotRestoreError("SNAPSHOT_CORRUPTED", f"npcs[{idx}].tasks 必须为列表。")
            for t_idx, t_item in enumerate(raw_tasks):
                if not isinstance(t_item, Mapping):
                    raise SnapshotRestoreError("SNAPSHOT_CORRUPTED", f"npcs[{idx}].tasks[{t_idx}] 必须为对象。")
                tid = _require_non_empty_str(t_item.get("task_id"), f"tasks[{t_idx}].task_id", 100)
                ttitle = _require_non_empty_str(t_item.get("title"), f"tasks[{t_idx}].title", 160)
                tstage = _require_non_empty_str(t_item.get("stage"), f"tasks[{t_idx}].stage", 120)
                tprog = _require_strict_int(
                    t_item.get("progress_percent", 0),
                    f"tasks[{t_idx}].progress_percent",
                    min_value=0,
                    max_value=100,
                )
                tstatus = str(t_item.get("status") or "active")
                if tstatus not in TASK_STATUSES:
                    raise SnapshotRestoreError("SNAPSHOT_CORRUPTED", f"非法任务状态: {tstatus!r}。")
                t_sources = _remap_source_ids(t_item.get("source_ids") or [], source_id_map)
                t_events = [str(e) for e in (t_item.get("cause_event_ids") or []) if str(e).strip()]
                t_facts = [str(f) for f in (t_item.get("cause_fact_ids") or []) if str(f).strip()]
                conn.execute(
                    """
                    INSERT INTO m3_npc_tasks (
                        instance_id, npc_id, task_id, title, stage, progress_percent,
                        status, motivation, cause_event_ids_json, cause_fact_ids_json,
                        source_ids_json, created_minute, updated_minute, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_id,
                        npc_id,
                        tid,
                        ttitle,
                        tstage,
                        tprog,
                        tstatus,
                        str(t_item.get("motivation") or ""),
                        json.dumps(t_events, ensure_ascii=False),
                        json.dumps(t_facts, ensure_ascii=False),
                        json.dumps(t_sources, ensure_ascii=False),
                        _require_strict_int(t_item.get("created_minute", 0), "task.created_minute", min_value=0),
                        _require_strict_int(t_item.get("updated_minute", 0), "task.updated_minute", min_value=0),
                        _require_strict_int(t_item.get("revision", 1), "task.revision", min_value=1),
                    ),
                )

            restored_npc_ids.append(npc_id)

        for l_idx, log_item in enumerate(raw_logs):
            if not isinstance(log_item, Mapping):
                raise SnapshotRestoreError("SNAPSHOT_CORRUPTED", f"action_log[{l_idx}] 必须为对象。")
            log_npc_id = _require_non_empty_str(log_item.get("npc_id"), f"action_log[{l_idx}].npc_id", 100)
            if log_npc_id not in seen_ids:
                raise SnapshotRestoreError(
                    "SNAPSHOT_LOG_ORPHAN",
                    f"action_log[{l_idx}] 引用了快照中不存在的 npc_id: {log_npc_id}。",
                )
            l_sources = _remap_source_ids(log_item.get("source_ids") or [], source_id_map)
            l_events = [str(e) for e in (log_item.get("cause_event_ids") or []) if str(e).strip()]
            l_facts = [str(f) for f in (log_item.get("cause_fact_ids") or []) if str(f).strip()]
            conn.execute(
                """
                INSERT INTO m3_npc_action_log (
                    instance_id, action_seq, action_id, npc_id, npc_revision,
                    op, action_kind, from_location, to_location, current_activity,
                    motivation, depth_tier, depth_reason, world_minute, next_due_minute,
                    cause_event_ids_json, cause_fact_ids_json, source_ids_json, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    int(log_item.get("action_seq") or (l_idx + 1)),
                    str(log_item.get("action_id") or _default_action_id()),
                    log_npc_id,
                    _require_strict_int(log_item.get("npc_revision", 1), "log.npc_revision", min_value=1),
                    str(log_item.get("op") or "restore"),
                    str(log_item.get("action_kind") or "task_progress"),
                    str(log_item.get("from_location") or ""),
                    str(log_item.get("to_location") or ""),
                    str(log_item.get("current_activity") or ""),
                    str(log_item.get("motivation") or ""),
                    normalize_depth_tier(log_item.get("depth_tier", "background")),
                    str(log_item.get("depth_reason") or ""),
                    _require_strict_int(log_item.get("world_minute", 0), "log.world_minute", min_value=0),
                    int(log_item["next_due_minute"]) if log_item.get("next_due_minute") is not None else None,
                    json.dumps(l_events, ensure_ascii=False),
                    json.dumps(l_facts, ensure_ascii=False),
                    json.dumps(l_sources, ensure_ascii=False),
                    json.dumps(dict(log_item.get("details") or {}), ensure_ascii=False),
                ),
            )

        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return {
            "instance_id": target_id,
            "restored_npc_count": len(restored_npc_ids),
            "restored_npc_ids": restored_npc_ids,
        }
    except SnapshotRestoreError:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise
    except NpcValidationError as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise SnapshotRestoreError(exc.code, exc.message, exc.details) from exc
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise


class NpcsAdapter:
    """符合 `app.m3.core.adapters.DomainAdapter` 协议的 NPC 领域快照适配器。"""

    name = "npcs"

    def snapshot(self, conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
        return snapshot(conn, instance_id)

    def restore(
        self,
        conn: sqlite3.Connection,
        snapshot_data: dict[str, Any],
        *,
        new_instance_id: str,
        world_id: str,
    ) -> None:
        restore(conn, new_instance_id, snapshot_data, world_id=world_id)
