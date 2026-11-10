"""M3.4 NPC 自主行动、行程与动态模拟深度领域类型、常量与预算结构（M3 Wave 3）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NpcDepthTier = Literal["foreground", "associated", "background"]
NpcActionKind = Literal["travel", "conversation_plan", "shift_change", "task_progress"]
NpcOperationType = Literal["seed_npc", "act"]
NpcStatus = Literal["active", "paused", "inactive"]
NpcAvailability = Literal["available", "in_transit", "busy", "off_duty"]
TaskStatus = Literal["active", "completed", "blocked"]
ProposalMode = Literal["seed", "advance"]

NPC_DEPTH_TIERS: frozenset[str] = frozenset({"foreground", "associated", "background"})
NPC_DEPTH_TIER_LABELS_ZH: dict[str, str] = {
    "foreground": "近景",
    "associated": "关联",
    "background": "背景",
}
NPC_DEPTH_TIER_ALIASES: dict[str, NpcDepthTier] = {
    "foreground": "foreground",
    "near": "foreground",
    "s": "foreground",
    "近景": "foreground",
    "associated": "associated",
    "related": "associated",
    "mid": "associated",
    "a": "associated",
    "b": "associated",
    "关联": "associated",
    "background": "background",
    "far": "background",
    "distant": "background",
    "c": "background",
    "背景": "background",
    "远景": "background",
}

NPC_ACTION_KINDS: frozenset[str] = frozenset(
    {"travel", "conversation_plan", "shift_change", "task_progress"}
)
NPC_STATUSES: frozenset[str] = frozenset({"active", "paused", "inactive"})
NPC_AVAILABILITY_STATES: frozenset[str] = frozenset(
    {"available", "in_transit", "busy", "off_duty"}
)
TASK_STATUSES: frozenset[str] = frozenset({"active", "completed", "blocked"})
PROPOSAL_MODES: frozenset[str] = frozenset({"seed", "advance"})

SNAPSHOT_FORMAT_NAME = "m3.npcs.snapshot.v1"
SNAPSHOT_FORMAT_VERSION = 1

# 动态模拟深度预算默认值（可测、可配置）
DEFAULT_MAX_FOREGROUND_NPCS = 3
DEFAULT_MAX_ASSOCIATED_NPCS = 3
DEFAULT_MAX_BACKGROUND_NPCS = 1
DEFAULT_MAX_DUE_NPCS_PER_PULSE = 6

DEFAULT_FOREGROUND_NPC_CHAR_BUDGET = 420
DEFAULT_ASSOCIATED_NPC_CHAR_BUDGET = 240
DEFAULT_BACKGROUND_NPC_CHAR_BUDGET = 120
DEFAULT_TOTAL_NPC_CHAR_BUDGET = 2200

# 各深度档位的最小行动冷却/间隔分钟数（远景 NPC 演进频率显著低于近景）
MIN_ACTION_INTERVAL_BY_TIER: dict[str, int] = {
    "foreground": 15,
    "associated": 30,
    "background": 120,
}

# 各动作类型的最小持续分钟数约束
MIN_DURATION_BY_ACTION_KIND: dict[str, int] = {
    "travel": 15,
    "conversation_plan": 10,
    "shift_change": 15,
    "task_progress": 15,
}

# 文本长度上限（UTF-16 单位）
MAX_NPC_ID_UNITS = 100
MAX_NAME_UNITS = 80
MAX_ROLE_UNITS = 160
MAX_LOCATION_UNITS = 100
MAX_ACTIVITY_UNITS = 300
MAX_MOTIVATION_UNITS = 360
MAX_GOAL_UNITS = 240
MAX_SHIFT_UNITS = 100
MAX_TOPIC_UNITS = 240
MAX_TASK_ID_UNITS = 100
MAX_TASK_TITLE_UNITS = 160
MAX_TASK_STAGE_UNITS = 120
MAX_OPERATIONS_PER_PROPOSAL = 16
MAX_CAUSES_PER_ACTION = 10

# 严禁后台 NPC 行动直接修改的玩家个人账本字段
PROTECTED_PLAYER_LEDGER_KEYS: frozenset[str] = frozenset(
    {
        "coins",
        "coin",
        "gold",
        "money",
        "currency",
        "wallet",
        "balance",
        "coins_delta",
        "inventory",
        "items",
        "item_changes",
        "inventory_changes",
        "bag",
        "equipment",
        "attributes",
        "stats",
        "hp",
        "mp",
        "stamina",
        "stamina_delta",
        "spirit",
        "spirit_delta",
        "energy",
        "level",
        "exp",
        "player_state",
        "player_ledger",
        "player_coins",
        "player_inventory",
        "player_attributes",
        "character_json",
        "character_state",
        "personal_ledger",
    }
)

# 严禁后台 NPC 行动直接写入的玩家知情事实字段
PROTECTED_PLAYER_KNOWLEDGE_KEYS: frozenset[str] = frozenset(
    {
        "player_knowledge",
        "player_facts",
        "player_news",
        "grant_player_fact",
        "notify_player",
        "tell_player",
        "player_known",
        "player_rumor",
    }
)

# 严禁任意 JSON 路径或未受限补丁字段
FORBIDDEN_PATH_KEYS: frozenset[str] = frozenset(
    {
        "path",
        "json_path",
        "target_path",
        "field_path",
        "pointer",
        "json_pointer",
        "patch",
        "json_patch",
        "raw_sql",
        "mutation_path",
        "$set",
        "$inc",
        "$unset",
        "$push",
    }
)


@dataclass(frozen=True)
class DepthBudget:
    """动态模拟深度三档配额与字符预算配置。"""

    max_foreground_npcs: int = DEFAULT_MAX_FOREGROUND_NPCS
    max_associated_npcs: int = DEFAULT_MAX_ASSOCIATED_NPCS
    max_background_npcs: int = DEFAULT_MAX_BACKGROUND_NPCS
    max_total_npcs: int = DEFAULT_MAX_DUE_NPCS_PER_PULSE
    foreground_char_budget: int = DEFAULT_FOREGROUND_NPC_CHAR_BUDGET
    associated_char_budget: int = DEFAULT_ASSOCIATED_NPC_CHAR_BUDGET
    background_char_budget: int = DEFAULT_BACKGROUND_NPC_CHAR_BUDGET
    total_char_budget: int = DEFAULT_TOTAL_NPC_CHAR_BUDGET

    def tier_char_cap(self, tier: str) -> int:
        if tier == "foreground":
            return self.foreground_char_budget
        if tier == "associated":
            return self.associated_char_budget
        return self.background_char_budget

    def tier_count_cap(self, tier: str) -> int:
        if tier == "foreground":
            return self.max_foreground_npcs
        if tier == "associated":
            return self.max_associated_npcs
        return self.max_background_npcs

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_foreground_npcs": self.max_foreground_npcs,
            "max_associated_npcs": self.max_associated_npcs,
            "max_background_npcs": self.max_background_npcs,
            "max_total_npcs": self.max_total_npcs,
            "foreground_char_budget": self.foreground_char_budget,
            "associated_char_budget": self.associated_char_budget,
            "background_char_budget": self.background_char_budget,
            "total_char_budget": self.total_char_budget,
            "min_action_interval_by_tier": dict(MIN_ACTION_INTERVAL_BY_TIER),
        }


@dataclass(frozen=True)
class NpcDepthEvaluation:
    """单个 NPC 的动态模拟深度判定、升降档理由与预算占用记录。"""

    npc_id: str
    name: str
    location: str
    previous_tier: NpcDepthTier
    current_tier: NpcDepthTier
    transition: Literal["promoted", "demoted", "unchanged"]
    relevance_score: int
    reason_codes: tuple[str, ...]
    reason_summary: str
    is_due: bool
    next_due_minute: int | None
    selected_for_simulation: bool
    excluded_reason: str | None = None
    allocated_char_budget: int = 0
    used_chars: int = 0
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "npc_id": self.npc_id,
            "name": self.name,
            "location": self.location,
            "previous_tier": self.previous_tier,
            "current_tier": self.current_tier,
            "tier_label_zh": NPC_DEPTH_TIER_LABELS_ZH.get(self.current_tier, "背景"),
            "transition": self.transition,
            "relevance_score": self.relevance_score,
            "reason_codes": list(self.reason_codes),
            "reason_summary": self.reason_summary,
            "is_due": self.is_due,
            "next_due_minute": self.next_due_minute,
            "selected_for_simulation": self.selected_for_simulation,
            "excluded_reason": self.excluded_reason,
            "allocated_char_budget": self.allocated_char_budget,
            "used_chars": self.used_chars,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class ValidatedNpcActionOp:
    """经过纯校验器标准化后的单条 NPC 结构化提议操作。"""

    op: NpcOperationType
    npc_id: str
    action_kind: NpcActionKind
    current_activity: str
    motivation: str
    location: str
    next_due_minute: int | None
    duration_minutes: int
    expected_npc_revision: int | None = None
    name: str | None = None
    role: str | None = None
    faction: str | None = None
    home_location: str | None = None
    goal: str | None = None
    depth_tier: NpcDepthTier | None = None
    depth_reason: str = ""
    status: NpcStatus = "active"
    availability: NpcAvailability = "available"
    from_location: str = ""
    to_location: str = ""
    target_npc_id: str = ""
    meeting_location: str = ""
    conversation_topic: str = ""
    from_shift: str = ""
    to_shift: str = ""
    shift_name: str = ""
    task_id: str = ""
    task_title: str = ""
    task_stage: str = ""
    task_progress_percent: int | None = None
    task_status: TaskStatus | None = None
    cause_event_ids: tuple[str, ...] = ()
    cause_fact_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ValidatedNpcProposal:
    """经过纯校验器验证通过的完整 NPC 提议。"""

    instance_id: str
    mode: ProposalMode
    world_minute: int
    operations: tuple[ValidatedNpcActionOp, ...]
    summary: str = ""
    base_world_revision: int | None = None
    idempotency_key: str = ""

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
class NpcValidationContext:
    """纯函数校验器上下文（无需访问数据库即可完成全部结构、时间、版本与因果核对）。"""

    instance_id: str
    world_minute: int
    mode: ProposalMode = "advance"
    base_world_revision: int | None = None
    existing_npcs: dict[str, dict[str, Any]] = field(default_factory=dict)
    existing_tasks: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    allowed_event_ids: frozenset[str] = field(default_factory=frozenset)
    allowed_fact_ids_by_npc: dict[str, frozenset[str]] = field(default_factory=dict)
    allowed_fact_ids: frozenset[str] = field(default_factory=frozenset)
    allowed_source_ids: frozenset[str] = field(default_factory=frozenset)
    other_instance_ids: frozenset[str] = field(default_factory=frozenset)
    enforce_tier_interval: bool = True

    @property
    def kind(self) -> ProposalMode:
        return self.mode

    @property
    def target_world_minute(self) -> int:
        return self.world_minute

    @property
    def world_revision(self) -> int | None:
        return self.base_world_revision
