"""M3 第四轮（Wave 4）导入设定与 AI 初始化领域数据结构、常量与预算定义。

契约依据（`docs/collaboration/M3_WAVE4_GEMINI_MARCIN.md` & `docs/contracts/M3_WAVE4_API.md`）：
1. 接收现有世界的已确认设定和导入资料，按起点挑选相关节选；支持长文分批与合并，显式记录每次请求的输入范围（`BatchInputRange`）。
2. AI 提议开局时在场人物、地点、目标、关系和初始事件线，分别标明资料依据（`canon`）与旅程内补写（`adventure_supplement`）。
3. 检查来源只属于该世界，处理重名（`homonym_disambiguator`）和身份复用（跨批次 `aliases` 合并与实例内稳定标识复用），限制未公开信息。
4. 初始人物的动作安排使用 Gemini Li 可以直接读取的实例内身份（`npc_id`、`target_npc_id`）和时间语义（`world_minute`、`duration_minutes`、`next_due_minute`）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

GroundingType = Literal["canon", "adventure_supplement"]
GROUNDING_TYPES: frozenset[str] = frozenset({"canon", "adventure_supplement"})
GROUNDING_TYPE_ALIASES: dict[str, GroundingType] = {
    "canon": "canon",
    "canon_grounded": "canon",
    "source": "canon",
    "documented": "canon",
    "资料依据": "canon",
    "原文依据": "canon",
    "adventure_supplement": "adventure_supplement",
    "supplement": "adventure_supplement",
    "supplemented": "adventure_supplement",
    "inferred": "adventure_supplement",
    "extrapolated": "adventure_supplement",
    "instance_extrapolation": "adventure_supplement",
    "旅程内补写": "adventure_supplement",
    "推演补写": "adventure_supplement",
}

VisibilityLevel = Literal["public", "local_observable", "faction_internal", "secret"]
VISIBILITY_LEVELS: frozenset[str] = frozenset(
    {"public", "local_observable", "faction_internal", "secret"}
)
PLAYER_SUMMARY_ALLOWED_VISIBILITY: frozenset[str] = frozenset(
    {"public", "local_observable"}
)

AnalysisRunMode = Literal["initial", "retry", "reopen", "branch"]
ANALYSIS_RUN_MODES: frozenset[str] = frozenset({"initial", "retry", "reopen", "branch"})

AnalysisStatus = Literal[
    "waiting",
    "insufficient_sources",
    "analyzing",
    "ready_to_confirm",
    "failed_retryable",
    "applied",
]
ANALYSIS_STATUSES: frozenset[str] = frozenset(
    {
        "waiting",
        "insufficient_sources",
        "analyzing",
        "ready_to_confirm",
        "failed_retryable",
        "applied",
    }
)

# 兼容 Li 的首版动作语义（出行、留在原处、改变计划、交谈安排）与现有 m3/npcs 的四种持久动作类型
NpcPlanKind = Literal[
    "stay",
    "travel",
    "change_plan",
    "conversation_plan",
    "shift_change",
    "task_progress",
]
NPC_PLAN_KINDS: frozenset[str] = frozenset(
    {
        "stay",
        "travel",
        "change_plan",
        "conversation_plan",
        "shift_change",
        "task_progress",
    }
)

NpcCanonicalActionKind = Literal[
    "travel",
    "conversation_plan",
    "shift_change",
    "task_progress",
]
NPC_CANONICAL_ACTION_KINDS: frozenset[str] = frozenset(
    {"travel", "conversation_plan", "shift_change", "task_progress"}
)

# 将高层开局计划类型映射为 m3/npcs 表结构支持的四种基础动作类型
PLAN_KIND_TO_NPC_ACTION_KIND: dict[str, NpcCanonicalActionKind] = {
    "travel": "travel",
    "conversation_plan": "conversation_plan",
    "stay": "shift_change",
    "shift_change": "shift_change",
    "change_plan": "task_progress",
    "task_progress": "task_progress",
}

# 长文分批与单次模型请求上下文预算常量（可测量、可配置）
DEFAULT_BATCH_MAX_ENTRIES = 6
DEFAULT_BATCH_MAX_CHUNKS = 5
DEFAULT_MAX_ENTRY_CHARS = 500
DEFAULT_MAX_CHUNK_CHARS = 700
DEFAULT_BATCH_CHAR_BUDGET = 2600
DEFAULT_TOTAL_CHAR_BUDGET = 10400
DEFAULT_MAX_BATCHES = 4

DEFAULT_MIN_INITIAL_THREADS = 3
DEFAULT_MAX_INITIAL_THREADS = 6
DEFAULT_MIN_INITIAL_NPCS = 2
DEFAULT_MAX_INITIAL_NPCS = 12
DEFAULT_MAX_LOCATIONS = 16
DEFAULT_MAX_RELATIONSHIPS = 24
DEFAULT_MAX_CONFLICTS = 12

# 设定条目类型稳定优先级（优先提取地点/地理、人物、势力/组织、规则、经济、历史）
ANALYSIS_ENTRY_TYPE_PRIORITY: dict[str, int] = {
    "location": 1,
    "geography": 1,
    "region": 1,
    "character": 2,
    "npc": 2,
    "person": 2,
    "faction": 3,
    "organization": 3,
    "politics": 3,
    "country": 3,
    "world_rule": 4,
    "rule": 4,
    "magic": 4,
    "economy": 5,
    "trade": 5,
    "resource": 5,
    "history": 6,
    "culture": 6,
}

# 玩家个人状态保护词表与任意路径禁用词表
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
class StartingPointSpec:
    """玩家选择的开局起点、时间与运行模式定义。"""

    location: str
    world_minute: int = 0
    starts_at: str = "0001-01-01T08:00"
    opening_id: str | None = None
    character_name: str = "旅人"
    focus_keywords: tuple[str, ...] = ()
    run_mode: AnalysisRunMode = "initial"
    request_id: str = ""
    parent_draft_id: str | None = None
    branch_from_checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "world_minute": self.world_minute,
            "starts_at": self.starts_at,
            "opening_id": self.opening_id,
            "character_name": self.character_name,
            "focus_keywords": list(self.focus_keywords),
            "run_mode": self.run_mode,
            "request_id": self.request_id,
            "parent_draft_id": self.parent_draft_id,
            "branch_from_checkpoint_id": self.branch_from_checkpoint_id,
        }


@dataclass(frozen=True)
class CanonExcerptItem:
    """按起点相关性挑选并消毒后的单条世界资料节选。"""

    source_id: str  # 格式：chunk:<id> 或 entry:<id>
    world_id: str
    kind: Literal["chunk", "entry"]
    title: str
    text: str
    document_id: str = ""
    filename: str = ""
    chunk_index: int | None = None
    start_char: int = 0
    end_char: int = 0
    heading_path: str = ""
    entry_type: str = ""
    relevance_score: int = 0
    matched_terms: tuple[str, ...] = ()
    char_count: int = 0
    truncated: bool = False
    prompt_injection_filtered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "world_id": self.world_id,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "document_id": self.document_id,
            "filename": self.filename,
            "chunk_index": self.chunk_index,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "heading_path": self.heading_path,
            "entry_type": self.entry_type,
            "relevance_score": self.relevance_score,
            "matched_terms": list(self.matched_terms),
            "char_count": self.char_count,
            "truncated": self.truncated,
            "prompt_injection_filtered": self.prompt_injection_filtered,
        }


@dataclass(frozen=True)
class DocumentSpanRange:
    """单次请求覆盖的单个文档分块与字符跨度范围。"""

    document_id: str
    filename: str
    start_chunk_index: int
    end_chunk_index: int
    start_char: int
    end_char: int
    chunk_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "start_chunk_index": self.start_chunk_index,
            "end_chunk_index": self.end_chunk_index,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "chunk_ids": list(self.chunk_ids),
            "chunk_count": len(self.chunk_ids),
        }


@dataclass(frozen=True)
class BatchInputRange:
    """长文分批策略下每次模型请求的确切输入范围记录。"""

    batch_index: int
    total_batches: int
    source_ids: tuple[str, ...]
    entry_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    document_ranges: tuple[DocumentSpanRange, ...]
    used_chars: int
    char_budget: int
    selection_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_index": self.batch_index,
            "total_batches": self.total_batches,
            "source_ids": list(self.source_ids),
            "entry_ids": list(self.entry_ids),
            "chunk_ids": list(self.chunk_ids),
            "document_ranges": [d.to_dict() for d in self.document_ranges],
            "used_chars": self.used_chars,
            "char_budget": self.char_budget,
            "selection_reason": self.selection_reason,
        }


@dataclass(frozen=True)
class ExtractionBatch:
    """单批待送入模型分析的受限资料包及其输入范围。"""

    batch_index: int
    total_batches: int
    world_id: str
    starting_point: StartingPointSpec
    sources: tuple[CanonExcerptItem, ...]
    input_range: BatchInputRange

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_index": self.batch_index,
            "total_batches": self.total_batches,
            "world_id": self.world_id,
            "starting_point": self.starting_point.to_dict(),
            "sources": [s.to_dict() for s in self.sources],
            "input_range": self.input_range.to_dict(),
        }


@dataclass(frozen=True)
class SettingConflictItem:
    """已有设定冲突记录（如已确认设定条目与导入文档原文冲突，或不同分块间说法矛盾）。"""

    conflict_id: str
    topic: str
    entity_ref: str
    summary: str
    primary_source_id: str
    conflicting_source_id: str
    resolution_policy: Literal["prefer_confirmed_entry", "preserve_both_as_rumor", "prompt_player"] = (
        "prefer_confirmed_entry"
    )
    authoritative_claim: str = ""
    conflicting_claim: str = ""
    player_visible_hint: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "topic": self.topic,
            "entity_ref": self.entity_ref,
            "summary": self.summary,
            "primary_source_id": self.primary_source_id,
            "conflicting_source_id": self.conflicting_source_id,
            "resolution_policy": self.resolution_policy,
            "authoritative_claim": self.authoritative_claim,
            "conflicting_claim": self.conflicting_claim,
            "player_visible_hint": self.player_visible_hint,
        }


@dataclass(frozen=True)
class ProposedLocation:
    """AI 提议的实例内稳定地点定义。"""

    location_id: str
    name: str
    region: str
    description: str
    is_starting_location: bool
    grounding_type: GroundingType
    source_ids: tuple[str, ...]
    supplement_reason: str = ""
    visibility: VisibilityLevel = "public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "name": self.name,
            "region": self.region,
            "description": self.description,
            "is_starting_location": self.is_starting_location,
            "grounding_type": self.grounding_type,
            "source_ids": list(self.source_ids),
            "supplement_reason": self.supplement_reason,
            "visibility": self.visibility,
        }


@dataclass(frozen=True)
class ProposedNpcRelationship:
    """人物之间的已知关系（标明资料依据 vs 旅程内补写，以及公开/私密可见性）。"""

    from_npc_id: str
    to_target_id: str
    relation_type: str
    summary: str
    grounding_type: GroundingType
    source_ids: tuple[str, ...]
    supplement_reason: str = ""
    visibility: VisibilityLevel = "local_observable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_npc_id": self.from_npc_id,
            "to_target_id": self.to_target_id,
            "relation_type": self.relation_type,
            "summary": self.summary,
            "grounding_type": self.grounding_type,
            "source_ids": list(self.source_ids),
            "supplement_reason": self.supplement_reason,
            "visibility": self.visibility,
        }


@dataclass(frozen=True)
class ProposedInitialNpc:
    """AI 提议的初始人物及其动作安排（直接对齐 Li 的实例内身份与时间语义）。"""

    npc_id: str
    name: str
    role: str
    faction: str
    home_location: str
    location: str
    present_at_start: bool
    goal: str
    motivation: str
    public_demeanor: str
    plan_kind: NpcPlanKind
    action_kind: NpcCanonicalActionKind
    current_activity: str
    availability: Literal["available", "in_transit", "busy", "off_duty"]
    duration_minutes: int
    next_due_minute: int
    depth_tier: Literal["foreground", "associated", "background"]
    grounding_type: GroundingType
    source_ids: tuple[str, ...]
    supplement_reason: str = ""
    visibility: VisibilityLevel = "public"
    homonym_disambiguator: str = ""
    aliases: tuple[str, ...] = ()
    from_location: str = ""
    to_location: str = ""
    target_npc_id: str = ""
    meeting_location: str = ""
    conversation_topic: str = ""
    shift_name: str = ""
    task_id: str = ""
    task_title: str = ""
    task_stage: str = ""
    task_progress_percent: int | None = None
    cause_event_ids: tuple[str, ...] = ()
    initial_known_event_ids: tuple[str, ...] = ()
    relationships: tuple[ProposedNpcRelationship, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "npc_id": self.npc_id,
            "name": self.name,
            "role": self.role,
            "identity": self.role,
            "faction": self.faction,
            "home_location": self.home_location,
            "location": self.location,
            "present_at_start": self.present_at_start,
            "goal": self.goal,
            "motivation": self.motivation,
            "public_demeanor": self.public_demeanor,
            "plan_kind": self.plan_kind,
            "action_kind": self.action_kind,
            "current_activity": self.current_activity,
            "availability": self.availability,
            "duration_minutes": self.duration_minutes,
            "next_due_minute": self.next_due_minute,
            "next_update_minute": self.next_due_minute,
            "depth_tier": self.depth_tier,
            "grounding_type": self.grounding_type,
            "source_ids": list(self.source_ids),
            "supplement_reason": self.supplement_reason,
            "visibility": self.visibility,
            "homonym_disambiguator": self.homonym_disambiguator,
            "aliases": list(self.aliases),
            "from_location": self.from_location,
            "to_location": self.to_location,
            "target_npc_id": self.target_npc_id,
            "meeting_location": self.meeting_location,
            "conversation_topic": self.conversation_topic,
            "shift_name": self.shift_name,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "task_stage": self.task_stage,
            "task_progress_percent": self.task_progress_percent,
            "cause_event_ids": list(self.cause_event_ids),
            "initial_known_event_ids": list(self.initial_known_event_ids),
            "relationships": [r.to_dict() for r in self.relationships],
        }


@dataclass(frozen=True)
class ProposedInitialEvent:
    """AI 提议的初始主要事件线（标明资料依据与旅程内补写，区分权威描述与玩家所知传闻）。"""

    event_id: str
    kind: Literal["thread"]
    title: str
    description: str
    location: str
    scope: Literal["local", "regional", "national", "international"]
    participants: tuple[str, ...]
    stage: str
    status: Literal["active"]
    created_minute: int
    next_update_minute: int
    grounding_type: GroundingType
    source_ids: tuple[str, ...]
    supplement_reason: str = ""
    visibility: VisibilityLevel = "local_observable"
    player_known_at_start: bool = False
    player_rumor_title: str = ""
    player_rumor_content: str = ""
    player_channel: Literal["observed", "notice", "rumor", ""] = ""
    player_knowledge_type: Literal["confirmed", "suspected", "rumor"] = "rumor"
    consequences: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "scope": self.scope,
            "participants": list(self.participants),
            "stage": self.stage,
            "status": self.status,
            "created_minute": self.created_minute,
            "next_update_minute": self.next_update_minute,
            "grounding_type": self.grounding_type,
            "source_ids": list(self.source_ids),
            "supplement_reason": self.supplement_reason,
            "visibility": self.visibility,
            "player_known_at_start": self.player_known_at_start,
            "player_rumor_title": self.player_rumor_title,
            "player_rumor_content": self.player_rumor_content,
            "player_channel": self.player_channel,
            "player_knowledge_type": self.player_knowledge_type,
            "consequences": [dict(c) for c in self.consequences],
        }


@dataclass(frozen=True)
class WorldAnalysisValidationContext:
    """纯函数校验器上下文（无需写数据库即可完成全部世界归属、身份消歧、未公开信息与时间/因果校验）。"""

    world_id: str
    instance_id: str
    starting_point: StartingPointSpec
    world_minute: int = 0
    base_world_revision: int = 0
    allowed_source_ids: frozenset[str] = field(default_factory=frozenset)
    confirmed_entry_source_ids: frozenset[str] = field(default_factory=frozenset)
    other_world_source_ids: dict[str, str] = field(default_factory=dict)
    other_instance_ids: frozenset[str] = field(default_factory=frozenset)
    input_ranges: tuple[BatchInputRange, ...] = ()
    detected_conflicts: tuple[SettingConflictItem, ...] = ()
    min_initial_threads: int = DEFAULT_MIN_INITIAL_THREADS
    max_initial_threads: int = DEFAULT_MAX_INITIAL_THREADS


@dataclass(frozen=True)
class ValidatedWorldAnalysisDraft:
    """经过 `validate_analysis_draft` 校验通过的完整初始局势结构化草案。"""

    draft_id: str
    world_id: str
    instance_id: str
    starting_point: StartingPointSpec
    world_minute: int
    base_world_revision: int
    status: AnalysisStatus
    run_mode: AnalysisRunMode
    request_id: str
    analysis_fingerprint: str
    summary: str
    scene_overview: str
    input_ranges: tuple[BatchInputRange, ...]
    allowed_source_ids: tuple[str, ...]
    referenced_source_ids: tuple[str, ...]
    locations: tuple[ProposedLocation, ...]
    npcs: tuple[ProposedInitialNpc, ...]
    events: tuple[ProposedInitialEvent, ...]
    relationships: tuple[ProposedNpcRelationship, ...]
    conflicts: tuple[SettingConflictItem, ...]
    supplement_ledger: tuple[dict[str, Any], ...]
    identity_resolution_log: tuple[dict[str, Any], ...]
    player_opening_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "world_id": self.world_id,
            "instance_id": self.instance_id,
            "starting_point": self.starting_point.to_dict(),
            "world_minute": self.world_minute,
            "base_world_revision": self.base_world_revision,
            "status": self.status,
            "run_mode": self.run_mode,
            "request_id": self.request_id,
            "analysis_fingerprint": self.analysis_fingerprint,
            "summary": self.summary,
            "scene_overview": self.scene_overview,
            "input_ranges": [r.to_dict() for r in self.input_ranges],
            "allowed_source_ids": list(self.allowed_source_ids),
            "referenced_source_ids": list(self.referenced_source_ids),
            "locations": [loc.to_dict() for loc in self.locations],
            "npcs": [npc.to_dict() for npc in self.npcs],
            "events": [ev.to_dict() for ev in self.events],
            "relationships": [rel.to_dict() for rel in self.relationships],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "supplement_ledger": [dict(item) for item in self.supplement_ledger],
            "identity_resolution_log": [dict(item) for item in self.identity_resolution_log],
            "player_opening_summary": dict(self.player_opening_summary),
        }
