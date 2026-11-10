"""M3.4 NPC 自主行动、行程、动态模拟深度与快照/恢复持久层服务（M3 Wave 4）。

契约要求（`docs/collaboration/M3_WAVE4_GEMINI_LI.md` & `docs/contracts/M3_WAVE4_API.md`）：
1. 四个核心接口：
   - 只读选取：`read_snapshot(conn, instance_id, job=None, ...)` 与 `select_npcs_for_pulse(conn, instance_id, ...)`
   - 纯校验：`validate_proposal(proposal, context)`（由 `validator.py` 提供）
   - 调用方事务内应用：`apply_proposal(conn, instance_id, validated, job=None, ..., advance_world_revision=None)`（`SAVEPOINT` 保护，不自行 `commit`）
   - 快照与恢复：`snapshot(conn, instance_id)` / `restore(conn, target_instance_id, snapshot, ...)` 及 `NpcsAdapter`
2. 从初始化输入接收人物状态（`seed_npcs_from_initialization` / `initialize_npcs_from_draft`），
   首版覆盖出行（`travel`）、留在原处（`stay`）、等待（`wait`）、无变化（`no_change`）、改变计划（`plan_change`）、
   交谈安排（`conversation_plan`）、换班（`shift_change`）与任务进度（`task_progress`）。
3. 解决现有 NPC 应用与 `PulseWorker` 双重更新实例版本的冲突：
   - 单独调用 `apply_proposal` 时默认 `advance_world_revision=True`，保持既有独立调用语义；
   - 在 `PulseWorker` 组合提交或传入 `advance_world_revision=False` / 运行期租约任务时，由外层 `PulseWorker` 统一推进一次 `world_instances.revision`；
   - 提供 `apply_pulse_proposal_in_worker` 与 `build_npc_pulse_callbacks` 供 DeepSeekHarness 直接挂载。
4. 保留地点、状态和行动历史，提供 `check_npc_accessibility`、`list_contactable_npcs` 与 `build_adventure_narration_npc_context`，
   让查证入口（`encounters`）和下一回合叙事共用同一份权威人物状态。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    NPC_ACTION_KIND_LABELS_ZH,
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
    _safe_u16_truncate,
    evaluate_npc_depths,
    normalize_action_kind,
    normalize_depth_tier,
    normalize_initial_npc_seed_proposal,
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


def _resolve_instance_player_location(
    conn: sqlite3.Connection,
    inst_row: Mapping[str, Any],
    explicit_location: str | None = None,
) -> str:
    if explicit_location and str(explicit_location).strip():
        return str(explicit_location).strip()
    adv_id = inst_row.get("adventure_id")
    if adv_id:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "adventures" in tables:
            adv_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(adventures)").fetchall()}
            if "character_json" in adv_cols:
                row = conn.execute(
                    "SELECT character_json FROM adventures WHERE id = ?",
                    (str(adv_id),),
                ).fetchone()
                if row and row[0]:
                    try:
                        c_data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        if isinstance(c_data, Mapping) and str(c_data.get("location") or "").strip():
                            return str(c_data["location"]).strip()
                    except Exception:
                        pass
    return "霜木镇"


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
    action_meta = json.loads(d.get("action_meta_json") or "{}")
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
        "action_meta": action_meta,
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
    """从同库 `world_events`、`m3_knowledge_event_stubs`、`m3_npc_event_stubs` 或 `m3_pulse_fake_events` 读取本实例已存在事件。"""
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

    if "m3_pulse_fake_events" in tables:
        rows = conn.execute(
            """
            SELECT event_id, kind, title, description, location, scope,
                   stage, status, created_minute, updated_minute,
                   next_update_minute, revision
            FROM m3_pulse_fake_events
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
                    "participants": [],
                    "stage": str(r[6]),
                    "status": str(r[7]),
                    "created_minute": int(r[8]),
                    "updated_minute": int(r[9]),
                    "next_update_minute": int(r[10]) if r[10] is not None else None,
                    "revision": int(r[11]),
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
    """读取本实例内各 NPC 已获知的事实 ID（兼容 `m3_knowledge_facts`、`m3_npc_knowledge_stubs` 与 `m3_encounter_knowledge_stubs`）。"""
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    by_holder: dict[str, set[str]] = {}
    all_facts: set[str] = set()

    for tbl in ("m3_knowledge_facts", "m3_npc_knowledge_stubs", "m3_encounter_knowledge_stubs"):
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
    if "m3_pulse_fake_sources" in tables:
        rows = conn.execute(
            "SELECT source_id FROM m3_pulse_fake_sources WHERE world_id = ?",
            (world_id,),
        ).fetchall()
        for r in rows:
            allowed.add(str(r[0]))
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
    player_location: str | None = None,
    world_minute: int | None = None,
) -> dict[str, Any]:
    """供 Ma 玩家主动查证/交谈模块（`encounters`）及下一回合叙事调用的只读 NPC 可接触性核验边界。"""
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

    resolved_loc = _resolve_instance_player_location(conn, inst, player_location)
    clean_player_loc = _require_non_empty_str(resolved_loc, "player_location", 100)
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
    elif avail == "in_transit" and npc["origin_location"] == clean_player_loc and npc_loc != clean_player_loc:
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
        "goal": npc["goal"],
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
    player_location: str | None = None,
    world_minute: int | None = None,
    include_unavailable_at_location: bool = False,
) -> dict[str, Any]:
    """供 Ma 玩家主动查证/交谈模块列出当前地点可接触的 NPC 列表。"""
    inst = _get_instance_row(conn, instance_id, read_only=True)
    confirmed_min = int(inst["world_minute"]) if world_minute is None else _require_strict_int(world_minute, "world_minute", min_value=0)
    resolved_loc = _resolve_instance_player_location(conn, inst, player_location)
    clean_loc = _require_non_empty_str(resolved_loc, "player_location", 100)
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


def build_adventure_narration_npc_context(
    conn: sqlite3.Connection,
    adventure_or_instance_id: str,
    *,
    player_location: str | None = None,
    world_minute: int | None = None,
    max_chars: int = 600,
) -> dict[str, Any]:
    """构建供下一回合自由行动叙事与查证共享的同地 NPC 状态摘要上下文。"""
    ensure_npcs_schema(conn)
    target = _require_non_empty_str(adventure_or_instance_id, "adventure_or_instance_id", 120)
    row = conn.execute(
        """
        SELECT id, world_id, adventure_id, world_minute, revision
        FROM world_instances
        WHERE id = ? OR adventure_id = ?
        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (target, target, target),
    ).fetchone()
    if row is None:
        return {
            "instance_id": "",
            "player_location": player_location or "",
            "world_minute": 0,
            "present_npcs": [],
            "departed_npcs": [],
            "prompt_block": "",
        }

    inst_id = str(row[0])
    contact_bundle = list_contactable_npcs(
        conn,
        inst_id,
        player_location=player_location,
        world_minute=world_minute,
        include_unavailable_at_location=True,
    )
    loc = contact_bundle["player_location"]
    present = contact_bundle["items"]
    unavailable = contact_bundle["unavailable_items"]

    lines: list[str] = []
    for p in present:
        kind_zh = NPC_ACTION_KIND_LABELS_ZH.get(p["current_action_kind"], p["current_action_kind"])
        shift_part = f"（{p['shift_name']}）" if p.get("shift_name") else ""
        goal_part = f"｜当前计划：{p['goal']}" if p.get("goal") else ""
        lines.append(f"- 在场：{p['name']}（{p['role']}）{shift_part}正在{p['current_activity']} [{kind_zh}]{goal_part}")

    for u in unavailable:
        if u["reason_code"] in ("NPC_DEPARTED_IN_TRANSIT", "NPC_AT_DIFFERENT_LOCATION"):
            lines.append(f"- 不在场：{u['name']}（{u['role']}）当前不在{loc}。")
        elif u["reason_code"] == "NPC_OFF_DUTY":
            lines.append(f"- 歇业中：{u['name']}（{u['role']}）当前{u.get('shift_name') or '休息中'}，暂不待客。")

    raw_block = "\n".join(lines)
    prompt_block = _safe_u16_truncate(raw_block, max_chars) if raw_block else ""
    return {
        "instance_id": inst_id,
        "player_location": loc,
        "world_minute": contact_bundle["world_minute"],
        "present_npcs": present,
        "departed_npcs": unavailable,
        "prompt_block": prompt_block,
    }


def read_snapshot(
    conn: sqlite3.Connection,
    instance_id: str,
    job: Mapping[str, Any] | None = None,
    *,
    mode: str | None = None,
    player_location: str | None = None,
    focus_npc_ids: list[str] | tuple[str, ...] = (),
    budget: DepthBudget | None = None,
    fallback_sources: list[Mapping[str, Any]] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """只读选取接口：读取已确认实例时间与版本、按三档模拟深度评估 NPC 并按预算挑选到期 NPC。"""
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

    payload_map = job_map.get("payload") if isinstance(job_map.get("payload"), Mapping) else {}
    raw_loc = (
        player_location
        or job_map.get("player_location")
        or payload_map.get("player_location")
    )
    resolved_player_location = _resolve_instance_player_location(
        conn, inst, str(raw_loc).strip() if isinstance(raw_loc, str) and raw_loc.strip() else None
    )
    if isinstance(job_map.get("focus_npc_ids"), (list, tuple)):
        focus_npc_ids = list(job_map["focus_npc_ids"])
    elif isinstance(payload_map.get("focus_npc_ids"), (list, tuple)):
        focus_npc_ids = list(payload_map["focus_npc_ids"])

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
        player_location=resolved_player_location,
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
            "player_location": resolved_player_location,
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
        "player_location": resolved_player_location,
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
    player_location: str | None = None,
    allowed_source_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    enforce_tier_interval: bool = True,
    advance_world_revision: bool | None = None,
) -> dict[str, Any]:
    """在调用方事务内（使用 `SAVEPOINT`）原子应用 NPC 提议。"""
    ensure_npcs_schema(conn)
    savepoint_name = f"sp_m3_npcs_apply_{uuid4().hex[:10]}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        inst = _get_instance_row(conn, instance_id)
        confirmed_minute = int(inst["world_minute"])
        current_world_rev = int(inst["revision"])
        world_id = str(inst["world_id"] or "")

        job_map = job if isinstance(job, Mapping) else {}
        payload_map = job_map.get("payload") if isinstance(job_map.get("payload"), Mapping) else {}
        raw_loc = (
            player_location
            or job_map.get("player_location")
            or payload_map.get("player_location")
        )
        resolved_player_location = _resolve_instance_player_location(
            conn, inst, str(raw_loc).strip() if isinstance(raw_loc, str) and raw_loc.strip() else None
        )

        # 1. 优先检查任务幂等重放
        raw_idem_key = ""
        if isinstance(proposal, ValidatedNpcProposal):
            raw_idem_key = proposal.idempotency_key
        elif isinstance(proposal, Mapping):
            raw_idem_key = str(proposal.get("idempotency_key") or proposal.get("job_id") or "").strip()
        if not raw_idem_key and job_map.get("job_id"):
            raw_idem_key = str(job_map["job_id"]).strip()
        if not raw_idem_key and job_map.get("idempotency_key"):
            raw_idem_key = str(job_map["idempotency_key"]).strip()

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

        # 2. 核对 job 的 base_world_revision 与 target_world_minute
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

        # 3. 判定是否由本函数直接推进 world_instances.revision
        if advance_world_revision is not None:
            should_advance_world_rev = bool(advance_world_revision)
        elif isinstance(job_map.get("advance_world_revision"), bool):
            should_advance_world_rev = bool(job_map["advance_world_revision"])
        elif bool(job_map.get("lease_token")) and str(job_map.get("status") or "") == "running":
            should_advance_world_rev = False
        else:
            should_advance_world_rev = True

        # 4. 从数据库实时加载最新状态构造校验上下文，执行二次完整核验
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
            player_location=resolved_player_location,
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
                        "previous_plan": op.previous_plan,
                        "new_plan": op.new_plan,
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
                        "grounding": op.grounding,
                        "relationships": list(op.relationships),
                    }
                    for op in proposal.operations
                ],
            }
            if proposal.base_world_revision is not None:
                raw_recheck["base_world_revision"] = proposal.base_world_revision
            validated = validate_proposal(raw_recheck, db_ctx)
        else:
            validated = validate_proposal(proposal, db_ctx)

        if not validated.operations:
            empty_res: dict[str, Any] = {
                "instance_id": instance_id,
                "world_minute": confirmed_minute,
                "world_revision": current_world_rev,
                "applied_operations": 0,
                "created_npc_ids": [],
                "updated_npc_ids": [],
                "updated_task_ids": [],
                "npcs": [],
                "idempotent_replay": False,
                "world_revision_advanced": False,
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
                        current_world_rev,
                        json.dumps(empty_res, ensure_ascii=False),
                    ),
                )
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

            prev_meta: dict[str, Any] = {}
            if op.op == "act" and op.npc_id in existing_npcs_map:
                raw_prev_meta = existing_npcs_map[op.npc_id].get("action_meta")
                if isinstance(raw_prev_meta, Mapping):
                    prev_meta = dict(raw_prev_meta)

            action_meta: dict[str, Any] = {
                **prev_meta,
                "duration_minutes": op.duration_minutes,
                "grounding": op.grounding if op.op == "seed_npc" else prev_meta.get("grounding", op.grounding),
            }
            if op.relationships:
                action_meta["relationships"] = list(op.relationships)

            if op.action_kind == "travel":
                action_meta.update(
                    {
                        "from_location": op.from_location,
                        "to_location": op.to_location,
                    }
                )
            elif op.action_kind in ("stay", "wait", "no_change"):
                action_meta.update(
                    {
                        "decision": op.action_kind,
                        "location": op.location,
                    }
                )
            elif op.action_kind == "plan_change":
                action_meta.update(
                    {
                        "previous_plan": op.previous_plan,
                        "new_plan": op.new_plan,
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
                origin_loc = op.from_location if op.action_kind == "travel" else op.location
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

            if op.task_id and op.task_stage:
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

        if should_advance_world_rev:
            cur_w = conn.execute(
                "UPDATE world_instances SET revision = revision + 1 WHERE id = ? AND revision = ?",
                (instance_id, current_world_rev),
            )
            if cur_w.rowcount != 1:
                raise WorldRevisionConflictError(instance_id, current_world_rev, current_world_rev + 1)
            new_world_rev = current_world_rev + 1
        else:
            new_world_rev = current_world_rev + 1

        updated_npcs = [
            get_npc(conn, instance_id, nid)
            for nid in (*created_npc_ids, *updated_npc_ids)
        ]

        result_payload: dict[str, Any] = {
            "instance_id": instance_id,
            "world_minute": confirmed_minute,
            "world_revision": new_world_rev,
            "pre_commit_world_revision": current_world_rev,
            "world_revision_advanced": should_advance_world_rev,
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


def apply_pulse_proposal_in_worker(
    conn: sqlite3.Connection,
    instance_id: str,
    proposal: ValidatedNpcProposal | Mapping[str, Any],
    job: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """供 `PulseWorker` 或组合提交器在 `SAVEPOINT` 内调用的专用入口（强制 `advance_world_revision=False`）。"""
    kwargs["advance_world_revision"] = False
    return apply_proposal(conn, instance_id, proposal, job, **kwargs)


def seed_npcs_from_initialization(
    conn: sqlite3.Connection,
    instance_id: str,
    init_payload: Any,
    *,
    world_minute: int | None = None,
    allowed_source_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    player_location: str | None = None,
    advance_world_revision: bool = True,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """从 AI 世界初始化分析（`world_analysis` / 开局确认草案）接收人物状态并写入 NPC 账本。"""
    ensure_npcs_schema(conn)
    inst = _get_instance_row(conn, instance_id)
    confirmed_min = int(inst["world_minute"]) if world_minute is None else _require_strict_int(world_minute, "world_minute", min_value=0)
    current_rev = int(inst["revision"])

    default_sources = list(allowed_source_ids or _discover_canon_source_ids(conn, str(inst["world_id"] or "")))
    seed_proposal = normalize_initial_npc_seed_proposal(
        init_payload,
        instance_id=instance_id,
        world_minute=confirmed_min,
        base_world_revision=current_rev,
        default_source_ids=default_sources,
        idempotency_key=idempotency_key,
    )

    savepoint_name = f"sp_m3_npcs_init_{uuid4().hex[:10]}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        if isinstance(init_payload, Mapping):
            for ev in init_payload.get("initial_events") or ():
                if isinstance(ev, Mapping) and ev.get("event_id") and ev.get("title"):
                    register_event_stub(
                        conn,
                        instance_id,
                        str(ev["event_id"]),
                        title=str(ev["title"]),
                        description=str(ev.get("description") or ""),
                        location=str(ev.get("location") or player_location or "霜木镇"),
                        scope=str(ev.get("scope") or "local"),
                        kind=str(ev.get("kind") or "thread"),
                        participants=list(ev.get("participants") or ()),
                        stage=str(ev.get("stage") or "active"),
                        status=str(ev.get("status") or "active"),
                        created_minute=confirmed_min,
                        updated_minute=confirmed_min,
                        next_update_minute=ev.get("next_update_minute"),
                    )
            for fact in (init_payload.get("initial_facts") or init_payload.get("npc_facts") or ()):
                if isinstance(fact, Mapping) and fact.get("fact_id") and fact.get("holder_id"):
                    register_knowledge_stub(
                        conn,
                        instance_id,
                        str(fact["fact_id"]),
                        holder_id=str(fact["holder_id"]),
                        title=str(fact.get("title") or ""),
                        content=str(fact.get("content") or ""),
                        source_event_id=str(fact.get("source_event_id") or ""),
                        knowledge_type=str(fact.get("knowledge_type") or "confirmed"),
                        learned_minute=int(fact.get("learned_minute", confirmed_min)),
                    )

        applied = apply_proposal(
            conn,
            instance_id,
            seed_proposal,
            player_location=player_location,
            allowed_source_ids=allowed_source_ids,
            advance_world_revision=advance_world_revision,
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return applied
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise


initialize_npcs_from_draft = seed_npcs_from_initialization


def _is_npc_operation(op: Mapping[str, Any]) -> bool:
    op_kind = str(op.get("op") or "").strip()
    if op_kind in ("seed_npc", "act"):
        return True
    if "npc_id" in op and op_kind not in ("create", "update"):
        return True
    return False


def _compact_npc_for_pulse_snapshot(npc: Mapping[str, Any]) -> dict[str, Any]:
    """裁剪供 PulseWorker 快照与纯校验使用的紧凑 NPC 字典，避免冗余大字段挤占单轮 Token 预算预留。"""
    out: dict[str, Any] = {
        "npc_id": npc["npc_id"],
        "name": npc["name"],
        "role": npc["role"],
        "location": npc["location"],
        "current_activity": npc["current_activity"],
        "current_action_kind": npc["current_action_kind"],
        "motivation": npc["motivation"],
        "goal": npc.get("goal", ""),
        "depth_tier": npc["depth_tier"],
        "shift_name": npc.get("shift_name", ""),
        "availability": npc.get("availability", "available"),
        "status": npc.get("status", "active"),
        "next_due_minute": npc.get("next_due_minute"),
        "revision": npc["revision"],
        "cause_event_ids": list(npc.get("cause_event_ids") or ()),
        "cause_fact_ids": list(npc.get("cause_fact_ids") or ()),
        "source_ids": list(npc.get("source_ids") or ()),
    }
    if npc.get("prompt_excerpt"):
        out["prompt_excerpt"] = npc["prompt_excerpt"]
    if npc.get("tasks"):
        out["tasks"] = [
            {
                "npc_id": t["npc_id"],
                "task_id": t["task_id"],
                "title": t["title"],
                "stage": t["stage"],
                "progress_percent": t["progress_percent"],
                "status": t["status"],
            }
            for t in npc["tasks"]
            if isinstance(t, Mapping)
        ]
    return out


def build_npc_pulse_callbacks(
    *,
    events_snapshot_reader: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]] | None = None,
    events_validator: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None,
    events_applier: Callable[[sqlite3.Connection, str, Any, dict[str, Any]], dict[str, Any] | None] | None = None,
    knowledge_validator: Callable[[list[dict[str, Any]], dict[str, Any]], Any] | None = None,
    knowledge_applier: Callable[[sqlite3.Connection, str, Any, dict[str, Any]], Any] | None = None,
    player_location_resolver: Callable[[sqlite3.Connection, str], str] | None = None,
    budget: DepthBudget | None = None,
) -> tuple[
    Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
    Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    Callable[[sqlite3.Connection, str, Any, dict[str, Any]], dict[str, Any]],
]:
    """构建可供 `PulseWorker` 直接挂载的 `(snapshot_reader, proposal_validator, proposal_applier)` 三阶段回调。"""

    def _snapshot_reader(
        conn: sqlite3.Connection,
        instance_id: str,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        player_loc = (
            player_location_resolver(conn, instance_id)
            if player_location_resolver is not None
            else None
        )
        npc_snap = read_snapshot(
            conn,
            instance_id,
            job,
            player_location=player_loc,
            budget=budget,
        )
        compact_due_npcs = [_compact_npc_for_pulse_snapshot(n) for n in (npc_snap.get("due_npcs") or ())]
        due_npc_ids = {n["npc_id"] for n in compact_due_npcs}
        compact_other_npcs = {
            nid: _compact_npc_for_pulse_snapshot(n)
            for nid, n in (npc_snap.get("existing_npcs") or {}).items()
            if nid not in due_npc_ids
        }
        compact_tasks = {
            k: {
                "npc_id": v["npc_id"],
                "task_id": v["task_id"],
                "title": v["title"],
                "stage": v["stage"],
                "progress_percent": v["progress_percent"],
                "status": v["status"],
            }
            for k, v in (npc_snap.get("existing_tasks") or {}).items()
            if isinstance(v, Mapping)
        }

        if events_snapshot_reader is None:
            if npc_snap.get("skip"):
                return {
                    "instance_id": instance_id,
                    "world_id": npc_snap["world_id"],
                    "mode": npc_snap["mode"],
                    "kind": npc_snap["kind"],
                    "world_minute": npc_snap["world_minute"],
                    "target_world_minute": npc_snap["target_world_minute"],
                    "world_revision": npc_snap["world_revision"],
                    "base_world_revision": npc_snap["base_world_revision"],
                    "player_location": npc_snap.get("player_location", ""),
                    "status": "skipped",
                    "skip": True,
                    "reason_code": npc_snap.get("reason_code", "NO_DUE_NPCS"),
                    "due_npcs": [],
                }
            return {
                "instance_id": instance_id,
                "world_id": npc_snap["world_id"],
                "mode": npc_snap["mode"],
                "kind": npc_snap["kind"],
                "world_minute": npc_snap["world_minute"],
                "target_world_minute": npc_snap["target_world_minute"],
                "world_revision": npc_snap["world_revision"],
                "base_world_revision": npc_snap["base_world_revision"],
                "player_location": npc_snap.get("player_location", ""),
                "status": "ready",
                "skip": False,
                "reason_code": "OK",
                "due_npcs": compact_due_npcs,
                "existing_npcs": compact_other_npcs,
                "existing_tasks": compact_tasks,
                "due_events": [
                    {
                        "event_id": ev["event_id"],
                        "title": ev["title"],
                        "location": ev["location"],
                        "stage": ev["stage"],
                        "status": ev["status"],
                    }
                    for ev in (npc_snap.get("due_events") or npc_snap.get("active_events") or ())[:4]
                    if isinstance(ev, Mapping)
                ],
                "allowed_event_ids": npc_snap.get("allowed_event_ids", []),
                "allowed_fact_ids_by_npc": npc_snap.get("allowed_fact_ids_by_npc", {}),
                "allowed_fact_ids": npc_snap.get("allowed_fact_ids", []),
                "allowed_source_ids": npc_snap.get("allowed_source_ids", []),
                "other_instance_ids": npc_snap.get("other_instance_ids", []),
            }

        ev_snap = dict(events_snapshot_reader(conn, instance_id, job) or {})
        ev_blocked = bool(ev_snap.get("blocked") or ev_snap.get("status") == "blocked")
        if ev_blocked:
            return ev_snap

        ev_skip = bool(
            ev_snap.get("skip")
            or ev_snap.get("skipped")
            or ev_snap.get("status") in ("skip", "skipped")
            or (job.get("kind") == "advance" and "due_events" in ev_snap and len(ev_snap.get("due_events") or ()) == 0)
        )
        npc_skip = bool(npc_snap.get("skip"))

        merged_sources = sorted(
            set(ev_snap.get("allowed_source_ids") or ())
            | set(npc_snap.get("allowed_source_ids") or ())
        )
        merged_events = sorted(
            set(ev_snap.get("allowed_event_ids") or ())
            | set(npc_snap.get("allowed_event_ids") or ())
            | {
                str(e["event_id"])
                for e in (ev_snap.get("due_events") or ())
                if isinstance(e, Mapping) and e.get("event_id")
            }
        )

        # 移除冗余的大体积字段以保持 PulseWorker 预算估算在标准上限以内
        slim_ev_snap = {
            k: v
            for k, v in ev_snap.items()
            if k not in ("active_events", "all_events", "tier_evaluations")
        }

        merged = {
            **slim_ev_snap,
            "instance_id": instance_id,
            "job_kind": job.get("kind", "advance"),
            "mode": job.get("kind", "advance"),
            "kind": job.get("kind", "advance"),
            "world_minute": npc_snap["world_minute"],
            "target_world_minute": npc_snap["target_world_minute"],
            "world_revision": npc_snap["world_revision"],
            "base_world_revision": npc_snap["base_world_revision"],
            "player_location": npc_snap.get("player_location", ""),
            "due_npcs": compact_due_npcs,
            "existing_npcs": compact_other_npcs,
            "existing_tasks": compact_tasks,
            "allowed_fact_ids_by_npc": npc_snap.get("allowed_fact_ids_by_npc", {}),
            "allowed_fact_ids": npc_snap.get("allowed_fact_ids", []),
            "allowed_source_ids": merged_sources,
            "allowed_event_ids": merged_events,
            "other_instance_ids": npc_snap.get("other_instance_ids", []),
        }
        if ev_skip and npc_skip:
            merged["skip"] = True
            merged["status"] = "skipped"
            merged["reason_code"] = npc_snap.get("reason_code") or ev_snap.get("reason_code") or "NO_DUE_EVENTS_OR_NPCS"
        else:
            merged["skip"] = False
            merged["status"] = "ready"
            merged["reason_code"] = "OK"
            if not merged.get("due_events"):
                merged.pop("due_events", None)
            if not merged.get("existing_events") and npc_snap.get("active_events"):
                merged["existing_events"] = {
                    str(ev["event_id"]): dict(ev)
                    for ev in (npc_snap.get("active_events") or ())
                    if isinstance(ev, Mapping) and ev.get("event_id")
                }
        return merged

    def _proposal_validator(proposal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raw_ops = proposal.get("operations", [])
        if not isinstance(raw_ops, list):
            raise NpcValidationError("VALIDATION_ERROR", "operations 必须为数组。")
        transmissions = proposal.get("transmissions", []) or []

        npc_ops = [op for op in raw_ops if isinstance(op, Mapping) and _is_npc_operation(op)]
        event_ops = [op for op in raw_ops if not (isinstance(op, Mapping) and _is_npc_operation(op))]

        validated_events: Any = None
        if events_validator is not None and (event_ops or (context.get("kind") == "seed" and not npc_ops)):
            ev_payload: dict[str, Any] = {
                "instance_id": context["instance_id"],
                "mode": context.get("kind", "advance"),
                "operations": event_ops,
            }
            if proposal.get("summary"):
                ev_payload["summary"] = proposal["summary"]
            validated_events = events_validator(ev_payload, context)

        validated_npcs: ValidatedNpcProposal | None = None
        if npc_ops or (events_validator is None and context.get("kind") == "seed"):
            npc_payload: dict[str, Any] = {
                "instance_id": context["instance_id"],
                "mode": context.get("kind", "advance"),
                "world_minute": context.get("target_world_minute", context.get("world_minute", 0)),
                "base_world_revision": context.get("base_world_revision", context.get("world_revision")),
                "operations": npc_ops,
            }
            if proposal.get("summary"):
                npc_payload["summary"] = proposal["summary"]
            npc_ctx = dict(context)
            npc_ctx["allow_transmissions_envelope"] = True
            validated_npcs = validate_proposal(npc_payload, npc_ctx)

        validated_transmissions: Any = transmissions
        if transmissions and knowledge_validator is not None:
            validated_transmissions = knowledge_validator(transmissions, context)

        return {
            "kind": context.get("kind", "advance"),
            "validated_events": validated_events,
            "validated_npcs": validated_npcs,
            "validated_transmissions": validated_transmissions,
            "raw_event_operations": event_ops,
            "raw_npc_operations": npc_ops,
            "raw_transmissions": transmissions,
        }

    def _proposal_applier(
        conn: sqlite3.Connection,
        instance_id: str,
        validated: Any,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(validated, ValidatedNpcProposal):
            npc_res = apply_pulse_proposal_in_worker(conn, instance_id, validated, job)
            return {
                "events": None,
                "npcs": npc_res,
                "transmissions": None,
                "applied_operations": npc_res["applied_operations"],
            }

        val_dict = validated if isinstance(validated, Mapping) else {}
        val_events = val_dict.get("validated_events")
        val_npcs = val_dict.get("validated_npcs")
        val_trans = val_dict.get("validated_transmissions")

        events_result: dict[str, Any] | None = None
        if val_events is not None and events_applier is not None:
            events_result = events_applier(conn, instance_id, val_events, job)

        npcs_result: dict[str, Any] | None = None
        if val_npcs is not None:
            npcs_result = apply_pulse_proposal_in_worker(conn, instance_id, val_npcs, job)

        trans_result: Any = None
        if val_trans and knowledge_applier is not None:
            trans_result = knowledge_applier(conn, instance_id, val_trans, job)

        total_ops = int((npcs_result or {}).get("applied_operations", 0))
        if isinstance(events_result, Mapping):
            total_ops += int(events_result.get("applied_operations") or len(events_result.get("events") or ()))

        return {
            "events": events_result,
            "npcs": npcs_result,
            "transmissions": trans_result,
            "applied_operations": total_ops,
        }

    return _snapshot_reader, _proposal_validator, _proposal_applier


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
    """将 NPC 快照原子恢复到目标世界实例。"""
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
            raw_act_kind = _require_non_empty_str(
                item.get("current_action_kind") or item.get("action_kind"),
                f"npcs[{idx}].current_action_kind",
                40,
            )
            try:
                action_kind = normalize_action_kind(raw_act_kind, f"npcs[{idx}].current_action_kind")
            except NpcValidationError as exc:
                raise SnapshotRestoreError("SNAPSHOT_INVALID_ACTION_KIND", f"非法动作类型: {raw_act_kind!r}。") from exc

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
            log_act_kind = normalize_action_kind(
                str(log_item.get("action_kind") or "task_progress"),
                f"action_log[{l_idx}].action_kind",
            )
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
                    log_act_kind,
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
