"""M3.1 / M3.5 世界事件账本、初始局势与因果校验数据类型与常量定义（M3 Wave 2 扩展）。"""

from __future__ import annotations

from collections.abc import Mapping
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

# M3 Wave 2 Canon 选取上限与字符预算常量
DEFAULT_SEED_MAX_ENTRIES = 6
DEFAULT_SEED_MAX_CHUNKS = 6
DEFAULT_SEED_MAX_ENTRY_CHARS = 600
DEFAULT_SEED_MAX_CHUNK_CHARS = 800
DEFAULT_SEED_CHAR_BUDGET = 4800

DEFAULT_ADVANCE_MAX_DUE_EVENTS = 6
DEFAULT_ADVANCE_MAX_CAUSAL_NEIGHBORS = 8
DEFAULT_ADVANCE_MAX_ENTRIES = 4
DEFAULT_ADVANCE_MAX_CHUNKS = 4
DEFAULT_ADVANCE_MAX_ENTRY_CHARS = 500
DEFAULT_ADVANCE_MAX_CHUNK_CHARS = 600
DEFAULT_ADVANCE_CHAR_BUDGET = 3200

# 设定条目类型稳定优先级映射（按世界规则 -> 地理/地点 -> 势力/政治 -> 经济/贸易 -> 历史 -> 人物 -> 其他）
LORE_ENTRY_TYPE_PRIORITY: dict[str, int] = {
    "world_rule": 1,
    "rule": 1,
    "magic": 1,
    "geography": 2,
    "location": 2,
    "region": 2,
    "faction": 3,
    "organization": 3,
    "country": 3,
    "politics": 3,
    "economy": 4,
    "trade": 4,
    "resource": 4,
    "history": 5,
    "culture": 5,
    "character": 6,
    "npc": 6,
    "person": 6,
}

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
    entry_type: str = ""
    document_id: str = ""
    chunk_index: int | None = None
    char_count: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        units = self.char_count if self.char_count > 0 else len(self.text.encode("utf-16-le")) // 2
        data: dict[str, Any] = {
            "source_id": self.source_id,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "char_count": units,
            "truncated": self.truncated,
        }
        if self.entry_type:
            data["entry_type"] = self.entry_type
        if self.document_id:
            data["document_id"] = self.document_id
        if self.chunk_index is not None:
            data["chunk_index"] = self.chunk_index
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CanonSourceItem":
        raw_kind = str(data.get("kind") or "")
        raw_sid = str(data.get("source_id") or "")
        if not raw_kind and raw_sid.startswith("entry:"):
            raw_kind = "entry"
        elif not raw_kind and raw_sid.startswith("chunk:"):
            raw_kind = "chunk"
        text = str(data.get("text") or "")
        units = int(data.get("char_count") or (len(text.encode("utf-16-le")) // 2 if text else 0))
        return cls(
            source_id=raw_sid,
            kind=raw_kind,  # type: ignore[arg-type]
            text=text,
            title=str(data.get("title") or ""),
            entry_type=str(data.get("entry_type") or ""),
            document_id=str(data.get("document_id") or ""),
            chunk_index=int(data["chunk_index"]) if data.get("chunk_index") is not None else None,
            char_count=units,
            truncated=bool(data.get("truncated", False)),
        )


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
    """经过 `validate_proposal` 完整校验的事件提议对象。

    同时暴露 `mode`/`kind`、`world_minute`/`target_world_minute`、
    `base_world_revision`/`world_revision` 属性以适配 `PulseWorker` 与公共契约。
    """

    instance_id: str
    mode: ProposalMode
    world_minute: int
    operations: tuple[ValidatedEventOp, ...]
    summary: str = ""
    base_world_revision: int | None = None
    transmissions: tuple[dict[str, Any], ...] = ()

    @property
    def kind(self) -> ProposalMode:
        return self.mode

    @property
    def target_world_minute(self) -> int:
        return self.world_minute

    @property
    def world_revision(self) -> int | None:
        return self.base_world_revision


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
    base_world_revision: int | None = None
    active_incident_count: int | None = None
    strict_mode_match: bool = False
    allowed_event_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def kind(self) -> ProposalMode:
        return self.mode

    @property
    def target_world_minute(self) -> int:
        return self.world_minute

    @property
    def world_revision(self) -> int | None:
        return self.base_world_revision


@dataclass(frozen=True)
class EventSeedRequest:
    """AI 初始局势生成受限输入。"""

    instance_id: str
    world_minute: int
    sources: tuple[CanonSourceItem, ...]
    world_intro: str = ""
    min_threads: int = DEFAULT_MIN_INITIAL_THREADS
    max_threads: int = DEFAULT_MAX_INITIAL_THREADS
    base_world_revision: int = 0


@dataclass(frozen=True)
class EventAdvanceRequest:
    """AI 到期事件推进受限输入（仅含需处理的到期活跃事件及最小因果邻域与授权 Canon 资料）。"""

    instance_id: str
    world_minute: int
    due_events: tuple[dict[str, Any], ...]
    related_facts: tuple[dict[str, Any], ...] = ()
    sources: tuple[CanonSourceItem, ...] = ()
    registered_precondition_ids: tuple[str, ...] = ()
    active_incident_count: int = 0
    max_active_incidents: int = DEFAULT_MAX_ACTIVE_INCIDENTS
    causal_neighborhood: tuple[dict[str, Any], ...] = ()
    base_world_revision: int = 0
