"""M3 第四轮（Wave 4）导入设定与 AI 初始化领域异常定义。

遵循 `server/app/errors.py` 的 `ApiError(status, code, message)` 结构，
供纯函数校验器、资料抽取器、草案适配层与集成端统一捕获并映射为标准 HTTP 错误响应。
"""

from __future__ import annotations

from typing import Any

from ...errors import ApiError


class WorldAnalysisError(ApiError):
    """世界资料分析与 AI 初始化模块基础异常。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status, code, message)
        self.details: dict[str, Any] = details or {}


class AnalysisSourceRequiredError(WorldAnalysisError):
    """当前世界缺少可用的已确认设定条目或就绪导入文档（409）。"""

    def __init__(
        self,
        world_id: str,
        message: str = "当前世界没有可用的已确认设定条目或导入资料，无法启动 AI 初始局势分析。",
        *,
        status: int = 409,
    ) -> None:
        super().__init__(
            status,
            "ANALYSIS_SOURCE_REQUIRED",
            message,
            {"world_id": world_id},
        )
        self.world_id = world_id


class CrossWorldSourceError(WorldAnalysisError):
    """引用了不属于当前世界、已软删除或未确认的资料来源（403）。"""

    def __init__(
        self,
        world_id: str,
        source_id: str,
        owner_world_id: str = "",
        message: str | None = None,
    ) -> None:
        msg = message or (
            f"来源 {source_id!r} 不属于当前世界 ({world_id})，"
            f"实际归属 ({owner_world_id or '其他世界或已删除'})，拒绝越界引用。"
        )
        super().__init__(
            403,
            "CROSS_WORLD_SOURCE_FORBIDDEN",
            msg,
            {
                "world_id": world_id,
                "source_id": source_id,
                "owner_world_id": owner_world_id,
            },
        )
        self.world_id = world_id
        self.source_id = source_id
        self.owner_world_id = owner_world_id


class CrossInstanceAnalysisError(WorldAnalysisError):
    """引用了其他世界实例或跨实例标识（403）。"""

    def __init__(
        self,
        expected_instance_id: str,
        actual_ref: str,
        other_instance_id: str = "",
        message: str | None = None,
    ) -> None:
        msg = message or (
            f"检测到跨实例越界引用 {actual_ref!r}（当前实例: {expected_instance_id}，"
            f"目标归属: {other_instance_id or '其他实例'}）。"
        )
        super().__init__(
            403,
            "CROSS_INSTANCE_FORBIDDEN",
            msg,
            {
                "expected_instance_id": expected_instance_id,
                "actual_ref": actual_ref,
                "other_instance_id": other_instance_id,
            },
        )


class SourceFabricatedError(WorldAnalysisError):
    """引用了伪造、格式非法或不在本次分析授权范围内的来源 ID（422）。"""

    def __init__(
        self,
        source_id: str,
        message: str | None = None,
    ) -> None:
        msg = message or f"AI 分析提议引用了未授权或伪造的资料来源: {source_id!r}。"
        super().__init__(
            422,
            "SOURCE_FABRICATED",
            msg,
            {"source_id": source_id},
        )
        self.source_id = source_id


class IdentityCollisionError(WorldAnalysisError):
    """实例内人物或地点标识冲突、重名未消歧或非法身份复用（422）。"""

    def __init__(
        self,
        entity_id: str,
        code: str = "IDENTITY_COLLISION",
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        msg = message or f"实例内标识或重名消歧冲突: {entity_id!r}。"
        info = {"entity_id": entity_id, **(details or {})}
        super().__init__(422, code, msg, info)
        self.entity_id = entity_id


class UnpublicInfoLeakError(WorldAnalysisError):
    """角色未获知的设定、隐藏事件或 NPC 私密动机被混入玩家开局摘要（422）。"""

    def __init__(
        self,
        field_name: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        msg = message or f"玩家开局摘要中包含角色尚未获知的隐藏信息或后台私密设定 ({field_name})。"
        info = {"field": field_name, **(details or {})}
        super().__init__(422, "UNPUBLIC_INFO_LEAK_FORBIDDEN", msg, info)
        self.field_name = field_name


class PlayerLedgerProtectedError(WorldAnalysisError):
    """分析提议试图直接修改玩家金钱、背包、属性等个人账本或执行任意路径写入（422）。"""

    def __init__(
        self,
        field_name: str,
        code: str = "PLAYER_LEDGER_PROTECTED",
        message: str | None = None,
    ) -> None:
        msg = message or f"AI 初始局势分析严禁直接修改玩家个人账本或角色属性字段: {field_name!r}。"
        super().__init__(422, code, msg, {"field": field_name})
        self.field_name = field_name


class AnalysisStateConflictError(WorldAnalysisError):
    """重复分析请求参数冲突、草案状态不合法或世界实例版本冲突（409）。"""

    def __init__(
        self,
        code: str = "ANALYSIS_STATE_CONFLICT",
        message: str = "分析请求或草案状态冲突，请刷新后重试。",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(409, code, message, details)


class AnalysisValidationError(WorldAnalysisError):
    """AI 初始化结构、时间、依据或关联校验失败（422）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        status: int = 422,
    ) -> None:
        super().__init__(status, code, message, details)
