"""M3.4 NPC 自主行动、动态模拟深度评估与结构化提议纯函数校验器（M3 Wave 4）。

核心职责：
1. `evaluate_npc_depths`：按相关性动态判定近景（`foreground`）、关联（`associated`）、背景（`background`）三档模拟深度，
   输出可测量的数量/字符预算与明确的升降档理由（`promoted` / `demoted` / `unchanged` + `reason_codes` + 中文说明）。
2. `validate_proposal`：在 SQLite 事务外运行的纯函数校验器（零数据库 I/O、零网络调用），
   严格校验八种动作类型（出行 `travel`、留在原处 `stay`、等待 `wait`、无变化 `no_change`、改变计划 `plan_change`、
   交谈安排 `conversation_plan`、换班 `shift_change`、任务进度 `task_progress`）、
   已确认世界时间约束、实例内已存在因果引用、版本一致性、重复任务拦截，
   并严禁直接修改玩家金钱、背包、属性和玩家知情事实。
3. `normalize_initial_npc_seed_proposal`：支持从 AI 初始化分析草案接收人物状态、动作安排、关系与推演标记。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import (
    ArbitraryPathForbiddenError,
    CrossInstanceReferenceError,
    DuplicateNpcTaskError,
    InvalidCausalReferenceError,
    NpcNotDueError,
    NpcNotFoundError,
    NpcRevisionConflictError,
    NpcValidationError,
    PlayerKnowledgeProtectedError,
    PlayerLedgerProtectedError,
    SourceFabricatedError,
    WorldRevisionConflictError,
)
from .types import (
    DepthBudget,
    FORBIDDEN_PATH_KEYS,
    MAX_ACTIVITY_UNITS,
    MAX_CAUSES_PER_ACTION,
    MAX_GOAL_UNITS,
    MAX_LOCATION_UNITS,
    MAX_MOTIVATION_UNITS,
    MAX_NAME_UNITS,
    MAX_NPC_ID_UNITS,
    MAX_OPERATIONS_PER_PROPOSAL,
    MAX_PLAN_UNITS,
    MAX_ROLE_UNITS,
    MAX_SHIFT_UNITS,
    MAX_TASK_ID_UNITS,
    MAX_TASK_STAGE_UNITS,
    MAX_TASK_TITLE_UNITS,
    MAX_TOPIC_UNITS,
    MIN_ACTION_INTERVAL_BY_TIER,
    MIN_DURATION_BY_ACTION_KIND,
    NPC_ACTION_KIND_ALIASES,
    NPC_ACTION_KINDS,
    NPC_AVAILABILITY_STATES,
    NPC_DEPTH_TIER_ALIASES,
    NPC_DEPTH_TIER_LABELS_ZH,
    NPC_GROUNDING_ALIASES,
    NPC_STATUSES,
    PROPOSAL_MODES,
    PROTECTED_PLAYER_KNOWLEDGE_KEYS,
    PROTECTED_PLAYER_LEDGER_KEYS,
    TASK_STATUSES,
    NpcActionKind,
    NpcDepthEvaluation,
    NpcDepthTier,
    NpcGroundingKind,
    NpcValidationContext,
    ValidatedNpcActionOp,
    ValidatedNpcProposal,
)

_ALLOWED_ROOT_KEYS = frozenset(
    {
        "operations",
        "npc_actions",
        "npcs",
        "initial_npcs",
        "transmissions",
        "mode",
        "kind",
        "summary",
        "instance_id",
        "world_instance_id",
        "world_minute",
        "target_world_minute",
        "world_revision",
        "base_world_revision",
        "revision",
        "idempotency_key",
        "job_id",
    }
)

_SERVER_MANAGED_TIME_KEYS = frozenset(
    {"created_minute", "updated_minute", "last_action_minute", "created_at", "updated_at"}
)

_ALLOWED_OP_KEYS = frozenset(
    {
        "op",
        "npc_id",
        "expected_npc_revision",
        "name",
        "role",
        "identity",
        "occupation",
        "faction",
        "home_location",
        "location",
        "current_location",
        "start_location",
        "from_location",
        "to_location",
        "destination_location",
        "current_activity",
        "activity",
        "initial_activity",
        "action_kind",
        "current_action_kind",
        "initial_action_kind",
        "arrangement",
        "decision",
        "motivation",
        "motive",
        "goal",
        "previous_plan",
        "new_plan",
        "plan_summary",
        "depth_tier",
        "depth_reason",
        "status",
        "availability",
        "duration_minutes",
        "due_in_minutes",
        "next_due_minute",
        "next_update_minute",
        "target_npc_id",
        "counterpart_npc_id",
        "meeting_location",
        "conversation_topic",
        "topic",
        "from_shift",
        "to_shift",
        "shift_name",
        "task_id",
        "task_title",
        "task_stage",
        "stage",
        "task_progress_percent",
        "progress_percent",
        "task_status",
        "cause_event_ids",
        "cause_fact_ids",
        "source_ids",
        "reason",
        "grounding",
        "origin",
        "is_inferred",
        "relationships",
        "relations",
        "known_relations",
        "instance_id",
    }
)

_TIER_RANK: dict[str, int] = {
    "foreground": 3,
    "associated": 2,
    "background": 1,
}


def _u16_units(text: str, field_name: str) -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise NpcValidationError(
            "INVALID_TEXT_ENCODING",
            f"字段 {field_name} 包含不完整的 UTF-16 代理对字符。",
            {"field": field_name},
        ) from exc


def _safe_u16_truncate(text: str, max_units: int) -> str:
    if max_units <= 0:
        return ""
    if _u16_units(text, "text") <= max_units:
        return text
    units = 0
    chars: list[str] = []
    for ch in text:
        ch_units = 2 if ord(ch) > 0xFFFF else 1
        if units + ch_units > max_units:
            break
        chars.append(ch)
        units += ch_units
    return "".join(chars)


def _require_non_empty_str(value: Any, field_name: str, max_units: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为非空字符串。",
            {"field": field_name},
        )
    cleaned = value.strip()
    units = _u16_units(cleaned, field_name)
    if units > max_units:
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 长度超过 {max_units} 个 UTF-16 单位限制。",
            {"field": field_name, "max_units": max_units, "actual_units": units},
        )
    return cleaned


def _optional_str(value: Any, field_name: str, max_units: int, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为字符串。",
            {"field": field_name},
        )
    cleaned = value.strip()
    if not cleaned:
        return default
    units = _u16_units(cleaned, field_name)
    if units > max_units:
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 长度超过 {max_units} 个 UTF-16 单位限制。",
            {"field": field_name, "max_units": max_units, "actual_units": units},
        )
    return cleaned


def _require_strict_int(
    value: Any,
    field_name: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if type(value) is not int:
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为整数（拒绝布尔值或其它类型）。",
            {"field": field_name},
        )
    if min_value is not None and value < min_value:
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 不能小于 {min_value}。",
            {"field": field_name, "min_value": min_value, "actual": value},
        )
    if max_value is not None and value > max_value:
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 不能大于 {max_value}。",
            {"field": field_name, "max_value": max_value, "actual": value},
        )
    return value


def normalize_depth_tier(raw_tier: Any, field_name: str = "depth_tier") -> NpcDepthTier:
    if not isinstance(raw_tier, str) or not raw_tier.strip():
        raise NpcValidationError(
            "INVALID_DEPTH_TIER",
            f"字段 {field_name} 必须为有效的模拟深度档位（foreground/associated/background）。",
            {"field": field_name},
        )
    key = raw_tier.strip().lower()
    resolved = NPC_DEPTH_TIER_ALIASES.get(key) or NPC_DEPTH_TIER_ALIASES.get(raw_tier.strip())
    if resolved is None:
        raise NpcValidationError(
            "INVALID_DEPTH_TIER",
            f"字段 {field_name} 档位无效: {raw_tier!r}，仅支持 foreground(近景)/associated(关联)/background(背景)。",
            {"field": field_name, "value": raw_tier},
        )
    return resolved


def normalize_action_kind(raw_kind: Any, field_name: str = "action_kind") -> NpcActionKind:
    if not isinstance(raw_kind, str) or not raw_kind.strip():
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为非空字符串。",
            {"field": field_name},
        )
    cleaned = raw_kind.strip()
    resolved = NPC_ACTION_KIND_ALIASES.get(cleaned.lower()) or NPC_ACTION_KIND_ALIASES.get(cleaned)
    if resolved is None or resolved not in NPC_ACTION_KINDS:
        raise NpcValidationError(
            "INVALID_ACTION_KIND",
            f"{field_name} ({cleaned!r}) 不受支持，仅支持 {sorted(NPC_ACTION_KINDS)}。",
            {"action_kind": cleaned},
        )
    return resolved


def _normalize_grounding(raw_op: Mapping[str, Any], field_prefix: str) -> NpcGroundingKind:
    if raw_op.get("is_inferred") is True:
        return "inferred"
    raw_val = raw_op.get("grounding") if raw_op.get("grounding") is not None else raw_op.get("origin")
    if raw_val is None:
        return "canon"
    if not isinstance(raw_val, str) or not raw_val.strip():
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"{field_prefix}.grounding 必须为非空字符串。",
            {"field": f"{field_prefix}.grounding"},
        )
    cleaned = raw_val.strip()
    resolved = NPC_GROUNDING_ALIASES.get(cleaned.lower()) or NPC_GROUNDING_ALIASES.get(cleaned)
    if resolved is None:
        raise NpcValidationError(
            "INVALID_GROUNDING",
            f"{field_prefix}.grounding ({cleaned!r}) 无效，仅支持 'canon' 或 'inferred'。",
            {"grounding": cleaned},
        )
    return resolved


def _normalize_relationships(raw_rels: Any, field_name: str) -> tuple[dict[str, Any], ...]:
    if raw_rels is None:
        return ()
    items: list[dict[str, Any]] = []
    if isinstance(raw_rels, Mapping):
        for target_id, rel_desc in raw_rels.items():
            tid = _require_non_empty_str(str(target_id), f"{field_name}.target_npc_id", MAX_NPC_ID_UNITS)
            if tid.lower() == "player":
                raise PlayerKnowledgeProtectedError(field_name, "NPC 初始关系表不得直接修改受保护的 player 个人账本。")
            if isinstance(rel_desc, Mapping):
                rel_text = _require_non_empty_str(
                    rel_desc.get("relation") or rel_desc.get("summary") or rel_desc.get("attitude") or "关联",
                    f"{field_name}[{tid}].relation",
                    160,
                )
                items.append({"target_npc_id": tid, "relation": rel_text})
            else:
                rel_text = _require_non_empty_str(str(rel_desc), f"{field_name}[{tid}]", 160)
                items.append({"target_npc_id": tid, "relation": rel_text})
        return tuple(items)

    if not isinstance(raw_rels, (list, tuple)):
        raise NpcValidationError("VALIDATION_ERROR", f"{field_name} 必须为数组或对象。", {"field": field_name})
    if len(raw_rels) > 20:
        raise NpcValidationError("VALIDATION_ERROR", f"{field_name} 数量不能超过 20 条。", {"field": field_name})

    for idx, r in enumerate(raw_rels):
        if not isinstance(r, Mapping):
            raise NpcValidationError("VALIDATION_ERROR", f"{field_name}[{idx}] 必须为对象。")
        tid = _require_non_empty_str(
            r.get("target_npc_id") or r.get("npc_id") or r.get("target_id") or r.get("person_id"),
            f"{field_name}[{idx}].target_npc_id",
            MAX_NPC_ID_UNITS,
        )
        if tid.lower() == "player":
            raise PlayerKnowledgeProtectedError(field_name, "NPC 初始关系表不得将 player 作为后台写库目标。")
        rel_text = _require_non_empty_str(
            r.get("relation") or r.get("relationship") or r.get("summary") or r.get("attitude"),
            f"{field_name}[{idx}].relation",
            160,
        )
        items.append({"target_npc_id": tid, "relation": rel_text})
    return tuple(items)


def _check_security_recursive(
    obj: Any,
    *,
    instance_id: str,
    allow_transmissions: bool = False,
    path: str = "proposal",
) -> None:
    """递归拦截玩家个人账本、直接写入玩家知情事实、任意 JSON 路径写入与跨实例标识。"""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise NpcValidationError("VALIDATION_ERROR", f"{path} 包含非字符串键。")
            lowered = k.strip().lower()
            if lowered in PROTECTED_PLAYER_KNOWLEDGE_KEYS or lowered.startswith("player_fact"):
                raise PlayerKnowledgeProtectedError(k)
            if (
                lowered in PROTECTED_PLAYER_LEDGER_KEYS
                or lowered.startswith(("player_ledger", "player_coin", "player_inv", "character_"))
            ):
                raise PlayerLedgerProtectedError(k)
            if (
                lowered in FORBIDDEN_PATH_KEYS
                or "." in k
                or "/" in k
                or "[" in k
                or k.startswith("$")
            ):
                raise ArbitraryPathForbiddenError(k)
            if lowered == "transmissions":
                if not allow_transmissions and v not in (None, [], ()):
                    raise PlayerKnowledgeProtectedError(
                        "transmissions",
                        "NPC 状态提议不得直接内嵌知识传播写入，请通过知识传播边界独立校验。",
                    )
                continue
            if lowered in ("holder_id", "target_npc_id", "counterpart_npc_id") and isinstance(v, str):
                if v.strip().lower() == "player":
                    raise PlayerKnowledgeProtectedError(
                        k,
                        "NPC 自主行动不得直接将 player 作为后台交谈/授知写库目标，玩家互动须由玩家主动发起。",
                    )
            if lowered in ("instance_id", "world_instance_id") and v is not None:
                if not isinstance(v, str) or not v.strip():
                    raise NpcValidationError("VALIDATION_ERROR", f"{path}.{k} 必须为非空字符串。")
                if instance_id and v.strip() != instance_id:
                    raise CrossInstanceReferenceError(instance_id, v.strip(), v.strip())
            _check_security_recursive(
                v,
                instance_id=instance_id,
                allow_transmissions=allow_transmissions,
                path=f"{path}.{k}",
            )
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _check_security_recursive(
                item,
                instance_id=instance_id,
                allow_transmissions=allow_transmissions,
                path=f"{path}[{idx}]",
            )


def _participant_matches_npc(participant: str, npc: Mapping[str, Any]) -> bool:
    p = participant.strip()
    if not p:
        return False
    npc_id = str(npc.get("npc_id") or "").strip()
    name = str(npc.get("name") or "").strip()
    if p == npc_id or p == name:
        return True
    if ":" in p and npc_id.endswith("_" + p.split(":", 1)[1]):
        return True
    return False


def _format_npc_prompt_excerpt(npc: Mapping[str, Any], tier: NpcDepthTier, max_chars: int) -> str:
    npc_id = str(npc.get("npc_id") or "")
    name = str(npc.get("name") or npc_id)
    role = str(npc.get("role") or npc.get("identity") or "")
    loc = str(npc.get("location") or "")
    act = str(npc.get("current_activity") or "")
    mot = str(npc.get("motivation") or "")
    due = npc.get("next_due_minute")
    rev = int(npc.get("revision") or 1)
    if tier == "foreground":
        goal = str(npc.get("goal") or "")
        shift = str(npc.get("shift_name") or "")
        task_id = str(npc.get("current_task_id") or "")
        raw = (
            f"[近景|{npc_id}|v{rev}] {name}（{role}）@{loc} | 活动:{act} | 动机:{mot}"
            f" | 目标:{goal or '无'} | 班次:{shift or '常规'} | 任务:{task_id or '无'} | 到期:{due}"
        )
    elif tier == "associated":
        raw = f"[关联|{npc_id}|v{rev}] {name}（{role}）@{loc} | 活动:{act} | 动机:{mot} | 到期:{due}"
    else:
        raw = f"[背景|{npc_id}|v{rev}] {name}@{loc} | {act} | 到期:{due}"
    return _safe_u16_truncate(raw, max_chars)


def evaluate_npc_depths(
    npcs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    world_minute: int,
    player_location: str = "",
    due_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
    active_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
    focus_npc_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str] = (),
    budget: DepthBudget | None = None,
) -> dict[str, Any]:
    """按相关性动态计算每个 NPC 的模拟深度（`foreground` / `associated` / `background`）、
    升降档理由以及可测量的数量和字符预算。
    """
    world_minute = _require_strict_int(world_minute, "world_minute", min_value=0)
    effective_budget = budget or DepthBudget()
    clean_player_loc = player_location.strip() if isinstance(player_location, str) else ""
    focus_set = {str(x).strip() for x in focus_npc_ids if str(x).strip()}

    all_events = list(due_events) + [
        ev for ev in active_events if ev.get("event_id") not in {d.get("event_id") for d in due_events}
    ]
    due_event_ids = {str(ev.get("event_id") or "") for ev in due_events if ev.get("event_id")}
    active_event_ids = {str(ev.get("event_id") or "") for ev in all_events if ev.get("event_id")}

    # 第一遍：识别直接处于近景（foreground）或关联（associated）的 NPC
    preliminary_tiers: dict[str, NpcDepthTier] = {}
    preliminary_reasons: dict[str, list[str]] = {}
    preliminary_scores: dict[str, int] = {}
    foreground_npc_ids: set[str] = set()
    foreground_counterparts: set[str] = set()

    for npc in npcs:
        npc_id = str(npc.get("npc_id") or "").strip()
        if not npc_id:
            continue
        loc = str(npc.get("location") or "").strip()
        prev_raw = str(npc.get("depth_tier") or "background").strip()
        prev_tier: NpcDepthTier = NPC_DEPTH_TIER_ALIASES.get(prev_raw, "background")

        reasons: list[str] = []
        score = 0

        co_located = bool(clean_player_loc and loc == clean_player_loc)
        in_focus = npc_id in focus_set

        in_due_event = False
        in_local_player_event = False
        in_any_active_event = False
        for ev in all_events:
            ev_id = str(ev.get("event_id") or "")
            ev_loc = str(ev.get("location") or "").strip()
            participants = ev.get("participants") or ()
            matched = any(_participant_matches_npc(str(p), npc) for p in participants)
            if matched:
                in_any_active_event = True
                if ev_id in due_event_ids:
                    in_due_event = True
                if clean_player_loc and ev_loc == clean_player_loc:
                    in_local_player_event = True

        npc_causes = set(npc.get("cause_event_ids") or ())
        has_due_causal_link = bool(npc_causes & due_event_ids)
        has_active_causal_link = bool(npc_causes & active_event_ids)

        if in_focus:
            score += 100
            reasons.append("PLAYER_FOCUS_TARGET")
        if co_located:
            score += 80
            reasons.append("CO_LOCATED_WITH_PLAYER")
        if in_local_player_event:
            score += 45
            reasons.append("LOCAL_PLAYER_LOCATION_EVENT")
        if in_due_event:
            score += 50
            reasons.append("ACTIVE_DUE_EVENT_PARTICIPANT")
        elif in_any_active_event:
            score += 35
            reasons.append("ACTIVE_EVENT_PARTICIPANT")
        if has_due_causal_link:
            score += 30
            reasons.append("DUE_EVENT_CAUSAL_MOTIVATION")
        elif has_active_causal_link:
            score += 20
            reasons.append("ACTIVE_EVENT_CAUSAL_MOTIVATION")

        # 近景（foreground）：要求与玩家同处当前地点，或为玩家直接关注对象（若未指定 player_location 则回退到本地到期事件）
        if in_focus or co_located or (not clean_player_loc and in_due_event):
            tier: NpcDepthTier = "foreground"
            foreground_npc_ids.add(npc_id)
            cp = str(npc.get("counterpart_npc_id") or "").strip()
            if cp:
                foreground_counterparts.add(cp)
        elif in_due_event or in_any_active_event or in_local_player_event or has_due_causal_link or has_active_causal_link:
            tier = "associated"
        else:
            tier = "background"

        if prev_tier == "foreground" and not co_located and clean_player_loc:
            reasons.insert(0, "LEFT_PLAYER_LOCATION")

        preliminary_tiers[npc_id] = tier
        preliminary_reasons[npc_id] = reasons
        preliminary_scores[npc_id] = score

    # 第二遍：识别与近景 NPC 存在直接交谈/协作关联的 NPC，将其提升至 associated
    for npc in npcs:
        npc_id = str(npc.get("npc_id") or "").strip()
        if not npc_id:
            continue
        tier = preliminary_tiers[npc_id]
        cp = str(npc.get("counterpart_npc_id") or "").strip()
        if tier == "background" and (npc_id in foreground_counterparts or (cp and cp in foreground_npc_ids)):
            preliminary_tiers[npc_id] = "associated"
            preliminary_reasons[npc_id].append("LINKED_TO_FOREGROUND_NPC")
            preliminary_scores[npc_id] += 25
        elif tier == "background" and not preliminary_reasons[npc_id]:
            preliminary_reasons[npc_id].append("DISTANT_NO_ACTIVE_LINK")

    # 第三遍：生成升降档说明并按三档配额与字符预算挑选本次脉动模拟的到期 NPC
    raw_evals: list[dict[str, Any]] = []
    for npc in npcs:
        npc_id = str(npc.get("npc_id") or "").strip()
        if not npc_id:
            continue
        name = str(npc.get("name") or npc_id)
        loc = str(npc.get("location") or "")
        status = str(npc.get("status") or "active")
        next_due = npc.get("next_due_minute")
        is_due = (
            status == "active"
            and isinstance(next_due, int)
            and not isinstance(next_due, bool)
            and next_due <= world_minute
        )
        prev_raw = str(npc.get("depth_tier") or "background").strip()
        prev_tier = NPC_DEPTH_TIER_ALIASES.get(prev_raw, "background")
        curr_tier = preliminary_tiers[npc_id]
        reasons = preliminary_reasons[npc_id]
        score = preliminary_scores[npc_id]

        prev_rank = _TIER_RANK[prev_tier]
        curr_rank = _TIER_RANK[curr_tier]
        if curr_rank > prev_rank:
            transition: str = "promoted"
            trans_zh = f"升档({NPC_DEPTH_TIER_LABELS_ZH[prev_tier]}->{NPC_DEPTH_TIER_LABELS_ZH[curr_tier]})"
        elif curr_rank < prev_rank:
            transition = "demoted"
            trans_zh = f"降档({NPC_DEPTH_TIER_LABELS_ZH[prev_tier]}->{NPC_DEPTH_TIER_LABELS_ZH[curr_tier]})"
        else:
            transition = "unchanged"
            trans_zh = f"维持({NPC_DEPTH_TIER_LABELS_ZH[curr_tier]})"

        reason_summary = f"{trans_zh}：{'、'.join(reasons)}（相关性得分 {score}）"
        raw_evals.append(
            {
                "npc": npc,
                "npc_id": npc_id,
                "name": name,
                "location": loc,
                "status": status,
                "previous_tier": prev_tier,
                "current_tier": curr_tier,
                "transition": transition,
                "relevance_score": score,
                "reason_codes": tuple(reasons),
                "reason_summary": reason_summary,
                "is_due": is_due,
                "next_due_minute": int(next_due) if isinstance(next_due, int) else None,
            }
        )

    due_candidates = [item for item in raw_evals if item["is_due"]]
    due_candidates.sort(
        key=lambda item: (
            -_TIER_RANK[item["current_tier"]],
            item["next_due_minute"] if item["next_due_minute"] is not None else 10**9,
            -item["relevance_score"],
            item["npc_id"],
        )
    )

    tier_selected_counts: dict[str, int] = {"foreground": 0, "associated": 0, "background": 0}
    total_selected = 0
    total_used_chars = 0
    selection_result_by_npc: dict[str, dict[str, Any]] = {}

    for item in due_candidates:
        npc_id = item["npc_id"]
        tier = item["current_tier"]
        tier_cap = effective_budget.tier_count_cap(tier)
        char_cap = effective_budget.tier_char_cap(tier)

        if total_selected >= effective_budget.max_total_npcs:
            selection_result_by_npc[npc_id] = {
                "selected": False,
                "excluded_reason": "TOTAL_COUNT_BUDGET_EXCEEDED",
                "allocated_char_budget": 0,
                "used_chars": 0,
                "excerpt": "",
            }
            continue

        if tier_selected_counts[tier] >= tier_cap:
            selection_result_by_npc[npc_id] = {
                "selected": False,
                "excluded_reason": f"TIER_{tier.upper()}_COUNT_BUDGET_EXCEEDED",
                "allocated_char_budget": 0,
                "used_chars": 0,
                "excerpt": "",
            }
            continue

        remaining_chars = effective_budget.total_char_budget - total_used_chars
        if remaining_chars < 24:
            selection_result_by_npc[npc_id] = {
                "selected": False,
                "excluded_reason": "TOTAL_CHAR_BUDGET_EXCEEDED",
                "allocated_char_budget": 0,
                "used_chars": 0,
                "excerpt": "",
            }
            continue

        effective_char_cap = min(char_cap, remaining_chars)
        excerpt = _format_npc_prompt_excerpt(item["npc"], tier, effective_char_cap)
        used = _u16_units(excerpt, "npc.excerpt")

        tier_selected_counts[tier] += 1
        total_selected += 1
        total_used_chars += used
        selection_result_by_npc[npc_id] = {
            "selected": True,
            "excluded_reason": None,
            "allocated_char_budget": effective_char_cap,
            "used_chars": used,
            "excerpt": excerpt,
        }

    evaluations: list[NpcDepthEvaluation] = []
    selected_npcs: list[dict[str, Any]] = []
    for item in raw_evals:
        npc_id = item["npc_id"]
        if item["status"] != "active":
            sel = {
                "selected": False,
                "excluded_reason": "NPC_NOT_ACTIVE",
                "allocated_char_budget": 0,
                "used_chars": 0,
                "excerpt": "",
            }
        elif not item["is_due"]:
            sel = {
                "selected": False,
                "excluded_reason": "NPC_NOT_DUE",
                "allocated_char_budget": 0,
                "used_chars": 0,
                "excerpt": "",
            }
        else:
            sel = selection_result_by_npc[npc_id]

        ev_obj = NpcDepthEvaluation(
            npc_id=npc_id,
            name=item["name"],
            location=item["location"],
            previous_tier=item["previous_tier"],
            current_tier=item["current_tier"],
            transition=item["transition"],
            relevance_score=item["relevance_score"],
            reason_codes=item["reason_codes"],
            reason_summary=item["reason_summary"],
            is_due=item["is_due"],
            next_due_minute=item["next_due_minute"],
            selected_for_simulation=bool(sel["selected"]),
            excluded_reason=sel["excluded_reason"],
            allocated_char_budget=int(sel["allocated_char_budget"]),
            used_chars=int(sel["used_chars"]),
            excerpt=str(sel["excerpt"]),
        )
        evaluations.append(ev_obj)
        if sel["selected"]:
            npc_copy = dict(item["npc"])
            npc_copy["depth_tier"] = item["current_tier"]
            npc_copy["depth_reason"] = item["reason_summary"]
            npc_copy["prompt_excerpt"] = sel["excerpt"]
            npc_copy["allocated_char_budget"] = int(sel["allocated_char_budget"])
            npc_copy["used_chars"] = int(sel["used_chars"])
            selected_npcs.append(npc_copy)

    selected_npcs.sort(
        key=lambda n: (
            -_TIER_RANK[n["depth_tier"]],
            n["next_due_minute"] if n.get("next_due_minute") is not None else 10**9,
            n["npc_id"],
        )
    )

    return {
        "evaluations": tuple(evaluations),
        "tier_evaluations": [e.to_dict() for e in evaluations],
        "selected_due_npcs": selected_npcs,
        "selected_npc_ids": [n["npc_id"] for n in selected_npcs],
        "budget": {
            **effective_budget.to_dict(),
            "selected_foreground_count": tier_selected_counts["foreground"],
            "selected_associated_count": tier_selected_counts["associated"],
            "selected_background_count": tier_selected_counts["background"],
            "selected_total_count": total_selected,
            "total_due_candidates": len(due_candidates),
            "total_evaluated_npcs": len(evaluations),
            "used_chars": total_used_chars,
        },
    }


def _normalize_id_list(
    raw_value: Any,
    field_name: str,
    *,
    max_items: int = MAX_CAUSES_PER_ACTION,
) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, (list, tuple)):
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为字符串列表。",
            {"field": field_name},
        )
    if len(raw_value) > max_items:
        raise NpcValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 数量超过上限 {max_items}。",
            {"field": field_name, "max_items": max_items},
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_value):
        s = _require_non_empty_str(item, f"{field_name}[{idx}]", 120)
        if s not in seen:
            seen.add(s)
            cleaned.append(s)
    return tuple(cleaned)


def _infer_action_kind_from_fields(raw_op: Mapping[str, Any], default_kind: str = "stay") -> str:
    raw_kind = (
        raw_op.get("action_kind")
        or raw_op.get("current_action_kind")
        or raw_op.get("initial_action_kind")
        or raw_op.get("arrangement")
        or raw_op.get("decision")
    )
    if isinstance(raw_kind, str) and raw_kind.strip():
        return raw_kind.strip()
    loc = str(raw_op.get("location") or raw_op.get("current_location") or raw_op.get("start_location") or "").strip()
    to_loc = str(raw_op.get("to_location") or raw_op.get("destination_location") or "").strip()
    if to_loc and to_loc != loc:
        return "travel"
    if raw_op.get("target_npc_id") or raw_op.get("counterpart_npc_id") or raw_op.get("conversation_topic") or raw_op.get("topic"):
        return "conversation_plan"
    if raw_op.get("new_plan") or raw_op.get("plan_summary") or raw_op.get("previous_plan"):
        return "plan_change"
    if raw_op.get("task_id") or raw_op.get("task_stage") or raw_op.get("stage"):
        return "task_progress"
    if raw_op.get("to_shift") or raw_op.get("shift_name"):
        return "shift_change"
    return default_kind


def normalize_initial_npc_seed_proposal(
    init_input: Any,
    *,
    instance_id: str,
    world_minute: int = 0,
    base_world_revision: int | None = None,
    default_source_ids: list[str] | tuple[str, ...] = (),
    idempotency_key: str = "",
) -> dict[str, Any]:
    """将世界初始化分析（world_analysis / 开局确认草案）的人物状态载荷标准化为 NPC seed 提议结构。

    支持：
    - 直接传入 NPC 字典数组 `[{...}, ...]`，或包含 `npcs` / `initial_npcs` / `operations` 的草案对象；
    - 首版覆盖 `travel`（出行）、`stay`（留在原处）、`wait`（等待）、`no_change`（无变化）、
      `plan_change`（改变计划）、`conversation_plan`（交谈安排）、`shift_change`（换班）、`task_progress`（任务进度）；
    - 自动归一化 `grounding`（`canon` 原文依据 vs `inferred` 旅程内补写）、`relationships` 已知关系与默认到期时间。
    """
    inst_id = _require_non_empty_str(instance_id, "instance_id", 120)
    w_min = _require_strict_int(world_minute, "world_minute", min_value=0)

    raw_items: Any
    summary = "从初始化设定草案建立人物初始地点、动机、关系与到期安排。"
    idem = idempotency_key
    if isinstance(init_input, (list, tuple)):
        raw_items = init_input
    elif isinstance(init_input, Mapping):
        if init_input.get("instance_id") and str(init_input["instance_id"]).strip() != inst_id:
            raise CrossInstanceReferenceError(inst_id, str(init_input["instance_id"]).strip(), str(init_input["instance_id"]).strip())
        if isinstance(init_input.get("summary"), str) and init_input["summary"].strip():
            summary = init_input["summary"].strip()
        if not idem and isinstance(init_input.get("idempotency_key") or init_input.get("job_id"), str):
            idem = str(init_input.get("idempotency_key") or init_input.get("job_id") or "").strip()
        if "operations" in init_input:
            raw_items = init_input["operations"]
        elif "npcs" in init_input:
            raw_items = init_input["npcs"]
        elif "initial_npcs" in init_input:
            raw_items = init_input["initial_npcs"]
        elif "npc_actions" in init_input:
            raw_items = init_input["npc_actions"]
        else:
            raise NpcValidationError("VALIDATION_ERROR", "初始化输入缺少 npcs / initial_npcs / operations 列表。")
    else:
        raise NpcValidationError("VALIDATION_ERROR", "初始化输入必须为 NPC 列表或包含 npcs 的对象。")

    if not isinstance(raw_items, (list, tuple)) or len(raw_items) == 0:
        raise NpcValidationError("EMPTY_SEED_PROPOSAL", "Seed 模式下 NPC 初始化列表不能为空。")

    ops: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise NpcValidationError("VALIDATION_ERROR", f"initial_npcs[{idx}] 必须为对象。")
        npc_id = str(
            item.get("npc_id")
            or item.get("id")
            or item.get("person_id")
            or item.get("character_id")
            or ""
        ).strip()
        name = str(item.get("name") or item.get("display_name") or item.get("title") or npc_id).strip()
        role = str(item.get("role") or item.get("identity") or item.get("occupation") or "居民").strip()
        loc = str(item.get("location") or item.get("current_location") or item.get("start_location") or "").strip()
        raw_kind = _infer_action_kind_from_fields(item, default_kind="stay")
        norm_kind = normalize_action_kind(raw_kind, f"initial_npcs[{idx}].action_kind")

        tier_raw = item.get("depth_tier") or "foreground"
        tier = normalize_depth_tier(tier_raw, f"initial_npcs[{idx}].depth_tier")
        default_dur = 60 if tier == "foreground" else (90 if tier == "associated" else 180)

        dur = item.get("duration_minutes") if item.get("duration_minutes") is not None else item.get("due_in_minutes")
        next_due = item.get("next_due_minute") if item.get("next_due_minute") is not None else item.get("next_update_minute")
        if dur is None and next_due is None:
            dur = default_dur
            next_due = w_min + default_dur

        activity = str(
            item.get("current_activity")
            or item.get("activity")
            or item.get("initial_activity")
            or item.get("new_plan")
            or item.get("plan_summary")
            or item.get("goal")
            or (f"在{loc}原地驻留观察局势" if loc else "驻留待命")
        ).strip()
        motivation = str(
            item.get("motivation")
            or item.get("motive")
            or item.get("goal")
            or item.get("reason")
            or "依据开局局势执行当前安排"
        ).strip()

        src_ids = list(item.get("source_ids") or default_source_ids or ())
        cause_evs = list(item.get("cause_event_ids") or ())
        cause_facts = list(item.get("cause_fact_ids") or ())
        grounding = _normalize_grounding(item, f"initial_npcs[{idx}]")
        if not (src_ids or cause_evs or cause_facts) and item.get("is_inferred") is None and item.get("grounding") is None and item.get("origin") is None:
            # 若初始化未显式传 source_ids 且提供了 default_source_ids 则已填充；否则保持原值交由 validate_proposal 核验
            pass

        op_dict: dict[str, Any] = {
            "op": "seed_npc",
            "npc_id": npc_id,
            "name": name,
            "role": role,
            "faction": str(item.get("faction") or item.get("organization") or "").strip(),
            "home_location": str(item.get("home_location") or loc).strip(),
            "location": loc,
            "action_kind": norm_kind,
            "current_activity": activity,
            "motivation": motivation,
            "goal": str(item.get("goal") or item.get("new_plan") or "").strip(),
            "depth_tier": tier,
            "depth_reason": str(item.get("depth_reason") or "开局初始化分级").strip(),
            "status": str(item.get("status") or "active").strip(),
            "grounding": grounding,
            "source_ids": src_ids,
            "cause_event_ids": cause_evs,
            "cause_fact_ids": cause_facts,
        }
        if item.get("availability") is not None:
            op_dict["availability"] = item["availability"]
        if dur is not None:
            op_dict["duration_minutes"] = dur
        if next_due is not None:
            op_dict["next_due_minute"] = next_due
        if item.get("from_location"):
            op_dict["from_location"] = item["from_location"]
        if item.get("to_location") or item.get("destination_location"):
            op_dict["to_location"] = item.get("to_location") or item.get("destination_location")
        if item.get("target_npc_id") or item.get("counterpart_npc_id"):
            op_dict["target_npc_id"] = item.get("target_npc_id") or item.get("counterpart_npc_id")
        if item.get("meeting_location"):
            op_dict["meeting_location"] = item["meeting_location"]
        if item.get("conversation_topic") or item.get("topic"):
            op_dict["conversation_topic"] = item.get("conversation_topic") or item.get("topic")
        if item.get("from_shift"):
            op_dict["from_shift"] = item["from_shift"]
        if item.get("to_shift"):
            op_dict["to_shift"] = item["to_shift"]
        if item.get("shift_name"):
            op_dict["shift_name"] = item["shift_name"]
        if item.get("previous_plan"):
            op_dict["previous_plan"] = item["previous_plan"]
        if item.get("new_plan") or item.get("plan_summary"):
            op_dict["new_plan"] = item.get("new_plan") or item.get("plan_summary")
        if item.get("task_id"):
            op_dict["task_id"] = item["task_id"]
        if item.get("task_title"):
            op_dict["task_title"] = item["task_title"]
        if item.get("task_stage") or item.get("stage"):
            op_dict["task_stage"] = item.get("task_stage") or item.get("stage")
        if item.get("task_progress_percent") is not None or item.get("progress_percent") is not None:
            op_dict["task_progress_percent"] = (
                item["task_progress_percent"]
                if item.get("task_progress_percent") is not None
                else item.get("progress_percent")
            )
        if item.get("task_status"):
            op_dict["task_status"] = item["task_status"]
        rels = item.get("relationships") or item.get("relations") or item.get("known_relations")
        if rels is not None:
            op_dict["relationships"] = rels
        ops.append(op_dict)

    out: dict[str, Any] = {
        "instance_id": inst_id,
        "mode": "seed",
        "world_minute": w_min,
        "summary": summary,
        "operations": ops,
    }
    if base_world_revision is not None:
        out["base_world_revision"] = base_world_revision
    if idem:
        out["idempotency_key"] = idem
    return out


def _normalize_context(context: NpcValidationContext | Mapping[str, Any]) -> NpcValidationContext:
    if isinstance(context, NpcValidationContext):
        return context
    if not isinstance(context, Mapping):
        raise NpcValidationError("CONTEXT_INVALID", "校验上下文必须为 NpcValidationContext 或映射对象。")

    snap_raw = context.get("snapshot")
    snap: Mapping[str, Any] = snap_raw if isinstance(snap_raw, Mapping) else {}

    raw_inst_id = context.get("instance_id") if context.get("instance_id") is not None else snap.get("instance_id")
    instance_id = _require_non_empty_str(raw_inst_id, "context.instance_id", 120)
    if snap.get("instance_id") is not None:
        snap_inst_id = _require_non_empty_str(snap.get("instance_id"), "snapshot.instance_id", 120)
        if snap_inst_id != instance_id:
            raise CrossInstanceReferenceError(instance_id, snap_inst_id, snap_inst_id)

    raw_mode = (
        context.get("mode")
        or context.get("kind")
        or snap.get("mode")
        or snap.get("kind")
        or snap.get("job_kind")
        or "advance"
    )
    if raw_mode not in PROPOSAL_MODES:
        raise NpcValidationError(
            "CONTEXT_INVALID",
            f"校验上下文 mode/kind 仅支持 'seed' 或 'advance'，实际为 {raw_mode!r}。",
        )

    raw_min = (
        context.get("world_minute")
        if context.get("world_minute") is not None
        else (
            context.get("target_world_minute")
            if context.get("target_world_minute") is not None
            else (
                snap.get("world_minute")
                if snap.get("world_minute") is not None
                else snap.get("target_world_minute")
            )
        )
    )
    world_minute = _require_strict_int(raw_min if raw_min is not None else 0, "context.world_minute", min_value=0)

    raw_rev = (
        context.get("base_world_revision")
        if context.get("base_world_revision") is not None
        else (
            context.get("world_revision")
            if context.get("world_revision") is not None
            else (
                snap.get("base_world_revision")
                if snap.get("base_world_revision") is not None
                else snap.get("world_revision")
            )
        )
    )
    base_world_revision = (
        _require_strict_int(raw_rev, "context.base_world_revision", min_value=0)
        if raw_rev is not None
        else None
    )

    existing_npcs_raw = context.get("existing_npcs") or snap.get("existing_npcs") or {}
    existing_npcs: dict[str, dict[str, Any]] = {}
    if isinstance(existing_npcs_raw, Mapping):
        for k, v in existing_npcs_raw.items():
            if isinstance(v, Mapping):
                existing_npcs[str(k)] = dict(v)
    elif isinstance(existing_npcs_raw, (list, tuple)):
        for item in existing_npcs_raw:
            if isinstance(item, Mapping) and item.get("npc_id"):
                existing_npcs[str(item["npc_id"])] = dict(item)

    for due_item in snap.get("due_npcs", ()) or ():
        if isinstance(due_item, Mapping) and due_item.get("npc_id"):
            nid = str(due_item["npc_id"])
            existing_npcs.setdefault(nid, dict(due_item))

    existing_tasks_raw = context.get("existing_tasks") or snap.get("existing_tasks") or {}
    existing_tasks: dict[tuple[str, str], dict[str, Any]] = {}
    if isinstance(existing_tasks_raw, Mapping):
        for k, v in existing_tasks_raw.items():
            if isinstance(k, tuple) and len(k) == 2 and isinstance(v, Mapping):
                existing_tasks[(str(k[0]), str(k[1]))] = dict(v)
            elif isinstance(k, str) and ":" in k and isinstance(v, Mapping):
                nid, tid = k.split(":", 1)
                existing_tasks[(nid, tid)] = dict(v)
    elif isinstance(existing_tasks_raw, (list, tuple)):
        for t in existing_tasks_raw:
            if isinstance(t, Mapping) and t.get("npc_id") and t.get("task_id"):
                existing_tasks[(str(t["npc_id"]), str(t["task_id"]))] = dict(t)

    allowed_events_raw = (
        context.get("allowed_event_ids")
        if context.get("allowed_event_ids") is not None
        else snap.get("allowed_event_ids", ())
    )
    allowed_event_ids = frozenset(str(x).strip() for x in (allowed_events_raw or ()) if str(x).strip())

    facts_by_npc_raw = (
        context.get("allowed_fact_ids_by_npc")
        if context.get("allowed_fact_ids_by_npc") is not None
        else snap.get("allowed_fact_ids_by_npc", {})
    )
    allowed_fact_ids_by_npc: dict[str, frozenset[str]] = {}
    all_fact_ids: set[str] = set()
    if isinstance(facts_by_npc_raw, Mapping):
        for nid, flist in facts_by_npc_raw.items():
            if isinstance(flist, (list, tuple, set, frozenset)):
                fset = frozenset(str(f).strip() for f in flist if str(f).strip())
                allowed_fact_ids_by_npc[str(nid)] = fset
                all_fact_ids.update(fset)

    raw_allowed_facts = (
        context.get("allowed_fact_ids")
        if context.get("allowed_fact_ids") is not None
        else snap.get("allowed_fact_ids", ())
    )
    for fid in raw_allowed_facts or ():
        if str(fid).strip():
            all_fact_ids.add(str(fid).strip())

    raw_sources = (
        context.get("allowed_source_ids")
        if context.get("allowed_source_ids") is not None
        else snap.get("allowed_source_ids", ())
    )
    allowed_source_ids = frozenset(str(s).strip() for s in (raw_sources or ()) if str(s).strip())

    raw_other_inst = (
        context.get("other_instance_ids")
        if context.get("other_instance_ids") is not None
        else snap.get("other_instance_ids", ())
    )
    other_instance_ids = frozenset(str(i).strip() for i in (raw_other_inst or ()) if str(i).strip())

    enforce_interval = bool(context.get("enforce_tier_interval", True))

    return NpcValidationContext(
        instance_id=instance_id,
        world_minute=world_minute,
        mode=raw_mode,
        base_world_revision=base_world_revision,
        existing_npcs=existing_npcs,
        existing_tasks=existing_tasks,
        allowed_event_ids=allowed_event_ids,
        allowed_fact_ids_by_npc=allowed_fact_ids_by_npc,
        allowed_fact_ids=frozenset(all_fact_ids),
        allowed_source_ids=allowed_source_ids,
        other_instance_ids=other_instance_ids,
        enforce_tier_interval=enforce_interval,
    )


def validate_proposal(
    proposal: ValidatedNpcProposal | Mapping[str, Any],
    context: NpcValidationContext | Mapping[str, Any],
) -> ValidatedNpcProposal:
    """在 SQLite 事务外纯函数校验 NPC 提议（结构、时间、因果、重复任务与账本/知情隔离）。"""
    allow_transmissions = bool(
        isinstance(context, Mapping) and context.get("allow_transmissions_envelope")
    )
    ctx = _normalize_context(context)
    if isinstance(proposal, ValidatedNpcProposal):
        if proposal.instance_id != ctx.instance_id:
            raise CrossInstanceReferenceError(ctx.instance_id, proposal.instance_id, proposal.instance_id)
        if proposal.base_world_revision is not None and ctx.base_world_revision is not None:
            if proposal.base_world_revision != ctx.base_world_revision:
                raise WorldRevisionConflictError(
                    ctx.instance_id,
                    proposal.base_world_revision,
                    ctx.base_world_revision,
                )
        return proposal

    if not isinstance(proposal, Mapping):
        raise NpcValidationError("PROPOSAL_INVALID", "NPC 提议顶层必须为 JSON 对象。")

    _check_security_recursive(
        proposal,
        instance_id=ctx.instance_id,
        allow_transmissions=allow_transmissions,
        path="proposal",
    )

    unknown_root = set(proposal.keys()) - _ALLOWED_ROOT_KEYS
    if unknown_root:
        raise NpcValidationError(
            "PROPOSAL_UNKNOWN_ROOT_FIELD",
            f"NPC 提议包含未知根字段: {sorted(unknown_root)}。",
            {"unknown_fields": sorted(unknown_root)},
        )

    prop_mode = proposal.get("mode") or proposal.get("kind")
    if prop_mode is not None:
        if proposal.get("mode") and proposal.get("kind") and proposal["mode"] != proposal["kind"]:
            raise NpcValidationError(
                "MODE_KIND_MISMATCH",
                f"提议中 mode ({proposal['mode']!r}) 与 kind ({proposal['kind']!r}) 不一致。",
            )
        if str(prop_mode).strip() != ctx.mode:
            raise NpcValidationError(
                "PROPOSAL_MODE_MISMATCH",
                f"提议回显的 mode/kind ({prop_mode!r}) 与上下文绑定模式 ({ctx.mode!r}) 不一致。",
            )

    for m_key in ("world_minute", "target_world_minute"):
        if m_key in proposal and proposal[m_key] is not None:
            m_val = _require_strict_int(proposal[m_key], f"proposal.{m_key}", min_value=0)
            if m_val != ctx.world_minute:
                raise NpcValidationError(
                    "TIMESTAMP_OVERRIDE_FORBIDDEN",
                    f"提议回显的 {m_key} ({m_val}) 与已确认世界分钟 ({ctx.world_minute}) 不一致。",
                    {"expected_world_minute": ctx.world_minute, "actual_world_minute": m_val},
                )

    rev_keys = [
        k for k in ("base_world_revision", "world_revision", "revision")
        if k in proposal and proposal[k] is not None
    ]
    if rev_keys:
        rev_vals = [_require_strict_int(proposal[k], f"proposal.{k}", min_value=0) for k in rev_keys]
        if len(set(rev_vals)) > 1:
            raise WorldRevisionConflictError(ctx.instance_id, rev_vals[0], rev_vals[1])
        if ctx.base_world_revision is not None and rev_vals[0] != ctx.base_world_revision:
            raise WorldRevisionConflictError(ctx.instance_id, rev_vals[0], ctx.base_world_revision)

    if "operations" in proposal:
        raw_ops = proposal.get("operations")
    elif "npc_actions" in proposal:
        raw_ops = proposal.get("npc_actions")
    elif "npcs" in proposal:
        raw_ops = proposal.get("npcs")
    elif "initial_npcs" in proposal:
        raw_ops = proposal.get("initial_npcs")
    else:
        raw_ops = []

    if not isinstance(raw_ops, (list, tuple)):
        raise NpcValidationError("VALIDATION_ERROR", "operations / npc_actions 必须为数组。")
    if len(raw_ops) > MAX_OPERATIONS_PER_PROPOSAL:
        raise NpcValidationError(
            "TOO_MANY_OPERATIONS",
            f"单次 NPC 提议操作数量不能超过 {MAX_OPERATIONS_PER_PROPOSAL} 条。",
        )
    if ctx.mode == "seed" and len(raw_ops) == 0:
        raise NpcValidationError("EMPTY_SEED_PROPOSAL", "Seed 模式下 NPC 初始化提议不能为空。")

    summary = _optional_str(proposal.get("summary"), "proposal.summary", 400, default="")
    idempotency_key = _optional_str(
        proposal.get("idempotency_key") or proposal.get("job_id"),
        "proposal.idempotency_key",
        120,
        default="",
    )

    seen_npc_ids: set[str] = set()
    seen_task_keys: set[tuple[str, str]] = set()
    validated_ops: list[ValidatedNpcActionOp] = []

    for idx, raw_op in enumerate(raw_ops):
        op_prefix = f"operations[{idx}]"
        if not isinstance(raw_op, Mapping):
            raise NpcValidationError("VALIDATION_ERROR", f"{op_prefix} 必须为 JSON 对象。")

        for t_key in _SERVER_MANAGED_TIME_KEYS:
            if t_key in raw_op:
                raise NpcValidationError(
                    "TIMESTAMP_OVERRIDE_FORBIDDEN",
                    f"{op_prefix} 严禁覆写服务端受管时间戳字段 {t_key}。",
                    {"field": t_key},
                )

        unknown_op = set(raw_op.keys()) - _ALLOWED_OP_KEYS
        if unknown_op:
            raise NpcValidationError(
                "UNKNOWN_OPERATION_FIELD",
                f"{op_prefix} 包含未知字段: {sorted(unknown_op)}。",
                {"unknown_fields": sorted(unknown_op)},
            )

        npc_id = _require_non_empty_str(raw_op.get("npc_id"), f"{op_prefix}.npc_id", MAX_NPC_ID_UNITS)
        if npc_id.lower() == "player":
            raise PlayerLedgerProtectedError("npc_id", "禁止将 player 作为 NPC 状态操作目标。")
        for other_inst in ctx.other_instance_ids:
            if other_inst and other_inst in npc_id:
                raise CrossInstanceReferenceError(ctx.instance_id, npc_id, other_inst)

        if npc_id in seen_npc_ids:
            raise DuplicateNpcTaskError(
                npc_id,
                code="DUPLICATE_NPC_OPERATION",
                message=f"同一提议批次中对 NPC {npc_id} 出现了重复操作。",
            )
        seen_npc_ids.add(npc_id)

        raw_op_type = str(raw_op.get("op") or ("seed_npc" if ctx.mode == "seed" else "act")).strip()
        if raw_op_type in ("seed_npc", "create", "register"):
            op_type = "seed_npc"
        elif raw_op_type in ("act", "update"):
            op_type = "act"
        else:
            raise NpcValidationError(
                "INVALID_OPERATION_TYPE",
                f"{op_prefix}.op 仅支持 'seed_npc' 或 'act'，实际为 {raw_op_type!r}。",
            )

        if ctx.mode == "seed" and op_type != "seed_npc":
            raise NpcValidationError("MODE_OP_MISMATCH", "Seed 模式下操作类型必须为 seed_npc。")
        if ctx.mode == "advance" and op_type != "act":
            raise NpcValidationError("MODE_OP_MISMATCH", "Advance 模式下操作类型必须为 act。")

        raw_action_kind = (
            raw_op.get("action_kind")
            or raw_op.get("current_action_kind")
            or raw_op.get("initial_action_kind")
            or raw_op.get("arrangement")
            or raw_op.get("decision")
        )
        action_kind = normalize_action_kind(raw_action_kind, f"{op_prefix}.action_kind")

        existing_npc: Mapping[str, Any] = ctx.existing_npcs.get(npc_id, {}) if op_type == "act" else {}

        raw_activity = (
            raw_op.get("current_activity")
            or raw_op.get("activity")
            or raw_op.get("initial_activity")
        )
        if raw_activity is None and op_type == "act" and action_kind in ("wait", "no_change", "stay"):
            fallback_loc = str(existing_npc.get("location") or "")
            raw_activity = str(existing_npc.get("current_activity") or f"在{fallback_loc}原地等待观察局势")
        elif raw_activity is None and action_kind == "plan_change" and (raw_op.get("new_plan") or raw_op.get("plan_summary")):
            raw_activity = str(raw_op.get("new_plan") or raw_op.get("plan_summary"))

        current_activity = _require_non_empty_str(
            raw_activity,
            f"{op_prefix}.current_activity",
            MAX_ACTIVITY_UNITS,
        )

        raw_motivation = raw_op.get("motivation") or raw_op.get("motive")
        if raw_motivation is None and op_type == "act" and action_kind in ("wait", "no_change", "stay"):
            raw_motivation = str(existing_npc.get("motivation") or "暂留原处观望局势变化")
        motivation = _require_non_empty_str(
            raw_motivation,
            f"{op_prefix}.motivation",
            MAX_MOTIVATION_UNITS,
        )

        raw_next_due = (
            raw_op.get("next_due_minute")
            if "next_due_minute" in raw_op
            else raw_op.get("next_update_minute")
        )
        raw_duration = (
            raw_op.get("duration_minutes")
            if raw_op.get("duration_minutes") is not None
            else raw_op.get("due_in_minutes")
        )

        next_due_minute: int | None = None
        if raw_next_due is not None:
            next_due_minute = _require_strict_int(
                raw_next_due, f"{op_prefix}.next_due_minute", min_value=0
            )
            if next_due_minute <= ctx.world_minute:
                raise NpcValidationError(
                    "INVALID_NEXT_DUE_MINUTE",
                    f"{op_prefix}.next_due_minute ({next_due_minute}) 必须严格大于当前已确认世界分钟 ({ctx.world_minute})。",
                    {"world_minute": ctx.world_minute, "next_due_minute": next_due_minute},
                )

        if raw_duration is not None:
            duration_minutes = _require_strict_int(
                raw_duration, f"{op_prefix}.duration_minutes", min_value=1
            )
            if next_due_minute is None:
                next_due_minute = ctx.world_minute + duration_minutes
            elif next_due_minute - ctx.world_minute < duration_minutes:
                raise NpcValidationError(
                    "DURATION_EXCEEDS_NEXT_DUE",
                    f"{op_prefix}.duration_minutes ({duration_minutes}) 超过下一次到期时间间隔 ({next_due_minute - ctx.world_minute})。",
                )
        else:
            if next_due_minute is None:
                raise NpcValidationError(
                    "MISSING_TIME_CONSTRAINT",
                    f"{op_prefix} 必须提供有效的 next_due_minute 或 duration_minutes。",
                )
            duration_minutes = next_due_minute - ctx.world_minute

        min_action_dur = MIN_DURATION_BY_ACTION_KIND[action_kind]
        if duration_minutes < min_action_dur:
            raise NpcValidationError(
                "ACTION_DURATION_TOO_SHORT",
                f"{op_prefix} 动作类型 {action_kind} 的持续时间 ({duration_minutes} 分钟) 小于最小约束 ({min_action_dur} 分钟)。",
                {"action_kind": action_kind, "duration_minutes": duration_minutes, "min_duration": min_action_dur},
            )

        cause_event_ids = _normalize_id_list(raw_op.get("cause_event_ids"), f"{op_prefix}.cause_event_ids")
        cause_fact_ids = _normalize_id_list(raw_op.get("cause_fact_ids"), f"{op_prefix}.cause_fact_ids")
        source_ids = _normalize_id_list(raw_op.get("source_ids"), f"{op_prefix}.source_ids")
        grounding = _normalize_grounding(raw_op, op_prefix)
        relationships = _normalize_relationships(
            raw_op.get("relationships") or raw_op.get("relations") or raw_op.get("known_relations"),
            f"{op_prefix}.relationships",
        )

        for sid in source_ids:
            if not (sid.startswith("chunk:") or sid.startswith("entry:")):
                raise SourceFabricatedError(sid, f"{op_prefix} 引用了格式非法的 Canon 来源 {sid!r}。")
            if ctx.allowed_source_ids and sid not in ctx.allowed_source_ids:
                raise SourceFabricatedError(sid, f"{op_prefix} 引用了未授权或伪造的 Canon 来源 {sid!r}。")

        for eid in cause_event_ids:
            for other_inst in ctx.other_instance_ids:
                if other_inst and other_inst in eid:
                    raise CrossInstanceReferenceError(ctx.instance_id, eid, other_inst)
            if eid.startswith(("tmp_", "temp_", "new_")):
                raise InvalidCausalReferenceError(
                    npc_id,
                    eid,
                    code="TEMP_EVENT_REFERENCE_FORBIDDEN",
                    message=f"{op_prefix} 禁止引用未落库的临时事件 ID {eid!r}。",
                )
            if eid not in ctx.allowed_event_ids:
                raise InvalidCausalReferenceError(
                    npc_id,
                    eid,
                    code="CAUSAL_EVENT_NOT_FOUND",
                    message=f"{op_prefix} 引用的原因事件 {eid!r} 在当前实例中不存在。",
                )

        npc_allowed_facts = ctx.allowed_fact_ids_by_npc.get(npc_id)
        for fid in cause_fact_ids:
            for other_inst in ctx.other_instance_ids:
                if other_inst and other_inst in fid:
                    raise CrossInstanceReferenceError(ctx.instance_id, fid, other_inst)
            if npc_allowed_facts is not None:
                if fid not in npc_allowed_facts:
                    raise InvalidCausalReferenceError(
                        npc_id,
                        fid,
                        code="CAUSAL_FACT_NOT_KNOWN",
                        message=f"{op_prefix} 引用的事实 {fid!r} 不在该 NPC ({npc_id}) 的已知记录中。",
                    )
            elif fid not in ctx.allowed_fact_ids:
                raise InvalidCausalReferenceError(
                    npc_id,
                    fid,
                    code="CAUSAL_FACT_NOT_FOUND",
                    message=f"{op_prefix} 引用的事实 {fid!r} 在当前实例中不存在。",
                )

        if op_type == "seed_npc":
            if grounding == "canon" and not (cause_event_ids or cause_fact_ids or source_ids):
                raise InvalidCausalReferenceError(
                    npc_id,
                    code="CAUSAL_MOTIVATION_UNGROUNDED",
                    message=f"{op_prefix} 初始化 NPC 动机必须至少绑定一项授权 Canon 来源、初始事件引用，或显式标记为旅程推演 (grounding='inferred')。",
                )
            name = _require_non_empty_str(raw_op.get("name"), f"{op_prefix}.name", MAX_NAME_UNITS)
            role = _require_non_empty_str(
                raw_op.get("role") or raw_op.get("identity") or raw_op.get("occupation"),
                f"{op_prefix}.role",
                MAX_ROLE_UNITS,
            )
            location = _require_non_empty_str(
                raw_op.get("location") or raw_op.get("current_location") or raw_op.get("start_location"),
                f"{op_prefix}.location",
                MAX_LOCATION_UNITS,
            )
            home_location = _optional_str(
                raw_op.get("home_location"), f"{op_prefix}.home_location", MAX_LOCATION_UNITS, default=location
            )
            faction = _optional_str(raw_op.get("faction"), f"{op_prefix}.faction", MAX_ROLE_UNITS, default="")
            goal = _optional_str(raw_op.get("goal"), f"{op_prefix}.goal", MAX_GOAL_UNITS, default="")
            depth_tier = (
                normalize_depth_tier(raw_op["depth_tier"], f"{op_prefix}.depth_tier")
                if raw_op.get("depth_tier") is not None
                else "background"
            )
            depth_reason = _optional_str(
                raw_op.get("depth_reason"), f"{op_prefix}.depth_reason", 240, default="初始化默认分级"
            )
            expected_rev = None
        else:
            if npc_id not in ctx.existing_npcs:
                raise NpcNotFoundError(ctx.instance_id, npc_id)
            existing_npc = ctx.existing_npcs[npc_id]
            current_rev = int(existing_npc.get("revision") or 1)
            if "expected_npc_revision" not in raw_op or raw_op["expected_npc_revision"] is None:
                raise NpcValidationError(
                    "MISSING_EXPECTED_REVISION",
                    f"{op_prefix} 更新 NPC 行动必须提供 expected_npc_revision。",
                    {"npc_id": npc_id},
                )
            expected_rev = _require_strict_int(
                raw_op["expected_npc_revision"], f"{op_prefix}.expected_npc_revision", min_value=1
            )
            if expected_rev != current_rev:
                raise NpcRevisionConflictError(ctx.instance_id, npc_id, expected_rev, current_rev)

            existing_status = str(existing_npc.get("status") or "active")
            existing_due = existing_npc.get("next_due_minute")
            if (
                existing_status != "active"
                or existing_due is None
                or int(existing_due) > ctx.world_minute
            ):
                raise NpcNotDueError(
                    ctx.instance_id,
                    npc_id,
                    ctx.world_minute,
                    int(existing_due) if existing_due is not None else None,
                )

            if not (cause_event_ids or cause_fact_ids or source_ids):
                if action_kind in ("wait", "no_change", "stay"):
                    # 等待与无变化/留在原处允许沿用人物既有因果或来源凭据
                    inherited_ev = tuple(
                        str(e).strip()
                        for e in (existing_npc.get("cause_event_ids") or ())
                        if str(e).strip() and str(e).strip() in ctx.allowed_event_ids
                    )
                    inherited_facts = tuple(
                        str(f).strip()
                        for f in (existing_npc.get("cause_fact_ids") or ())
                        if str(f).strip()
                        and (
                            (npc_allowed_facts is not None and str(f).strip() in npc_allowed_facts)
                            or (npc_allowed_facts is None and str(f).strip() in ctx.allowed_fact_ids)
                        )
                    )
                    inherited_srcs = tuple(
                        str(s).strip()
                        for s in (existing_npc.get("source_ids") or ())
                        if str(s).strip() and (not ctx.allowed_source_ids or str(s).strip() in ctx.allowed_source_ids)
                    )
                    cause_event_ids = inherited_ev
                    cause_fact_ids = inherited_facts
                    source_ids = inherited_srcs

                if not (cause_event_ids or cause_fact_ids or source_ids) and action_kind not in ("wait", "no_change"):
                    raise InvalidCausalReferenceError(
                        npc_id,
                        code="CAUSAL_MOTIVATION_UNGROUNDED",
                        message=f"{op_prefix} 的 NPC 自主行动必须引用至少一项当前实例内已存在的事件、自身已知事实或 Canon 来源。",
                    )

            name = _optional_str(raw_op.get("name"), f"{op_prefix}.name", MAX_NAME_UNITS, default=str(existing_npc.get("name") or npc_id))
            role = _optional_str(
                raw_op.get("role") or raw_op.get("identity") or raw_op.get("occupation"),
                f"{op_prefix}.role",
                MAX_ROLE_UNITS,
                default=str(existing_npc.get("role") or ""),
            )
            faction = _optional_str(
                raw_op.get("faction"),
                f"{op_prefix}.faction",
                MAX_ROLE_UNITS,
                default=str(existing_npc.get("faction") or ""),
            )
            home_location = _optional_str(
                raw_op.get("home_location"),
                f"{op_prefix}.home_location",
                MAX_LOCATION_UNITS,
                default=str(existing_npc.get("home_location") or existing_npc.get("location") or ""),
            )
            goal = _optional_str(
                raw_op.get("goal"),
                f"{op_prefix}.goal",
                MAX_GOAL_UNITS,
                default=str(existing_npc.get("goal") or ""),
            )
            depth_tier = (
                normalize_depth_tier(raw_op["depth_tier"], f"{op_prefix}.depth_tier")
                if raw_op.get("depth_tier") is not None
                else normalize_depth_tier(existing_npc.get("depth_tier", "background"), f"{op_prefix}.depth_tier")
            )
            depth_reason = _optional_str(
                raw_op.get("depth_reason"),
                f"{op_prefix}.depth_reason",
                240,
                default=str(existing_npc.get("depth_reason") or ""),
            )
            location = _optional_str(
                raw_op.get("location") or raw_op.get("current_location"),
                f"{op_prefix}.location",
                MAX_LOCATION_UNITS,
                default=str(existing_npc.get("location") or ""),
            )

            if ctx.enforce_tier_interval and next_due_minute is not None:
                min_interval = MIN_ACTION_INTERVAL_BY_TIER.get(depth_tier, 15)
                if next_due_minute - ctx.world_minute < min_interval:
                    raise NpcValidationError(
                        "TIER_INTERVAL_TOO_SHORT",
                        f"{op_prefix} 处于 {depth_tier}({NPC_DEPTH_TIER_LABELS_ZH[depth_tier]}) 档位，下一次到期时间间隔 ({next_due_minute - ctx.world_minute} 分钟) 小于该档位最小间隔 ({min_interval} 分钟)。",
                        {
                            "npc_id": npc_id,
                            "depth_tier": depth_tier,
                            "min_interval": min_interval,
                            "actual_interval": next_due_minute - ctx.world_minute,
                        },
                    )

        from_location = _optional_str(
            raw_op.get("from_location"),
            f"{op_prefix}.from_location",
            MAX_LOCATION_UNITS,
            default=str(existing_npc.get("location") or location),
        )
        to_location = _optional_str(
            raw_op.get("to_location") or raw_op.get("destination_location"),
            f"{op_prefix}.to_location",
            MAX_LOCATION_UNITS,
            default="",
        )
        target_npc_id = _optional_str(
            raw_op.get("target_npc_id") or raw_op.get("counterpart_npc_id"),
            f"{op_prefix}.target_npc_id",
            MAX_NPC_ID_UNITS,
            default="",
        )
        meeting_location = _optional_str(
            raw_op.get("meeting_location"),
            f"{op_prefix}.meeting_location",
            MAX_LOCATION_UNITS,
            default="",
        )
        conversation_topic = _optional_str(
            raw_op.get("conversation_topic") or raw_op.get("topic"),
            f"{op_prefix}.conversation_topic",
            MAX_TOPIC_UNITS,
            default="",
        )
        from_shift = _optional_str(
            raw_op.get("from_shift"),
            f"{op_prefix}.from_shift",
            MAX_SHIFT_UNITS,
            default=str(existing_npc.get("shift_name") or ""),
        )
        to_shift = _optional_str(
            raw_op.get("to_shift"),
            f"{op_prefix}.to_shift",
            MAX_SHIFT_UNITS,
            default="",
        )
        shift_name = _optional_str(
            raw_op.get("shift_name") or to_shift,
            f"{op_prefix}.shift_name",
            MAX_SHIFT_UNITS,
            default=str(existing_npc.get("shift_name") or ""),
        )
        previous_plan = _optional_str(
            raw_op.get("previous_plan"),
            f"{op_prefix}.previous_plan",
            MAX_PLAN_UNITS,
            default=str(existing_npc.get("goal") or existing_npc.get("current_activity") or ""),
        )
        new_plan = _optional_str(
            raw_op.get("new_plan") or raw_op.get("plan_summary"),
            f"{op_prefix}.new_plan",
            MAX_PLAN_UNITS,
            default="",
        )
        task_id = _optional_str(raw_op.get("task_id"), f"{op_prefix}.task_id", MAX_TASK_ID_UNITS, default="")
        task_title = _optional_str(raw_op.get("task_title"), f"{op_prefix}.task_title", MAX_TASK_TITLE_UNITS, default="")
        task_stage = _optional_str(
            raw_op.get("task_stage") or raw_op.get("stage"),
            f"{op_prefix}.task_stage",
            MAX_TASK_STAGE_UNITS,
            default="",
        )
        raw_progress = (
            raw_op.get("task_progress_percent")
            if "task_progress_percent" in raw_op
            else raw_op.get("progress_percent")
        )
        task_progress_percent: int | None = None
        if raw_progress is not None:
            task_progress_percent = _require_strict_int(
                raw_progress, f"{op_prefix}.task_progress_percent", min_value=0, max_value=100
            )

        raw_task_status = raw_op.get("task_status")
        task_status: str | None = None
        if raw_task_status is not None:
            task_status = _require_non_empty_str(raw_task_status, f"{op_prefix}.task_status", 24)
            if task_status not in TASK_STATUSES:
                raise NpcValidationError(
                    "INVALID_TASK_STATUS",
                    f"{op_prefix}.task_status 仅支持 {sorted(TASK_STATUSES)}。",
                )

        raw_availability = raw_op.get("availability")
        availability: str = (
            _require_non_empty_str(raw_availability, f"{op_prefix}.availability", 24)
            if raw_availability is not None
            else ("in_transit" if action_kind == "travel" else "available")
        )
        if availability not in NPC_AVAILABILITY_STATES:
            raise NpcValidationError(
                "INVALID_AVAILABILITY",
                f"{op_prefix}.availability 仅支持 {sorted(NPC_AVAILABILITY_STATES)}。",
            )

        raw_status = raw_op.get("status", "active")
        status_val = _require_non_empty_str(raw_status, f"{op_prefix}.status", 24)
        if status_val not in NPC_STATUSES:
            raise NpcValidationError(
                "INVALID_NPC_STATUS",
                f"{op_prefix}.status 仅支持 {sorted(NPC_STATUSES)}。",
            )

        if action_kind == "travel":
            if op_type == "act":
                curr_loc = str(existing_npc.get("location") or "")
                if from_location != curr_loc:
                    raise NpcValidationError(
                        "TRAVEL_ORIGIN_MISMATCH",
                        f"{op_prefix} 出行起点 ({from_location!r}) 与 NPC 当前实际所在地 ({curr_loc!r}) 不一致。",
                        {"npc_id": npc_id, "from_location": from_location, "current_location": curr_loc},
                    )
            if not to_location:
                if location and location != from_location:
                    to_location = location
                else:
                    raise NpcValidationError(
                        "TRAVEL_DESTINATION_REQUIRED",
                        f"{op_prefix} 出行动作 (travel) 必须指定不同于起点 ({from_location!r}) 的目的地 to_location。",
                        {"npc_id": npc_id, "from_location": from_location},
                    )
            if to_location == from_location:
                raise NpcValidationError(
                    "TRAVEL_DESTINATION_SAME_AS_ORIGIN",
                    f"{op_prefix} 出行目的地 ({to_location!r}) 不能与起点相同。",
                    {"npc_id": npc_id, "location": to_location},
                )
            location = to_location
            if raw_availability is None:
                availability = "in_transit"

        elif action_kind in ("stay", "wait", "no_change"):
            if op_type == "act":
                curr_loc = str(existing_npc.get("location") or "")
                if (to_location and to_location != curr_loc) or (location and location != curr_loc):
                    raise NpcValidationError(
                        "STAY_LOCATION_MISMATCH",
                        f"{op_prefix} 动作 {action_kind} 表示留在原处或等待，不能将地点从 {curr_loc!r} 更改为 {to_location or location!r}。",
                        {"npc_id": npc_id, "current_location": curr_loc, "requested_location": to_location or location},
                    )
                location = curr_loc
                from_location = curr_loc
                to_location = curr_loc
            else:
                from_location = location
                to_location = location

        elif action_kind == "plan_change":
            if op_type == "act":
                curr_loc = str(existing_npc.get("location") or "")
                if (to_location and to_location != curr_loc) or (location and location != curr_loc):
                    raise NpcValidationError(
                        "PLAN_CHANGE_LOCATION_MISMATCH",
                        f"{op_prefix} 改变计划 (plan_change) 不直接跨地点移动，若立即动身请使用 travel。",
                        {"npc_id": npc_id, "current_location": curr_loc, "requested_location": to_location or location},
                    )
                location = curr_loc
            effective_new_plan = new_plan or (
                str(raw_op.get("goal") or "").strip() if raw_op.get("goal") is not None else current_activity
            )
            if not effective_new_plan:
                raise NpcValidationError(
                    "PLAN_SUMMARY_REQUIRED",
                    f"{op_prefix} 改变计划 (plan_change) 必须提供非空的 new_plan、goal 或 current_activity。",
                )
            if (
                op_type == "act"
                and effective_new_plan == str(existing_npc.get("goal") or "")
                and current_activity == str(existing_npc.get("current_activity") or "")
                and not to_shift
                and not task_id
                and not target_npc_id
            ):
                raise DuplicateNpcTaskError(
                    npc_id,
                    code="DUPLICATE_PLAN_CHANGE",
                    message=f"{op_prefix} 改变计划动作未产生任何目标、活动或安排变化。",
                )
            new_plan = effective_new_plan
            if not raw_op.get("goal"):
                goal = effective_new_plan

        elif action_kind == "conversation_plan":
            if not target_npc_id:
                raise NpcValidationError(
                    "CONVERSATION_TARGET_REQUIRED",
                    f"{op_prefix} 交谈安排 (conversation_plan) 必须指定 target_npc_id。",
                )
            if target_npc_id == npc_id:
                raise NpcValidationError(
                    "SELF_CONVERSATION_FORBIDDEN",
                    f"{op_prefix} NPC 不能与自身 ({npc_id}) 安排交谈。",
                )
            for other_inst in ctx.other_instance_ids:
                if other_inst and other_inst in target_npc_id:
                    raise CrossInstanceReferenceError(ctx.instance_id, target_npc_id, other_inst)
            if op_type == "act" and ctx.existing_npcs and target_npc_id not in ctx.existing_npcs:
                raise InvalidCausalReferenceError(
                    npc_id,
                    target_npc_id,
                    code="TARGET_NPC_NOT_FOUND",
                    message=f"{op_prefix} 交谈目标 NPC {target_npc_id!r} 在当前实例中不存在。",
                )
            if not conversation_topic:
                raise NpcValidationError(
                    "CONVERSATION_TOPIC_REQUIRED",
                    f"{op_prefix} 交谈安排 (conversation_plan) 必须提供非空 conversation_topic。",
                )
            if not meeting_location:
                meeting_location = location

        elif action_kind == "shift_change":
            if not to_shift and not shift_name:
                raise NpcValidationError(
                    "SHIFT_NAME_REQUIRED",
                    f"{op_prefix} 换班动作 (shift_change) 必须指定 to_shift 或 shift_name。",
                )
            effective_to_shift = to_shift or shift_name
            if (
                op_type == "act"
                and existing_npc.get("shift_name")
                and effective_to_shift == str(existing_npc.get("shift_name"))
                and availability == str(existing_npc.get("availability") or "available")
            ):
                raise DuplicateNpcTaskError(
                    npc_id,
                    code="DUPLICATE_SHIFT_CHANGE",
                    message=f"{op_prefix} 换班目标班次 ({effective_to_shift!r}) 与当前班次及状态完全相同。",
                )
            to_shift = effective_to_shift
            shift_name = effective_to_shift

        elif action_kind == "task_progress":
            if not task_id:
                raise NpcValidationError(
                    "TASK_ID_REQUIRED",
                    f"{op_prefix} 任务进度动作 (task_progress) 必须指定 task_id。",
                )
            if not task_stage:
                raise NpcValidationError(
                    "TASK_STAGE_REQUIRED",
                    f"{op_prefix} 任务进度动作 (task_progress) 必须指定 task_stage。",
                )
            if task_progress_percent is None:
                task_progress_percent = 100 if task_status == "completed" else 25
            if task_status is None:
                task_status = "completed" if task_progress_percent == 100 else "active"

            task_key = (npc_id, task_id)
            if task_key in seen_task_keys:
                raise DuplicateNpcTaskError(
                    npc_id,
                    task_id,
                    code="DUPLICATE_NPC_TASK",
                    message=f"{op_prefix} 同一批次中重复提交了任务 {task_id!r}。",
                )
            seen_task_keys.add(task_key)

            existing_task = ctx.existing_tasks.get(task_key)
            if existing_task is not None:
                old_status = str(existing_task.get("status") or "active")
                old_progress = int(existing_task.get("progress_percent") or 0)
                old_stage = str(existing_task.get("stage") or "")
                if old_status == "completed":
                    raise DuplicateNpcTaskError(
                        npc_id,
                        task_id,
                        code="TASK_ALREADY_COMPLETED",
                        message=f"NPC {npc_id} 的任务 {task_id!r} 已完成，禁止重复执行。",
                    )
                if task_progress_percent < old_progress:
                    raise NpcValidationError(
                        "TASK_PROGRESS_REGRESSION",
                        f"任务 {task_id!r} 的进度 ({task_progress_percent}%) 不能倒退于已有进度 ({old_progress}%)。",
                        {"npc_id": npc_id, "task_id": task_id, "old_progress": old_progress, "new_progress": task_progress_percent},
                    )
                if (
                    op_type == "act"
                    and task_progress_percent == old_progress
                    and task_stage == old_stage
                    and task_status == old_status
                ):
                    raise DuplicateNpcTaskError(
                        npc_id,
                        task_id,
                        code="DUPLICATE_NPC_TASK",
                        message=f"NPC {npc_id} 的任务 {task_id!r} 未产生任何阶段或进度变化，拒绝重复任务提交。",
                    )
                if not task_title:
                    task_title = str(existing_task.get("title") or task_id)
            else:
                if not task_title:
                    task_title = task_stage

        reason = _optional_str(raw_op.get("reason"), f"{op_prefix}.reason", 300, default=motivation)

        validated_ops.append(
            ValidatedNpcActionOp(
                op=op_type,  # type: ignore[arg-type]
                npc_id=npc_id,
                action_kind=action_kind,
                current_activity=current_activity,
                motivation=motivation,
                location=location,
                next_due_minute=next_due_minute,
                duration_minutes=duration_minutes,
                expected_npc_revision=expected_rev,
                name=name,
                role=role,
                faction=faction,
                home_location=home_location,
                goal=goal,
                depth_tier=depth_tier,
                depth_reason=depth_reason,
                status=status_val,  # type: ignore[arg-type]
                availability=availability,  # type: ignore[arg-type]
                from_location=from_location,
                to_location=to_location,
                target_npc_id=target_npc_id,
                meeting_location=meeting_location,
                conversation_topic=conversation_topic,
                from_shift=from_shift,
                to_shift=to_shift,
                shift_name=shift_name,
                previous_plan=previous_plan,
                new_plan=new_plan,
                task_id=task_id,
                task_title=task_title,
                task_stage=task_stage,
                task_progress_percent=task_progress_percent,
                task_status=task_status,  # type: ignore[arg-type]
                cause_event_ids=cause_event_ids,
                cause_fact_ids=cause_fact_ids,
                source_ids=source_ids,
                reason=reason,
                grounding=grounding,
                relationships=relationships,
            )
        )

    return ValidatedNpcProposal(
        instance_id=ctx.instance_id,
        mode=ctx.mode,
        world_minute=ctx.world_minute,
        operations=tuple(validated_ops),
        summary=summary,
        base_world_revision=ctx.base_world_revision,
        idempotency_key=idempotency_key,
    )
