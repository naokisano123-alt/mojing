"""M3.3 人物知识、信息传播与玩家视角模块公共入口。"""

from __future__ import annotations

from .errors import KnowledgeError
from .fixtures import (
    advance_player_conversation_with_innkeeper,
    create_instance_fixture,
    seed_north_town_multi_hop_fixture,
)
from .schema import SCHEMA, ensure_knowledge_schema
from .service import (
    build_narration_knowledge_context,
    correct_knowledge,
    get_world_news,
    list_known,
    project_player_news,
    record_transmission,
    register_event_stub,
    register_holder,
    register_knowledge_routes,
    resolve_adventure_instance_id,
    restore,
    snapshot,
)
from .types import (
    CHANNELS,
    KNOWLEDGE_TYPES,
    PLAYER_HOLDER_ID,
    SNAPSHOT_FORMAT,
    SNAPSHOT_VERSION,
    STATUSES,
    KnownPage,
    NarrationKnowledgeBundle,
    TransmissionBatchResult,
    decode_holder_cursor,
    encode_holder_cursor,
    sanitize_untrusted_knowledge_text,
)

__all__ = [
    "CHANNELS",
    "KNOWLEDGE_TYPES",
    "PLAYER_HOLDER_ID",
    "SCHEMA",
    "SNAPSHOT_FORMAT",
    "SNAPSHOT_VERSION",
    "STATUSES",
    "KnowledgeError",
    "KnownPage",
    "NarrationKnowledgeBundle",
    "TransmissionBatchResult",
    "advance_player_conversation_with_innkeeper",
    "build_narration_knowledge_context",
    "correct_knowledge",
    "create_instance_fixture",
    "decode_holder_cursor",
    "encode_knowledge_cursor",
    "encode_holder_cursor",
    "ensure_knowledge_schema",
    "get_world_news",
    "list_known",
    "project_player_news",
    "record_transmission",
    "register_event_stub",
    "register_holder",
    "register_knowledge_routes",
    "resolve_adventure_instance_id",
    "restore",
    "sanitize_untrusted_knowledge_text",
    "seed_north_town_multi_hop_fixture",
    "snapshot",
]

encode_knowledge_cursor = encode_holder_cursor
