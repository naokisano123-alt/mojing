"""中文资料检索模块。

对外导出 search_chunks 及核心类型与异常。
"""

from .fts import check_fts5_available
from .service import search_chunks, u16len
from .types import (
    MatchField,
    SearchError,
    SearchItem,
    SearchResult,
    SearchValidationError,
    SearchWorldNotFoundError,
)

__all__ = [
    "search_chunks",
    "u16len",
    "check_fts5_available",
    "SearchError",
    "SearchValidationError",
    "SearchWorldNotFoundError",
    "SearchItem",
    "SearchResult",
    "MatchField",
]
