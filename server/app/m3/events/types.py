"""M3.1 / M3.5 世界事件账本、初始局势与因果校验数据类型与常量定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SNAPSHOT_FORMAT_VERSION = "m3.events.v1"

EventKind = Literal["thread", "incident"]
EVENT_KINDS: tuple[str, ...] = ("thread", "incident")

EventScope = Literal["local", "regional", "national", "international"]
EVENT_SCOPES: tuple[str, ...] = ("local", "regional", "national", "international")

EventStatus = Literal["active", "stalled", "completed", "failed"]
EVENT_STATUSES: tuple[str, ...] = ("active", "stalled", "completed", "failed")
TERMINAL_EVENT_STATUSES: frozenset[str] = frozenset({"completed", "failed"})

ProposalMode = Literal["seed", "advance"]
PROPOSAL_MODES: tuple[str, ...] = ("seed", "advance")

ImpactType = Literal["direct", "indirect", "independent"]
IMPACT_TYPES: tuple[str, ...] = ("direct", "indirect", "independent")

# 受限的世界动态影响域（严禁包含任何玩家个人账本域）
CONSEQUENCE_DOMAINS: frozenset[str] = frozenset(
    {
        "trade_route",
        "regional_supply",
        "market_price",
        "npc_inventory",
        "shop_status",
        "faction_relation",
        "npc_schedule",
        "politics",
        "security",
    }
)

# 玩家个人状态保护词表：一旦出现在提议字段或后果目标中立即以 PLAYER_LEDGER_PROTECTED 拒绝
PROTECTED_PLAYER_KEYS: frozenset[str] = frozenset(
    {
        "player",
        "player_id",
        "player_state",
        "player_ledger",
        "player_changes",
        "character",
        "character_json",
        "character_state",
        "coins",
        "coins_delta",
        "money",
        "gold",
        "inventory",
        "inventory_changes",
        "items",
        "equipment",
        "stamina",
        "stamina_delta",
        "spirit",
        "spirit_delta",
        "hp",
        "attributes",
        "personal_ledger",
    }
)

# 任意 JSON 路径写入/补丁词表：一旦出现立即以 ARBITRARY_PATH_FORBIDDEN 拒绝
FORBIDDEN_PATH_KEYS: frozenset[str] = frozenset(
    {
        "json_path",
        "target_path",
        "path",
        "pointer",
        "json_pointer",
        "patch",
        "json_patch",
        "$set",
        "$inc",
        "$unset",
        "$push",
        "raw_sql",
        "mutation_path",
    }
)

# 重大历史转折类型与必须遵循的有序演进阶段
MAJOR_TURNPOINT_TYPES: frozenset[str] = frozenset(
    {
        "war",
        "regime_change",
        "secession",
        "dynasty_fall",
        "faction_collapse",
    }
)

MAJOR_TURNPOINT_STAGES: tuple[str, ...] = (
    "omen",           # 征兆/酝酿期（初始阶段）
    "escalation",     # 升级/对峙期（需满足前置条件与因果链）
    "turning_point",  # 爆发/历史转折期（需已历经升级期并具备完整前置条件）
    "aftermath",      # 余波/新秩序确立期
)

DEFAULT_MIN_INITIAL_THREADS = 3
DEFAULT_MAX_INITIAL_THREADS = 6
DEFAULT_MAX_ACTIVE_INCIDENTS = 10

MAX_TITLE_UNITS = 120
MAX_DESCRIPTION_UNITS = 2000
MAX_LOCATION_UNITS = 120
MAX_STAGE_UNITS = 80
MAX_PARTICIPANTS = 30
MAX_PARTICIPANT_NAME_UNITS = 80
MAX_CAUSES_PER_EVENT = 20
MAX_SOURCES_PER_EVENT = 20
MAX_CONSEQUENCES_PER_OP = 10
MAX_OPERATIONS_PER_PROPOSAL = 16


@dataclass(frozen=True)
class CanonSourceItem:
    """提供给事件模型的受限 Canon 设定来源项。"""

    source_id: str  # 格式：chunk:<id> 或 entry:<id>
    kind: Literal["chunk", "entry"]
    text: str
    title: str = ""


@dataclass(frozen=True)
class ValidatedConsequence:
    """经过校验的结构化事件后果项。"""

    domain: str
    target: str
    summary: str
    impact_type: ImpactType = "direct"

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "target": self.target,
            "summary": self.summary,
            "impact_type": self.impact_type,
        }


@dataclass(frozen=True)
class ValidatedEventOp:
    """经过纯函数校验的单个事件操作（create 或 update）。"""

    op: Literal["create", "update"]
    event_id: str | None = None
    expected_event_revision: int | None = None
    kind: EventKind | None = None
    title: str | None = None
    description: str | None = None
    location: str | None = None
    scope: EventScope | None = None
    participants: tuple[str, ...] | None = None
    stage: str | None = None
    status: EventStatus | None = None
    next_update_minute: int | None = None
    next_update_minute_provided: bool = False
    cause_event_ids: tuple[str, ...] | None = None
    source_ids: tuple[str, ...] | None = None
    consequences: tuple[ValidatedConsequence, ...] = ()
    turnpoint_type: str | None = None
    precondition_ids: tuple[str, ...] | None = None
    reason: str = ""


@dataclass(frozen=True)
class ValidatedEventProposal:
    """经过 `validate_proposal` 完整校验的事件提议对象。"""

    instance_id: str
    mode: ProposalMode
    world_minute: int
    operations: tuple[ValidatedEventOp, ...]
    summary: str = ""


@dataclass(frozen=True)
class ProposalValidationContext:
    """纯函数校验器上下文。"""

    instance_id: str
    world_minute: int
    mode: ProposalMode = "advance"
    allowed_source_ids: frozenset[str] = field(default_factory=frozenset)
    existing_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    other_instance_event_ids: dict[str, str] = field(default_factory=dict)
    registered_precondition_ids: frozenset[str] = field(default_factory=frozenset)
    min_initial_threads: int = DEFAULT_MIN_INITIAL_THREADS
    max_initial_threads: int = DEFAULT_MAX_INITIAL_THREADS
    max_active_incidents: int = DEFAULT_MAX_ACTIVE_INCIDENTS


@dataclass(frozen=True)
class EventSeedRequest:
    """AI 初始局势生成受限输入。"""

    instance_id: str
    world_minute: int
    sources: tuple[CanonSourceItem, ...]
    world_intro: str = ""
    min_threads: int = DEFAULT_MIN_INITIAL_THREADS
    max_threads: int = DEFAULT_MAX_INITIAL_THREADS


@dataclass(frozen=True)
class EventAdvanceRequest:
    """AI 到期事件推进受限输入（仅含需处理的到期事件及相关事实）。"""

    instance_id: str
    world_minute: int
    due_events: tuple[dict[str, Any], ...]
    related_facts: tuple[dict[str, Any], ...] = ()
    sources: tuple[CanonSourceItem, ...] = ()
    registered_precondition_ids: tuple[str, ...] = ()
    active_incident_count: int = 0
    max_active_incidents: int = DEFAULT_MAX_ACTIVE_INCIDENTS
