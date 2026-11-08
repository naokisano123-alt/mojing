"""人工设定条目后端服务模块（M1-D / R3-A）。

导出接口供 DSH 在路由层调用；模块内部连接注入，不自行 commit，不私建正式库。
"""

from __future__ import annotations

from .coords import check_utf16_offset, generate_excerpt, u16len
from .errors import (
    LoreEntryConflictError,
    LoreEntryError,
    LoreEntryNotFoundError,
    LoreEntrySourceChunkMismatchError,
    LoreEntrySourceCrossWorldError,
    LoreEntrySourceDeletedError,
    LoreEntrySourceInvalidOffsetError,
    LoreEntrySourceOutOfRangeError,
    LoreEntryValidationError,
    LoreEntryWorldNotFoundError,
)
from .schema import LORE_ENTRIES_SCHEMA, SCHEMA_VERSION, ensure_lore_entries_schema
from .service import (
    create_entry,
    delete_entry,
    get_entry,
    list_entries,
    update_entry,
)
from .types import (
    VALID_ENTRY_STATUSES,
    VALID_ENTRY_TYPES,
    LoreEntry,
    LoreEntryListResult,
    LoreEntrySource,
    LoreEntrySourceDraft,
    LoreEntryStatus,
    LoreEntryType,
    SourceAvailability,
)

__all__ = [
    "create_entry",
    "get_entry",
    "list_entries",
    "update_entry",
    "delete_entry",
    "ensure_lore_entries_schema",
    "LORE_ENTRIES_SCHEMA",
    "SCHEMA_VERSION",
    "LoreEntry",
    "LoreEntrySource",
    "LoreEntrySourceDraft",
    "LoreEntryType",
    "LoreEntryStatus",
    "SourceAvailability",
    "LoreEntryListResult",
    "VALID_ENTRY_TYPES",
    "VALID_ENTRY_STATUSES",
    "LoreEntryError",
    "LoreEntryNotFoundError",
    "LoreEntryWorldNotFoundError",
    "LoreEntryConflictError",
    "LoreEntryValidationError",
    "LoreEntrySourceCrossWorldError",
    "LoreEntrySourceDeletedError",
    "LoreEntrySourceChunkMismatchError",
    "LoreEntrySourceOutOfRangeError",
    "LoreEntrySourceInvalidOffsetError",
    "u16len",
    "check_utf16_offset",
    "generate_excerpt",
]
