"""人工设定条目数据类型与 DTO 定义。

依据：
- docs/contracts/M1_R3_API.md §1.1 数据模型规范
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

LoreEntryType = Literal["character", "location", "faction", "rule", "other"]
LoreEntryStatus = Literal["draft", "confirmed"]
SourceAvailability = Literal["active", "deleted", "unavailable"]

VALID_ENTRY_TYPES: set[str] = {"character", "location", "faction", "rule", "other"}
VALID_ENTRY_STATUSES: set[str] = {"draft", "confirmed"}


class LoreEntrySourceDraft(TypedDict, total=False):
    """新建或更新条目时客户端上送的来源引用数据。"""

    document_id: str
    chunk_id: str
    start_char: int
    end_char: int


class LoreEntrySource(TypedDict):
    """持久化并返回的完整来源引用结构。"""

    document_id: str
    chunk_id: str
    start_char: int  # 文档全文 UTF-16 绝对起点
    end_char: int  # 文档全文 UTF-16 绝对终点
    excerpt: str  # 服务端从真实块文本提取，<=120 UTF-16 单位
    availability: SourceAvailability


class LoreEntry(TypedDict):
    """人工设定条目核心模型。"""

    id: str  # UUID v4
    world_id: str
    type: LoreEntryType
    title: str  # 1–80 字符，去首尾空白
    summary: str  # 0–500 字符
    status: LoreEntryStatus  # draft | confirmed
    revision: int  # 初值 1，更新递增
    origin: Literal["manual"]  # 固定为 manual
    source_links: list[LoreEntrySource]
    needs_source_review: bool  # 当且仅当 status === 'confirmed' 且存在非 active 来源时为 True
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    deleted_at: str | None


class LoreEntryListResult(TypedDict):
    """条目分页列表结果。"""

    items: list[LoreEntry]
    total: int
    limit: int
    offset: int
