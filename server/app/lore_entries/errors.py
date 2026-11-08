"""人工设定条目业务异常定义。

依据：
- docs/contracts/M1_R3_API.md §1.4 冲突与错误码规范
- 统一错误格式：{"error": {"code": str, "message": str, "details": dict | None}}
"""

from __future__ import annotations

from typing import Any


class LoreEntryError(Exception):
    """人工设定条目基础异常。"""

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


class LoreEntryNotFoundError(LoreEntryError):
    """条目不存在或已被软删除（404）。"""

    def __init__(
        self,
        entry_id: str,
        message: str = "设定条目不存在或已被删除。",
    ) -> None:
        super().__init__(404, "LORE_ENTRY_NOT_FOUND", message, {"entry_id": entry_id})


class LoreEntryWorldNotFoundError(LoreEntryError):
    """世界不存在或已被软删除（404）。"""

    def __init__(
        self,
        world_id: str,
        message: str = "目标世界不存在，或已被删除。",
    ) -> None:
        super().__init__(404, "WORLD_NOT_FOUND", message, {"world_id": world_id})


class LoreEntryConflictError(LoreEntryError):
    """乐观并发版本冲突（409）：expected_revision 与当前 revision 不一致。"""

    def __init__(
        self,
        expected_revision: int,
        current_revision: int,
        message: str = "版本冲突：该条目已被其他操作更新，请拉取最新数据后再试。",
    ) -> None:
        super().__init__(
            409,
            "LORE_ENTRY_CONFLICT",
            message,
            {
                "expected_revision": expected_revision,
                "current_revision": current_revision,
            },
        )


class LoreEntryValidationError(LoreEntryError):
    """字段合法性校验失败（422）：如标题为空/超长、摘要超长、枚举不合法等。"""

    def __init__(
        self,
        field: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"field": field}
        if extra:
            details.update(extra)
        super().__init__(422, "VALIDATION_ERROR", message, details)


class LoreEntrySourceCrossWorldError(LoreEntryError):
    """来源文档不属于该世界（422）。"""

    def __init__(
        self,
        document_id: str,
        message: str = "引用的来源文档不属于当前世界。",
    ) -> None:
        super().__init__(422, "SOURCE_CROSS_WORLD", message, {"document_id": document_id})


class LoreEntrySourceDeletedError(LoreEntryError):
    """来源文档已被软删除（422）。"""

    def __init__(
        self,
        document_id: str,
        message: str = "引用的来源文档已被删除，无法建立新的引用。",
    ) -> None:
        super().__init__(422, "SOURCE_DELETED", message, {"document_id": document_id})


class LoreEntrySourceChunkMismatchError(LoreEntryError):
    """来源切片不属于该文档或不存在（422）。"""

    def __init__(
        self,
        chunk_id: str,
        message: str = "指定的切片不属于该文档或不存在。",
    ) -> None:
        super().__init__(422, "SOURCE_CHUNK_MISMATCH", message, {"chunk_id": chunk_id})


class LoreEntrySourceOutOfRangeError(LoreEntryError):
    """引用区间越界、反向或为空（422）。"""

    def __init__(
        self,
        start_char: int,
        end_char: int,
        chunk_length: int,
        message: str = "引用区间越界或反向，必须满足 0 <= start_char < end_char 且位于块边界内。",
    ) -> None:
        super().__init__(
            422,
            "SOURCE_OUT_OF_RANGE",
            message,
            {
                "start_char": start_char,
                "end_char": end_char,
                "chunk_length": chunk_length,
            },
        )


class LoreEntrySourceInvalidOffsetError(LoreEntryError):
    """起止偏移切断了 UTF-16 代理对（422）。"""

    def __init__(
        self,
        start_char: int,
        end_char: int,
        message: str = "引用的起止偏移切开了 UTF-16 代理对，不是合法的字符边界。",
    ) -> None:
        super().__init__(
            422,
            "SOURCE_INVALID_OFFSET",
            message,
            {"start_char": start_char, "end_char": end_char},
        )
