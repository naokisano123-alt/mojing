"""M3.4 NPC 自主行动、行程与动态模拟深度异常体系（M3 Wave 3）。

遵循 `server/app/errors.py` 的统一错误格式：
`{"error": {"code": str, "message": str, "details": dict | None}}`
"""

from __future__ import annotations

from typing import Any

from app.errors import ApiError


class NpcError(ApiError):
    """NPC 模块基础异常。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status=status, code=code, message=message, details=details)


class InstanceNotFoundError(NpcError):
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


class NpcNotFoundError(NpcError):
    """NPC 不存在（404）。"""

    def __init__(
        self,
        instance_id: str,
        npc_id: str,
        message: str = "指定的 NPC 在当前世界实例中不存在。",
    ) -> None:
        super().__init__(
            404,
            "NPC_NOT_FOUND",
            message,
            {"instance_id": instance_id, "npc_id": npc_id},
        )


class NpcRevisionConflictError(NpcError):
    """NPC 状态版本乐观并发冲突（409）。"""

    def __init__(
        self,
        instance_id: str,
        npc_id: str,
        expected_revision: int,
        current_revision: int,
        message: str = "NPC 版本冲突：expected_npc_revision 与当前版本不一致。",
    ) -> None:
        super().__init__(
            409,
            "NPC_REVISION_CONFLICT",
            message,
            {
                "instance_id": instance_id,
                "npc_id": npc_id,
                "expected_npc_revision": expected_revision,
                "current_npc_revision": current_revision,
            },
        )


class WorldRevisionConflictError(NpcError):
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


class NpcNotDueError(NpcError):
    """NPC 尚未到达下一次到期时间，禁止提前变更（409）。"""

    def __init__(
        self,
        instance_id: str,
        npc_id: str,
        world_minute: int,
        next_due_minute: int | None,
        message: str = "该 NPC 尚未到达到期时间，当前世界分钟不得提前推进其行动。",
    ) -> None:
        super().__init__(
            409,
            "NPC_NOT_DUE",
            message,
            {
                "instance_id": instance_id,
                "npc_id": npc_id,
                "world_minute": world_minute,
                "next_due_minute": next_due_minute,
            },
        )


class NpcValidationError(NpcError):
    """NPC 提议或字段结构校验失败（422）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, code, message, details)


class DuplicateNpcTaskError(NpcValidationError):
    """重复的 NPC 操作、重复创建的任务或无进展重复提交（422）。"""

    def __init__(
        self,
        npc_id: str,
        task_id: str = "",
        code: str = "DUPLICATE_NPC_TASK",
        message: str = "检测到重复的 NPC 任务或同一批次重复行动提议。",
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"npc_id": npc_id}
        if task_id:
            details["task_id"] = task_id
        if extra:
            details.update(extra)
        super().__init__(code, message, details)


class InvalidCausalReferenceError(NpcValidationError):
    """NPC 行动未引用实例内已存在的合法原因（事件、知情记录或授权来源）（422）。"""

    def __init__(
        self,
        npc_id: str,
        reference_id: str = "",
        code: str = "CAUSAL_REFERENCE_INVALID",
        message: str = "NPC 行动必须引用当前实例内已存在的事件、自身已知事实或授权 Canon 来源。",
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"npc_id": npc_id}
        if reference_id:
            details["reference_id"] = reference_id
        if extra:
            details.update(extra)
        super().__init__(code, message, details)


class SourceFabricatedError(NpcValidationError):
    """引用了未授权或伪造的 Canon 来源（422）。"""

    def __init__(
        self,
        source_id: str,
        message: str = "NPC 提议包含未授权或伪造的 Canon 来源引用。",
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"source_id": source_id}
        if extra:
            details.update(extra)
        super().__init__("SOURCE_FABRICATED", message, details)


class CrossInstanceReferenceError(NpcValidationError):
    """跨世界实例引用 NPC、事件或事实（422）。"""

    def __init__(
        self,
        instance_id: str,
        referenced_id: str,
        other_instance_id: str | None = None,
        message: str = "禁止跨世界实例引用 NPC、事件或知识状态。",
    ) -> None:
        details: dict[str, Any] = {
            "instance_id": instance_id,
            "referenced_id": referenced_id,
        }
        if other_instance_id is not None:
            details["other_instance_id"] = other_instance_id
        super().__init__("CROSS_INSTANCE_REFERENCE", message, details)


class PlayerLedgerProtectedError(NpcValidationError):
    """后台 NPC 行动尝试直接修改受保护的玩家个人账本（金钱、背包、属性等）（422）。"""

    def __init__(
        self,
        field: str,
        message: str = "NPC 自主行动严禁直接修改玩家金钱、背包、装备或角色属性账本。",
    ) -> None:
        super().__init__("PLAYER_LEDGER_PROTECTED", message, {"field": field})


class PlayerKnowledgeProtectedError(NpcValidationError):
    """后台 NPC 行动尝试绕过传播系统直接写入玩家知情事实（422）。"""

    def __init__(
        self,
        field: str,
        message: str = "NPC 自主行动严禁直接记入玩家知情事实，玩家获知信息须走知识传播或主动查证边界。",
    ) -> None:
        super().__init__("PLAYER_KNOWLEDGE_PROTECTED", message, {"field": field})


class ArbitraryPathForbiddenError(NpcValidationError):
    """提议尝试执行任意 JSON 路径写入或未受限补丁（422）。"""

    def __init__(
        self,
        field: str,
        message: str = "禁止任意 JSON 路径写入或非受限字段操作。",
    ) -> None:
        super().__init__("ARBITRARY_PATH_FORBIDDEN", message, {"field": field})


class SnapshotRestoreError(NpcError):
    """NPC 快照格式不合法或恢复失败（422）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(422, code, message, details)
