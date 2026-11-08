"""可追溯上下文选择模块数据类型与异常定义。

契约与规范依据：
- docs/contracts/M2_A_API.md §核心接口与模块拼装 (Ma 职责)
- docs/collaboration/ROUND6.md、R6_GEMINI_MA.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.narration import ContextSource

# 限制常量与契约对齐
MAX_ACTION_UNITS = 2000
MAX_SOURCES = 8
MAX_SOURCE_UNITS = 4000
MAX_CONTEXT_UNITS = 16000


class ContextError(ValueError):
    """上下文选择模块业务异常，与 M2_A_API 错误规范及 HTTP 映射对齐。"""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def body(self) -> dict[str, Any]:
        """返回统一错误响应结构 {"error": {"code", "message", "details"}}。"""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return {"error": payload}

    def __repr__(self) -> str:
        return f"ContextError(code={self.code!r}, message={self.message!r}, status={self.status})"


@dataclass(frozen=True)
class ContextBundle:
    """上下文组装结果包。

    sources 与 metadata 必须完全一一对应：
    - sources: tuple[ContextSource, ...] 供模型调用验证与输入
    - metadata: tuple[dict[str, Any], ...] 供 API 响应与前端回跳查看
    """

    sources: tuple[ContextSource, ...] = ()
    metadata: tuple[dict[str, Any], ...] = ()


def u16len(text: str) -> int:
    """计算字符串对应的 UTF-16 code unit 长度（与前端 JS string.length 完全一致）。"""
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ContextError(
            "INVALID_TEXT",
            "文字包含不完整的 Unicode 字符。",
            status=422,
        ) from exc


def safe_u16_truncate(text: str, max_units: int) -> str:
    """按 UTF-16 code unit 预算截断前缀，绝不切断代理对（Surrogate Pairs，如 Emoji）。"""
    if max_units <= 0:
        return ""
    if u16len(text) <= max_units:
        return text

    units = 0
    chars: list[str] = []
    for ch in text:
        try:
            ch_units = len(ch.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise ContextError(
                "INVALID_TEXT",
                "文字包含不完整的 Unicode 字符。",
                status=422,
            ) from exc
        if units + ch_units > max_units:
            break
        units += ch_units
        chars.append(ch)

    return "".join(chars)
