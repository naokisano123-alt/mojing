"""M3.1 / M3.5 世界事件账本、AI 初始局势生成、事件版本与因果校验模块公共入口。

本模块遵循 `docs/contracts/M3_WAVE1_API.md` 与 `docs/collaboration/M3_WAVE1_GEMINI_MARCIN.md` 契约，
提供以下核心接口供 DeepSeekHarness、Gemini Li 与 Gemini Ma 集成调用：
- `validate_proposal(payload, context)`：纯函数结构、时间、版本、来源、跨实例、因果环与账本保护校验器；
- `apply_proposal(conn, instance_id, proposal, ...)`：`SAVEPOINT` 原子应用的事件账本写入入口；
- `snapshot(conn, instance_id)` / `restore(conn, target_instance_id, snapshot, ...)`：可移植事件快照导出与分支恢复；
- `list_due_events(conn, instance_id, ...)`：到期事件稳定分页读取；
- `get_event`, `list_events`, `list_event_revisions`, `list_consequences`, `trace_causality`：权威事件、版本历史与因果追溯查询；
- `register_precondition`, `list_preconditions`：重大历史转折（战争、政权更替等）前置条件登记与查询；
- `FakeEventModelClient`, `OpenAIEventModelClient`, `generate_initial_situation`, `generate_event_advancement`：受限模型输入与测试替身；
- `run_northern_trade_causal_chain`, `get_northern_trade_integration_bundle`：北境贸易端到端因果链 Fixture。
"""

from __future__ import annotations

from .ai import (
    EventModelClient,
    FakeEventModelClient,
    OpenAIEventModelClient,
    build_advance_request_from_db,
    generate_event_advancement,
    generate_initial_situation,
    validate_advance_request,
    validate_seed_request,
)
from .errors import (
    ArbitraryPathForbiddenError,
    CausalCycleError,
    CrossInstanceReferenceError,
    EventError,
    EventNotFoundError,
    EventRevisionConflictError,
    EventValidationError,
    InstanceNotFoundError,
    PlayerLedgerProtectedError,
    SnapshotRestoreError,
    SourceFabricatedError,
    TurnpointValidationError,
)
from .fixtures import (
    NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
    NORTHERN_TRADE_CANON_SOURCES,
    NORTHERN_TRADE_INSTANCE_ID,
    NORTHERN_TRADE_NPCS,
    NORTHERN_TRADE_WORLD_ID,
    build_northern_trade_seed_proposal,
    get_northern_trade_integration_bundle,
    run_northern_trade_causal_chain,
)
from .schema import EVENTS_SCHEMA, EVENTS_SCHEMA_VERSION, ensure_events_schema
from .service import (
    apply_proposal,
    ensure_instance_exists,
    get_event,
    list_consequences,
    list_due_events,
    list_event_revisions,
    list_events,
    list_preconditions,
    register_precondition,
    restore,
    snapshot,
    trace_causality,
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
    PROPOSAL_MODES,
    PROTECTED_PLAYER_KEYS,
    SNAPSHOT_FORMAT_VERSION,
    TERMINAL_EVENT_STATUSES,
    CanonSourceItem,
    EventAdvanceRequest,
    EventKind,
    EventScope,
    EventSeedRequest,
    EventStatus,
    ImpactType,
    ProposalMode,
    ProposalValidationContext,
    ValidatedConsequence,
    ValidatedEventOp,
    ValidatedEventProposal,
)
from .validator import validate_proposal

# 兼容别名
get_due_events = list_due_events

__all__ = [
    "CONSEQUENCE_DOMAINS",
    "DEFAULT_MAX_ACTIVE_INCIDENTS",
    "DEFAULT_MAX_INITIAL_THREADS",
    "DEFAULT_MIN_INITIAL_THREADS",
    "EVENTS_SCHEMA",
    "EVENTS_SCHEMA_VERSION",
    "EVENT_KINDS",
    "EVENT_SCOPES",
    "EVENT_STATUSES",
    "FORBIDDEN_PATH_KEYS",
    "IMPACT_TYPES",
    "MAJOR_TURNPOINT_STAGES",
    "MAJOR_TURNPOINT_TYPES",
    "NORTHERN_TRADE_ALLOWED_SOURCE_IDS",
    "NORTHERN_TRADE_CANON_SOURCES",
    "NORTHERN_TRADE_INSTANCE_ID",
    "NORTHERN_TRADE_NPCS",
    "NORTHERN_TRADE_WORLD_ID",
    "PROPOSAL_MODES",
    "PROTECTED_PLAYER_KEYS",
    "SNAPSHOT_FORMAT_VERSION",
    "TERMINAL_EVENT_STATUSES",
    "ArbitraryPathForbiddenError",
    "CanonSourceItem",
    "CausalCycleError",
    "CrossInstanceReferenceError",
    "EventAdvanceRequest",
    "EventError",
    "EventKind",
    "EventModelClient",
    "EventNotFoundError",
    "EventRevisionConflictError",
    "EventScope",
    "EventSeedRequest",
    "EventStatus",
    "EventValidationError",
    "FakeEventModelClient",
    "ImpactType",
    "InstanceNotFoundError",
    "OpenAIEventModelClient",
    "PlayerLedgerProtectedError",
    "ProposalMode",
    "ProposalValidationContext",
    "SnapshotRestoreError",
    "SourceFabricatedError",
    "TurnpointValidationError",
    "ValidatedConsequence",
    "ValidatedEventOp",
    "ValidatedEventProposal",
    "apply_proposal",
    "build_advance_request_from_db",
    "build_northern_trade_seed_proposal",
    "ensure_events_schema",
    "ensure_instance_exists",
    "generate_event_advancement",
    "generate_initial_situation",
    "get_due_events",
    "get_event",
    "get_northern_trade_integration_bundle",
    "list_consequences",
    "list_due_events",
    "list_event_revisions",
    "list_events",
    "list_preconditions",
    "register_precondition",
    "restore",
    "run_northern_trade_causal_chain",
    "snapshot",
    "trace_causality",
    "validate_advance_request",
    "validate_proposal",
    "validate_seed_request",
]
