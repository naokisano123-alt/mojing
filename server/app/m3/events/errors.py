"""M3.1 / M3.5 世界事件账本与因果校验异常体系（M3 Wave 2 扩展）。

遵循 `server/app/errors.py` 的统一错误格式：
`{"error": {"code": str, "message": str, "details": dict | None}}`
"""

from __future__ import annotations

from typing import Any

from app.errors import ApiError


class EventError(ApiError):
    """世界事件模块基础异常。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status=status, code=code, message=message, details=details)


class EventNotFoundError(EventError):
    """事件不存在（404）。"""

    def __init__(
        self,
        instance_id: str,
        event_id: str,
        message: str = "指定的世界事件不存在。",
    ) -> None:
        super().__init__(
            404,
            "EVENT_NOT_FOUND",
            message,
            {"instance_id": instance_id, "event_id": event_id},
        )


class InstanceNotFoundError(EventError):
    """世界实例不存在（404）。"""

    def __init__(
        self,
        instance_id: str,
        message: str = "指定的世界实例不存在。",
    ) -> None:
        super().__init__(
            404,
            "INSTANCE_NOT_FOUND",
            message,
            {"instance_id": instance_id},
        )


class EventRevisionConflictError(EventError):
    """事件版本乐观并发冲突（409）。"""

    def __init__(
        self,
        instance_id: str,
        event_id: str,
        expected_revision: int,
        current_revision: int,
        message: str = "事件版本冲突：expected_event_revision 与当前版本不一致。",
    ) -> None:
        super().__init__(
            409,
            "EVENT_REVISION_CONFLICT",
            message,
            {
                "instance_id": instance_id,
                "event_id": event_id,
                "expected_event_revision": expected_revision,
                "current_event_revision": current_revision,
            },
        )


class WorldRevisionConflictError(EventError):
    """世界实例版本（world_instances.revision / world_revision）并发冲突（409）。"""

    def __init__(
        self,
        instance_id: str,
        expected_world_revision: int,
        current_world_revision: int,
        message: str = "世界实例版本冲突：base_world_revision 与当前已确认版本不一致。",
    ) -> None:
        super().__init__(
            409,
            "WORLD_REVISION_CONFLICT",
            message,
            {
                "instance_id": instance_id,
                "expected_world_revision": expected_world_revision,
                "current_world_revision": current_world_revision,
            },
        )


class PulseSourceRequiredError(EventError):
    """世界缺少已确认的设定条目或有效文档分块，阻塞初始局势生成（409/422）。"""

    def __init__(
        self,
        world_id: str,
        instance_id: str = "",
        message: str = "当前世界没有可用的已确认设定条目或导入资料，无法生成初始事件线。",
        *,
        status: int = 409,
    ) -> None:
        details: dict[str, Any] = {"world_id": world_id}
        if instance_id:
            details["instance_id"] = instance_id
        super().__init__(status, "PULSE_SOURCE_REQUIRED", message, details)


class EventValidationError(EventError):
    """事件提议或字段结构校验失败（422）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, code, message, details)


class SourceFabricatedError(EventValidationError):
    """模型或提议引用了未授权或伪造的 Canon 来源（422）。"""

    def __init__(
        self,
        source_id: str,
        message: str = "提议包含未授权或伪造的来源引用。",
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"source_id": source_id}
        if extra:
            details.update(extra)
        super().__init__("SOURCE_FABRICATED", message, details)


class CrossInstanceReferenceError(EventValidationError):
    """跨世界实例引用事件或实体（422）。"""

    def __init__(
        self,
        instance_id: str,
        referenced_id: str,
        other_instance_id: str | None = None,
        message: str = "禁止跨世界实例引用事件或状态。",
    ) -> None:
        details: dict[str, Any] = {
            "instance_id": instance_id,
            "referenced_id": referenced_id,
        }
        if other_instance_id is not None:
            details["other_instance_id"] = other_instance_id
        super().__init__("CROSS_INSTANCE_REFERENCE", message, details)


class CausalCycleError(EventValidationError):
    """事件自引用或因果有向图出现环路（422）。"""

    def __init__(
        self,
        code: str = "CAUSAL_CYCLE_DETECTED",
        message: str = "事件因果引用存在自引用或环路。",
        cycle_path: list[str] | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if cycle_path is not None:
            details["cycle_path"] = cycle_path
        super().__init__(code, message, details or None)


class PlayerLedgerProtectedError(EventValidationError):
    """后台事件提议尝试直接修改受保护的玩家个人账本（422）。"""

    def __init__(
        self,
        field: str,
        message: str = "后台世界事件严禁直接更改玩家金钱、背包、属性或个人账本。",
    ) -> None:
        super().__init__("PLAYER_LEDGER_PROTECTED", message, {"field": field})


class ArbitraryPathForbiddenError(EventValidationError):
    """提议尝试执行任意 JSON 路径写入或未受限补丁（422）。"""

    def __init__(
        self,
        field: str,
        message: str = "禁止任意 JSON 路径写入或非受限字段操作。",
    ) -> None:
        super().__init__("ARBITRARY_PATH_FORBIDDEN", message, {"field": field})


class TurnpointValidationError(EventValidationError):
    """重大历史转折（如战争、政权更替）违反阶段性发展或前置条件约束（422）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details)


class SnapshotRestoreError(EventError):
    """事件快照格式不合法或恢复失败（422）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, code, message, details)
