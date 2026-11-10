"""M3 Wave 3 可续玩迁移、已删除历史来源审计与快照性能模块异常体系。

遵循 `server/app/errors.py` 的统一错误格式：
`{"error": {"code": str, "message": str, "details": dict | None}}`
"""

from __future__ import annotations

from typing import Any

from app.errors import ApiError


class PortabilityError(ApiError):
    """M3 可续玩迁移与快照兼容基础异常。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status=status, code=code, message=message, details=details)


class TransferInvalidError(PortabilityError):
    """迁移文件损坏、格式不受支持、包含伪造引用或历史不完整（422 WORLD_TRANSFER_INVALID）。"""

    def __init__(
        self,
        message: str = "迁移文件损坏或格式不受支持，请选择墨境导出的世界观、存档或 ZIP。",
        *,
        reason_code: str = "TRANSFER_INVALID",
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = {"reason_code": reason_code, **(details or {})}
        super().__init__(422, "WORLD_TRANSFER_INVALID", message, payload)


class TransferMismatchError(PortabilityError):
    """世界观和存档版本不匹配（422 WORLD_TRANSFER_MISMATCH）。"""

    def __init__(
        self,
        message: str = "世界观和存档版本不同，请选择同一次导出的文件。",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, "WORLD_TRANSFER_MISMATCH", message, details)


class TransferSettingsRequiredError(PortabilityError):
    """单独导入存档时找不到匹配的世界观版本（422 WORLD_TRANSFER_SETTINGS_REQUIRED）。"""

    def __init__(
        self,
        message: str = "找不到匹配版本，请选择这份存档对应的世界观文件一起导入。",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, "WORLD_TRANSFER_SETTINGS_REQUIRED", message, details)


class TransferLimitExceededError(PortabilityError):
    """迁移文件总大小或解压大小超过上限（413 WORLD_TRANSFER_LIMIT）。"""

    def __init__(
        self,
        message: str = "迁移文件的总大小或解压大小超过 128 MiB。",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(413, "WORLD_TRANSFER_LIMIT", message, details)


class ForgedHistoricalReferenceError(TransferInvalidError):
    """存档事件或检查点引用了从未在该世界存在过的伪造来源 ID（422 WORLD_TRANSFER_INVALID）。"""

    def __init__(
        self,
        source_id: str,
        message: str = "存档包含未授权或伪造的历史来源引用，拒绝导入。",
    ) -> None:
        super().__init__(
            message,
            reason_code="FORGED_HISTORICAL_SOURCE",
            details={"source_id": source_id},
        )


class CrossWorldSourceBorrowError(TransferInvalidError):
    """存档试图借用其他世界的同名/同 ID 资料作为来源引用（422 WORLD_TRANSFER_INVALID）。"""

    def __init__(
        self,
        source_id: str,
        other_world_id: str = "",
        message: str = "禁止跨世界借用其他世界的设定或文档分块 ID。",
    ) -> None:
        details: dict[str, Any] = {"source_id": source_id}
        if other_world_id:
            details["other_world_id"] = other_world_id
        super().__init__(
            message,
            reason_code="CROSS_WORLD_SOURCE_FORBIDDEN",
            details=details,
        )


class HistoricalSourceNotCanonError(PortabilityError):
    """尝试将已删除的历史审计来源当作当前活跃 Canon 使用（409 HISTORICAL_SOURCE_NOT_CANON）。"""

    def __init__(
        self,
        source_id: str,
        message: str = "该引用为已删除资料的历史归档墓碑，不可作为当前活跃 Canon 使用。",
    ) -> None:
        super().__init__(
            409,
            "HISTORICAL_SOURCE_NOT_CANON",
            message,
            {"source_id": source_id, "selectable_as_canon": False},
        )
