"""中文资料检索服务数据类型与异常定义。

契约与规范：docs/collaboration/ROUND2.md、docs/collaboration/R2_GEMINI_MA.md。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class SearchError(Exception):
    """检索模块基础业务异常，符合 ApiError 统一错误形状：{"error": {"code", "message", "details"}}。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return {"error": payload}


class SearchValidationError(SearchError):
    """参数校验失败（422），如搜索词为空、超长，limit/offset 越界等。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, code, message, details)


class SearchWorldNotFoundError(SearchError):
    """世界不存在或已被软删除（404）。"""

    def __init__(
        self,
        world_id: str,
        message: str = "这个世界不存在，或已被删除。",
    ) -> None:
        super().__init__(404, "WORLD_NOT_FOUND", message, {"world_id": world_id})


MatchField = Literal["text", "heading", "filename"]


class SearchItem(TypedDict):
    """单条检索命中条目，完全满足 ROUND2.md 规范，且标注 UTF-16 坐标。"""

    world_id: str
    document_id: str
    chunk_id: str
    chunk_index: int
    filename: str
    heading_path: list[str]
    start_char: int
    end_char: int
    snippet: str
    match_field: MatchField
    match_start_char: int  # 文档全文绝对起点（UTF-16）
    match_end_char: int  # 文档全文绝对终点（UTF-16）
    match_chunk_start_char: int  # 块内相对起点（UTF-16）
    match_chunk_end_char: int  # 块内相对终点（UTF-16）
    match_coords_scope: str  # 声明单位与范围，固定为 "document_and_chunk_utf16"


class SearchResult(TypedDict):
    """检索结果分页响应结构。"""

    items: list[SearchItem]
    total: int
    limit: int
    offset: int
