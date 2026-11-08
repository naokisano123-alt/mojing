"""可追溯上下文选择模块。

对外导出 build_context、ContextBundle、ContextError 及预算常量。
"""

from .service import build_context
from .types import (
    MAX_ACTION_UNITS,
    MAX_CONTEXT_UNITS,
    MAX_SOURCE_UNITS,
    MAX_SOURCES,
    ContextBundle,
    ContextError,
    safe_u16_truncate,
    u16len,
)

__all__ = [
    "build_context",
    "ContextBundle",
    "ContextError",
    "MAX_ACTION_UNITS",
    "MAX_SOURCES",
    "MAX_SOURCE_UNITS",
    "MAX_CONTEXT_UNITS",
    "u16len",
    "safe_u16_truncate",
]
