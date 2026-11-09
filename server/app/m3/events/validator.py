"""M3.1 / M3.5 世界事件提议纯函数校验器（`validate_proposal`）。

契约要求（`docs/contracts/M3_WAVE1_API.md` & `docs/collaboration/M3_WAVE1_GEMINI_MARCIN.md`）：
1. 校验器内部纯计算，不执行 SQLite I/O 或网络请求。
2. 拒绝未知字段、跨实例引用、非法时间或版本、伪造来源、任意 JSON 路径写入与直接修改玩家个人账本。
3. 验证时间先后、生命周期转换、父事件引用、自引用与因果有向无环图（DAG）环路。
4. 验证初始局势生成主线数量（3～6 条活跃主线）与临时事件活跃上限。
5. 验证战争、政权更替等重大转折的阶段性演进与已登记前置条件。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import (
    ArbitraryPathForbiddenError,
    CausalCycleError,
    CrossInstanceReferenceError,
    EventNotFoundError,
    EventRevisionConflictError,
    EventValidationError,
    PlayerLedgerProtectedError,
    SourceFabricatedError,
    TurnpointValidationError,
)
from .types import (
    CONSEQUENCE_DOMAINS,
    DEFAULT_MAX_ACTIVE_INCIDENTS,
    DEFAULT_MAX_INITIAL_THREADS,
    DEFAULT_MIN_INITIAL_THREADS,
    EVENT_KINDS,
    EVENT_SCOPES,
    EVENT_STATUSES,
    FORBIDDEN_PATH_KEYS,
    IMPACT_TYPES,
    MAJOR_TURNPOINT_STAGES,
    MAJOR_TURNPOINT_TYPES,
    MAX_CAUSES_PER_EVENT,
    MAX_CONSEQUENCES_PER_OP,
    MAX_DESCRIPTION_UNITS,
    MAX_LOCATION_UNITS,
    MAX_OPERATIONS_PER_PROPOSAL,
    MAX_PARTICIPANT_NAME_UNITS,
    MAX_PARTICIPANTS,
    MAX_SOURCES_PER_EVENT,
    MAX_STAGE_UNITS,
    MAX_TITLE_UNITS,
    PROPOSAL_MODES,
    PROTECTED_PLAYER_KEYS,
    TERMINAL_EVENT_STATUSES,
    ProposalValidationContext,
    ValidatedConsequence,
    ValidatedEventOp,
    ValidatedEventProposal,
)

_ALLOWED_ROOT_KEYS = frozenset({"operations", "mode", "summary", "instance_id"})

_SERVER_MANAGED_TIME_KEYS = frozenset({"created_minute", "updated_minute", "created_at", "updated_at"})
_SERVER_MANAGED_ID_KEYS = frozenset({"event_id", "revision", "expected_event_revision"})

_ALLOWED_CREATE_KEYS = frozenset(
    {
        "op",
        "kind",
        "title",
        "description",
        "location",
        "scope",
        "participants",
        "stage",
        "status",
        "next_update_minute",
        "cause_event_ids",
        "source_ids",
        "consequences",
        "turnpoint_type",
        "precondition_ids",
        "reason",
        "instance_id",
    }
)

_ALLOWED_UPDATE_KEYS = frozenset(
    {
        "op",
        "event_id",
        "expected_event_revision",
        "kind",
        "title",
        "description",
        "location",
        "scope",
        "participants",
        "stage",
        "status",
        "next_update_minute",
        "cause_event_ids",
        "source_ids",
        "consequences",
        "turnpoint_type",
        "precondition_ids",
        "reason",
        "instance_id",
    }
)

_ALLOWED_CONSEQUENCE_KEYS = frozenset({"domain", "target", "summary", "impact_type"})


def _u16_units(text: str, field_name: str) -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise EventValidationError(
            "INVALID_TEXT_ENCODING",
            f"字段 {field_name} 包含不完整的 UTF-16 代理对字符。",
            {"field": field_name},
        ) from exc


def _require_non_empty_str(value: Any, field_name: str, max_units: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为非空字符串。",
            {"field": field_name},
        )
    cleaned = value.strip()
    units = _u16_units(cleaned, field_name)
    if units > max_units:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 长度超过 {max_units} 个 UTF-16 单位限制。",
            {"field": field_name, "max_units": max_units, "actual_units": units},
        )
    return cleaned


def _require_strict_int(value: Any, field_name: str, *, min_value: int | None = None) -> int:
    if type(value) is not int:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为整数（拒绝布尔值或其它类型）。",
            {"field": field_name},
        )
    if min_value is not None and value < min_value:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 不能小于 {min_value}。",
            {"field": field_name, "min_value": min_value, "actual": value},
        )
    return value


def _check_forbidden_keys(mapping: Mapping[str, Any], location_hint: str) -> None:
    for key in mapping:
        if not isinstance(key, str):
            raise EventValidationError(
                "VALIDATION_ERROR",
                f"{location_hint} 包含非字符串键。",
            )
        lowered = key.lower()
        if lowered in PROTECTED_PLAYER_KEYS or lowered.startswith(("player_", "character_")):
            raise PlayerLedgerProtectedError(key)
        if (
            lowered in FORBIDDEN_PATH_KEYS
            or "." in key
            or "/" in key
            or "[" in key
            or key.startswith("$")
        ):
            raise ArbitraryPathForbiddenError(key)


def _normalize_context(context: ProposalValidationContext | Mapping[str, Any]) -> ProposalValidationContext:
    if isinstance(context, ProposalValidationContext):
        return context
    if not isinstance(context, Mapping):
        raise EventValidationError("CONTEXT_INVALID", "校验上下文必须为 ProposalValidationContext 或映射对象。")

    instance_id = _require_non_empty_str(context.get("instance_id"), "context.instance_id", 120)
    world_minute = _require_strict_int(context.get("world_minute", 0), "context.world_minute", min_value=0)
    mode = context.get("mode", "advance")
    if mode not in PROPOSAL_MODES:
        raise EventValidationError(
            "CONTEXT_INVALID",
            "context.mode 仅支持 'seed' 或 'advance'。",
            {"mode": mode},
        )

    raw_sources = context.get("allowed_source_ids", ())
    if not isinstance(raw_sources, (list, tuple, set, frozenset)) or any(
        not isinstance(s, str) or not s.strip() for s in raw_sources
    ):
        raise EventValidationError("CONTEXT_INVALID", "context.allowed_source_ids 必须为字符串集合。")

    raw_existing = context.get("existing_events", {})
    if not isinstance(raw_existing, Mapping):
        raise EventValidationError("CONTEXT_INVALID", "context.existing_events 必须为映射对象。")
    existing_events: dict[str, dict[str, Any]] = {}
    for eid, ev in raw_existing.items():
        if not isinstance(eid, str) or not isinstance(ev, Mapping):
            raise EventValidationError("CONTEXT_INVALID", "context.existing_events 条目结构无效。")
        existing_events[eid] = dict(ev)

    raw_other = context.get("other_instance_event_ids", {})
    if not isinstance(raw_other, Mapping):
        raise EventValidationError("CONTEXT_INVALID", "context.other_instance_event_ids 必须为映射对象。")
    other_instance_event_ids = {str(k): str(v) for k, v in raw_other.items()}

    raw_preconditions = context.get("registered_precondition_ids", ())
    if not isinstance(raw_preconditions, (list, tuple, set, frozenset)) or any(
        not isinstance(p, str) or not p.strip() for p in raw_preconditions
    ):
        raise EventValidationError("CONTEXT_INVALID", "context.registered_precondition_ids 必须为字符串集合。")

    min_threads = _require_strict_int(
        context.get("min_initial_threads", DEFAULT_MIN_INITIAL_THREADS),
        "context.min_initial_threads",
        min_value=1,
    )
    max_threads = _require_strict_int(
        context.get("max_initial_threads", DEFAULT_MAX_INITIAL_THREADS),
        "context.max_initial_threads",
        min_value=min_threads,
    )
    max_incidents = _require_strict_int(
        context.get("max_active_incidents", DEFAULT_MAX_ACTIVE_INCIDENTS),
        "context.max_active_incidents",
        min_value=0,
    )

    return ProposalValidationContext(
        instance_id=instance_id,
        world_minute=world_minute,
        mode=mode,
        allowed_source_ids=frozenset(s.strip() for s in raw_sources),
        existing_events=existing_events,
        other_instance_event_ids=other_instance_event_ids,
        registered_precondition_ids=frozenset(p.strip() for p in raw_preconditions),
        min_initial_threads=min_threads,
        max_initial_threads=max_threads,
        max_active_incidents=max_incidents,
    )


def _validate_participants(raw_participants: Any, *, field_name: str = "participants") -> tuple[str, ...]:
    if not isinstance(raw_participants, (list, tuple)):
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为字符串数组。",
            {"field": field_name},
        )
    if len(raw_participants) > MAX_PARTICIPANTS:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"参与方数量不能超过 {MAX_PARTICIPANTS} 个。",
            {"field": field_name, "max": MAX_PARTICIPANTS},
        )
    cleaned_list: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_participants):
        name = _require_non_empty_str(item, f"{field_name}[{idx}]", MAX_PARTICIPANT_NAME_UNITS)
        if name not in seen:
            seen.add(name)
            cleaned_list.append(name)
    return tuple(cleaned_list)


def _validate_source_ids(
    raw_sources: Any,
    allowed_source_ids: frozenset[str],
    *,
    require_non_empty: bool,
    inherited_source_ids: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    if not isinstance(raw_sources, (list, tuple)):
        raise EventValidationError(
            "VALIDATION_ERROR",
            "字段 source_ids 必须为字符串数组。",
            {"field": "source_ids"},
        )
    if len(raw_sources) > MAX_SOURCES_PER_EVENT:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"单事件来源引用不能超过 {MAX_SOURCES_PER_EVENT} 项。",
            {"field": "source_ids"},
        )
    result: list[str] = []
    seen: set[str] = set()
    permitted = allowed_source_ids | inherited_source_ids
    for idx, item in enumerate(raw_sources):
        sid = _require_non_empty_str(item, f"source_ids[{idx}]", 160)
        if sid in seen:
            raise EventValidationError(
                "DUPLICATE_SOURCE_ID",
                f"source_ids 中存在重复引用: {sid}。",
                {"source_id": sid},
            )
        if not (sid.startswith("chunk:") or sid.startswith("entry:")):
            raise SourceFabricatedError(
                sid,
                "来源标识格式非法，必须为已授权的 chunk:<id> 或 entry:<id>。",
            )
        if sid not in permitted:
            raise SourceFabricatedError(
                sid,
                f"引用了未在本次上下文中授权的伪造来源: {sid}。",
            )
        seen.add(sid)
        result.append(sid)

    if require_non_empty and not result:
        raise SourceFabricatedError(
            "",
            "初始事件线必须至少引用一项本次授权的 Canon 来源资料。",
        )
    return tuple(result)


def _validate_consequences(raw_consequences: Any) -> tuple[ValidatedConsequence, ...]:
    if raw_consequences is None:
        return ()
    if not isinstance(raw_consequences, (list, tuple)):
        raise EventValidationError(
            "VALIDATION_ERROR",
            "字段 consequences 必须为结构化对象数组。",
            {"field": "consequences"},
        )
    if len(raw_consequences) > MAX_CONSEQUENCES_PER_OP:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"单次事件操作最多包含 {MAX_CONSEQUENCES_PER_OP} 条后果。",
            {"field": "consequences"},
        )
    results: list[ValidatedConsequence] = []
    for idx, item in enumerate(raw_consequences):
        if not isinstance(item, Mapping):
            raise EventValidationError(
                "VALIDATION_ERROR",
                f"consequences[{idx}] 必须为对象。",
                {"field": f"consequences[{idx}]"},
            )
        _check_forbidden_keys(item, f"consequences[{idx}]")
        unknown = set(item.keys()) - _ALLOWED_CONSEQUENCE_KEYS
        if unknown:
            raise EventValidationError(
                "UNKNOWN_FIELD",
                f"consequences[{idx}] 包含未知字段: {sorted(unknown)}。",
                {"field": f"consequences[{idx}]", "unknown_fields": sorted(unknown)},
            )
        domain = _require_non_empty_str(item.get("domain"), f"consequences[{idx}].domain", 60)
        domain_lower = domain.lower()
        if (
            domain_lower in PROTECTED_PLAYER_KEYS
            or domain_lower.startswith(("player", "character"))
        ):
            raise PlayerLedgerProtectedError(f"consequences[{idx}].domain:{domain}")
        if domain not in CONSEQUENCE_DOMAINS:
            raise EventValidationError(
                "INVALID_CONSEQUENCE_DOMAIN",
                f"不受支持的后果影响域: {domain}。",
                {"domain": domain, "allowed_domains": sorted(CONSEQUENCE_DOMAINS)},
            )
        target = _require_non_empty_str(item.get("target"), f"consequences[{idx}].target", 120)
        target_lower = target.lower()
        if (
            target_lower in PROTECTED_PLAYER_KEYS
            or target_lower.startswith(("player", "character", "玩家账本", "玩家金钱", "玩家背包", "玩家属性"))
            or any(tok in target for tok in ("玩家金钱", "玩家背包", "玩家个人账本", "角色金钱", "角色背包"))
        ):
            raise PlayerLedgerProtectedError(f"consequences[{idx}].target:{target}")
        summary = _require_non_empty_str(item.get("summary"), f"consequences[{idx}].summary", 500)
        impact_type = item.get("impact_type", "direct")
        if impact_type not in IMPACT_TYPES:
            raise EventValidationError(
                "VALIDATION_ERROR",
                f"consequences[{idx}].impact_type 仅支持 {IMPACT_TYPES}。",
                {"field": f"consequences[{idx}].impact_type"},
            )
        results.append(
            ValidatedConsequence(
                domain=domain,
                target=target,
                summary=summary,
                impact_type=impact_type,
            )
        )
    return tuple(results)


def _validate_preconditions(
    raw_preconditions: Any,
    registered_ids: frozenset[str],
    *,
    field_name: str = "precondition_ids",
) -> tuple[str, ...]:
    if raw_preconditions is None:
        return ()
    if not isinstance(raw_preconditions, (list, tuple)):
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"字段 {field_name} 必须为字符串数组。",
            {"field": field_name},
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_preconditions):
        pid = _require_non_empty_str(item, f"{field_name}[{idx}]", 120)
        if pid not in registered_ids:
            raise TurnpointValidationError(
                "TURNPOINT_PRECONDITION_MISSING",
                f"重大转折引用的前置条件未登记或未满足: {pid}。",
                {"precondition_id": pid},
            )
        if pid not in seen:
            seen.add(pid)
            cleaned.append(pid)
    return tuple(cleaned)


def _validate_major_turnpoint(
    *,
    op: str,
    turnpoint_type: str | None,
    old_stage: str | None,
    new_stage: str,
    new_status: str,
    participants: tuple[str, ...],
    cause_event_ids: tuple[str, ...],
    precondition_ids: tuple[str, ...],
) -> None:
    if turnpoint_type is None:
        return
    if turnpoint_type not in MAJOR_TURNPOINT_TYPES:
        raise TurnpointValidationError(
            "TURNPOINT_TYPE_INVALID",
            f"未知的重大历史转折类型: {turnpoint_type}。",
            {"turnpoint_type": turnpoint_type, "allowed": sorted(MAJOR_TURNPOINT_TYPES)},
        )
    if new_stage not in MAJOR_TURNPOINT_STAGES:
        raise TurnpointValidationError(
            "TURNPOINT_STAGE_INVALID",
            f"重大历史转折阶段必须属于 {MAJOR_TURNPOINT_STAGES}，实际收到: {new_stage}。",
            {"stage": new_stage, "allowed_stages": list(MAJOR_TURNPOINT_STAGES)},
        )
    if len(participants) < 2:
        raise TurnpointValidationError(
            "TURNPOINT_PARTICIPANTS_REQUIRED",
            "重大历史转折（如战争、政权更替）必须至少包含 2 个参与方（国家、组织或关键人物）。",
            {"turnpoint_type": turnpoint_type, "participants_count": len(participants)},
        )

    new_stage_idx = MAJOR_TURNPOINT_STAGES.index(new_stage)
    if op == "create":
        if new_stage != "omen" or new_status != "active":
            raise TurnpointValidationError(
                "TURNPOINT_STAGE_INVALID",
                "重大历史转折创建时必须处于初始征兆阶段（stage='omen', status='active'），禁止毫无铺垫地瞬间爆发或完成。",
                {"stage": new_stage, "status": new_status},
            )
    else:
        assert old_stage is not None
        if old_stage not in MAJOR_TURNPOINT_STAGES:
            raise TurnpointValidationError(
                "TURNPOINT_STAGE_INVALID",
                f"原事件阶段 {old_stage} 不属于合法的重大转折阶段序列。",
                {"old_stage": old_stage},
            )
        old_stage_idx = MAJOR_TURNPOINT_STAGES.index(old_stage)
        if new_stage_idx < old_stage_idx or new_stage_idx > old_stage_idx + 1:
            raise TurnpointValidationError(
                "TURNPOINT_STAGE_INVALID",
                f"重大历史转折必须按阶段逐步推进（{old_stage} -> {new_stage} 跨阶段跳跃或倒退被拒绝）。",
                {"from_stage": old_stage, "to_stage": new_stage},
            )
        if new_stage_idx >= 1:  # escalation, turning_point, aftermath
            if not precondition_ids:
                raise TurnpointValidationError(
                    "TURNPOINT_PRECONDITION_MISSING",
                    f"重大历史转折进入 {new_stage} 阶段必须提供已登记的前置条件（precondition_ids）。",
                    {"stage": new_stage, "turnpoint_type": turnpoint_type},
                )
            if not cause_event_ids:
                raise TurnpointValidationError(
                    "TURNPOINT_CAUSE_REQUIRED",
                    f"重大历史转折进入 {new_stage} 阶段必须具备可追溯的起因事件（cause_event_ids）。",
                    {"stage": new_stage, "turnpoint_type": turnpoint_type},
                )
        if new_status == "completed" and new_stage not in ("turning_point", "aftermath"):
            raise TurnpointValidationError(
                "TURNPOINT_STAGE_INVALID",
                "重大历史转折尚未达到转折爆发或余波阶段，不能直接标记为 completed。",
                {"stage": new_stage, "status": new_status},
            )


def _detect_causal_cycles(cause_graph: dict[str, tuple[str, ...]]) -> None:
    """使用三色 DFS 检测事件因果图是否存在环路。

    其中 `cause_graph[event_id]` 保存该事件引用的 `cause_event_ids`。
    若存在路径使得 E -> ... -> E，则抛出 `CausalCycleError`。
    """
    state: dict[str, int] = {}  # 0=未访问, 1=访问中, 2=已完成
    stack: list[str] = []

    def dfs(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for parent in cause_graph.get(node, ()):
            if parent == node:
                raise CausalCycleError(
                    "CAUSAL_SELF_REFERENCE",
                    f"事件 {node} 存在因果自引用。",
                    [node, node],
                )
            parent_state = state.get(parent, 0)
            if parent_state == 1:
                idx = stack.index(parent)
                cycle = stack[idx:] + [parent]
                raise CausalCycleError(
                    "CAUSAL_CYCLE_DETECTED",
                    f"事件因果链存在环路: {' -> '.join(cycle)}。",
                    cycle,
                )
            if parent_state == 0 and parent in cause_graph:
                dfs(parent)
        stack.pop()
        state[node] = 2

    for event_id in list(cause_graph.keys()):
        if state.get(event_id, 0) == 0:
            dfs(event_id)


def validate_proposal(
    payload: object,
    context: ProposalValidationContext | Mapping[str, Any],
) -> ValidatedEventProposal:
    """纯函数校验模型或调用方提交的事件变更提议。"""
    ctx = _normalize_context(context)

    if not isinstance(payload, Mapping):
        raise EventValidationError("PROPOSAL_INVALID", "事件提议顶层必须为 JSON 对象。")

    _check_forbidden_keys(payload, "proposal")
    unknown_root = set(payload.keys()) - _ALLOWED_ROOT_KEYS
    if unknown_root:
        raise EventValidationError(
            "UNKNOWN_FIELD",
            f"事件提议顶层包含未知字段: {sorted(unknown_root)}。",
            {"unknown_fields": sorted(unknown_root)},
        )

    if "instance_id" in payload and payload["instance_id"] is not None:
        prop_instance_id = _require_non_empty_str(payload["instance_id"], "instance_id", 120)
        if prop_instance_id != ctx.instance_id:
            raise CrossInstanceReferenceError(ctx.instance_id, prop_instance_id, prop_instance_id)

    mode = payload.get("mode", ctx.mode)
    if mode not in PROPOSAL_MODES:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"提议模式 mode 仅支持 {PROPOSAL_MODES}。",
            {"mode": mode},
        )

    summary = ""
    if "summary" in payload and payload["summary"] is not None:
        summary = _require_non_empty_str(payload["summary"], "summary", 500)

    raw_ops = payload.get("operations")
    if not isinstance(raw_ops, (list, tuple)) or not raw_ops:
        raise EventValidationError(
            "VALIDATION_ERROR",
            "事件提议必须包含非空的 operations 数组。",
            {"field": "operations"},
        )
    if len(raw_ops) > MAX_OPERATIONS_PER_PROPOSAL:
        raise EventValidationError(
            "VALIDATION_ERROR",
            f"单次提议最多包含 {MAX_OPERATIONS_PER_PROPOSAL} 项操作。",
            {"field": "operations", "max": MAX_OPERATIONS_PER_PROPOSAL},
        )

    # 工作态事件视图（用于同批次演进与因果环检查）
    working_events: dict[str, dict[str, Any]] = {
        eid: dict(ev) for eid, ev in ctx.existing_events.items()
    }
    cause_graph: dict[str, tuple[str, ...]] = {
        eid: tuple(ev.get("cause_event_ids", ())) for eid, ev in working_events.items()
    }

    validated_ops: list[ValidatedEventOp] = []
    seen_update_ids: set[str] = set()

    for idx, raw_op in enumerate(raw_ops):
        op_label = f"operations[{idx}]"
        if not isinstance(raw_op, Mapping):
            raise EventValidationError(
                "VALIDATION_ERROR",
                f"{op_label} 必须为对象。",
                {"field": op_label},
            )
        _check_forbidden_keys(raw_op, op_label)

        if "instance_id" in raw_op and raw_op["instance_id"] is not None:
            op_inst = _require_non_empty_str(raw_op["instance_id"], f"{op_label}.instance_id", 120)
            if op_inst != ctx.instance_id:
                raise CrossInstanceReferenceError(ctx.instance_id, op_inst, op_inst)

        # 检查是否试图覆写服务端管理的时间戳字段（禁止回填过去或预设未来创建时间）
        time_override = set(raw_op.keys()) & _SERVER_MANAGED_TIME_KEYS
        if time_override:
            raise EventValidationError(
                "TIMESTAMP_OVERRIDE_FORBIDDEN",
                f"{op_label} 禁止指定服务端管理的时间戳字段: {sorted(time_override)}。",
                {"field": op_label, "forbidden_fields": sorted(time_override)},
            )

        if "revision" in raw_op:
            raise EventValidationError(
                "SERVER_FIELD_FORBIDDEN",
                f"{op_label} 禁止直接指定 revision，版本由服务端统一递增分配。",
                {"field": f"{op_label}.revision"},
            )

        op_type = raw_op.get("op")
        if op_type not in ("create", "update"):
            raise EventValidationError(
                "VALIDATION_ERROR",
                f"{op_label}.op 仅支持 'create' 或 'update'。",
                {"field": f"{op_label}.op"},
            )

        if op_type == "create":
            if set(raw_op.keys()) & _SERVER_MANAGED_ID_KEYS:
                raise EventValidationError(
                    "SERVER_ID_FORBIDDEN",
                    f"{op_label} 在 create 操作中禁止指定 event_id 或 expected_event_revision。",
                    {"field": op_label},
                )
            unknown_keys = set(raw_op.keys()) - _ALLOWED_CREATE_KEYS
            if unknown_keys:
                raise EventValidationError(
                    "UNKNOWN_FIELD",
                    f"{op_label} 包含未知字段: {sorted(unknown_keys)}。",
                    {"field": op_label, "unknown_fields": sorted(unknown_keys)},
                )

            kind = raw_op.get("kind")
            if kind not in EVENT_KINDS:
                raise EventValidationError(
                    "VALIDATION_ERROR",
                    f"{op_label}.kind 必须为 'thread' 或 'incident'。",
                    {"field": f"{op_label}.kind"},
                )
            if mode == "seed" and kind != "thread":
                raise EventValidationError(
                    "SEED_KIND_INVALID",
                    "初始化种子阶段（mode='seed'）仅允许创建主要事件线（kind='thread'）。",
                    {"field": f"{op_label}.kind"},
                )

            title = _require_non_empty_str(raw_op.get("title"), f"{op_label}.title", MAX_TITLE_UNITS)
            description = _require_non_empty_str(
                raw_op.get("description"), f"{op_label}.description", MAX_DESCRIPTION_UNITS
            )
            location = _require_non_empty_str(
                raw_op.get("location"), f"{op_label}.location", MAX_LOCATION_UNITS
            )
            scope = raw_op.get("scope")
            if scope not in EVENT_SCOPES:
                raise EventValidationError(
                    "VALIDATION_ERROR",
                    f"{op_label}.scope 必须属于 {EVENT_SCOPES}。",
                    {"field": f"{op_label}.scope"},
                )
            participants = _validate_participants(
                raw_op.get("participants", []), field_name=f"{op_label}.participants"
            )
            stage = _require_non_empty_str(
                raw_op.get("stage", "initial"), f"{op_label}.stage", MAX_STAGE_UNITS
            )
            status = raw_op.get("status", "active")
            if status not in EVENT_STATUSES:
                raise EventValidationError(
                    "VALIDATION_ERROR",
                    f"{op_label}.status 必须属于 {EVENT_STATUSES}。",
                    {"field": f"{op_label}.status"},
                )
            if status != "active":
                raise EventValidationError(
                    "INVALID_LIFECYCLE_TRANSITION",
                    f"{op_label} 新建事件初始状态必须为 'active'，不能直接创建为 '{status}'。",
                    {"field": f"{op_label}.status", "status": status},
                )

            next_update_minute = _require_strict_int(
                raw_op.get("next_update_minute"),
                f"{op_label}.next_update_minute",
            )
            if next_update_minute <= ctx.world_minute:
                raise EventValidationError(
                    "INVALID_NEXT_UPDATE_MINUTE",
                    f"{op_label}.next_update_minute ({next_update_minute}) 必须严格大于当前世界分钟 ({ctx.world_minute})。",
                    {
                        "field": f"{op_label}.next_update_minute",
                        "next_update_minute": next_update_minute,
                        "world_minute": ctx.world_minute,
                    },
                )

            raw_causes = raw_op.get("cause_event_ids", [])
            if not isinstance(raw_causes, (list, tuple)) or len(raw_causes) > MAX_CAUSES_PER_EVENT:
                raise EventValidationError(
                    "VALIDATION_ERROR",
                    f"{op_label}.cause_event_ids 必须为不超过 {MAX_CAUSES_PER_EVENT} 项的数组。",
                    {"field": f"{op_label}.cause_event_ids"},
                )
            cause_ids: list[str] = []
            seen_causes: set[str] = set()
            for c_idx, raw_cid in enumerate(raw_causes):
                cid = _require_non_empty_str(
                    raw_cid, f"{op_label}.cause_event_ids[{c_idx}]", 120
                )
                if cid in seen_causes:
                    raise EventValidationError(
                        "DUPLICATE_CAUSE_EVENT_ID",
                        f"{op_label}.cause_event_ids 存在重复父事件引用: {cid}。",
                        {"cause_event_id": cid},
                    )
                if cid in ctx.other_instance_event_ids:
                    raise CrossInstanceReferenceError(
                        ctx.instance_id, cid, ctx.other_instance_event_ids[cid]
                    )
                parent_ev = working_events.get(cid)
                if parent_ev is None:
                    raise EventValidationError(
                        "CAUSE_EVENT_NOT_FOUND",
                        f"{op_label} 引用的父事件 {cid} 在当前实例中不存在。",
                        {"cause_event_id": cid},
                    )
                if (
                    parent_ev.get("created_minute", 0) > ctx.world_minute
                    or parent_ev.get("updated_minute", 0) > ctx.world_minute
                ):
                    raise EventValidationError(
                        "CAUSE_TIME_INVALID",
                        f"{op_label} 引用的父事件 {cid} 时间晚于当前世界分钟 ({ctx.world_minute})。",
                        {"cause_event_id": cid, "world_minute": ctx.world_minute},
                    )
                seen_causes.add(cid)
                cause_ids.append(cid)

            source_ids = _validate_source_ids(
                raw_op.get("source_ids", []),
                ctx.allowed_source_ids,
                require_non_empty=(mode == "seed"),
            )
            if mode == "advance" and not cause_ids and not source_ids:
                raise EventValidationError(
                    "UNGROUNDED_EVENT_CREATION",
                    f"{op_label} 在推进阶段创建新事件必须至少关联一个父事件（cause_event_ids）或授权来源（source_ids）。",
                    {"field": op_label},
                )

            consequences = _validate_consequences(raw_op.get("consequences"))
            turnpoint_type = (
                _require_non_empty_str(raw_op["turnpoint_type"], f"{op_label}.turnpoint_type", 60)
                if raw_op.get("turnpoint_type") is not None
                else None
            )
            precondition_ids = _validate_preconditions(
                raw_op.get("precondition_ids"),
                ctx.registered_precondition_ids,
                field_name=f"{op_label}.precondition_ids",
            )

            _validate_major_turnpoint(
                op="create",
                turnpoint_type=turnpoint_type,
                old_stage=None,
                new_stage=stage,
                new_status=status,
                participants=participants,
                cause_event_ids=tuple(cause_ids),
                precondition_ids=precondition_ids,
            )

            reason = ""
            if raw_op.get("reason") is not None:
                reason = _require_non_empty_str(raw_op["reason"], f"{op_label}.reason", 300)

            temp_id = f"__pending_create_{idx}__"
            working_events[temp_id] = {
                "event_id": temp_id,
                "instance_id": ctx.instance_id,
                "kind": kind,
                "title": title,
                "description": description,
                "location": location,
                "scope": scope,
                "participants": list(participants),
                "stage": stage,
                "status": status,
                "created_minute": ctx.world_minute,
                "updated_minute": ctx.world_minute,
                "next_update_minute": next_update_minute,
                "cause_event_ids": list(cause_ids),
                "source_ids": list(source_ids),
                "turnpoint_type": turnpoint_type,
                "precondition_ids": list(precondition_ids),
                "revision": 1,
            }
            cause_graph[temp_id] = tuple(cause_ids)

            validated_ops.append(
                ValidatedEventOp(
                    op="create",
                    kind=kind,
                    title=title,
                    description=description,
                    location=location,
                    scope=scope,
                    participants=participants,
                    stage=stage,
                    status=status,
                    next_update_minute=next_update_minute,
                    next_update_minute_provided=True,
                    cause_event_ids=tuple(cause_ids),
                    source_ids=source_ids,
                    consequences=consequences,
                    turnpoint_type=turnpoint_type,
                    precondition_ids=precondition_ids,
                    reason=reason,
                )
            )

        else:  # op_type == "update"
            if mode == "seed":
                raise EventValidationError(
                    "SEED_OP_INVALID",
                    "初始化种子阶段（mode='seed'）仅允许 'create' 操作，禁止 'update'。",
                    {"field": f"{op_label}.op"},
                )
            unknown_keys = set(raw_op.keys()) - _ALLOWED_UPDATE_KEYS
            if unknown_keys:
                raise EventValidationError(
                    "UNKNOWN_FIELD",
                    f"{op_label} 包含未知字段: {sorted(unknown_keys)}。",
                    {"field": op_label, "unknown_fields": sorted(unknown_keys)},
                )

            event_id = _require_non_empty_str(raw_op.get("event_id"), f"{op_label}.event_id", 120)
            if event_id in ctx.other_instance_event_ids:
                raise CrossInstanceReferenceError(
                    ctx.instance_id, event_id, ctx.other_instance_event_ids[event_id]
                )
            existing = working_events.get(event_id)
            if existing is None or event_id.startswith("__pending_create_"):
                raise EventNotFoundError(ctx.instance_id, event_id)

            if event_id in seen_update_ids:
                raise EventValidationError(
                    "DUPLICATE_EVENT_UPDATE",
                    f"同一份提议中不能重复更新同一个事件: {event_id}。",
                    {"event_id": event_id},
                )
            seen_update_ids.add(event_id)

            expected_rev = _require_strict_int(
                raw_op.get("expected_event_revision"),
                f"{op_label}.expected_event_revision",
                min_value=1,
            )
            current_rev = int(existing["revision"])
            if expected_rev != current_rev:
                raise EventRevisionConflictError(
                    ctx.instance_id, event_id, expected_rev, current_rev
                )

            old_status = existing["status"]
            if old_status in TERMINAL_EVENT_STATUSES:
                raise EventValidationError(
                    "EVENT_TERMINAL_STATE",
                    f"事件 {event_id} 已处于终态 ({old_status})，不允许再次推进或修改。",
                    {"event_id": event_id, "current_status": old_status},
                )

            if ctx.world_minute < int(existing["updated_minute"]) or ctx.world_minute < int(
                existing["created_minute"]
            ):
                raise EventValidationError(
                    "TIME_REGRESSION",
                    f"提议的世界时间 ({ctx.world_minute}) 早于事件 {event_id} 的已确认历史时刻 ({existing['updated_minute']})。",
                    {
                        "event_id": event_id,
                        "world_minute": ctx.world_minute,
                        "event_updated_minute": existing["updated_minute"],
                    },
                )

            if "kind" in raw_op and raw_op["kind"] is not None:
                if raw_op["kind"] != existing["kind"]:
                    raise EventValidationError(
                        "KIND_IMMUTABLE",
                        f"事件 {event_id} 的类型 ({existing['kind']}) 创建后不可更改。",
                        {"event_id": event_id, "kind": raw_op["kind"]},
                    )

            title = (
                _require_non_empty_str(raw_op["title"], f"{op_label}.title", MAX_TITLE_UNITS)
                if "title" in raw_op and raw_op["title"] is not None
                else existing["title"]
            )
            description = (
                _require_non_empty_str(
                    raw_op["description"], f"{op_label}.description", MAX_DESCRIPTION_UNITS
                )
                if "description" in raw_op and raw_op["description"] is not None
                else existing["description"]
            )
            location = (
                _require_non_empty_str(raw_op["location"], f"{op_label}.location", MAX_LOCATION_UNITS)
                if "location" in raw_op and raw_op["location"] is not None
                else existing["location"]
            )
            if "scope" in raw_op and raw_op["scope"] is not None:
                scope = raw_op["scope"]
                if scope not in EVENT_SCOPES:
                    raise EventValidationError(
                        "VALIDATION_ERROR",
                        f"{op_label}.scope 必须属于 {EVENT_SCOPES}。",
                        {"field": f"{op_label}.scope"},
                    )
            else:
                scope = existing["scope"]

            participants = (
                _validate_participants(
                    raw_op["participants"], field_name=f"{op_label}.participants"
                )
                if "participants" in raw_op and raw_op["participants"] is not None
                else tuple(existing.get("participants", ()))
            )

            stage = (
                _require_non_empty_str(raw_op["stage"], f"{op_label}.stage", MAX_STAGE_UNITS)
                if "stage" in raw_op and raw_op["stage"] is not None
                else existing["stage"]
            )

            if "status" in raw_op and raw_op["status"] is not None:
                new_status = raw_op["status"]
                if new_status not in EVENT_STATUSES:
                    raise EventValidationError(
                        "VALIDATION_ERROR",
                        f"{op_label}.status 必须属于 {EVENT_STATUSES}。",
                        {"field": f"{op_label}.status"},
                    )
            else:
                new_status = old_status

            next_update_provided = "next_update_minute" in raw_op
            raw_next_minute = raw_op.get("next_update_minute")
            if new_status in TERMINAL_EVENT_STATUSES:
                if next_update_provided and raw_next_minute is not None:
                    raise EventValidationError(
                        "INVALID_NEXT_UPDATE_MINUTE",
                        f"{op_label} 进入终态 ({new_status}) 时 next_update_minute 必须为空（None）。",
                        {"field": f"{op_label}.next_update_minute"},
                    )
                effective_next_minute = None
            else:
                if next_update_provided:
                    effective_next_minute = _require_strict_int(
                        raw_next_minute, f"{op_label}.next_update_minute"
                    )
                else:
                    effective_next_minute = existing.get("next_update_minute")
                if effective_next_minute is None or effective_next_minute <= ctx.world_minute:
                    raise EventValidationError(
                        "INVALID_NEXT_UPDATE_MINUTE",
                        f"{op_label} 处于非终态 ({new_status}) 时，next_update_minute 必须严格大于当前世界分钟 ({ctx.world_minute})。",
                        {
                            "field": f"{op_label}.next_update_minute",
                            "next_update_minute": effective_next_minute,
                            "world_minute": ctx.world_minute,
                        },
                    )

            existing_causes = tuple(existing.get("cause_event_ids", ()))
            if "cause_event_ids" in raw_op and raw_op["cause_event_ids"] is not None:
                raw_causes = raw_op["cause_event_ids"]
                if not isinstance(raw_causes, (list, tuple)) or len(raw_causes) > MAX_CAUSES_PER_EVENT:
                    raise EventValidationError(
                        "VALIDATION_ERROR",
                        f"{op_label}.cause_event_ids 必须为不超过 {MAX_CAUSES_PER_EVENT} 项的数组。",
                        {"field": f"{op_label}.cause_event_ids"},
                    )
                cause_ids = []
                seen_causes = set()
                for c_idx, raw_cid in enumerate(raw_causes):
                    cid = _require_non_empty_str(
                        raw_cid, f"{op_label}.cause_event_ids[{c_idx}]", 120
                    )
                    if cid == event_id:
                        raise CausalCycleError(
                            "CAUSAL_SELF_REFERENCE",
                            f"事件 {event_id} 不能将自身列为因果父事件。",
                            [event_id, event_id],
                        )
                    if cid in seen_causes:
                        raise EventValidationError(
                            "DUPLICATE_CAUSE_EVENT_ID",
                            f"{op_label}.cause_event_ids 存在重复父事件引用: {cid}。",
                            {"cause_event_id": cid},
                        )
                    if cid in ctx.other_instance_event_ids:
                        raise CrossInstanceReferenceError(
                            ctx.instance_id, cid, ctx.other_instance_event_ids[cid]
                        )
                    parent_ev = working_events.get(cid)
                    if parent_ev is None or cid.startswith("__pending_create_"):
                        raise EventValidationError(
                            "CAUSE_EVENT_NOT_FOUND",
                            f"{op_label} 引用的父事件 {cid} 在当前实例中不存在。",
                            {"cause_event_id": cid},
                        )
                    if (
                        parent_ev.get("created_minute", 0) > ctx.world_minute
                        or parent_ev.get("updated_minute", 0) > ctx.world_minute
                    ):
                        raise EventValidationError(
                            "CAUSE_TIME_INVALID",
                            f"{op_label} 引用的父事件 {cid} 时间晚于当前世界分钟 ({ctx.world_minute})。",
                            {"cause_event_id": cid, "world_minute": ctx.world_minute},
                        )
                    seen_causes.add(cid)
                    cause_ids.append(cid)
                # 既定历史因果不能被静默抹除
                missing_old_causes = set(existing_causes) - set(cause_ids)
                if missing_old_causes:
                    raise EventValidationError(
                        "CAUSE_HISTORY_IMMUTABLE",
                        f"更新事件 {event_id} 时不能抹除已确认的历史因果引用: {sorted(missing_old_causes)}。",
                        {"event_id": event_id, "missing_cause_ids": sorted(missing_old_causes)},
                    )
                effective_causes = tuple(cause_ids)
            else:
                effective_causes = existing_causes

            existing_sources = frozenset(existing.get("source_ids", ()))
            if "source_ids" in raw_op and raw_op["source_ids"] is not None:
                effective_sources = _validate_source_ids(
                    raw_op["source_ids"],
                    ctx.allowed_source_ids,
                    require_non_empty=False,
                    inherited_source_ids=existing_sources,
                )
            else:
                effective_sources = tuple(existing.get("source_ids", ()))

            consequences = _validate_consequences(raw_op.get("consequences"))

            existing_turnpoint = existing.get("turnpoint_type")
            if "turnpoint_type" in raw_op and raw_op["turnpoint_type"] is not None:
                tp = _require_non_empty_str(
                    raw_op["turnpoint_type"], f"{op_label}.turnpoint_type", 60
                )
                if existing_turnpoint is not None and tp != existing_turnpoint:
                    raise TurnpointValidationError(
                        "TURNPOINT_TYPE_IMMUTABLE",
                        f"事件 {event_id} 的重大转折类型 ({existing_turnpoint}) 不可更改为 {tp}。",
                        {"event_id": event_id},
                    )
                turnpoint_type = tp
            else:
                turnpoint_type = existing_turnpoint

            if "precondition_ids" in raw_op and raw_op["precondition_ids"] is not None:
                effective_preconditions = _validate_preconditions(
                    raw_op["precondition_ids"],
                    ctx.registered_precondition_ids | frozenset(existing.get("precondition_ids", ())),
                    field_name=f"{op_label}.precondition_ids",
                )
            else:
                effective_preconditions = tuple(existing.get("precondition_ids", ()))

            _validate_major_turnpoint(
                op="update",
                turnpoint_type=turnpoint_type,
                old_stage=existing["stage"],
                new_stage=stage,
                new_status=new_status,
                participants=participants,
                cause_event_ids=effective_causes,
                precondition_ids=effective_preconditions,
            )

            reason = ""
            if raw_op.get("reason") is not None:
                reason = _require_non_empty_str(raw_op["reason"], f"{op_label}.reason", 300)

            working_events[event_id] = {
                **existing,
                "title": title,
                "description": description,
                "location": location,
                "scope": scope,
                "participants": list(participants),
                "stage": stage,
                "status": new_status,
                "updated_minute": ctx.world_minute,
                "next_update_minute": effective_next_minute,
                "cause_event_ids": list(effective_causes),
                "source_ids": list(effective_sources),
                "turnpoint_type": turnpoint_type,
                "precondition_ids": list(effective_preconditions),
                "revision": current_rev + 1,
            }
            cause_graph[event_id] = effective_causes

            validated_ops.append(
                ValidatedEventOp(
                    op="update",
                    event_id=event_id,
                    expected_event_revision=expected_rev,
                    kind=existing["kind"],
                    title=title,
                    description=description,
                    location=location,
                    scope=scope,
                    participants=participants,
                    stage=stage,
                    status=new_status,
                    next_update_minute=effective_next_minute,
                    next_update_minute_provided=True,
                    cause_event_ids=effective_causes,
                    source_ids=effective_sources,
                    consequences=consequences,
                    turnpoint_type=turnpoint_type,
                    precondition_ids=effective_preconditions,
                    reason=reason,
                )
            )

    # 全图因果环路检查（覆盖双节点环 A<->B 与多跳环 A->B->C->A）
    _detect_causal_cycles(cause_graph)

    # 校验初始种子主线数量范围（默认 3～6 条活跃主线）
    if mode == "seed":
        created_threads = [
            op for op in validated_ops if op.op == "create" and op.kind == "thread" and op.status == "active"
        ]
        if not (ctx.min_initial_threads <= len(created_threads) <= ctx.max_initial_threads):
            raise EventValidationError(
                "SEED_THREAD_COUNT_INVALID",
                f"初始局势必须生成 {ctx.min_initial_threads}～{ctx.max_initial_threads} 条活跃主要事件线，实际提交 {len(created_threads)} 条。",
                {
                    "min_threads": ctx.min_initial_threads,
                    "max_threads": ctx.max_initial_threads,
                    "actual_threads": len(created_threads),
                },
            )

    # 校验未完成临时事件数量上限
    active_incidents = sum(
        1
        for ev in working_events.values()
        if ev.get("kind") == "incident" and ev.get("status") not in TERMINAL_EVENT_STATUSES
    )
    if active_incidents > ctx.max_active_incidents:
        raise EventValidationError(
            "INCIDENT_LIMIT_EXCEEDED",
            f"当前实例未完成的临时事件数量 ({active_incidents}) 超出上限 ({ctx.max_active_incidents})。",
            {
                "active_incidents": active_incidents,
                "max_active_incidents": ctx.max_active_incidents,
            },
        )

    return ValidatedEventProposal(
        instance_id=ctx.instance_id,
        mode=mode,
        world_minute=ctx.world_minute,
        operations=tuple(validated_ops),
        summary=summary,
    )
