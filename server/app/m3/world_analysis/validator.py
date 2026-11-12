"""M3 第四轮（Wave 4）AI 初始化草案纯函数校验器与未公开信息过滤边界。

契约依据（`docs/collaboration/M3_WAVE4_GEMINI_MARCIN.md` & `docs/contracts/M3_WAVE4_API.md`）：
1. 纯函数校验，不执行 SQLite 写操作或网络请求。
2. 严格区分并标明资料依据（`grounding_type='canon'`，必须附带本次授权的 `source_ids`）与旅程内补写（`grounding_type='adventure_supplement'`，记入 `supplement_ledger`）。
3. 检查来源只属于该世界：拦截跨世界来源（`403 CROSS_WORLD_SOURCE_FORBIDDEN`）、跨实例标识（`403 CROSS_INSTANCE_FORBIDDEN`）与伪造来源（`422 SOURCE_FABRICATED`）。
4. 处理重名与身份复用：校验实例内 `npc_id`、`location_id`、`event_id` 唯一性；若存在同名人物，要求或补齐 `homonym_disambiguator` 并记入 `identity_resolution_log`；严禁未消歧的同名同职重复人物。
5. 限制未公开信息：角色未获知的设定、远方未传开事件、NPC 私密动机与隐藏关系严禁进入 `player_opening_summary`。
6. 初始人物动作安排对齐 Li 的实例内身份（`npc_id`、`target_npc_id`）和时间语义（`world_minute`、`duration_minutes`、`next_due_minute`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from .errors import (
    AnalysisValidationError,
    CrossInstanceAnalysisError,
    CrossWorldSourceError,
    IdentityCollisionError,
    PlayerLedgerProtectedError,
    SourceFabricatedError,
    UnpublicInfoLeakError,
)
from .extractor import normalize_starting_point, safe_u16_truncate, u16_units
from .types import (
    player_knows_initial_event,
    ANALYSIS_RUN_MODES,
    ANALYSIS_STATUSES,
    DEFAULT_MAX_INITIAL_THREADS,
    DEFAULT_MIN_INITIAL_THREADS,
    FORBIDDEN_PATH_KEYS,
    GROUNDING_TYPE_ALIASES,
    NPC_CANONICAL_ACTION_KINDS,
    NPC_PLAN_KINDS,
    PLAN_KIND_TO_NPC_ACTION_KIND,
    PLAYER_SUMMARY_ALLOWED_VISIBILITY,
    PROTECTED_PLAYER_LEDGER_KEYS,
    VISIBILITY_LEVELS,
    BatchInputRange,
    GroundingType,
    ProposedInitialEvent,
    ProposedInitialNpc,
    ProposedLocation,
    ProposedNpcRelationship,
    SettingConflictItem,
    ValidatedWorldAnalysisDraft,
    VisibilityLevel,
    WorldAnalysisValidationContext,
)

_MIN_DURATION_BY_ACTION: dict[str, int] = {
    "travel": 15,
    "conversation_plan": 10,
    "shift_change": 15,
    "task_progress": 15,
}

_FORBIDDEN_PLAYER_SUMMARY_NPC_KEYS: frozenset[str] = frozenset(
    {
        "motivation",
        "secret_motivation",
        "private_motivation",
        "secret_goal",
        "hidden_plan",
        "cause_event_ids",
        "initial_known_event_ids",
        "known_facts",
    }
)


def _check_security_recursive(
    obj: Any,
    *,
    world_id: str,
    instance_id: str,
    other_instance_ids: frozenset[str],
    path: str = "draft",
) -> None:
    """递归拦截直接修改玩家个人账本、任意 JSON 路径操作符与跨世界/跨实例标识。"""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise AnalysisValidationError("VALIDATION_ERROR", f"{path} 包含非字符串键。")
            lowered = k.strip().lower()
            if (
                lowered in PROTECTED_PLAYER_LEDGER_KEYS
                or lowered.startswith(("player_ledger", "player_coin", "player_inv", "character_state"))
            ):
                raise PlayerLedgerProtectedError(k)
            if (
                lowered in FORBIDDEN_PATH_KEYS
                or "." in k
                or "/" in k
                or "[" in k
                or k.startswith("$")
            ):
                raise PlayerLedgerProtectedError(
                    k,
                    code="ARBITRARY_PATH_FORBIDDEN",
                    message=f"{path} 严禁包含任意 JSON 路径或补丁操作符字段: {k!r}。",
                )
            if lowered == "world_id" and v is not None:
                if not isinstance(v, str) or not v.strip():
                    raise AnalysisValidationError("VALIDATION_ERROR", f"{path}.world_id 必须为非空字符串。")
                if world_id and v.strip() != world_id:
                    raise CrossWorldSourceError(
                        world_id,
                        f"world:{v.strip()}",
                        v.strip(),
                        f"{path}.world_id ({v.strip()!r}) 与当前目标世界 ({world_id!r}) 不一致，拒绝越界操作。",
                    )
            if lowered in ("instance_id", "world_instance_id") and v is not None:
                if not isinstance(v, str) or not v.strip():
                    raise AnalysisValidationError("VALIDATION_ERROR", f"{path}.{k} 必须为非空字符串。")
                if instance_id and v.strip() != instance_id:
                    raise CrossInstanceAnalysisError(instance_id, v.strip(), v.strip())
            _check_security_recursive(
                v,
                world_id=world_id,
                instance_id=instance_id,
                other_instance_ids=other_instance_ids,
                path=f"{path}.{k}",
            )
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _check_security_recursive(
                item,
                world_id=world_id,
                instance_id=instance_id,
                other_instance_ids=other_instance_ids,
                path=f"{path}[{idx}]",
            )


def _require_str(value: Any, field_name: str, max_units: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为非空字符串。",
            {"field": field_name},
        )
    cleaned = value.strip()
    units = u16_units(cleaned, field_name)
    if units > max_units:
        raise AnalysisValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 长度 ({units}) 超过 {max_units} UTF-16 单位上限。",
            {"field": field_name, "max_units": max_units, "actual_units": units},
        )
    return cleaned


def _optional_str(value: Any, field_name: str, max_units: int = 300, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise AnalysisValidationError("VALIDATION_ERROR", f"字段 {field_name} 必须为字符串。")
    cleaned = value.strip()
    if not cleaned:
        return default
    units = u16_units(cleaned, field_name)
    if units > max_units:
        raise AnalysisValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 长度 ({units}) 超过 {max_units} UTF-16 单位上限。",
            {"field": field_name, "max_units": max_units, "actual_units": units},
        )
    return cleaned


def _available_location_id(preferred: str, name: str, occupied: set[str]) -> str:
    """Model-local labels are not authoritative identities; repair collisions deterministically."""
    candidate = preferred
    salt = 0
    while candidate in occupied:
        salt += 1
        token = hashlib.sha256(f"{preferred}\0{name}\0{salt}".encode()).hexdigest()[:20]
        candidate = f"loc_{token}"
    return candidate


def _require_int(
    value: Any,
    field_name: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if type(value) is not int:
        raise AnalysisValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为整数（拒绝布尔值或其它类型）。",
            {"field": field_name},
        )
    if min_value is not None and value < min_value:
        raise AnalysisValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} ({value}) 不能小于 {min_value}。",
            {"field": field_name, "min_value": min_value, "actual": value},
        )
    if max_value is not None and value > max_value:
        raise AnalysisValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} ({value}) 不能大于 {max_value}。",
            {"field": field_name, "max_value": max_value, "actual": value},
        )
    return value


def _normalize_grounding_type(raw_val: Any, field_name: str) -> GroundingType:
    if raw_val is None:
        return "canon"
    if not isinstance(raw_val, str) or not raw_val.strip():
        raise AnalysisValidationError("INVALID_GROUNDING_TYPE", f"{field_name} 必须为有效的依据类型字符串。")
    key = raw_val.strip().lower()
    resolved = GROUNDING_TYPE_ALIASES.get(key) or GROUNDING_TYPE_ALIASES.get(raw_val.strip())
    if resolved is None:
        raise AnalysisValidationError(
            "INVALID_GROUNDING_TYPE",
            f"{field_name} 值 {raw_val!r} 无效，仅支持 'canon'（资料依据）或 'adventure_supplement'（旅程内补写）。",
            {"field": field_name, "value": raw_val},
        )
    return resolved


def _normalize_visibility(raw_val: Any, field_name: str, default: VisibilityLevel = "public") -> VisibilityLevel:
    if raw_val is None:
        return default
    if not isinstance(raw_val, str) or raw_val.strip() not in VISIBILITY_LEVELS:
        raise AnalysisValidationError(
            "INVALID_VISIBILITY",
            f"{field_name} 仅支持 {sorted(VISIBILITY_LEVELS)}，实际收到: {raw_val!r}。",
            {"field": field_name},
        )
    return raw_val.strip()  # type: ignore[return-value]


def _validate_and_clean_source_ids(
    raw_sources: Any,
    *,
    field_name: str,
    grounding_type: GroundingType,
    ctx: WorldAnalysisValidationContext,
    anchor_fallback_for_supplement: bool = False,
) -> tuple[str, ...]:
    if raw_sources is None:
        raw_list: Sequence[Any] = ()
    elif isinstance(raw_sources, (list, tuple)):
        raw_list = raw_sources
    else:
        raise AnalysisValidationError("VALIDATION_ERROR", f"{field_name} 必须为字符串数组。")

    cleaned: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_list):
        sid = _require_str(item, f"{field_name}[{idx}]", 160)
        if sid in ctx.other_world_source_ids:
            raise CrossWorldSourceError(
                ctx.world_id,
                sid,
                ctx.other_world_source_ids[sid],
            )
        if not (sid.startswith("chunk:") or sid.startswith("entry:")):
            raise SourceFabricatedError(sid, f"{field_name}[{idx}] 来源格式非法 ({sid!r})，须为 chunk:<id> 或 entry:<id>。")
        if ctx.allowed_source_ids and sid not in ctx.allowed_source_ids:
            raise SourceFabricatedError(
                sid,
                f"{field_name}[{idx}] 引用了未在当前世界分析输入中授权的来源: {sid!r}。",
            )
        if sid not in seen:
            seen.add(sid)
            cleaned.append(sid)

    if grounding_type == "canon" and not cleaned:
        raise SourceFabricatedError(
            "",
            f"{field_name} 标记为资料依据 (grounding_type='canon')，但未提供任何有效的授权 source_ids。",
        )

    if grounding_type == "adventure_supplement" and not cleaned and anchor_fallback_for_supplement and ctx.allowed_source_ids:
        # 旅程内补写事件线在落库至 world_events 时绑定首条世界锚点来源，同时保留 adventure_supplement 标记与补写原因
        sorted_allowed = sorted(ctx.allowed_source_ids)
        cleaned.append(sorted_allowed[0])

    return tuple(cleaned)


def _validate_caller_player_summary_no_leaks(
    raw_summary: Mapping[str, Any],
    *,
    starting_location: str,
    npcs_by_id: Mapping[str, ProposedInitialNpc],
    events_by_id: Mapping[str, ProposedInitialEvent],
) -> None:
    """若输入草案显式携带了 `player_opening_summary`，严格检查其中是否混入角色未获知或私密信息。"""
    raw_present = raw_summary.get("present_npcs") or ()
    if isinstance(raw_present, Sequence):
        for idx, item in enumerate(raw_present):
            if not isinstance(item, Mapping):
                continue
            leaked_keys = set(item.keys()) & _FORBIDDEN_PLAYER_SUMMARY_NPC_KEYS
            for lk in leaked_keys:
                if item.get(lk):
                    raise UnpublicInfoLeakError(
                        f"player_opening_summary.present_npcs[{idx}].{lk}",
                        f"玩家开局摘要禁止暴露人物私密动机或内部因果字段 ({lk})。",
                    )
            nid = str(item.get("npc_id") or "").strip()
            if nid and nid in npcs_by_id:
                auth_npc = npcs_by_id[nid]
                if not auth_npc.present_at_start or auth_npc.location != starting_location:
                    raise UnpublicInfoLeakError(
                        f"player_opening_summary.present_npcs[{idx}]",
                        f"人物「{auth_npc.name}」({nid}) 当前位于「{auth_npc.location}」而非起点「{starting_location}」，不得列入玩家开局在场人物摘要。",
                    )
                if auth_npc.visibility not in PLAYER_SUMMARY_ALLOWED_VISIBILITY:
                    raise UnpublicInfoLeakError(
                        f"player_opening_summary.present_npcs[{idx}]",
                        f"人物「{auth_npc.name}」({nid}) 处于隐藏/未公开状态 ({auth_npc.visibility})，不得进入玩家开局摘要。",
                    )

    raw_situations = raw_summary.get("known_situations") or raw_summary.get("events") or ()
    if isinstance(raw_situations, Sequence):
        for idx, item in enumerate(raw_situations):
            if not isinstance(item, Mapping):
                continue
            eid = str(item.get("event_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if "【隐藏" in title or "【绝密" in title:
                raise UnpublicInfoLeakError(
                    f"player_opening_summary.known_situations[{idx}].title",
                    "禁止将带有【隐藏】或【绝密】标记的内部事件暴露进玩家开局摘要。",
                )
            if eid and eid in events_by_id:
                auth_ev = events_by_id[eid]
                if not player_knows_initial_event(auth_ev):
                    raise UnpublicInfoLeakError(
                        f"player_opening_summary.known_situations[{idx}]",
                        f"事件「{auth_ev.title}」({eid}) 为角色尚未获知的隐藏或远方事件，禁止进入玩家开局摘要。",
                    )


def build_player_opening_summary(
    *,
    draft_id: str,
    world_id: str,
    instance_id: str,
    status: str,
    run_mode: str,
    starting_point: Any,
    scene_overview: str,
    locations: Sequence[ProposedLocation],
    npcs: Sequence[ProposedInitialNpc],
    events: Sequence[ProposedInitialEvent],
    relationships: Sequence[ProposedNpcRelationship],
    conflicts: Sequence[SettingConflictItem],
) -> dict[str, Any]:
    """构建仅包含玩家在开局起点应知信息的只读开局摘要（供 Ma 手机端直接展示）。

    过滤保证：
    1. 仅包含位于 `starting_point.location`、`present_at_start=True` 且 `visibility in ('public', 'local_observable')` 的在场人物；
       不返回 `motivation`、`goal`、`cause_event_ids`、`initial_known_event_ids` 等后台私密字段。
    2. 包含公开已知局势，以及有开局亲历依据的玩家私密认知；不直接展示后台事件描述，
       使用玩家视角标题与内容（`player_rumor_title` / `player_rumor_content`），隐藏后台 `event_id` 与全知权威描述。
    3. 仅展示双方均在场且公开可见（`visibility in ('public', 'local_observable')`）的人物关系。
    """
    sp = normalize_starting_point(starting_point)
    present_npcs_public: list[dict[str, Any]] = []
    present_npc_ids: set[str] = set()

    for npc in npcs:
        if (
            npc.present_at_start
            and npc.location == sp.location
            and npc.availability != "in_transit"
            and npc.visibility in PLAYER_SUMMARY_ALLOWED_VISIBILITY
        ):
            present_npc_ids.add(npc.npc_id)
            display_name = (
                f"{npc.name}（{npc.homonym_disambiguator}）"
                if npc.homonym_disambiguator
                else npc.name
            )
            present_npcs_public.append(
                {
                    "npc_id": npc.npc_id,
                    "name": npc.name,
                    "display_name": display_name,
                    "role": npc.role,
                    "faction": npc.faction,
                    "location": npc.location,
                    "current_activity": npc.current_activity,
                    "public_demeanor": npc.public_demeanor,
                    "availability": npc.availability,
                    "grounding_type": npc.grounding_type,
                    "source_ids": list(npc.source_ids),
                }
            )

    known_situations: list[dict[str, Any]] = []
    for ev in events:
        if player_knows_initial_event(ev) and ev.player_rumor_content:
            rumor_title = ev.player_rumor_title or f"{ev.location}传闻"
            rumor_body = ev.player_rumor_content
            known_situations.append(
                {
                    "title": rumor_title,
                    "summary": rumor_body,
                    "location": ev.location,
                    "knowledge_type": ev.player_knowledge_type,
                    "channel": ev.player_channel or "rumor",
                    "grounding_type": ev.grounding_type,
                    "source_ids": list(ev.source_ids),
                }
            )

    public_relationships: list[dict[str, Any]] = []
    for rel in relationships:
        if (
            rel.visibility in PLAYER_SUMMARY_ALLOWED_VISIBILITY
            and rel.from_npc_id in present_npc_ids
        ):
            public_relationships.append(
                {
                    "from_npc_id": rel.from_npc_id,
                    "to_target_id": rel.to_target_id,
                    "relation_type": rel.relation_type,
                    "summary": rel.summary,
                    "grounding_type": rel.grounding_type,
                    "source_ids": list(rel.source_ids),
                }
            )

    visible_conflicts = [
        {
            "conflict_id": c.conflict_id,
            "topic": c.topic,
            "summary": c.summary,
            "resolution_policy": c.resolution_policy,
            "primary_source_id": c.primary_source_id,
            "conflicting_source_id": c.conflicting_source_id,
        }
        for c in conflicts
        if c.player_visible_hint
    ]

    canon_count = sum(1 for n in npcs if n.grounding_type == "canon") + sum(
        1 for e in events if e.grounding_type == "canon"
    )
    supplement_count = sum(1 for n in npcs if n.grounding_type == "adventure_supplement") + sum(
        1 for e in events if e.grounding_type == "adventure_supplement"
    )

    return {
        "draft_id": draft_id,
        "world_id": world_id,
        "instance_id": instance_id,
        "status": status,
        "run_mode": run_mode,
        "starting_point": {
            "location": sp.location,
            "world_minute": sp.world_minute,
            "starts_at": sp.starts_at,
            "character_name": sp.character_name,
        },
        "scene_overview": scene_overview,
        "present_npcs": present_npcs_public,
        "known_situations": known_situations,
        "public_relationships": public_relationships,
        "conflicts": visible_conflicts,
        "grounding_stats": {
            "canon_grounded_count": canon_count,
            "adventure_supplement_count": supplement_count,
        },
    }


def _normalize_context(
    context: WorldAnalysisValidationContext | Mapping[str, Any],
) -> WorldAnalysisValidationContext:
    if isinstance(context, WorldAnalysisValidationContext):
        return context
    if not isinstance(context, Mapping):
        raise AnalysisValidationError("CONTEXT_INVALID", "context 必须为 WorldAnalysisValidationContext 或字典。")

    world_id = _require_str(context.get("world_id"), "context.world_id", 120)
    instance_id = _require_str(context.get("instance_id") or "inst_default", "context.instance_id", 120)
    sp = normalize_starting_point(
        context.get("starting_point") or context.get("starting_location") or "默认起点",
        default_world_minute=int(context.get("world_minute") or 0),
    )
    world_minute = _require_int(
        context.get("world_minute", sp.world_minute),
        "context.world_minute",
        min_value=0,
    )
    base_rev = _require_int(
        context.get("base_world_revision", 0),
        "context.base_world_revision",
        min_value=0,
    )
    allowed_sids = frozenset(
        str(s).strip() for s in (context.get("allowed_source_ids") or ()) if str(s).strip()
    )
    confirmed_sids = frozenset(
        str(s).strip() for s in (context.get("confirmed_entry_source_ids") or ()) if str(s).strip()
    )
    other_world_raw = context.get("other_world_source_ids") or {}
    other_world_map = (
        {str(k): str(v) for k, v in other_world_raw.items()}
        if isinstance(other_world_raw, Mapping)
        else {}
    )
    other_inst = frozenset(
        str(i).strip() for i in (context.get("other_instance_ids") or ()) if str(i).strip()
    )
    raw_ranges = context.get("input_ranges") or ()
    input_ranges = tuple(r for r in raw_ranges if isinstance(r, BatchInputRange))
    raw_conflicts = context.get("detected_conflicts") or ()
    detected_conflicts = tuple(c for c in raw_conflicts if isinstance(c, SettingConflictItem))

    min_threads = _require_int(
        context.get("min_initial_threads", DEFAULT_MIN_INITIAL_THREADS),
        "context.min_initial_threads",
        min_value=1,
    )
    max_threads = _require_int(
        context.get("max_initial_threads", DEFAULT_MAX_INITIAL_THREADS),
        "context.max_initial_threads",
        min_value=min_threads,
    )

    return WorldAnalysisValidationContext(
        world_id=world_id,
        instance_id=instance_id,
        starting_point=sp,
        world_minute=world_minute,
        base_world_revision=base_rev,
        allowed_source_ids=allowed_sids,
        confirmed_entry_source_ids=confirmed_sids,
        other_world_source_ids=other_world_map,
        other_instance_ids=other_inst,
        input_ranges=input_ranges,
        detected_conflicts=detected_conflicts,
        min_initial_threads=min_threads,
        max_initial_threads=max_threads,
    )


def validate_analysis_draft(
    raw_draft: ValidatedWorldAnalysisDraft | Mapping[str, Any],
    context: WorldAnalysisValidationContext | Mapping[str, Any],
) -> ValidatedWorldAnalysisDraft:
    """在事务外纯函数校验 AI 初始化草案。"""
    ctx = _normalize_context(context)

    if isinstance(raw_draft, ValidatedWorldAnalysisDraft):
        if raw_draft.world_id != ctx.world_id:
            raise CrossWorldSourceError(ctx.world_id, f"world:{raw_draft.world_id}", raw_draft.world_id)
        if raw_draft.instance_id != ctx.instance_id:
            raise CrossInstanceAnalysisError(ctx.instance_id, raw_draft.instance_id, raw_draft.instance_id)
        return raw_draft

    if not isinstance(raw_draft, Mapping):
        raise AnalysisValidationError("DRAFT_INVALID", "AI 初始化草案必须为 JSON 对象。")

    _check_security_recursive(
        raw_draft,
        world_id=ctx.world_id,
        instance_id=ctx.instance_id,
        other_instance_ids=ctx.other_instance_ids,
    )

    if "world_minute" in raw_draft and raw_draft["world_minute"] is not None:
        echo_min = _require_int(raw_draft["world_minute"], "draft.world_minute", min_value=0)
        if echo_min != ctx.world_minute:
            raise AnalysisValidationError(
                "TIMESTAMP_OVERRIDE_FORBIDDEN",
                f"草案回显的 world_minute ({echo_min}) 与已确认开局时间 ({ctx.world_minute}) 不一致。",
                {"expected_world_minute": ctx.world_minute, "actual_world_minute": echo_min},
            )

    if "base_world_revision" in raw_draft and raw_draft["base_world_revision"] is not None:
        echo_rev = _require_int(raw_draft["base_world_revision"], "draft.base_world_revision", min_value=0)
        if echo_rev != ctx.base_world_revision:
            raise AnalysisValidationError(
                "WORLD_REVISION_MISMATCH",
                f"草案回显的 base_world_revision ({echo_rev}) 与当前基准版本 ({ctx.base_world_revision}) 不一致。",
                status=409,
            )

    draft_id = _optional_str(raw_draft.get("draft_id"), "draft.draft_id", 120, default=f"wad_{uuid4().hex[:12]}")
    status_val = _optional_str(raw_draft.get("status"), "draft.status", 40, default="ready_to_confirm")
    if status_val not in ANALYSIS_STATUSES:
        raise AnalysisValidationError("INVALID_STATUS", f"不支持的草案状态: {status_val!r}。")

    run_mode = _optional_str(
        raw_draft.get("run_mode"),
        "draft.run_mode",
        40,
        default=ctx.starting_point.run_mode,
    )
    if run_mode not in ANALYSIS_RUN_MODES:
        raise AnalysisValidationError("INVALID_RUN_MODE", f"不支持的运行模式: {run_mode!r}。")

    request_id = _optional_str(
        raw_draft.get("request_id"),
        "draft.request_id",
        120,
        default=ctx.starting_point.request_id or f"req_{uuid4().hex[:10]}",
    )
    summary = _optional_str(
        raw_draft.get("summary"),
        "draft.summary",
        500,
        default=f"围绕起点「{ctx.starting_point.location}」建立初始世界局势。",
    )
    scene_overview = _optional_str(
        raw_draft.get("scene_overview"),
        "draft.scene_overview",
        1000,
        default=f"你身处{ctx.starting_point.location}，四周人物各自忙碌。",
    )

    supplement_ledger: list[dict[str, Any]] = []
    identity_resolution_log: list[dict[str, Any]] = list(raw_draft.get("identity_resolution_log") or [])
    all_referenced_sources: set[str] = set()

    # 1. 校验地点列表
    raw_locations = raw_draft.get("locations") or ()
    if not isinstance(raw_locations, Sequence):
        raise AnalysisValidationError("VALIDATION_ERROR", "draft.locations 必须为数组。")

    validated_locations: list[ProposedLocation] = []
    seen_loc_ids: set[str] = set()
    seen_loc_names: set[str] = set()
    location_index: dict[str, int] = {}

    for idx, r_loc in enumerate(raw_locations):
        prefix = f"locations[{idx}]"
        if not isinstance(r_loc, Mapping):
            raise AnalysisValidationError("VALIDATION_ERROR", f"{prefix} 必须为对象。")
        name = _require_str(r_loc.get("name"), f"{prefix}.name", 100)
        loc_id = _optional_str(
            r_loc.get("location_id"),
            f"{prefix}.location_id",
            100,
            default=f"loc_{idx + 1}",
        )
        for other_inst in ctx.other_instance_ids:
            if other_inst and other_inst in loc_id:
                raise CrossInstanceAnalysisError(ctx.instance_id, loc_id, other_inst)
        g_type = _normalize_grounding_type(r_loc.get("grounding_type"), f"{prefix}.grounding_type")
        sids = _validate_and_clean_source_ids(
            r_loc.get("source_ids"),
            field_name=f"{prefix}.source_ids",
            grounding_type=g_type,
            ctx=ctx,
        )
        all_referenced_sources.update(sids)
        supp_reason = _optional_str(r_loc.get("supplement_reason"), f"{prefix}.supplement_reason", 300)
        region = _optional_str(r_loc.get("region"), f"{prefix}.region", 100, default=name)
        description = _require_str(r_loc.get("description") or f"{name}的局势场景。", f"{prefix}.description", 600)
        vis = _normalize_visibility(r_loc.get("visibility"), f"{prefix}.visibility", default="public")
        if name in location_index:
            position = location_index[name]
            existing = validated_locations[position]
            # Keep contradictory descriptions and source evidence in the audit log,
            # without selecting a new world fact or broadening visibility.
            identity_resolution_log.append({
                "action": "merged_duplicate_location", "canonical_location_id": existing.location_id,
                "original_location_id": loc_id, "name": name,
                "retained_region": existing.region, "incoming_region": region,
                "retained_description": existing.description, "incoming_description": description,
                "incoming_source_ids": list(sids),
                "reason": "同名地点合并为一个身份，保留不同描述与来源供核对，不据此创建另一地点。",
            })
            rank = {"public": 0, "local_observable": 1, "faction_internal": 2, "secret": 3}
            visibility = max((existing.visibility, vis), key=lambda value: rank[value])
            preferred = existing
            if existing.grounding_type != "canon" and g_type == "canon":
                preferred = replace(existing, region=region, description=description,
                                    grounding_type=g_type, supplement_reason=supp_reason)
            validated_locations[position] = replace(preferred,
                source_ids=tuple(dict.fromkeys((*existing.source_ids, *sids))), visibility=visibility,
                is_starting_location=existing.is_starting_location or name == ctx.starting_point.location or bool(r_loc.get("is_starting_location")))
            continue
        original_id = loc_id
        loc_id = _available_location_id(loc_id, name, seen_loc_ids)
        if loc_id != original_id:
            identity_resolution_log.append({
                "action": "reassigned_location_id", "original_location_id": original_id,
                "canonical_location_id": loc_id, "name": name,
                "reason": "不同名称的地点误用同一模型编号，分配独立身份并保留地点名称与来源。",
            })
        location_index[name] = len(validated_locations)
        seen_loc_ids.add(loc_id)
        seen_loc_names.add(name)
        if g_type == "adventure_supplement":
            if not supp_reason:
                supp_reason = f"为完善起点「{ctx.starting_point.location}」周边地理连通性而作的旅程内推演补写。"
            supplement_ledger.append(
                {
                    "entity_kind": "location",
                    "entity_id": loc_id,
                    "name": name,
                    "reason": supp_reason,
                    "anchor_source_ids": list(sids),
                }
            )
        validated_locations.append(
            ProposedLocation(
                location_id=loc_id,
                name=name,
                region=region,
                description=description,
                is_starting_location=(name == ctx.starting_point.location or bool(r_loc.get("is_starting_location"))),
                grounding_type=g_type,
                source_ids=sids,
                supplement_reason=supp_reason,
                visibility=vis,
            )
        )

    # 若未显式包含起点地点，自动补齐起点地点记录
    if ctx.starting_point.location not in seen_loc_names:
        anchor_sids = (sorted(ctx.allowed_source_ids)[0],) if ctx.allowed_source_ids else ()
        validated_locations.insert(
            0,
            ProposedLocation(
                location_id=_available_location_id("loc_starting_point", ctx.starting_point.location, seen_loc_ids),
                name=ctx.starting_point.location,
                region=ctx.starting_point.location,
                description=scene_overview,
                is_starting_location=True,
                grounding_type="canon" if anchor_sids else "adventure_supplement",
                source_ids=anchor_sids,
                supplement_reason="" if anchor_sids else "起点基础位置补写",
                visibility="public",
            ),
        )

    # 2. 校验初始主要事件线（3～6 条）
    raw_events = raw_draft.get("events") or ()
    if not isinstance(raw_events, Sequence) or not (ctx.min_initial_threads <= len(raw_events) <= ctx.max_initial_threads):
        raise AnalysisValidationError(
            "INITIAL_EVENT_COUNT_INVALID",
            f"初始事件线数量须在 {ctx.min_initial_threads}～{ctx.max_initial_threads} 条之间，实际为 {len(raw_events) if isinstance(raw_events, Sequence) else 0} 条。",
            {
                "min_initial_threads": ctx.min_initial_threads,
                "max_initial_threads": ctx.max_initial_threads,
                "actual_count": len(raw_events) if isinstance(raw_events, Sequence) else 0,
            },
        )

    validated_events: list[ProposedInitialEvent] = []
    seen_event_ids: set[str] = set()
    seen_event_titles: set[str] = set()

    for idx, r_ev in enumerate(raw_events):
        prefix = f"events[{idx}]"
        if not isinstance(r_ev, Mapping):
            raise AnalysisValidationError("VALIDATION_ERROR", f"{prefix} 必须为对象。")
        ev_id = _optional_str(r_ev.get("event_id"), f"{prefix}.event_id", 120, default=f"evt_init_{idx + 1:02d}")
        for other_inst in ctx.other_instance_ids:
            if other_inst and other_inst in ev_id:
                raise CrossInstanceAnalysisError(ctx.instance_id, ev_id, other_inst)
        if ev_id.startswith(("tmp_", "temp_", "new_", "__pending")):
            raise AnalysisValidationError("TEMP_EVENT_ID_FORBIDDEN", f"{prefix}.event_id 不能使用临时 ID 前缀: {ev_id!r}。")
        title = _require_str(r_ev.get("title"), f"{prefix}.title", 120)
        if ev_id in seen_event_ids or title in seen_event_titles:
            raise IdentityCollisionError(
                ev_id,
                code="DUPLICATE_EVENT_ID",
                message=f"{prefix} 存在重复的初始事件标识或标题: {title} ({ev_id})。",
            )
        seen_event_ids.add(ev_id)
        seen_event_titles.add(title)

        description = _require_str(r_ev.get("description"), f"{prefix}.description", 2000)
        ev_loc = _require_str(r_ev.get("location") or ctx.starting_point.location, f"{prefix}.location", 120)
        scope = _optional_str(r_ev.get("scope"), f"{prefix}.scope", 40, default="local")
        if scope not in ("local", "regional", "national", "international"):
            raise AnalysisValidationError("INVALID_EVENT_SCOPE", f"{prefix}.scope 无效: {scope!r}。")

        raw_parts = r_ev.get("participants") or ()
        if not isinstance(raw_parts, Sequence) or not raw_parts:
            raise AnalysisValidationError("EVENT_PARTICIPANTS_REQUIRED", f"{prefix} 必须包含至少 1 名参与方。")
        participants = tuple(
            dict.fromkeys(_require_str(p, f"{prefix}.participants[{p_i}]", 80) for p_i, p in enumerate(raw_parts))
        )

        stage = _optional_str(r_ev.get("stage"), f"{prefix}.stage", 80, default="initial")
        next_upd = _require_int(
            r_ev.get("next_update_minute", ctx.world_minute + 120),
            f"{prefix}.next_update_minute",
            min_value=ctx.world_minute + 1,
        )

        g_type = _normalize_grounding_type(r_ev.get("grounding_type"), f"{prefix}.grounding_type")
        sids = _validate_and_clean_source_ids(
            r_ev.get("source_ids"),
            field_name=f"{prefix}.source_ids",
            grounding_type=g_type,
            ctx=ctx,
            anchor_fallback_for_supplement=True,
        )
        all_referenced_sources.update(sids)
        supp_reason = _optional_str(r_ev.get("supplement_reason"), f"{prefix}.supplement_reason", 300)
        if g_type == "adventure_supplement":
            if not supp_reason:
                supp_reason = "基于世界资料背景空白补写的本旅程初始动态局势推演。"
            supplement_ledger.append(
                {
                    "entity_kind": "event",
                    "entity_id": ev_id,
                    "title": title,
                    "reason": supp_reason,
                    "anchor_source_ids": list(sids),
                }
            )

        vis = _normalize_visibility(
            r_ev.get("visibility"),
            f"{prefix}.visibility",
            default="local_observable" if ev_loc == ctx.starting_point.location else "faction_internal",
        )
        player_known = bool(
            r_ev.get(
                "player_known_at_start",
                ev_loc == ctx.starting_point.location and vis in PLAYER_SUMMARY_ALLOWED_VISIBILITY,
            )
        )
        basis = _optional_str(r_ev.get("player_known_basis"), f"{prefix}.player_known_basis", 360)
        private_known = vis not in PLAYER_SUMMARY_ALLOWED_VISIBILITY and player_known
        if private_known:
            opening_text = ctx.starting_point.player_description
            # The opening is the player's own confirmed description, not the world
            # source library or the model's authoritative event description.
            candidate = basis or str(r_ev.get("player_rumor_content") or "").strip()
            sentences = re.split(r"[。！？!?\n]", opening_text)
            contexts = [sentence for sentence in sentences if candidate and candidate in sentence]
            unknown = ("你不知道", "你并不知道", "你尚不知", "你还不知道", "你未曾得知", "你对此不知情", "殊不知", "我不知道", "我尚不知")
            if candidate and len(candidate) <= 360 and candidate in opening_text and contexts and not any(
                marker in context for context in contexts for marker in unknown
            ):
                basis = candidate
            else:
                # An inconsistent model projection cannot grant private knowledge or
                # block the entire opening. Keep the event hidden and the user's
                # complete opening narrative intact; don't invent an access channel.
                player_known = False
                basis = ""
                identity_resolution_log.append({
                    "action": "withheld_ungrounded_player_knowledge", "event_id": ev_id,
                    "reason": "私密事件缺少玩家开局中可核对的亲历依据，仅保留后台事件。",
                })
        elif not private_known:
            basis = ""

        rumor_title = _optional_str(
            r_ev.get("player_rumor_title"),
            f"{prefix}.player_rumor_title",
            100,
            default=f"{ev_loc}近况风声" if player_known else "",
        )
        rumor_content = _optional_str(
            r_ev.get("player_rumor_content"),
            f"{prefix}.player_rumor_content",
            400,
            default="",
        )
        if private_known and player_known:
            # Never publish the event's hidden explanation or a model-invented
            # spoiler title along with a legitimate private commission.
            rumor_title = "你的开局经历"
            rumor_content = basis
        if player_known:
            if not rumor_content or (not basis and rumor_title == title and rumor_content == description):
                rumor_content = ""
            if "【隐藏" in rumor_title or "【绝密" in rumor_title or "【隐藏" in rumor_content or "【绝密" in rumor_content:
                raise UnpublicInfoLeakError(
                    f"{prefix}.player_rumor_content",
                    f"事件「{title}」的玩家开局传闻摘要不得包含【隐藏】或【绝密】全知字样。",
                )

        p_channel = _optional_str(
            r_ev.get("player_channel"),
            f"{prefix}.player_channel",
            24,
            default="notice" if scope == "local" else "rumor",
        )
        if basis:
            p_channel = "observed"
        if player_known and p_channel not in ("observed", "notice", "rumor"):
            p_channel = "rumor"
        p_ktype = _optional_str(
            r_ev.get("player_knowledge_type"),
            f"{prefix}.player_knowledge_type",
            24,
            default="confirmed" if p_channel in ("observed", "notice") else "rumor",
        )
        if p_ktype not in ("confirmed", "suspected", "rumor"):
            p_ktype = "rumor"

        validated_events.append(
            ProposedInitialEvent(
                event_id=ev_id,
                kind="thread",
                title=title,
                description=description,
                location=ev_loc,
                scope=scope,  # type: ignore[arg-type]
                participants=participants,
                stage=stage,
                status="active",
                created_minute=ctx.world_minute,
                next_update_minute=next_upd,
                grounding_type=g_type,
                source_ids=sids,
                supplement_reason=supp_reason,
                visibility=vis,
                player_known_at_start=player_known,
                player_known_basis=basis,
                player_rumor_title=rumor_title if player_known else "",
                player_rumor_content=rumor_content if player_known else "",
                player_channel=p_channel if player_known else "",  # type: ignore[arg-type]
                player_knowledge_type=p_ktype,  # type: ignore[arg-type]
                consequences=tuple(
                    dict(c) for c in (r_ev.get("consequences") or ()) if isinstance(c, Mapping)
                ),
            )
        )

    events_by_id = {ev.event_id: ev for ev in validated_events}

    # 3. 校验初始人物列表（处理重名、身份复用、Li 时间语义与动作约束）
    raw_npcs = raw_draft.get("npcs") or ()
    if not isinstance(raw_npcs, Sequence) or len(raw_npcs) == 0:
        raise AnalysisValidationError("EMPTY_NPCS_PROPOSAL", "AI 初始化草案必须包含至少 1 名初始人物。")

    # 先收集全部合法 npc_id 并检查重名与 ID 唯一性
    seen_npc_ids: set[str] = set()
    npcs_by_name: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for idx, r_npc in enumerate(raw_npcs):
        prefix = f"npcs[{idx}]"
        if not isinstance(r_npc, Mapping):
            raise AnalysisValidationError("VALIDATION_ERROR", f"{prefix} 必须为对象。")
        nid = _require_str(r_npc.get("npc_id"), f"{prefix}.npc_id", 100)
        if nid.lower() == "player":
            raise PlayerLedgerProtectedError("npc_id", message="严禁将 player 作为 NPC 初始化身份。")
        for other_inst in ctx.other_instance_ids:
            if other_inst and other_inst in nid:
                raise CrossInstanceAnalysisError(ctx.instance_id, nid, other_inst)
        if nid in seen_npc_ids:
            raise IdentityCollisionError(
                nid,
                code="DUPLICATE_NPC_ID",
                message=f"同一草案中出现重复的实例内人物标识: {nid!r}。",
            )
        seen_npc_ids.add(nid)
        n_name = _require_str(r_npc.get("name"), f"{prefix}.name", 80)
        npcs_by_name.setdefault(n_name, []).append((idx, r_npc))

    # 检查同名人物是否经过有效消歧（同人未合并 vs 同名不同人消歧）
    auto_disamb_by_idx: dict[int, str] = {}
    for shared_name, entries in npcs_by_name.items():
        if len(entries) < 2:
            continue
        signatures: set[tuple[str, str]] = set()
        for idx, r_npc in entries:
            r_role = str(r_npc.get("role") or r_npc.get("identity") or "").strip()
            r_home = str(r_npc.get("home_location") or r_npc.get("location") or "").strip()
            r_disamb = str(r_npc.get("homonym_disambiguator") or "").strip()
            sig = (r_role, r_home)
            if sig in signatures and not r_disamb:
                raise IdentityCollisionError(
                    str(r_npc.get("npc_id")),
                    code="UNRESOLVED_DUPLICATE_NPC",
                    message=(
                        f"人物「{shared_name}」在同一地点 ({r_home}) 与同一身份 ({r_role}) 下被重复创建且未消歧，"
                        "请合并同一人物或为同名不同人提供区分信息。"
                    ),
                )
            signatures.add(sig)
            if not r_disamb:
                auto_disamb_by_idx[idx] = f"{r_home or '本地'}·{r_role or '人物'}"
        if len(entries) >= 2:
            identity_resolution_log.append(
                {
                    "action": "verified_homonym_separation",
                    "name": shared_name,
                    "npc_ids": [str(e[1].get("npc_id")) for e in entries],
                    "reason": f"同名人物「{shared_name}」已确认为不同身份/地点的独立个体并绑定各自实例 ID。",
                }
            )

    validated_npcs: list[ProposedInitialNpc] = []
    all_validated_rels: list[ProposedNpcRelationship] = []

    for idx, r_npc in enumerate(raw_npcs):
        prefix = f"npcs[{idx}]"
        npc_id = str(r_npc["npc_id"]).strip()
        name = str(r_npc["name"]).strip()
        role = _require_str(r_npc.get("role") or r_npc.get("identity"), f"{prefix}.role", 160)
        faction = _optional_str(r_npc.get("faction"), f"{prefix}.faction", 120, default="")
        location = _require_str(r_npc.get("location"), f"{prefix}.location", 100)
        home_location = _optional_str(r_npc.get("home_location"), f"{prefix}.home_location", 100, default=location)
        goal = _require_str(r_npc.get("goal") or r_npc.get("motivation"), f"{prefix}.goal", 240)
        motivation = _require_str(r_npc.get("motivation") or goal, f"{prefix}.motivation", 360)
        current_activity = _require_str(
            r_npc.get("current_activity") or r_npc.get("activity"),
            f"{prefix}.current_activity",
            300,
        )
        public_demeanor = _optional_str(
            r_npc.get("public_demeanor"),
            f"{prefix}.public_demeanor",
            240,
            default=f"{name}（{role}）正在{location}{current_activity}。",
        )

        raw_plan = str(r_npc.get("plan_kind") or r_npc.get("action_kind") or "stay").strip()
        if raw_plan not in NPC_PLAN_KINDS:
            raise AnalysisValidationError(
                "INVALID_NPC_PLAN_KIND",
                f"{prefix}.plan_kind ({raw_plan!r}) 无效，仅支持 {sorted(NPC_PLAN_KINDS)}。",
            )
        plan_kind = raw_plan  # type: ignore[assignment]
        raw_act = str(r_npc.get("action_kind") or PLAN_KIND_TO_NPC_ACTION_KIND[plan_kind]).strip()
        if raw_act not in NPC_CANONICAL_ACTION_KINDS:
            raise AnalysisValidationError(
                "INVALID_NPC_ACTION_KIND",
                f"{prefix}.action_kind ({raw_act!r}) 必须属于 {sorted(NPC_CANONICAL_ACTION_KINDS)}。",
            )
        action_kind = raw_act  # type: ignore[assignment]

        min_dur = _MIN_DURATION_BY_ACTION[action_kind]
        raw_dur = r_npc.get("duration_minutes")
        raw_due = (
            r_npc.get("next_due_minute")
            if "next_due_minute" in r_npc
            else r_npc.get("next_update_minute")
        )
        if raw_dur is not None:
            duration_minutes = _require_int(raw_dur, f"{prefix}.duration_minutes", min_value=min_dur)
        elif raw_due is not None:
            due_val = _require_int(raw_due, f"{prefix}.next_due_minute", min_value=ctx.world_minute + min_dur)
            duration_minutes = due_val - ctx.world_minute
        else:
            duration_minutes = max(30, min_dur)

        if raw_due is not None:
            next_due_minute = _require_int(raw_due, f"{prefix}.next_due_minute", min_value=ctx.world_minute + 1)
            if next_due_minute - ctx.world_minute < duration_minutes:
                raise AnalysisValidationError(
                    "NPC_DURATION_EXCEEDS_DUE",
                    f"{prefix} 持续时间 ({duration_minutes}) 超过下次到期时间间隔 ({next_due_minute - ctx.world_minute})。",
                )
        else:
            next_due_minute = ctx.world_minute + duration_minutes

        from_loc = _optional_str(r_npc.get("from_location"), f"{prefix}.from_location", 100, default=home_location)
        to_loc = _optional_str(r_npc.get("to_location"), f"{prefix}.to_location", 100, default="")
        target_npc = _optional_str(r_npc.get("target_npc_id"), f"{prefix}.target_npc_id", 100, default="")
        meeting_loc = _optional_str(r_npc.get("meeting_location"), f"{prefix}.meeting_location", 100, default=location)
        conv_topic = _optional_str(r_npc.get("conversation_topic"), f"{prefix}.conversation_topic", 240, default="")
        shift_name = _optional_str(
            r_npc.get("shift_name") or r_npc.get("to_shift"),
            f"{prefix}.shift_name",
            100,
            default="当值守备" if action_kind == "shift_change" else "",
        )
        task_id = _optional_str(
            r_npc.get("task_id"),
            f"{prefix}.task_id",
            100,
            default=f"task_{npc_id}_init" if action_kind == "task_progress" else "",
        )
        task_title = _optional_str(
            r_npc.get("task_title"),
            f"{prefix}.task_title",
            160,
            default=current_activity if action_kind == "task_progress" else "",
        )
        task_stage = _optional_str(
            r_npc.get("task_stage"),
            f"{prefix}.task_stage",
            120,
            default="筹备阶段" if action_kind == "task_progress" else "",
        )
        task_prog = (
            _require_int(r_npc["task_progress_percent"], f"{prefix}.task_progress_percent", min_value=0, max_value=100)
            if r_npc.get("task_progress_percent") is not None
            else (20 if action_kind == "task_progress" else None)
        )

        if action_kind == "travel":
            if not to_loc:
                raise AnalysisValidationError("TRAVEL_DESTINATION_REQUIRED", f"{prefix} 为出行动作，必须指定 to_location。")
            if to_loc == from_loc:
                raise AnalysisValidationError("TRAVEL_SAME_LOCATION", f"{prefix} 出行起点与目的地不能相同 ({to_loc})。")
        elif action_kind == "conversation_plan":
            if not target_npc or target_npc == npc_id or target_npc.lower() == "player":
                raise AnalysisValidationError(
                    "INVALID_CONVERSATION_TARGET",
                    f"{prefix} 交谈安排必须指向同实例内另一名有效 NPC（target_npc_id={target_npc!r}）。",
                )
            if target_npc not in seen_npc_ids:
                raise AnalysisValidationError(
                    "TARGET_NPC_NOT_FOUND",
                    f"{prefix} 交谈目标 {target_npc!r} 不在本次初始化的 NPC 集合中。",
                )
            if not conv_topic:
                conv_topic = f"商议{location}近期事务"

        availability = _optional_str(
            r_npc.get("availability"),
            f"{prefix}.availability",
            24,
            default="in_transit" if action_kind == "travel" else "available",
        )
        if availability not in ("available", "in_transit", "busy", "off_duty"):
            raise AnalysisValidationError("INVALID_AVAILABILITY", f"{prefix}.availability 无效: {availability!r}。")

        present_at_start = bool(
            r_npc.get(
                "present_at_start",
                location == ctx.starting_point.location and availability != "in_transit",
            )
        )
        if location != ctx.starting_point.location or availability == "in_transit":
            present_at_start = False

        raw_tier = _optional_str(
            r_npc.get("depth_tier"),
            f"{prefix}.depth_tier",
            24,
            default="foreground" if present_at_start else "associated",
        )
        if raw_tier not in ("foreground", "associated", "background"):
            raw_tier = "foreground" if present_at_start else "associated"

        g_type = _normalize_grounding_type(r_npc.get("grounding_type"), f"{prefix}.grounding_type")
        sids = _validate_and_clean_source_ids(
            r_npc.get("source_ids"),
            field_name=f"{prefix}.source_ids",
            grounding_type=g_type,
            ctx=ctx,
            anchor_fallback_for_supplement=True,
        )
        all_referenced_sources.update(sids)
        supp_reason = _optional_str(r_npc.get("supplement_reason"), f"{prefix}.supplement_reason", 300)
        if g_type == "adventure_supplement":
            if not supp_reason:
                supp_reason = f"根据起点「{ctx.starting_point.location}」职能需求补写的人物日程与动机细节。"
            supplement_ledger.append(
                {
                    "entity_kind": "npc",
                    "entity_id": npc_id,
                    "name": name,
                    "reason": supp_reason,
                    "anchor_source_ids": list(sids),
                }
            )

        # 校验人物关联的事件引用
        raw_causes = r_npc.get("cause_event_ids") or ()
        cause_eids: list[str] = []
        for c_eid in raw_causes:
            c_str = _require_str(c_eid, f"{prefix}.cause_event_ids", 120)
            if c_str not in events_by_id:
                raise AnalysisValidationError(
                    "NPC_CAUSE_EVENT_NOT_FOUND",
                    f"{prefix} 引用的原因事件 {c_str!r} 不在本次初始事件线列表中。",
                )
            if c_str not in cause_eids:
                cause_eids.append(c_str)
        if not cause_eids and validated_events:
            cause_eids.append(validated_events[0].event_id)

        # Being affected by an event does not imply knowing its backstage facts.
        raw_known = r_npc.get("initial_known_event_ids") or ()
        known_eids: list[str] = []
        for k_eid in raw_known:
            k_str = _require_str(k_eid, f"{prefix}.initial_known_event_ids", 120)
            if k_str not in events_by_id:
                raise AnalysisValidationError(
                    "NPC_KNOWN_EVENT_NOT_FOUND",
                    f"{prefix} 引用的已知事件 {k_str!r} 不在本次初始事件线列表中。",
                )
            if k_str not in known_eids:
                known_eids.append(k_str)

        # 校验人物内嵌关系
        npc_rels: list[ProposedNpcRelationship] = []
        for r_idx, r_rel in enumerate(r_npc.get("relationships") or ()):
            if not isinstance(r_rel, Mapping):
                continue
            rel_g = _normalize_grounding_type(r_rel.get("grounding_type"), f"{prefix}.relationships[{r_idx}].grounding_type")
            rel_sids = _validate_and_clean_source_ids(
                r_rel.get("source_ids") or sids,
                field_name=f"{prefix}.relationships[{r_idx}].source_ids",
                grounding_type=rel_g,
                ctx=ctx,
                anchor_fallback_for_supplement=True,
            )
            rel_obj = ProposedNpcRelationship(
                from_npc_id=npc_id,
                to_target_id=_require_str(r_rel.get("to_target_id"), f"{prefix}.relationships[{r_idx}].to_target_id", 100),
                relation_type=_require_str(r_rel.get("relation_type"), f"{prefix}.relationships[{r_idx}].relation_type", 60),
                summary=_require_str(r_rel.get("summary"), f"{prefix}.relationships[{r_idx}].summary", 300),
                grounding_type=rel_g,
                source_ids=rel_sids,
                supplement_reason=_optional_str(r_rel.get("supplement_reason"), f"{prefix}.relationships[{r_idx}].supplement_reason", 240),
                visibility=_normalize_visibility(r_rel.get("visibility"), f"{prefix}.relationships[{r_idx}].visibility", default="local_observable"),
            )
            npc_rels.append(rel_obj)
            all_validated_rels.append(rel_obj)

        vis = _normalize_visibility(r_npc.get("visibility"), f"{prefix}.visibility", default="public")
        disamb = _optional_str(
            r_npc.get("homonym_disambiguator"),
            f"{prefix}.homonym_disambiguator",
            80,
            default=auto_disamb_by_idx.get(idx, ""),
        )
        aliases = tuple(
            dict.fromkeys(
                _require_str(a, f"{prefix}.aliases", 80)
                for a in (r_npc.get("aliases") or ())
                if str(a).strip()
            )
        )

        validated_npcs.append(
            ProposedInitialNpc(
                npc_id=npc_id,
                name=name,
                role=role,
                faction=faction,
                home_location=home_location,
                location=location,
                present_at_start=present_at_start,
                goal=goal,
                motivation=motivation,
                public_demeanor=public_demeanor,
                plan_kind=plan_kind,
                action_kind=action_kind,
                current_activity=current_activity,
                availability=availability,  # type: ignore[arg-type]
                duration_minutes=duration_minutes,
                next_due_minute=next_due_minute,
                depth_tier=raw_tier,  # type: ignore[arg-type]
                grounding_type=g_type,
                source_ids=sids,
                supplement_reason=supp_reason,
                visibility=vis,
                homonym_disambiguator=disamb,
                aliases=aliases,
                from_location=from_loc,
                to_location=to_loc,
                target_npc_id=target_npc,
                meeting_location=meeting_loc,
                conversation_topic=conv_topic,
                shift_name=shift_name,
                task_id=task_id,
                task_title=task_title,
                task_stage=task_stage,
                task_progress_percent=task_prog,
                cause_event_ids=tuple(cause_eids),
                initial_known_event_ids=tuple(known_eids),
                relationships=tuple(npc_rels),
            )
        )

    # 4. 校验顶层附加人物关系
    for r_idx, r_rel in enumerate(raw_draft.get("relationships") or ()):
        if not isinstance(r_rel, Mapping):
            continue
        from_id = _require_str(r_rel.get("from_npc_id"), f"relationships[{r_idx}].from_npc_id", 100)
        to_id = _require_str(r_rel.get("to_target_id"), f"relationships[{r_idx}].to_target_id", 100)
        if from_id not in seen_npc_ids:
            raise AnalysisValidationError(
                "RELATIONSHIP_SOURCE_NPC_NOT_FOUND",
                f"relationships[{r_idx}].from_npc_id ({from_id!r}) 不在初始化的 NPC 集合中。",
            )
        rel_g = _normalize_grounding_type(r_rel.get("grounding_type"), f"relationships[{r_idx}].grounding_type")
        rel_sids = _validate_and_clean_source_ids(
            r_rel.get("source_ids"),
            field_name=f"relationships[{r_idx}].source_ids",
            grounding_type=rel_g,
            ctx=ctx,
            anchor_fallback_for_supplement=True,
        )
        all_referenced_sources.update(rel_sids)
        supp_reason = _optional_str(r_rel.get("supplement_reason"), f"relationships[{r_idx}].supplement_reason", 240)
        if rel_g == "adventure_supplement":
            if not supp_reason:
                supp_reason = "基于人物同处一地或事件交集推演的初始人际关系。"
            supplement_ledger.append(
                {
                    "entity_kind": "relationship",
                    "entity_id": f"{from_id}->{to_id}",
                    "name": f"{from_id}与{to_id}的关系",
                    "reason": supp_reason,
                    "anchor_source_ids": list(rel_sids),
                }
            )
        rel_obj = ProposedNpcRelationship(
            from_npc_id=from_id,
            to_target_id=to_id,
            relation_type=_require_str(r_rel.get("relation_type"), f"relationships[{r_idx}].relation_type", 60),
            summary=_require_str(r_rel.get("summary"), f"relationships[{r_idx}].summary", 300),
            grounding_type=rel_g,
            source_ids=rel_sids,
            supplement_reason=supp_reason,
            visibility=_normalize_visibility(r_rel.get("visibility"), f"relationships[{r_idx}].visibility", default="local_observable"),
        )
        if (rel_obj.from_npc_id, rel_obj.to_target_id, rel_obj.relation_type) not in {
            (x.from_npc_id, x.to_target_id, x.relation_type) for x in all_validated_rels
        }:
            all_validated_rels.append(rel_obj)

    # 5. 汇总设定冲突记录
    conflicts_map: dict[str, SettingConflictItem] = {
        c.conflict_id: c for c in ctx.detected_conflicts
    }
    for c_idx, r_conf in enumerate(raw_draft.get("conflicts") or ()):
        if not isinstance(r_conf, Mapping):
            continue
        p_sid = _require_str(r_conf.get("primary_source_id"), f"conflicts[{c_idx}].primary_source_id", 160)
        c_sid = _require_str(r_conf.get("conflicting_source_id"), f"conflicts[{c_idx}].conflicting_source_id", 160)
        for sid in (p_sid, c_sid):
            if sid in ctx.other_world_source_ids:
                raise CrossWorldSourceError(ctx.world_id, sid, ctx.other_world_source_ids[sid])
            if ctx.allowed_source_ids and sid not in ctx.allowed_source_ids:
                raise SourceFabricatedError(sid, f"冲突记录引用了未授权来源: {sid!r}。")
        cid = _optional_str(r_conf.get("conflict_id"), f"conflicts[{c_idx}].conflict_id", 80, default=f"conf_{c_idx + 1}")
        pol = _optional_str(
            r_conf.get("resolution_policy"),
            f"conflicts[{c_idx}].resolution_policy",
            40,
            default="prefer_confirmed_entry",
        )
        if pol not in ("prefer_confirmed_entry", "preserve_both_as_rumor", "prompt_player"):
            pol = "prefer_confirmed_entry"
        conflicts_map[cid] = SettingConflictItem(
            conflict_id=cid,
            topic=_require_str(r_conf.get("topic"), f"conflicts[{c_idx}].topic", 120),
            entity_ref=_optional_str(r_conf.get("entity_ref"), f"conflicts[{c_idx}].entity_ref", 80, default=""),
            summary=_require_str(r_conf.get("summary"), f"conflicts[{c_idx}].summary", 300),
            primary_source_id=p_sid,
            conflicting_source_id=c_sid,
            resolution_policy=pol,  # type: ignore[arg-type]
            authoritative_claim=_optional_str(r_conf.get("authoritative_claim"), f"conflicts[{c_idx}].authoritative_claim", 200),
            conflicting_claim=_optional_str(r_conf.get("conflicting_claim"), f"conflicts[{c_idx}].conflicting_claim", 200),
            player_visible_hint=bool(r_conf.get("player_visible_hint", True)),
        )

    npcs_by_id = {n.npc_id: n for n in validated_npcs}
    if isinstance(raw_draft.get("player_opening_summary"), Mapping):
        _validate_caller_player_summary_no_leaks(
            raw_draft["player_opening_summary"],
            starting_location=ctx.starting_point.location,
            npcs_by_id=npcs_by_id,
            events_by_id=events_by_id,
        )

    clean_player_summary = build_player_opening_summary(
        draft_id=draft_id,
        world_id=ctx.world_id,
        instance_id=ctx.instance_id,
        status=status_val,
        run_mode=run_mode,
        starting_point=ctx.starting_point,
        scene_overview=scene_overview,
        locations=validated_locations,
        npcs=validated_npcs,
        events=validated_events,
        relationships=all_validated_rels,
        conflicts=tuple(conflicts_map.values()),
    )

    fingerprint_payload = json.dumps(
        {
            "world_id": ctx.world_id,
            "starting_location": ctx.starting_point.location,
            "world_minute": ctx.world_minute,
            "run_mode": run_mode,
            "allowed_source_ids": sorted(ctx.allowed_source_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:16]

    return ValidatedWorldAnalysisDraft(
        draft_id=draft_id,
        world_id=ctx.world_id,
        instance_id=ctx.instance_id,
        starting_point=ctx.starting_point,
        world_minute=ctx.world_minute,
        base_world_revision=ctx.base_world_revision,
        status=status_val,  # type: ignore[arg-type]
        run_mode=run_mode,  # type: ignore[arg-type]
        request_id=request_id,
        analysis_fingerprint=fingerprint,
        summary=summary,
        scene_overview=scene_overview,
        input_ranges=ctx.input_ranges,
        allowed_source_ids=tuple(sorted(ctx.allowed_source_ids)),
        referenced_source_ids=tuple(sorted(all_referenced_sources)),
        locations=tuple(validated_locations),
        npcs=tuple(validated_npcs),
        events=tuple(validated_events),
        relationships=tuple(all_validated_rels),
        conflicts=tuple(conflicts_map.values()),
        supplement_ledger=tuple(supplement_ledger),
        identity_resolution_log=tuple(identity_resolution_log),
        player_opening_summary=clean_player_summary,
    )
