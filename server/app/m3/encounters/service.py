"""M3.3 / M3.6 玩家主动查证、交谈、NPC 可接触性与知情查询边界及传播提议核心服务（M3 Wave 3）。

契约要求（`docs/collaboration/M3_WAVE3_GEMINI_MA.md` & `docs/collaboration/M3_WAVE3.md`）：
1. 把「世界消息」变成行动入口：从一则玩家已获知的传闻出发，提供询问可接触人物（`ask_npc`）、
   查阅公告（`check_notice`）或自由编辑行动（`free_edit`）建议。选择建议只生成/追加输入草稿，
   绝不推进世界时间（`world_minute`）或自动确认回合。
2. 提供可注入的 NPC 可接触性（`NpcContactabilityProvider`）与知情查询边界（`HolderKnowledgeProvider`）：
   - NPC 严格只使用自身已获知记录（`holder_id == npc_id` 且 `learned_minute <= world_minute`）回答，
     绝不越权读取权威 `world_events` 或其他 NPC 的私密记录；
   - 完整支持获得线索（`clue`）、拒答（`refused`）、含混回应（`vague`）、互相冲突的说法（`conflicting`）、
     NPC 不知情（`unknown`）与途中离开（`npc_departed`）；
   - 核对地点、实例、渠道与确认时刻，生成可由集成层在回合确认事务中提交的传播提议（`transmission_proposals`）；
   - 保留事实（`confirmed`）、传闻（`rumor`）、怀疑（`suspected`）、误信（`mistaken`）的区分，严禁传闻直接改写玩家金钱、背包或属性账本。
3. 支持检查点快照导出（`snapshot`）与分支隔离恢复（`restore` / `EncountersAdapter`）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
import json
import sqlite3
from typing import Any
from uuid import uuid4

from .errors import EncounterError
from .schema import ensure_encounters_schema
from .types import (
    CHANNEL_LABELS,
    CHANNELS,
    FORBIDDEN_BROADCAST_HOLDERS,
    FORBIDDEN_PATH_KEYS,
    KNOWLEDGE_TYPE_LABELS,
    KNOWLEDGE_TYPES,
    MAX_ACTION_TEXT_UNITS,
    MAX_CONTENT_UNITS,
    MAX_ID_LEN,
    MAX_LOCATION_UNITS,
    MAX_TITLE_UNITS,
    OPTION_KINDS,
    OUTCOME_LABELS,
    PLAYER_HOLDER_ID,
    PROTECTED_PLAYER_LEDGER_KEYS,
    RESPONSE_OUTCOMES,
    RESPONSE_POLICIES,
    SNAPSHOT_FORMAT,
    SNAPSHOT_VERSION,
    EncounterActionOption,
    EncounterOptionKind,
    EncounterResolutionResult,
    EncounterResponseOutcome,
    EncounterTransmissionProposal,
    HolderKnowledgeProvider,
    KnowledgeChannel,
    KnowledgeType,
    NpcContactabilityProvider,
    append_action_suggestion,
    safe_u16_truncate,
    u16_len,
    validate_clean_text,
)


def _check_forbidden_keys_recursive(obj: Any, location_hint: str = "payload") -> None:
    """递归拦截任何尝试直接修改玩家金钱、背包、属性账本或任意 JSON 路径的字段。"""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise EncounterError(422, "VALIDATION_ERROR", f"{location_hint} 包含非字符串字段名。")
            lowered = k.strip().lower()
            if lowered in PROTECTED_PLAYER_LEDGER_KEYS or lowered.startswith(("player_ledger", "character_")):
                raise EncounterError(
                    422,
                    "PLAYER_LEDGER_PROTECTED",
                    "严禁在传闻查证或人物交谈中直接修改玩家金钱、背包、属性或个人账本。",
                    {"field": k},
                )
            if (
                lowered in FORBIDDEN_PATH_KEYS
                or "." in k
                or "/" in k
                or "[" in k
                or "]" in k
                or k.startswith("$")
            ):
                raise EncounterError(
                    422,
                    "ARBITRARY_PATH_FORBIDDEN",
                    "禁止使用任意 JSON 路径或补丁操作符。",
                    {"field": k},
                )
            _check_forbidden_keys_recursive(v, f"{location_hint}.{k}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _check_forbidden_keys_recursive(item, f"{location_hint}[{idx}]")


def _require_strict_int(
    value: Any,
    field_name: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if type(value) is not int:
        raise EncounterError(
            422,
            "VALIDATION_ERROR",
            f"{field_name} 必须是整数。",
            {"field": field_name},
        )
    if min_value is not None and value < min_value:
        raise EncounterError(
            422,
            "VALIDATION_ERROR",
            f"{field_name} 不能小于 {min_value}。",
            {"field": field_name},
        )
    if max_value is not None and value > max_value:
        raise EncounterError(
            422,
            "VALIDATION_ERROR",
            f"{field_name} 不能大于 {max_value}。",
            {"field": field_name},
        )
    return value


def _validate_id(value: Any, label: str = "id") -> str:
    return validate_clean_text(value, label, MAX_ID_LEN)


def ensure_instance(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_id: str = "world_default",
    adventure_id: str | None = None,
    world_minute: int = 0,
    revision: int = 0,
) -> dict[str, Any]:
    """确保 `world_instances` 中存在指定实例（供独立测试或初始化使用，不调用 commit）。"""
    ensure_encounters_schema(conn)
    inst_id = _validate_id(instance_id, "instance_id")
    w_min = _require_strict_int(world_minute, "world_minute", min_value=0)
    rev = _require_strict_int(revision, "revision", min_value=0)
    row = conn.execute(
        "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE id = ?",
        (inst_id,),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO world_instances (id, world_id, adventure_id, world_minute, revision)
            VALUES (?, ?, ?, ?, ?)
            """,
            (inst_id, world_id, adventure_id, w_min, rev),
        )
        row = conn.execute(
            "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE id = ?",
            (inst_id,),
        ).fetchone()
    return {
        "id": str(row[0]),
        "world_id": str(row[1] or ""),
        "adventure_id": row[2],
        "world_minute": int(row[3] or 0),
        "revision": int(row[4] or 0),
        "world_revision": int(row[4] or 0),
    }


def _get_instance(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    ensure_encounters_schema(conn)
    inst_id = _validate_id(instance_id, "instance_id")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(world_instances)").fetchall()}
    rev_col = "revision" if "revision" in cols else ("world_revision" if "world_revision" in cols else "0")
    row = conn.execute(
        f"SELECT id, world_id, adventure_id, world_minute, {rev_col} FROM world_instances WHERE id = ?",
        (inst_id,),
    ).fetchone()
    if row is None:
        raise EncounterError(404, "INSTANCE_NOT_FOUND", "世界实例不存在。")
    rev = int(row[4] or 0)
    return {
        "id": str(row[0]),
        "world_id": str(row[1] or ""),
        "adventure_id": row[2],
        "world_minute": int(row[3] or 0),
        "revision": rev,
        "world_revision": rev,
    }


def _resolve_player_location(
    conn: sqlite3.Connection,
    inst_row: Mapping[str, Any],
    explicit_location: str | None = None,
) -> str:
    if explicit_location and str(explicit_location).strip():
        return validate_clean_text(explicit_location, "player_location", MAX_LOCATION_UNITS)
    adv_id = inst_row.get("adventure_id")
    if adv_id:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "adventures" in tables:
            adv_cols = {r[1] for r in conn.execute("PRAGMA table_info(adventures)").fetchall()}
            if "character_json" in adv_cols:
                row = conn.execute(
                    "SELECT character_json FROM adventures WHERE id = ?",
                    (str(adv_id),),
                ).fetchone()
                if row and row[0]:
                    try:
                        c_data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        if isinstance(c_data, Mapping) and c_data.get("location"):
                            return str(c_data["location"]).strip()
                    except Exception:
                        pass
    return "霜木镇"


# ==================== Fixture 与 Stub 注册辅助函数 ====================


def register_npc_stub(
    conn: sqlite3.Connection,
    instance_id: str,
    npc_data: Mapping[str, Any],
) -> dict[str, Any]:
    """在独立测试或 Fixture 中注册 NPC 状态与回应策略存根。"""
    _get_instance(conn, instance_id)
    npc_id = _validate_id(npc_data.get("npc_id"), "npc_id")
    name = validate_clean_text(npc_data.get("name"), "name", 80)
    role = validate_clean_text(npc_data.get("role") or "居民", "role", 120)
    location = validate_clean_text(npc_data.get("location"), "location", MAX_LOCATION_UNITS)
    activity = validate_clean_text(
        npc_data.get("current_activity") or f"在{location}处理日常事务",
        "current_activity",
        300,
    )
    action_kind = validate_clean_text(
        npc_data.get("current_action_kind") or npc_data.get("action_kind") or "idle",
        "current_action_kind",
        40,
    )
    schedule_slot = validate_clean_text(
        npc_data.get("schedule_slot") or "day_shift",
        "schedule_slot",
        80,
    )
    contactable = 1 if bool(npc_data.get("contactable", True)) else 0
    policy = str(npc_data.get("response_policy") or "cooperative").strip()
    if policy not in RESPONSE_POLICIES:
        raise EncounterError(422, "VALIDATION_ERROR", f"response_policy 非法: {policy}")
    refusal_reason = validate_clean_text(
        npc_data.get("refusal_reason") or "",
        "refusal_reason",
        300,
        allow_empty=True,
    )
    updated_min = _require_strict_int(npc_data.get("updated_minute", 0), "updated_minute", min_value=0)
    next_due = (
        _require_strict_int(npc_data["next_due_minute"], "next_due_minute", min_value=0)
        if npc_data.get("next_due_minute") is not None
        else None
    )
    rev = _require_strict_int(npc_data.get("revision", 1), "revision", min_value=1)

    conn.execute(
        """
        INSERT OR REPLACE INTO m3_encounter_npc_stubs (
            instance_id, npc_id, name, role, location, current_activity,
            current_action_kind, schedule_slot, contactable, response_policy,
            refusal_reason, updated_minute, next_due_minute, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instance_id,
            npc_id,
            name,
            role,
            location,
            activity,
            action_kind,
            schedule_slot,
            contactable,
            policy,
            refusal_reason,
            updated_min,
            next_due,
            rev,
        ),
    )
    return {
        "instance_id": instance_id,
        "npc_id": npc_id,
        "name": name,
        "role": role,
        "location": location,
        "current_activity": activity,
        "current_action_kind": action_kind,
        "schedule_slot": schedule_slot,
        "contactable": bool(contactable),
        "response_policy": policy,
        "refusal_reason": refusal_reason,
        "updated_minute": updated_min,
        "next_due_minute": next_due,
        "revision": rev,
    }


def update_npc_stub_state(
    conn: sqlite3.Connection,
    instance_id: str,
    npc_id: str,
    *,
    location: str | None = None,
    current_activity: str | None = None,
    current_action_kind: str | None = None,
    contactable: bool | None = None,
    response_policy: str | None = None,
    updated_minute: int | None = None,
) -> dict[str, Any]:
    """模拟 NPC 在世界中发生移动（如途中离开）或状态变化，用于验证交谈与确认时的边界检查。"""
    _get_instance(conn, instance_id)
    row = conn.execute(
        """
        SELECT npc_id, name, role, location, current_activity, current_action_kind,
               schedule_slot, contactable, response_policy, refusal_reason,
               updated_minute, next_due_minute, revision
        FROM m3_encounter_npc_stubs
        WHERE instance_id = ? AND npc_id = ?
        """,
        (instance_id, npc_id),
    ).fetchone()
    if row is None:
        raise EncounterError(404, "NPC_NOT_FOUND", "指定人物不存在。", {"npc_id": npc_id})

    new_loc = validate_clean_text(location, "location", MAX_LOCATION_UNITS) if location is not None else str(row[3])
    new_act = validate_clean_text(current_activity, "current_activity", 300) if current_activity is not None else str(row[4])
    new_kind = validate_clean_text(current_action_kind, "current_action_kind", 40) if current_action_kind is not None else str(row[5])
    new_cont = (1 if contactable else 0) if contactable is not None else int(row[7])
    new_pol = response_policy if response_policy is not None else str(row[8])
    new_min = _require_strict_int(updated_minute, "updated_minute", min_value=0) if updated_minute is not None else int(row[10])
    new_rev = int(row[12]) + 1

    conn.execute(
        """
        UPDATE m3_encounter_npc_stubs
        SET location = ?, current_activity = ?, current_action_kind = ?,
            contactable = ?, response_policy = ?, updated_minute = ?, revision = ?
        WHERE instance_id = ? AND npc_id = ?
        """,
        (new_loc, new_act, new_kind, new_cont, new_pol, new_min, new_rev, instance_id, npc_id),
    )
    return {
        "instance_id": instance_id,
        "npc_id": npc_id,
        "name": str(row[1]),
        "role": str(row[2]),
        "location": new_loc,
        "current_activity": new_act,
        "current_action_kind": new_kind,
        "contactable": bool(new_cont),
        "response_policy": new_pol,
        "updated_minute": new_min,
        "revision": new_rev,
    }


def register_holder_fact(
    conn: sqlite3.Connection,
    instance_id: str,
    fact_data: Mapping[str, Any],
) -> dict[str, Any]:
    """在独立测试或提交传播时写入持有人（NPC 或 player）的知情记录。"""
    _get_instance(conn, instance_id)
    _check_forbidden_keys_recursive(fact_data, "fact_data")

    holder_id = _validate_id(fact_data.get("holder_id"), "holder_id")
    if holder_id.casefold() in FORBIDDEN_BROADCAST_HOLDERS:
        raise EncounterError(422, "GLOBAL_BROADCAST_FORBIDDEN", "禁止向全员或通配符广播知情记录。")

    ktype = str(fact_data.get("knowledge_type") or "rumor").strip()
    if ktype not in KNOWLEDGE_TYPES:
        raise EncounterError(422, "INVALID_KNOWLEDGE_TYPE", f"knowledge_type 非法: {ktype}")

    channel = str(fact_data.get("channel") or "rumor").strip()
    if channel not in CHANNELS:
        raise EncounterError(422, "INVALID_CHANNEL", f"channel 非法: {channel}")

    title = validate_clean_text(fact_data.get("title"), "title", MAX_TITLE_UNITS)
    content = validate_clean_text(fact_data.get("content"), "content", MAX_CONTENT_UNITS)
    src_event_id = _validate_id(fact_data.get("source_event_id"), "source_event_id")
    src_person_id = (
        _validate_id(fact_data["source_person_id"], "source_person_id")
        if fact_data.get("source_person_id")
        else None
    )
    src_fact_id = (
        _validate_id(fact_data["source_fact_id"], "source_fact_id")
        if fact_data.get("source_fact_id")
        else None
    )
    learned_min = _require_strict_int(fact_data.get("learned_minute", 0), "learned_minute", min_value=0)
    raw_conf = fact_data.get("confidence", 0.8)
    if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
        raise EncounterError(422, "VALIDATION_ERROR", "confidence 必须为 0.0..1.0 数值。")
    confidence = float(raw_conf)
    if confidence < 0.0 or confidence > 1.0:
        raise EncounterError(422, "VALIDATION_ERROR", "confidence 必须在 0.0 到 1.0 之间。")

    fact_id = (
        _validate_id(fact_data["fact_id"], "fact_id")
        if fact_data.get("fact_id")
        else f"efact_{uuid4().hex[:12]}"
    )

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='m3_knowledge_facts'"
    ).fetchone():
        from ..events import get_event
        from ..knowledge import record_transmission

        get_event(conn, instance_id, src_event_id)
        applied = record_transmission(conn, instance_id, {
            "fact_id": fact_id, "holder_id": holder_id, "knowledge_type": ktype,
            "channel": channel, "title": title, "content": content,
            "source_event_id": src_event_id, "source_person_id": src_person_id,
            "source_fact_id": src_fact_id, "learned_minute": learned_min, "confidence": confidence,
        })[0]
        return {
            **applied, "instance_id": instance_id, "source_event_id": src_event_id,
            "source_fact_id": src_fact_id,
        }

    max_hseq_row = conn.execute(
        "SELECT COALESCE(MAX(holder_seq), 0) FROM m3_encounter_knowledge_stubs WHERE instance_id = ? AND holder_id = ?",
        (instance_id, holder_id),
    ).fetchone()
    holder_seq = int(max_hseq_row[0] or 0) + 1

    max_seq_row = conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM m3_encounter_knowledge_stubs WHERE instance_id = ?",
        (instance_id,),
    ).fetchone()
    sequence = int(max_seq_row[0] or 0) + 1

    conn.execute(
        """
        INSERT INTO m3_encounter_knowledge_stubs (
            instance_id, fact_id, holder_id, holder_seq, sequence,
            knowledge_type, title, content, source_event_id, source_person_id,
            source_fact_id, channel, learned_minute, confidence, status, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1)
        """,
        (
            instance_id,
            fact_id,
            holder_id,
            holder_seq,
            sequence,
            ktype,
            title,
            content,
            src_event_id,
            src_person_id,
            src_fact_id,
            channel,
            learned_min,
            confidence,
        ),
    )

    return {
        "instance_id": instance_id,
        "fact_id": fact_id,
        "holder_id": holder_id,
        "holder_seq": holder_seq,
        "sequence": sequence,
        "knowledge_type": ktype,
        "title": title,
        "content": content,
        "source_event_id": src_event_id,
        "source_person_id": src_person_id,
        "source_fact_id": src_fact_id,
        "channel": channel,
        "learned_minute": learned_min,
        "confidence": confidence,
        "status": "active",
        "revision": 1,
    }


def register_notice(
    conn: sqlite3.Connection,
    instance_id: str,
    notice_data: Mapping[str, Any],
) -> dict[str, Any]:
    """在指定地点公告栏登记一条可由玩家主动查阅的告示。"""
    _get_instance(conn, instance_id)
    notice_id = _validate_id(notice_data.get("notice_id") or f"notice_{uuid4().hex[:8]}", "notice_id")
    loc = validate_clean_text(notice_data.get("location"), "location", MAX_LOCATION_UNITS)
    title = validate_clean_text(notice_data.get("title"), "title", MAX_TITLE_UNITS)
    content = validate_clean_text(notice_data.get("content"), "content", MAX_CONTENT_UNITS)
    src_event_id = _validate_id(notice_data.get("source_event_id"), "source_event_id")
    ktype = str(notice_data.get("knowledge_type") or "confirmed").strip()
    if ktype not in KNOWLEDGE_TYPES:
        raise EncounterError(422, "INVALID_KNOWLEDGE_TYPE", f"knowledge_type 非法: {ktype}")
    posted_min = _require_strict_int(notice_data.get("posted_minute", 0), "posted_minute", min_value=0)
    conf = float(notice_data.get("confidence", 0.9))

    conn.execute(
        """
        INSERT OR REPLACE INTO m3_encounter_notices (
            instance_id, notice_id, location, title, content,
            source_event_id, knowledge_type, posted_minute, confidence, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (instance_id, notice_id, loc, title, content, src_event_id, ktype, posted_min, conf),
    )
    return {
        "instance_id": instance_id,
        "notice_id": notice_id,
        "location": loc,
        "title": title,
        "content": content,
        "source_event_id": src_event_id,
        "knowledge_type": ktype,
        "posted_minute": posted_min,
        "confidence": conf,
        "status": "active",
    }


# ==================== 默认可注入边界实现（SQLite + 跨表兼容） ====================


class SqlNpcContactabilityProvider:
    """默认 SQLite NPC 可接触性提供器（优先使用 NPC 账本，兼容独立查证场景）。"""

    def __init__(self, *, player_view: bool = False) -> None:
        self.player_view = player_view

    def _load_npc_row(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        npc_id: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT npc_id, name, role, location, current_activity, current_action_kind,
                   schedule_slot, contactable, response_policy, refusal_reason,
                   updated_minute, next_due_minute, revision
            FROM m3_encounter_npc_stubs
            WHERE instance_id = ? AND npc_id = ?
            """,
            (instance_id, npc_id),
        ).fetchone()
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "m3_npcs" in tables:
            nrow = conn.execute(
                """
                SELECT npc_id, name, role, location, current_activity, current_action_kind,
                       shift_name, availability, status, updated_minute, next_due_minute, revision
                FROM m3_npcs
                WHERE instance_id = ? AND npc_id = ?
                """,
                (instance_id, npc_id),
            ).fetchone()
            if nrow is not None:
                is_active = str(nrow[8] or "active") == "active"
                return {
                    "npc_id": str(nrow[0]),
                    "name": str(nrow[1]),
                    "role": str(nrow[2]),
                    "location": str(nrow[3]),
                    "current_activity": str(nrow[4]),
                    "current_action_kind": str(nrow[5]),
                    "schedule_slot": str(nrow[6]),
                    "contactable": nrow[7] == "available" and is_active,
                    "response_policy": str(row[8]) if row else "cooperative",
                    "refusal_reason": str(row[9]) if row else "",
                    "updated_minute": int(nrow[9] or 0),
                    "next_due_minute": int(nrow[10]) if nrow[10] is not None else None,
                    "revision": int(nrow[11] or 1),
                }

        if row is not None:
            return {
                "npc_id": str(row[0]),
                "name": str(row[1]),
                "role": str(row[2]),
                "location": str(row[3]),
                "current_activity": str(row[4]),
                "current_action_kind": str(row[5]),
                "schedule_slot": str(row[6]),
                "contactable": bool(int(row[7])),
                "response_policy": str(row[8] or "cooperative"),
                "refusal_reason": str(row[9] or ""),
                "updated_minute": int(row[10] or 0),
                "next_due_minute": int(row[11]) if row[11] is not None else None,
                "revision": int(row[12] or 1),
            }

        # 检查是否属于其他世界实例
        other_stub = conn.execute(
            "SELECT instance_id FROM m3_encounter_npc_stubs WHERE npc_id = ? AND instance_id != ? LIMIT 1",
            (npc_id, instance_id),
        ).fetchone()
        if other_stub is not None:
            raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "禁止跨世界实例访问人物状态。")
        if "m3_npcs" in tables:
            other_npc = conn.execute(
                "SELECT instance_id FROM m3_npcs WHERE npc_id = ? AND instance_id != ? LIMIT 1",
                (npc_id, instance_id),
            ).fetchone()
            if other_npc is not None:
                raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "禁止跨世界实例访问人物状态。")
        return None

    def check_contactability(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        npc_id: str,
        *,
        player_location: str,
        world_minute: int,
    ) -> dict[str, Any]:
        npc = self._load_npc_row(conn, instance_id, npc_id)
        if npc is None:
            raise EncounterError(404, "NPC_NOT_FOUND", "目标人物在当前实例中不存在。", {"npc_id": npc_id})

        npc_loc = npc["location"]
        same_loc = npc_loc == player_location
        if self.player_view and not same_loc:
            return {
                "npc_id": npc_id, "name": "目标人物", "role": "", "contactable": False,
                "reason_code": "NPC_AT_DIFFERENT_LOCATION", "reason": "你在这里没找到想询问的人。",
                "npc_location": "", "player_location": player_location,
            }
        if not same_loc:
            contactable = False
            if npc["current_action_kind"] == "travel":
                reason_code = "NPC_LEFT_FOR_TRAVEL"
                reason = f"{npc['name']}已动身前往「{npc_loc}」（{npc['current_activity']}），当前不在「{player_location}」。"
            else:
                reason_code = "NPC_AT_DIFFERENT_LOCATION"
                reason = f"{npc['name']}当前位于「{npc_loc}」，不在玩家所在地「{player_location}」。"
        elif not npc["contactable"]:
            contactable = False
            reason_code = "NPC_BUSY_UNCONTACTABLE"
            reason = f"{npc['name']}当前正在「{npc['current_activity']}」，暂时无法接待交谈。"
        else:
            contactable = True
            reason_code = "NPC_CONTACTABLE"
            reason = f"{npc['name']}当前在「{npc_loc}」（{npc['current_activity']}），可以上前交谈。"

        return {
            "instance_id": instance_id,
            "npc_id": npc["npc_id"],
            "name": npc["name"],
            "role": npc["role"],
            "contactable": contactable,
            "reason_code": reason_code,
            "reason": reason,
            "player_location": player_location,
            "npc_location": npc_loc,
            "current_activity": npc["current_activity"],
            "current_action_kind": npc["current_action_kind"],
            "schedule_slot": npc["schedule_slot"],
            "response_policy": npc.get("response_policy", "cooperative"),
            "refusal_reason": npc.get("refusal_reason", ""),
            "world_minute": world_minute,
            "revision": npc["revision"],
        }

    def list_contactable_npcs(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        *,
        location: str,
        world_minute: int,
    ) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        rows = conn.execute(
            """
            SELECT npc_id FROM m3_encounter_npc_stubs
            WHERE instance_id = ?
            ORDER BY npc_id ASC
            """,
            (instance_id,),
        ).fetchall()
        for r in rows:
            nid = str(r[0])
            by_id[nid] = self.check_contactability(
                conn, instance_id, nid, player_location=location, world_minute=world_minute
            )

        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "m3_npcs" in tables:
            nrows = conn.execute(
                "SELECT npc_id FROM m3_npcs WHERE instance_id = ? ORDER BY npc_id ASC",
                (instance_id,),
            ).fetchall()
            for r in nrows:
                nid = str(r[0])
                if nid not in by_id:
                    by_id[nid] = self.check_contactability(
                        conn, instance_id, nid, player_location=location, world_minute=world_minute
                    )

        return [status for status in by_id.values() if not self.player_view or status["npc_location"] == location]


class SqlHolderKnowledgeProvider:
    """默认 SQLite 知情记录查询器（严格只读指定 `holder_id` 在 `learned_minute <= up_to_minute` 的自身知情记录）。"""

    def list_holder_facts(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        holder_id: str,
        *,
        up_to_minute: int,
        source_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "instance_id = ?",
            "holder_id = ?",
            "status = 'active'",
            "learned_minute <= ?",
        ]
        params: list[Any] = [instance_id, holder_id, up_to_minute]
        if source_event_id is not None:
            clauses.append("source_event_id = ?")
            params.append(source_event_id)
        where_sql = " AND ".join(clauses)

        table = "m3_knowledge_facts" if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='m3_knowledge_facts'"
        ).fetchone() else "m3_encounter_knowledge_stubs"
        rows = conn.execute(
            f"""
            SELECT fact_id, holder_id, holder_seq, sequence, knowledge_type,
                   title, content, source_event_id, source_person_id, source_fact_id,
                   channel, learned_minute, confidence, status, revision
            FROM {table}
            WHERE {where_sql}
            ORDER BY holder_seq DESC, learned_minute DESC
            """,
            tuple(params),
        ).fetchall()
        fields = (
            "fact_id", "holder_id", "holder_seq", "sequence", "knowledge_type",
            "title", "content", "source_event_id", "source_person_id", "source_fact_id",
            "channel", "learned_minute", "confidence", "status", "revision",
        )
        facts_by_id = {row[0]: dict(zip(fields, row)) for row in rows}

        items = list(facts_by_id.values())
        items.sort(key=lambda item: (item["learned_minute"], item["holder_seq"]), reverse=True)
        return items


_DEFAULT_CONTACTABILITY_PROVIDER = SqlNpcContactabilityProvider()
_DEFAULT_KNOWLEDGE_PROVIDER = SqlHolderKnowledgeProvider()


def _format_public_player_news_item(
    fact: Mapping[str, Any],
    npc_names_by_id: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """将玩家知情记录投影为前端公开的 WorldNewsItem（严禁包含 source_event_id 或隐藏字段）。"""
    channel = str(fact.get("channel") or "rumor")
    src_pid = fact.get("source_person_id")
    speaker_name = ""
    if src_pid and npc_names_by_id and str(src_pid) in npc_names_by_id:
        speaker_name = npc_names_by_id[str(src_pid)]
    base_label = CHANNEL_LABELS.get(channel, "未知渠道")
    if (speaker_name or src_pid) and channel in ("conversation", "rumor"):
        source_label = f"{base_label} · {speaker_name or src_pid}"
    else:
        source_label = base_label

    return {
        "fact_id": str(fact["fact_id"]),
        "holder_id": PLAYER_HOLDER_ID,
        "knowledge_type": str(fact["knowledge_type"]),
        "title": str(fact["title"]),
        "content": str(fact["content"]),
        "channel": channel,
        "source_person_id": str(src_pid) if src_pid else None,
        "source_label": source_label,
        "learned_minute": int(fact["learned_minute"]),
        "confidence": float(fact["confidence"]),
        "status": str(fact.get("status") or "active"),
        "revision": int(fact.get("revision") or 1),
    }


def list_player_world_news(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    world_minute: int | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
) -> list[dict[str, Any]]:
    """只读返回玩家当前已获知的公开世界消息列表（不含任何隐藏事件字段）。"""
    inst = _get_instance(conn, instance_id)
    effective_min = inst["world_minute"] if world_minute is None else _require_strict_int(
        world_minute, "world_minute", min_value=0
    )
    kp = knowledge_provider or _DEFAULT_KNOWLEDGE_PROVIDER
    raw_facts = kp.list_holder_facts(
        conn, instance_id, PLAYER_HOLDER_ID, up_to_minute=effective_min
    )
    npc_names: dict[str, str] = {}
    for row in conn.execute(
        "SELECT npc_id, name FROM m3_encounter_npc_stubs WHERE instance_id = ?",
        (instance_id,),
    ).fetchall():
        npc_names[str(row[0])] = str(row[1])
    return [_format_public_player_news_item(f, npc_names) for f in raw_facts]


def _find_player_known_fact(
    conn: sqlite3.Connection,
    instance_id: str,
    fact_id: str,
    *,
    world_minute: int,
    knowledge_provider: HolderKnowledgeProvider,
) -> dict[str, Any]:
    """核实指定 `fact_id` 确实为当前实例玩家已获知的记录，拦截跨实例或未获知探测。"""
    fid = _validate_id(fact_id, "fact_id")
    player_facts = knowledge_provider.list_holder_facts(
        conn, instance_id, PLAYER_HOLDER_ID, up_to_minute=world_minute
    )
    for pf in player_facts:
        if pf["fact_id"] == fid:
            return pf

    # 检查是否属于其他实例
    other_row = conn.execute(
        "SELECT instance_id FROM m3_encounter_knowledge_stubs WHERE fact_id = ? AND instance_id != ? LIMIT 1",
        (fid, instance_id),
    ).fetchone()
    if other_row is not None:
        raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "禁止跨世界实例引用消息记录。")
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "m3_knowledge_facts" in tables:
        other_kf = conn.execute(
            "SELECT instance_id FROM m3_knowledge_facts WHERE fact_id = ? AND instance_id != ? LIMIT 1",
            (fid, instance_id),
        ).fetchone()
        if other_kf is not None:
            raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "禁止跨世界实例引用消息记录。")

    raise EncounterError(
        404,
        "FACT_NOT_KNOWN_TO_PLAYER",
        "玩家当前尚未通过任何渠道获知该消息，无法从该条目发起查证行动。",
    )


# ==================== 1. 从「世界消息」生成行动入口与输入草稿 ====================


def list_news_action_options(
    conn: sqlite3.Connection,
    instance_id: str,
    fact_id: str,
    *,
    player_location: str | None = None,
    world_minute: int | None = None,
    contactability_provider: NpcContactabilityProvider | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
) -> dict[str, Any]:
    """从玩家已获知的一则消息/传闻出发，只读构建行动入口选项（询问人物 / 查阅公告 / 自由编辑）。

    契约保证：
    - 只读操作，绝不推进 `world_minute` 或自动确认回合；
    - 每个选项均携带 `suggested_action_text`，供移动端写入 `ActionComposer` 由玩家编辑后发送。
    """
    inst = _get_instance(conn, instance_id)
    confirmed_min = inst["world_minute"] if world_minute is None else _require_strict_int(
        world_minute, "world_minute", min_value=0
    )
    if confirmed_min > inst["world_minute"]:
        raise EncounterError(
            422,
            "FUTURE_ENCOUNTER_FORBIDDEN",
            "查证时刻不能超过当前已确认的世界时间。",
            {"expected_world_minute": inst["world_minute"], "current_world_minute": confirmed_min},
        )

    loc = _resolve_player_location(conn, inst, player_location)
    cp = contactability_provider or _DEFAULT_CONTACTABILITY_PROVIDER
    kp = knowledge_provider or _DEFAULT_KNOWLEDGE_PROVIDER

    origin_fact = _find_player_known_fact(
        conn, instance_id, fact_id, world_minute=confirmed_min, knowledge_provider=kp
    )
    topic_title = origin_fact["title"]

    all_npc_statuses = cp.list_contactable_npcs(
        conn, instance_id, location=loc, world_minute=confirmed_min
    )

    options: list[EncounterActionOption] = []

    # 1. 询问可接触人物选项（包含在场可交谈 NPC 以及已动身离开的 NPC 提示）
    for npc_st in all_npc_statuses:
        nid = str(npc_st["npc_id"])
        nname = str(npc_st["name"])
        nrole = str(npc_st.get("role") or "")
        is_cont = bool(npc_st["contactable"])
        r_code = str(npc_st.get("reason_code") or ("NPC_CONTACTABLE" if is_cont else "NPC_UNAVAILABLE"))
        r_text = str(npc_st.get("reason") or "")

        action_text = (
            f"在{loc}向{nname}（{nrole}）当面打听关于「{topic_title}」的实情，询问其所知的来龙去脉。"
            if nrole
            else f"在{loc}向{nname}当面打听关于「{topic_title}」的实情。"
        )
        options.append(
            EncounterActionOption(
                option_id=f"opt_{fact_id}_ask_{nid}",
                fact_id=fact_id,
                kind="ask_npc",
                label=f"询问{nname}" if is_cont else f"寻找{nname}（不在场）",
                description=r_text or (f"向{nname}求证「{topic_title}」" if is_cont else f"{nname}当前不在{loc}"),
                suggested_action_text=action_text,
                target_location=loc,
                target_npc_id=nid,
                target_npc_name=nname,
                target_npc_role=nrole,
                contactable=is_cont,
                reason_code=r_code,
                disabled_reason=None if is_cont else r_text,
            )
        )

    # 2. 查阅公告选项
    options.append(
        EncounterActionOption(
            option_id=f"opt_{fact_id}_check_notice",
            fact_id=fact_id,
            kind="check_notice",
            label=f"查阅{loc}公告栏",
            description=f"前往{loc}公告栏或商栈揭示板查阅与「{topic_title}」相关的正式告示。",
            suggested_action_text=f"前往{loc}公告栏仔细查阅近期张贴的告示，核实「{topic_title}」是否有官府或商会的正式通告。",
            target_location=loc,
            contactable=True,
            reason_code="NOTICE_BOARD_AVAILABLE",
        )
    )

    # 3. 自由编辑行动选项
    options.append(
        EncounterActionOption(
            option_id=f"opt_{fact_id}_free_edit",
            fact_id=fact_id,
            kind="free_edit",
            label="自由编辑查证行动",
            description=f"围绕「{topic_title}」在输入框自由编写你的调查、蹲守或交涉行动。",
            suggested_action_text=f"围绕「{topic_title}」展开查证：",
            target_location=loc,
            contactable=True,
            reason_code="FREE_EDIT_READY",
        )
    )

    return {
        "instance_id": instance_id,
        "fact_id": fact_id,
        "fact_title": topic_title,
        "fact_knowledge_type": origin_fact["knowledge_type"],
        "player_location": loc,
        "world_minute": confirmed_min,
        "world_revision": inst["revision"],
        "advances_clock": False,
        "auto_commits_turn": False,
        "options": [opt.to_dict() for opt in options],
    }


def get_encounter_entrypoint_bundle(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    player_location: str | None = None,
    world_minute: int | None = None,
    contactability_provider: NpcContactabilityProvider | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
) -> dict[str, Any]:
    """获取当前实例玩家「世界消息行动入口」完整视图包（消息列表 + 当前地点人物状态 + 每条消息的行动建议）。"""
    inst = _get_instance(conn, instance_id)
    confirmed_min = inst["world_minute"] if world_minute is None else _require_strict_int(
        world_minute, "world_minute", min_value=0
    )
    loc = _resolve_player_location(conn, inst, player_location)
    cp = contactability_provider or _DEFAULT_CONTACTABILITY_PROVIDER
    kp = knowledge_provider or _DEFAULT_KNOWLEDGE_PROVIDER

    news_items = list_player_world_news(
        conn, instance_id, world_minute=confirmed_min, knowledge_provider=kp
    )
    npcs = cp.list_contactable_npcs(
        conn, instance_id, location=loc, world_minute=confirmed_min
    )

    options_by_fact_id: dict[str, list[dict[str, Any]]] = {}
    for item in news_items:
        fid = item["fact_id"]
        opt_res = list_news_action_options(
            conn,
            instance_id,
            fid,
            player_location=loc,
            world_minute=confirmed_min,
            contactability_provider=cp,
            knowledge_provider=kp,
        )
        options_by_fact_id[fid] = opt_res["options"]

    return {
        "instance_id": instance_id,
        "adventure_id": inst.get("adventure_id") or instance_id,
        "player_location": loc,
        "world_minute": confirmed_min,
        "world_revision": inst["revision"],
        "advances_clock": False,
        "auto_commits_turn": False,
        "news_items": news_items,
        "contactable_npcs": npcs,
        "options_by_fact_id": options_by_fact_id,
    }


def select_action_option_for_composer(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    fact_id: str,
    option_id: str | None = None,
    option_kind: str | None = None,
    target_npc_id: str | None = None,
    current_draft: str = "",
    max_length: int = MAX_ACTION_TEXT_UNITS,
    player_location: str | None = None,
    world_minute: int | None = None,
    contactability_provider: NpcContactabilityProvider | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
) -> dict[str, Any]:
    """选择一则消息下的建议项，生成或追加写入现有输入区的行动草稿文本。

    严格遵守契约：
    - 只生成输入区草稿，不修改数据库中的 `world_minute` 或 `revision`，也不自动确认回合。
    """
    bundle = list_news_action_options(
        conn,
        instance_id,
        fact_id,
        player_location=player_location,
        world_minute=world_minute,
        contactability_provider=contactability_provider,
        knowledge_provider=knowledge_provider,
    )
    options = bundle["options"]
    selected: dict[str, Any] | None = None

    for opt in options:
        if option_id and opt["option_id"] == option_id:
            selected = opt
            break
        if option_kind and opt["kind"] == option_kind:
            if option_kind != "ask_npc" or opt.get("target_npc_id") == target_npc_id:
                selected = opt
                break

    if selected is None:
        raise EncounterError(404, "OPTION_NOT_FOUND", "未找到对应的消息行动建议项。")

    next_draft = append_action_suggestion(
        current_draft,
        selected["suggested_action_text"],
        limit=max_length,
    )

    return {
        "instance_id": instance_id,
        "fact_id": fact_id,
        "option": selected,
        "previous_draft": current_draft,
        "composer_draft_text": next_draft,
        "world_minute": bundle["world_minute"],
        "world_revision": bundle["world_revision"],
        "advances_clock": False,
        "auto_commits_turn": False,
    }


# ==================== 2. NPC 交谈与公告查证推演（草案阶段） ====================


def evaluate_encounter_inquiry(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    origin_fact_id: str,
    option_kind: str = "ask_npc",
    target_npc_id: str | None = None,
    action_text: str,
    player_location: str | None = None,
    world_minute: int | None = None,
    contactability_provider: NpcContactabilityProvider | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
) -> EncounterResolutionResult:
    """根据玩家从传闻发起的行动，严格基于 NPC 自身知情记录或本地公告推演回应与传播提议。

    核心边界：
    1. 核对实例、地点、渠道与确认时刻（拒绝跨实例、未来时间与未获知来源）。
    2. NPC 只使用自身已获知记录（`holder_id == target_npc_id` 且 `learned_minute <= confirmed_world_minute`）回答，
       绝不读取 `world_events` 权威描述或其他 NPC 的知情记录。
    3. 支持六种明确结果：`clue`（获得线索）、`refused`（拒答）、`vague`（含混回应）、
       `conflicting`（互相冲突的说法）、`unknown`（NPC 不知情）、`npc_departed`（NPC 途中离开或不在场）。
    4. 保留 `confirmed` / `suspected` / `rumor` / `mistaken` 的区分，禁止传闻直接改账（`can_settle_ledger = False`）。
    """
    inst = _get_instance(conn, instance_id)
    confirmed_min = inst["world_minute"] if world_minute is None else _require_strict_int(
        world_minute, "world_minute", min_value=0
    )
    if confirmed_min > inst["world_minute"]:
        raise EncounterError(
            422,
            "FUTURE_ENCOUNTER_FORBIDDEN",
            "查证时刻不能超过当前已确认世界时间。",
            {"expected_world_minute": inst["world_minute"], "current_world_minute": confirmed_min},
        )

    if option_kind not in OPTION_KINDS:
        raise EncounterError(
            422,
            "INVALID_OPTION_KIND",
            f"option_kind 必须是 {OPTION_KINDS} 之一。",
            {"option_kind": option_kind},
        )

    cleaned_action = validate_clean_text(action_text, "action_text", MAX_ACTION_TEXT_UNITS)
    loc = _resolve_player_location(conn, inst, player_location)
    cp = contactability_provider or _DEFAULT_CONTACTABILITY_PROVIDER
    kp = knowledge_provider or _DEFAULT_KNOWLEDGE_PROVIDER

    origin_fact = _find_player_known_fact(
        conn, instance_id, origin_fact_id, world_minute=confirmed_min, knowledge_provider=kp
    )
    target_event_id = str(origin_fact["source_event_id"])
    player_facts_for_event = kp.list_holder_facts(
        conn,
        instance_id,
        PLAYER_HOLDER_ID,
        up_to_minute=confirmed_min,
        source_event_id=target_event_id,
    )

    # ---- 分支 A：查阅本地公告栏 (check_notice) ----
    if option_kind == "check_notice":
        notice_row = conn.execute(
            """
            SELECT notice_id, title, content, source_event_id, knowledge_type, posted_minute, confidence
            FROM m3_encounter_notices
            WHERE instance_id = ? AND location = ? AND status = 'active'
              AND source_event_id = ? AND posted_minute <= ?
            ORDER BY posted_minute DESC, notice_id ASC
            LIMIT 1
            """,
            (instance_id, loc, target_event_id, confirmed_min),
        ).fetchone()
        if notice_row is None:
            return EncounterResolutionResult(
                instance_id=instance_id,
                origin_fact_id=origin_fact_id,
                option_kind="check_notice",
                target_npc_id=None,
                player_location=loc,
                world_minute=confirmed_min,
                outcome="unknown",
                reason_code="NOTICE_NOT_FOUND",
                narrative_reply=f"你在「{loc}」公告栏前仔细翻看，暂未找到关于「{origin_fact['title']}」的新告示。",
                transmission_proposals=(),
            )

        n_title = str(notice_row[1])
        n_content = str(notice_row[2])
        n_eid = str(notice_row[3])
        n_ktype: KnowledgeType = str(notice_row[4])  # type: ignore[assignment]
        n_conf = float(notice_row[6])

        prop = EncounterTransmissionProposal(
            holder_id=PLAYER_HOLDER_ID,
            knowledge_type=n_ktype,
            channel="notice",
            title=n_title,
            content=n_content,
            source_event_id=n_eid,
            source_person_id=None,
            source_fact_id=None,
            learned_minute=confirmed_min,
            confidence=n_conf,
            reason=f"玩家在{loc}公告栏主动查阅告示核实传闻。",
        )
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind="check_notice",
            target_npc_id=None,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="clue",
            reason_code="NOTICE_VERIFIED",
            narrative_reply=f"你在「{loc}」公告栏找到了相关告示：《{n_title}》——{n_content}",
            transmission_proposals=(prop,),
        )

    # ---- 分支 B：自由编辑行动（若未指定 target_npc_id，可先返回自由调查回应或匹配目标 NPC） ----
    if option_kind == "free_edit" and not target_npc_id:
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind="free_edit",
            target_npc_id=None,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="vague",
            reason_code="FREE_INQUIRY_RECORDED",
            narrative_reply=f"你在「{loc}」按照自己的思路展开查证（{cleaned_action}），留意着四周关于「{origin_fact['title']}」的蛛丝马迹。",
            transmission_proposals=(),
        )

    # ---- 分支 C：向特定 NPC 当面询问 (ask_npc) ----
    if not target_npc_id:
        raise EncounterError(422, "TARGET_NPC_REQUIRED", "询问人物行动必须指定 target_npc_id。")

    npc_status = cp.check_contactability(
        conn,
        instance_id,
        target_npc_id,
        player_location=loc,
        world_minute=confirmed_min,
    )
    npc_name = str(npc_status["name"])

    # 1. 检查 NPC 是否途中离开或不在当前地点
    if not npc_status["contactable"]:
        r_code = str(npc_status.get("reason_code") or "NPC_LEFT_FOR_TRAVEL")
        r_msg = str(npc_status.get("reason") or f"{npc_name}当前不在{loc}。")
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind=option_kind,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="npc_departed",
            reason_code=r_code,
            narrative_reply=r_msg,
            transmission_proposals=(),
        )

    # 2. 严格只查询该 NPC 自身在 confirmed_min 及之前已获知的记录
    npc_facts = kp.list_holder_facts(
        conn,
        instance_id,
        target_npc_id,
        up_to_minute=confirmed_min,
        source_event_id=target_event_id,
    )
    if not npc_facts:
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind=option_kind,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="unknown",
            reason_code="NPC_HAS_NO_KNOWLEDGE",
            narrative_reply=f"{npc_name}听罢摇了摇头，表示自己目前也未听说关于「{origin_fact['title']}」的确切内情。",
            transmission_proposals=(),
        )

    top_npc_fact = npc_facts[0]
    policy = str(npc_status.get("response_policy") or "cooperative")

    # 3. 若 NPC 策略为拒答 (reluctant)
    if policy == "reluctant":
        refusal_text = str(npc_status.get("refusal_reason") or "").strip()
        reply = (
            f"{npc_name}神色微敛，婉拒透露详情：{refusal_text}"
            if refusal_text
            else f"{npc_name}对此事讳莫如深，只推说事关商行账目与行规，不便向外人多言。"
        )
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind=option_kind,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="refused",
            reason_code="NPC_REFUSED_TO_ANSWER",
            narrative_reply=reply,
            transmission_proposals=(),
        )

    npc_ktype: KnowledgeType = str(top_npc_fact["knowledge_type"])  # type: ignore[assignment]
    npc_conf = float(top_npc_fact["confidence"])
    npc_title = str(top_npc_fact["title"])
    npc_content = str(top_npc_fact["content"])
    npc_fid = str(top_npc_fact["fact_id"])

    # 4. 若 NPC 策略为含混回应 (evasive) 或其自身可信度较低 (< 0.55)
    if policy == "evasive" or npc_conf < 0.55:
        vague_ktype: KnowledgeType = "rumor" if npc_ktype == "rumor" else "suspected"
        vague_conf = round(max(0.25, min(0.5, npc_conf * 0.85)), 2)
        vague_title = f"{npc_name}半遮半掩的说法：{npc_title}"
        vague_content = f"{npc_name}言辞含混地提到：{npc_content}（其本人亦不愿打包票）。"
        prop = EncounterTransmissionProposal(
            holder_id=PLAYER_HOLDER_ID,
            knowledge_type=vague_ktype,
            channel="conversation",
            title=safe_u16_truncate(vague_title, MAX_TITLE_UNITS),
            content=safe_u16_truncate(vague_content, MAX_CONTENT_UNITS),
            source_event_id=target_event_id,
            source_person_id=target_npc_id,
            source_fact_id=npc_fid,
            learned_minute=confirmed_min,
            confidence=vague_conf,
            reason=f"玩家向{npc_name}打听传闻，获得含混回应。",
        )
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind=option_kind,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="vague",
            reason_code="NPC_VAGUE_RESPONSE",
            narrative_reply=vague_content,
            transmission_proposals=(prop,),
        )

    # 5. 检查是否与玩家已获知的同一事件说法存在冲突（例如先前为 mistaken/rumor 且内容不一致，或另一位转述者说法相反）
    conflicting_ids: list[str] = []
    for pf in player_facts_for_event:
        pf_ktype = str(pf["knowledge_type"])
        pf_content = str(pf["content"])
        pf_speaker = pf.get("source_person_id")
        if pf_content != npc_content and (
            (pf_ktype == "mistaken" and npc_ktype == "confirmed")
            or (pf_ktype == "confirmed" and npc_ktype == "mistaken")
            or (pf_speaker and pf_speaker != target_npc_id)
        ):
            conflicting_ids.append(str(pf["fact_id"]))

    relayed_conf = round(max(0.3, min(1.0, npc_conf * 0.95)), 2)
    prop = EncounterTransmissionProposal(
        holder_id=PLAYER_HOLDER_ID,
        knowledge_type=npc_ktype,
        channel="conversation",
        title=npc_title,
        content=npc_content,
        source_event_id=target_event_id,
        source_person_id=target_npc_id,
        source_fact_id=npc_fid,
        learned_minute=confirmed_min,
        confidence=relayed_conf,
        reason=f"玩家当面询问{npc_name}，获知其关于「{origin_fact['title']}」的知情记录。",
    )

    if conflicting_ids:
        conflict_note = (
            f"{npc_name}的说法（{KNOWLEDGE_TYPE_LABELS.get(npc_ktype, npc_ktype)}：「{npc_title}」）"
            f"与你此前听到的版本（「{origin_fact['title']}」）存在明显出入，双方认知保留各自来源供你甄别。"
        )
        return EncounterResolutionResult(
            instance_id=instance_id,
            origin_fact_id=origin_fact_id,
            option_kind=option_kind,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            player_location=loc,
            world_minute=confirmed_min,
            outcome="conflicting",
            reason_code="CONFLICTING_ACCOUNTS_DETECTED",
            narrative_reply=f"{npc_name}向你讲述了另一番实情：{npc_content}",
            conflict_summary=conflict_note,
            conflicting_fact_ids=tuple(conflicting_ids),
            transmission_proposals=(prop,),
        )

    return EncounterResolutionResult(
        instance_id=instance_id,
        origin_fact_id=origin_fact_id,
        option_kind=option_kind,  # type: ignore[arg-type]
        target_npc_id=target_npc_id,
        player_location=loc,
        world_minute=confirmed_min,
        outcome="clue",
        reason_code="NPC_SHARED_CLUE",
        narrative_reply=f"{npc_name}向你透露了所知详情：{npc_content}",
        transmission_proposals=(prop,),
    )


def prepare_encounter_draft(
    conn: sqlite3.Connection,
    instance_id: str,
    payload: Mapping[str, Any],
    *,
    contactability_provider: NpcContactabilityProvider | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
    draft_id_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """根据玩家编辑后的行动生成待确认查证草案（不推进世界时钟、不直接写入玩家消息账本）。"""
    if not isinstance(payload, Mapping):
        raise EncounterError(422, "VALIDATION_ERROR", "草案请求体必须是 JSON 对象。")
    _check_forbidden_keys_recursive(payload, "draft_payload")

    inst = _get_instance(conn, instance_id)
    if payload.get("instance_id") is not None and str(payload["instance_id"]) != instance_id:
        raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "草案请求的 instance_id 与当前实例不一致。")

    origin_fact_id = _validate_id(payload.get("fact_id") or payload.get("origin_fact_id"), "fact_id")
    option_kind = str(payload.get("option_kind") or "ask_npc").strip()
    target_npc_id = (
        _validate_id(payload["target_npc_id"], "target_npc_id")
        if payload.get("target_npc_id")
        else None
    )
    action_text = validate_clean_text(payload.get("action_text"), "action_text", MAX_ACTION_TEXT_UNITS)
    player_loc = _resolve_player_location(conn, inst, payload.get("player_location"))
    confirmed_min = inst["world_minute"]
    if payload.get("world_minute") is not None:
        req_min = _require_strict_int(payload["world_minute"], "world_minute", min_value=0)
        if req_min != confirmed_min:
            raise EncounterError(
                422,
                "TIMESTAMP_MISMATCH",
                "草案请求的 world_minute 与当前实例已确认时间不一致。",
                {"expected_world_minute": confirmed_min, "current_world_minute": req_min},
            )

    resolution = evaluate_encounter_inquiry(
        conn,
        instance_id,
        origin_fact_id=origin_fact_id,
        option_kind=option_kind,
        target_npc_id=target_npc_id,
        action_text=action_text,
        player_location=player_loc,
        world_minute=confirmed_min,
        contactability_provider=contactability_provider,
        knowledge_provider=knowledge_provider,
    )

    draft_id = (
        draft_id_factory()
        if draft_id_factory is not None
        else f"edraft_{uuid4().hex[:12]}"
    )
    adv_id = str(inst.get("adventure_id") or instance_id)
    proposals_list = [p.to_dict() for p in resolution.transmission_proposals]

    conn.execute(
        """
        INSERT INTO m3_encounter_drafts (
            instance_id, draft_id, adventure_id, origin_fact_id, option_kind,
            target_npc_id, player_location, action_text, outcome, reason_code,
            narrative_reply, conflict_summary, conflicting_fact_ids_json,
            transmission_proposals_json, base_world_revision, draft_world_minute,
            status, committed_minute, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', NULL, 1)
        """,
        (
            instance_id,
            draft_id,
            adv_id,
            origin_fact_id,
            resolution.option_kind,
            target_npc_id,
            player_loc,
            action_text,
            resolution.outcome,
            resolution.reason_code,
            resolution.narrative_reply,
            resolution.conflict_summary,
            json.dumps(list(resolution.conflicting_fact_ids), ensure_ascii=False),
            json.dumps(proposals_list, ensure_ascii=False),
            inst["revision"],
            confirmed_min,
        ),
    )

    return {
        "draft_id": draft_id,
        "instance_id": instance_id,
        "adventure_id": adv_id,
        "fact_id": origin_fact_id,
        "origin_fact_id": origin_fact_id,
        "option_kind": resolution.option_kind,
        "target_npc_id": target_npc_id,
        "player_location": player_loc,
        "action_text": action_text,
        "outcome": resolution.outcome,
        "outcome_label": OUTCOME_LABELS.get(resolution.outcome, resolution.outcome),
        "reason_code": resolution.reason_code,
        "narrative_reply": resolution.narrative_reply,
        "conflict_note": resolution.conflict_summary,
        "conflicting_fact_ids": list(resolution.conflicting_fact_ids),
        "proposed_transmissions": proposals_list,
        "world_minute": confirmed_min,
        "base_world_revision": inst["revision"],
        "clock_advanced": False,
        "committed": False,
        "can_settle_ledger": False,
    }


# ==================== 3. 传播提议纯校验与回合确认事务内应用 ====================


def validate_encounter_proposal(
    proposal: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> list[EncounterTransmissionProposal]:
    """在事务外纯函数校验互动产生的传播提议（拦截账本篡改、任意路径、非法渠道、未来时间与全员广播）。"""
    if not isinstance(proposal, Mapping):
        raise EncounterError(422, "VALIDATION_ERROR", "传播提议必须是 JSON 对象。")
    _check_forbidden_keys_recursive(proposal, "proposal")

    ctx = context or {}
    expected_inst = str(ctx.get("instance_id") or proposal.get("instance_id") or "")
    if proposal.get("instance_id") and expected_inst and str(proposal["instance_id"]) != expected_inst:
        raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "传播提议实例标识不匹配。")

    max_minute = ctx.get("world_minute")
    raw_list = (
        proposal.get("transmissions")
        if "transmissions" in proposal
        else proposal.get("proposed_transmissions", [])
    )
    if not isinstance(raw_list, list):
        raise EncounterError(422, "VALIDATION_ERROR", "transmissions 必须是列表。")

    validated: list[EncounterTransmissionProposal] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, Mapping):
            raise EncounterError(422, "VALIDATION_ERROR", f"transmissions[{idx}] 必须是对象。")
        _check_forbidden_keys_recursive(item, f"transmissions[{idx}]")

        holder = _validate_id(item.get("holder_id") or PLAYER_HOLDER_ID, f"transmissions[{idx}].holder_id")
        if holder.casefold() in FORBIDDEN_BROADCAST_HOLDERS:
            raise EncounterError(422, "GLOBAL_BROADCAST_FORBIDDEN", "禁止全局或通配符授知。")

        ktype = str(item.get("knowledge_type") or "rumor").strip()
        if ktype not in KNOWLEDGE_TYPES:
            raise EncounterError(422, "INVALID_KNOWLEDGE_TYPE", f"knowledge_type 非法: {ktype}")

        channel = str(item.get("channel") or "conversation").strip()
        if channel not in CHANNELS:
            raise EncounterError(422, "INVALID_CHANNEL", f"channel 非法: {channel}")

        src_person = (
            _validate_id(item["source_person_id"], f"transmissions[{idx}].source_person_id")
            if item.get("source_person_id")
            else None
        )
        if channel == "conversation" and not src_person:
            raise EncounterError(
                422,
                "SOURCE_PERSON_REQUIRED",
                "当面交谈（conversation）渠道必须指明转述人 source_person_id。",
                {"channel": channel},
            )

        learned_min = _require_strict_int(
            item.get("learned_minute", 0),
            f"transmissions[{idx}].learned_minute",
            min_value=0,
        )
        if max_minute is not None and learned_min > int(max_minute):
            raise EncounterError(
                422,
                "FUTURE_TRANSMISSION_FORBIDDEN",
                "传播记录的获知时间不能晚于当前已确认的世界时间。",
                {"expected_world_minute": int(max_minute), "current_world_minute": learned_min},
            )

        title = validate_clean_text(item.get("title"), f"transmissions[{idx}].title", MAX_TITLE_UNITS)
        content = validate_clean_text(item.get("content"), f"transmissions[{idx}].content", MAX_CONTENT_UNITS)
        src_event = _validate_id(item.get("source_event_id"), f"transmissions[{idx}].source_event_id")
        src_fact = (
            _validate_id(item["source_fact_id"], f"transmissions[{idx}].source_fact_id")
            if item.get("source_fact_id")
            else None
        )
        conf = float(item.get("confidence", 0.8))
        if conf < 0.0 or conf > 1.0:
            raise EncounterError(422, "VALIDATION_ERROR", "confidence 超出 0.0..1.0 范围。")

        validated.append(
            EncounterTransmissionProposal(
                holder_id=holder,
                knowledge_type=ktype,  # type: ignore[arg-type]
                channel=channel,  # type: ignore[arg-type]
                title=title,
                content=content,
                source_event_id=src_event,
                source_person_id=src_person,
                source_fact_id=src_fact,
                learned_minute=learned_min,
                confidence=conf,
                reason=str(item.get("reason") or ""),
            )
        )
    return validated


def commit_encounter_draft(
    conn: sqlite3.Connection,
    instance_id: str,
    draft_id: str,
    *,
    commit_world_minute: int | None = None,
    expected_world_revision: int | None = None,
    require_current_draft: bool = False,
    contactability_provider: NpcContactabilityProvider | None = None,
    knowledge_provider: HolderKnowledgeProvider | None = None,
    knowledge_applier: Callable[[sqlite3.Connection, str, list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """在调用方回合确认的短写事务（SAVEPOINT）内复核地点、实例、版本与确认时刻，并提交传播提议。

    - 不自行调用 `conn.commit()`；
    - 若草案生成后、确认前目标 NPC 已动身离开当前地点（途中离开），抛出 `409 NPC_DEPARTED_BEFORE_COMMIT` 并回滚；
    - 若世界实例版本冲突，抛出 `409 WORLD_REVISION_CONFLICT` 并回滚；
    - 绝不修改玩家金钱、背包或角色属性账本。
    """
    ensure_encounters_schema(conn)
    inst_id = _validate_id(instance_id, "instance_id")
    did = _validate_id(draft_id, "draft_id")
    cp = contactability_provider or _DEFAULT_CONTACTABILITY_PROVIDER
    kp = knowledge_provider or _DEFAULT_KNOWLEDGE_PROVIDER

    conn.execute("SAVEPOINT sp_m3_encounters_commit")
    try:
        inst = _get_instance(conn, inst_id)
        db_rev = int(inst["revision"])
        db_min = int(inst["world_minute"])

        if expected_world_revision is not None:
            exp_rev = _require_strict_int(expected_world_revision, "expected_world_revision", min_value=0)
            if exp_rev != db_rev:
                raise EncounterError(
                    409,
                    "WORLD_REVISION_CONFLICT",
                    "世界实例版本已变化，请刷新后再确认查证回合。",
                    {"expected_world_revision": exp_rev, "current_world_revision": db_rev},
                )

        effective_commit_min = (
            _require_strict_int(commit_world_minute, "commit_world_minute", min_value=db_min)
            if commit_world_minute is not None
            else db_min
        )

        row = conn.execute(
            """
            SELECT instance_id, draft_id, adventure_id, origin_fact_id, option_kind,
                   target_npc_id, player_location, action_text, outcome, reason_code,
                   narrative_reply, conflict_summary, conflicting_fact_ids_json,
                   transmission_proposals_json, base_world_revision, draft_world_minute,
                   status, committed_minute, revision
            FROM m3_encounter_drafts
            WHERE instance_id = ? AND draft_id = ?
            """,
            (inst_id, did),
        ).fetchone()
        if row is None:
            other_d = conn.execute(
                "SELECT instance_id FROM m3_encounter_drafts WHERE draft_id = ? AND instance_id != ? LIMIT 1",
                (did, inst_id),
            ).fetchone()
            if other_d is not None:
                raise EncounterError(403, "CROSS_INSTANCE_ACCESS", "禁止跨世界实例确认草案。")
            raise EncounterError(404, "DRAFT_NOT_FOUND", "待确认的查证草案不存在。")

        status_now = str(row[16])
        if status_now == "committed":
            # 幂等重放：返回当前已获知消息与已提交状态，不重复写入
            conn.execute("RELEASE SAVEPOINT sp_m3_encounters_commit")
            return {
                "instance_id": inst_id,
                "adventure_id": str(row[2]),
                "draft_id": did,
                "committed": True,
                "idempotent_replay": True,
                "outcome": str(row[8]),
                "world_minute": int(row[17] or effective_commit_min),
                "world_revision": db_rev,
                "new_messages": [],
                "all_player_news": list_player_world_news(conn, inst_id, world_minute=effective_commit_min, knowledge_provider=kp),
                "ledger_modified": False,
            }

        option_kind = str(row[4])
        target_npc_id = row[5]
        player_loc = str(row[6])
        outcome = str(row[8])
        raw_proposals = json.loads(row[13] or "[]")

        # 若为向在场 NPC 询问且草案预计会产生传播，必须在确认时刻再次核实该 NPC 仍在原地且可接触
        if option_kind == "ask_npc" and target_npc_id and raw_proposals:
            live_contact = cp.check_contactability(
                conn,
                inst_id,
                str(target_npc_id),
                player_location=player_loc,
                world_minute=effective_commit_min,
            )
            if not live_contact["contactable"]:
                raise EncounterError(
                    409,
                    "NPC_DEPARTED_BEFORE_COMMIT",
                    f"{live_contact['name']}在回合确认前已离开「{player_loc}」（{live_contact['reason']}），本次交谈未能完成。",
                    {
                        "npc_id": str(target_npc_id),
                        "player_location": player_loc,
                        "npc_location": str(live_contact["npc_location"]),
                        "reason_code": str(live_contact["reason_code"]),
                    },
                )

        if require_current_draft and (
            int(row[14]) != db_rev or player_loc != _resolve_player_location(conn, inst)
        ):
            raise EncounterError(
                409, "WORLD_REVISION_CONFLICT", "旅程状态已变化，请重新生成查证草案。",
            )

        # 校验并应用传播提议
        for p in raw_proposals:
            p["learned_minute"] = effective_commit_min

        validated_props = validate_encounter_proposal(
            {"instance_id": inst_id, "transmissions": raw_proposals},
            {"instance_id": inst_id, "world_minute": effective_commit_min},
        )

        applied_records: list[dict[str, Any]] = []
        for vp in validated_props:
            # 核实转述人此刻确实拥有该事件知情记录且获知时间不晚于确认时刻
            if vp.channel == "conversation" and vp.source_person_id:
                speaker_facts = kp.list_holder_facts(
                    conn,
                    inst_id,
                    vp.source_person_id,
                    up_to_minute=effective_commit_min,
                    source_event_id=vp.source_event_id,
                )
                if not speaker_facts:
                    raise EncounterError(
                        422,
                        "RELAY_HOLDER_UNKNOWN",
                        "转述人在确认时刻尚未获知该事件，拒绝写入无源传闻。",
                        {"npc_id": vp.source_person_id},
                    )
            rec = register_holder_fact(conn, inst_id, vp.to_dict())
            applied_records.append(rec)

        if knowledge_applier is not None and validated_props:
            knowledge_applier(conn, inst_id, [vp.to_dict() for vp in validated_props])

        conn.execute(
            """
            UPDATE world_instances
            SET world_minute = MAX(world_minute, ?),
                revision = revision + 1
            WHERE id = ?
            """,
            (effective_commit_min, inst_id),
        )
        conn.execute(
            """
            UPDATE m3_encounter_drafts
            SET status = 'committed', committed_minute = ?, revision = revision + 1
            WHERE instance_id = ? AND draft_id = ?
            """,
            (effective_commit_min, inst_id, did),
        )

        hist_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(history_seq), 0) FROM m3_encounter_history WHERE instance_id = ?",
                (inst_id,),
            ).fetchone()[0]
            or 0
        ) + 1
        conn.execute(
            """
            INSERT INTO m3_encounter_history (
                instance_id, history_seq, draft_id, origin_fact_id, option_kind,
                target_npc_id, player_location, action_text, outcome, world_minute,
                applied_fact_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inst_id,
                hist_seq,
                did,
                str(row[3]),
                option_kind,
                target_npc_id,
                player_loc,
                str(row[7]),
                outcome,
                effective_commit_min,
                json.dumps([r["fact_id"] for r in applied_records], ensure_ascii=False),
            ),
        )

        npc_names: dict[str, str] = {}
        for nrow in conn.execute(
            "SELECT npc_id, name FROM m3_encounter_npc_stubs WHERE instance_id = ?",
            (inst_id,),
        ).fetchall():
            npc_names[str(nrow[0])] = str(nrow[1])

        public_new_messages = [
            _format_public_player_news_item(r, npc_names) for r in applied_records
        ]
        all_news = list_player_world_news(
            conn, inst_id, world_minute=effective_commit_min, knowledge_provider=kp
        )

        conn.execute("RELEASE SAVEPOINT sp_m3_encounters_commit")
        return {
            "instance_id": inst_id,
            "adventure_id": str(row[2]),
            "draft_id": did,
            "committed": True,
            "idempotent_replay": False,
            "outcome": outcome,
            "world_minute": effective_commit_min,
            "world_revision": db_rev + 1,
            "new_messages": public_new_messages,
            "all_player_news": all_news,
            "ledger_modified": False,
        }
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_m3_encounters_commit")
        conn.execute("RELEASE SAVEPOINT sp_m3_encounters_commit")
        raise


# ==================== 4. 快照导出与分支恢复（snapshot / restore / EncountersAdapter） ====================


def snapshot(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    """导出指定世界实例的互动查证模块完整 JSON 可序列化快照。"""
    inst = _get_instance(conn, instance_id)

    npc_rows = conn.execute(
        """
        SELECT npc_id, name, role, location, current_activity, current_action_kind,
               schedule_slot, contactable, response_policy, refusal_reason,
               updated_minute, next_due_minute, revision
        FROM m3_encounter_npc_stubs
        WHERE instance_id = ?
        ORDER BY npc_id ASC
        """,
        (instance_id,),
    ).fetchall()
    npc_stubs = [
        {
            "npc_id": str(r[0]),
            "name": str(r[1]),
            "role": str(r[2]),
            "location": str(r[3]),
            "current_activity": str(r[4]),
            "current_action_kind": str(r[5]),
            "schedule_slot": str(r[6]),
            "contactable": bool(int(r[7])),
            "response_policy": str(r[8]),
            "refusal_reason": str(r[9]),
            "updated_minute": int(r[10]),
            "next_due_minute": int(r[11]) if r[11] is not None else None,
            "revision": int(r[12]),
        }
        for r in npc_rows
    ]

    fact_rows = conn.execute(
        """
        SELECT fact_id, holder_id, holder_seq, sequence, knowledge_type,
               title, content, source_event_id, source_person_id, source_fact_id,
               channel, learned_minute, confidence, status, revision
        FROM m3_encounter_knowledge_stubs
        WHERE instance_id = ?
        ORDER BY sequence ASC
        """,
        (instance_id,),
    ).fetchall()
    knowledge_stubs = [
        {
            "fact_id": str(r[0]),
            "holder_id": str(r[1]),
            "holder_seq": int(r[2]),
            "sequence": int(r[3]),
            "knowledge_type": str(r[4]),
            "title": str(r[5]),
            "content": str(r[6]),
            "source_event_id": str(r[7]),
            "source_person_id": r[8],
            "source_fact_id": r[9],
            "channel": str(r[10]),
            "learned_minute": int(r[11]),
            "confidence": float(r[12]),
            "status": str(r[13]),
            "revision": int(r[14]),
        }
        for r in fact_rows
    ]

    notice_rows = conn.execute(
        """
        SELECT notice_id, location, title, content, source_event_id,
               knowledge_type, posted_minute, confidence, status
        FROM m3_encounter_notices
        WHERE instance_id = ?
        ORDER BY notice_id ASC
        """,
        (instance_id,),
    ).fetchall()
    notices = [
        {
            "notice_id": str(r[0]),
            "location": str(r[1]),
            "title": str(r[2]),
            "content": str(r[3]),
            "source_event_id": str(r[4]),
            "knowledge_type": str(r[5]),
            "posted_minute": int(r[6]),
            "confidence": float(r[7]),
            "status": str(r[8]),
        }
        for r in notice_rows
    ]

    hist_rows = conn.execute(
        """
        SELECT history_seq, draft_id, origin_fact_id, option_kind, target_npc_id,
               player_location, action_text, outcome, world_minute, applied_fact_ids_json
        FROM m3_encounter_history
        WHERE instance_id = ?
        ORDER BY history_seq ASC
        """,
        (instance_id,),
    ).fetchall()
    history = [
        {
            "history_seq": int(r[0]),
            "draft_id": str(r[1]),
            "origin_fact_id": str(r[2]),
            "option_kind": str(r[3]),
            "target_npc_id": r[4],
            "player_location": str(r[5]),
            "action_text": str(r[6]),
            "outcome": str(r[7]),
            "world_minute": int(r[8]),
            "applied_fact_ids": json.loads(r[9] or "[]"),
        }
        for r in hist_rows
    ]

    return {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "instance_id": instance_id,
        "world_id": inst["world_id"],
        "world_minute": inst["world_minute"],
        "world_revision": inst["revision"],
        "npc_stubs": npc_stubs,
        "knowledge_stubs": knowledge_stubs,
        "notices": notices,
        "history": history,
    }


def restore(
    conn: sqlite3.Connection,
    target_instance_id: str,
    snapshot_data: Mapping[str, Any],
    *,
    world_id: str | None = None,
) -> dict[str, Any]:
    """在 `SAVEPOINT` 保护下将快照恢复至新分支实例 `target_instance_id`，确保分支间完全隔离。"""
    ensure_encounters_schema(conn)
    target_id = _validate_id(target_instance_id, "target_instance_id")
    if not isinstance(snapshot_data, Mapping):
        raise EncounterError(422, "SNAPSHOT_INVALID", "快照必须是字典对象。")
    if snapshot_data.get("format") != SNAPSHOT_FORMAT or snapshot_data.get("version") != SNAPSHOT_VERSION:
        raise EncounterError(422, "SNAPSHOT_INVALID", "不支持的互动快照格式或版本。")

    snap_min = _require_strict_int(snapshot_data.get("world_minute", 0), "world_minute", min_value=0)
    snap_rev = _require_strict_int(snapshot_data.get("world_revision", 0), "world_revision", min_value=0)
    snap_world_id = str(world_id or snapshot_data.get("world_id") or "world_default")

    conn.execute("SAVEPOINT sp_m3_encounters_restore")
    try:
        ensure_instance(
            conn,
            target_id,
            world_id=snap_world_id,
            world_minute=snap_min,
            revision=snap_rev,
        )
        conn.execute("DELETE FROM m3_encounter_history WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_encounter_drafts WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_encounter_notices WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_encounter_knowledge_stubs WHERE instance_id = ?", (target_id,))
        conn.execute("DELETE FROM m3_encounter_npc_stubs WHERE instance_id = ?", (target_id,))

        for npc in snapshot_data.get("npc_stubs", []):
            register_npc_stub(conn, target_id, npc)

        for kf in snapshot_data.get("knowledge_stubs", []):
            conn.execute(
                """
                INSERT INTO m3_encounter_knowledge_stubs (
                    instance_id, fact_id, holder_id, holder_seq, sequence,
                    knowledge_type, title, content, source_event_id, source_person_id,
                    source_fact_id, channel, learned_minute, confidence, status, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    str(kf["fact_id"]),
                    str(kf["holder_id"]),
                    int(kf["holder_seq"]),
                    int(kf["sequence"]),
                    str(kf["knowledge_type"]),
                    str(kf["title"]),
                    str(kf["content"]),
                    str(kf["source_event_id"]),
                    kf.get("source_person_id"),
                    kf.get("source_fact_id"),
                    str(kf["channel"]),
                    int(kf["learned_minute"]),
                    float(kf["confidence"]),
                    str(kf.get("status") or "active"),
                    int(kf.get("revision") or 1),
                ),
            )

        for nt in snapshot_data.get("notices", []):
            register_notice(conn, target_id, nt)

        for h in snapshot_data.get("history", []):
            conn.execute(
                """
                INSERT INTO m3_encounter_history (
                    instance_id, history_seq, draft_id, origin_fact_id, option_kind,
                    target_npc_id, player_location, action_text, outcome, world_minute,
                    applied_fact_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    int(h["history_seq"]),
                    str(h["draft_id"]),
                    str(h["origin_fact_id"]),
                    str(h["option_kind"]),
                    h.get("target_npc_id"),
                    str(h["player_location"]),
                    str(h["action_text"]),
                    str(h["outcome"]),
                    int(h["world_minute"]),
                    json.dumps(list(h.get("applied_fact_ids") or []), ensure_ascii=False),
                ),
            )

        conn.execute("RELEASE SAVEPOINT sp_m3_encounters_restore")
        return snapshot(conn, target_id)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_m3_encounters_restore")
        conn.execute("RELEASE SAVEPOINT sp_m3_encounters_restore")
        raise


class EncountersAdapter:
    """兼容 `app.m3.core.adapters.DomainAdapter` 的领域适配器（`name = "encounters"`）。"""

    name = "encounters"

    def snapshot(self, conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
        return snapshot(conn, instance_id)

    def restore(
        self,
        conn: sqlite3.Connection,
        snapshot_payload: Mapping[str, Any],
        *,
        new_instance_id: str,
        world_id: str,
    ) -> None:
        restore(conn, new_instance_id, snapshot_payload, world_id=world_id)
