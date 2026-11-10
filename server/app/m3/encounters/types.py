"""M3.3 / M3.6 玩家主动查证、交谈与消息后续行动类型契约与可注入边界定义（M3 Wave 3）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import re
from typing import Any, Literal, Protocol

from .errors import EncounterError

KnowledgeType = Literal["confirmed", "suspected", "rumor", "mistaken"]
KnowledgeChannel = Literal["observed", "notice", "conversation", "rumor"]
EncounterOptionKind = Literal["ask_npc", "check_notice", "free_edit"]
EncounterResponseOutcome = Literal[
    "clue",
    "refused",
    "vague",
    "conflicting",
    "unknown",
    "npc_departed",
]
NpcResponsePolicy = Literal["cooperative", "reluctant", "evasive"]

KNOWLEDGE_TYPES: tuple[str, ...] = ("confirmed", "suspected", "rumor", "mistaken")
CHANNELS: tuple[str, ...] = ("observed", "notice", "conversation", "rumor")
OPTION_KINDS: tuple[str, ...] = ("ask_npc", "check_notice", "free_edit")
RESPONSE_OUTCOMES: tuple[str, ...] = (
    "clue",
    "refused",
    "vague",
    "conflicting",
    "unknown",
    "npc_departed",
)
RESPONSE_POLICIES: tuple[str, ...] = ("cooperative", "reluctant", "evasive")

PLAYER_HOLDER_ID = "player"
SNAPSHOT_FORMAT = "mojing.m3.encounters.snapshot"
SNAPSHOT_VERSION = 1

KNOWLEDGE_TYPE_LABELS: dict[str, str] = {
    "confirmed": "确认事实",
    "suspected": "怀疑推测",
    "rumor": "市井传闻",
    "mistaken": "误信传言",
}

CHANNEL_LABELS: dict[str, str] = {
    "observed": "亲眼目睹",
    "notice": "公开告示",
    "conversation": "当面交谈",
    "rumor": "道听途说",
}

OUTCOME_LABELS: dict[str, str] = {
    "clue": "获得线索",
    "refused": "婉拒回答",
    "vague": "含混回应",
    "conflicting": "说法冲突",
    "unknown": "并不知情",
    "npc_departed": "人物不在场",
}

PROTECTED_PLAYER_LEDGER_KEYS = frozenset(
    {
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
        "player_ledger",
        "personal_ledger",
        "character",
        "character_json",
        "character_state",
        "state_before",
        "state_after",
        "proposed_changes",
        "projected_state",
    }
)

FORBIDDEN_PATH_KEYS = frozenset(
    {
        "$set",
        "$unset",
        "$inc",
        "$push",
        "$where",
        "json_path",
        "target_path",
        "pointer",
        "json_pointer",
        "patch",
        "json_patch",
        "__proto__",
        "constructor",
        "prototype",
    }
)

FORBIDDEN_BROADCAST_HOLDERS = frozenset(
    {
        "*",
        "all",
        "everyone",
        "all_npcs",
        "npcs",
        "world",
        "global",
        "public",
        "system",
    }
)

MAX_ID_LEN = 120
MAX_TITLE_UNITS = 120
MAX_CONTENT_UNITS = 1000
MAX_ACTION_TEXT_UNITS = 1000
MAX_LOCATION_UNITS = 120


def u16_len(text: str) -> int:
    if not isinstance(text, str):
        raise EncounterError(422, "VALIDATION_ERROR", "文本字段类型无效。")
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise EncounterError(422, "VALIDATION_ERROR", "文本包含不完整的 Unicode 字符。") from exc


def safe_u16_truncate(text: str, max_units: int) -> str:
    if max_units <= 0:
        return ""
    if u16_len(text) <= max_units:
        return text
    units = 0
    out: list[str] = []
    for ch in text:
        ch_units = 2 if ord(ch) > 0xFFFF else 1
        if units + ch_units > max_units:
            break
        out.append(ch)
        units += ch_units
    return "".join(out)


def validate_clean_text(
    value: Any,
    label: str,
    max_units: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise EncounterError(422, "VALIDATION_ERROR", f"{label}须为字符串。")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise EncounterError(422, "VALIDATION_ERROR", f"{label}不能为空。")
    if "\x00" in cleaned:
        raise EncounterError(422, "VALIDATION_ERROR", f"{label}不得包含空字符。")
    if u16_len(cleaned) > max_units:
        raise EncounterError(422, "VALIDATION_ERROR", f"{label}长度不能超过 {max_units} 字。")
    return cleaned


def append_action_suggestion(draft: str, suggestion: str, limit: int = MAX_ACTION_TEXT_UNITS) -> str:
    """与前端 ActionComposer.appendSuggestion 保持一致的行动建议追加逻辑。"""
    clean_sug = validate_clean_text(suggestion, "行动建议", limit)
    if not draft or not draft.strip():
        value = clean_sug
    else:
        sep = "" if draft.endswith("\n") else "\n"
        value = f"{draft}{sep}{clean_sug}"
    if u16_len(value) > limit:
        raise EncounterError(
            422,
            "ACTION_DRAFT_LIMIT_EXCEEDED",
            "草稿空间不足，请先精简文字。已有内容未改变。",
            {"limit": limit, "max_units": limit},
        )
    return value


class NpcContactabilityProvider(Protocol):
    """可注入的 NPC 可接触性查询边界协议（解耦 Marcin 的 npcs 模块与独立测试 fixture）。"""

    def check_contactability(
        self,
        conn: Any,
        instance_id: str,
        npc_id: str,
        *,
        player_location: str,
        world_minute: int,
    ) -> dict[str, Any]: ...

    def list_contactable_npcs(
        self,
        conn: Any,
        instance_id: str,
        *,
        location: str,
        world_minute: int,
    ) -> list[dict[str, Any]]: ...


class HolderKnowledgeProvider(Protocol):
    """可注入的 NPC / 玩家知情查询边界协议。"""

    def list_holder_facts(
        self,
        conn: Any,
        instance_id: str,
        holder_id: str,
        *,
        up_to_minute: int,
        source_event_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class EncounterActionOption:
    """从一条已获知消息/传闻生成的行动入口建议项（只写回输入框，不自动推进时钟或确认回合）。"""

    option_id: str
    fact_id: str
    kind: EncounterOptionKind
    label: str
    description: str
    suggested_action_text: str
    target_location: str
    target_npc_id: str | None = None
    target_npc_name: str | None = None
    target_npc_role: str | None = None
    contactable: bool = True
    reason_code: str = "OPTION_READY"
    disabled_reason: str | None = None
    advances_clock: bool = False
    auto_commits_turn: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "fact_id": self.fact_id,
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
            "suggested_action_text": self.suggested_action_text,
            "target_location": self.target_location,
            "target_npc_id": self.target_npc_id,
            "target_npc_name": self.target_npc_name,
            "target_npc_role": self.target_npc_role,
            "contactable": self.contactable,
            "reason_code": self.reason_code,
            "disabled_reason": self.disabled_reason,
            "advances_clock": False,
            "auto_commits_turn": False,
        }


@dataclass(frozen=True)
class EncounterTransmissionProposal:
    """玩家完成查证/交谈确认后可由集成层提交给知识模块的传播提议（严禁直接改账）。"""

    holder_id: str
    knowledge_type: KnowledgeType
    channel: KnowledgeChannel
    title: str
    content: str
    source_event_id: str
    source_person_id: str | None
    source_fact_id: str | None
    learned_minute: int
    confidence: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op": "record",
            "holder_id": self.holder_id,
            "knowledge_type": self.knowledge_type,
            "channel": self.channel,
            "title": self.title,
            "content": self.content,
            "source_event_id": self.source_event_id,
            "learned_minute": self.learned_minute,
            "confidence": self.confidence,
            "status": "active",
            "can_settle_ledger": False,
        }
        if self.source_person_id is not None:
            payload["source_person_id"] = self.source_person_id
        if self.source_fact_id is not None:
            payload["source_fact_id"] = self.source_fact_id
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class EncounterResolutionResult:
    """NPC 交谈或公告查证的推演与校验结果（严格基于 NPC 自身知情记录）。"""

    instance_id: str
    origin_fact_id: str
    option_kind: EncounterOptionKind
    target_npc_id: str | None
    player_location: str
    world_minute: int
    outcome: EncounterResponseOutcome
    reason_code: str
    narrative_reply: str
    conflict_summary: str | None = None
    conflicting_fact_ids: tuple[str, ...] = ()
    transmission_proposals: tuple[EncounterTransmissionProposal, ...] = ()
    can_settle_ledger: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "origin_fact_id": self.origin_fact_id,
            "option_kind": self.option_kind,
            "target_npc_id": self.target_npc_id,
            "player_location": self.player_location,
            "world_minute": self.world_minute,
            "outcome": self.outcome,
            "outcome_label": OUTCOME_LABELS.get(self.outcome, self.outcome),
            "reason_code": self.reason_code,
            "narrative_reply": self.narrative_reply,
            "conflict_summary": self.conflict_summary,
            "conflicting_fact_ids": list(self.conflicting_fact_ids),
            "transmission_proposals": [p.to_dict() for p in self.transmission_proposals],
            "can_settle_ledger": False,
        }
