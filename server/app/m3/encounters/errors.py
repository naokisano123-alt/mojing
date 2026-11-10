"""M3.3 / M3.6 玩家主动查证、交谈与消息后续行动异常定义（M3 Wave 3）。

所有对外错误消息与 details 均经过白名单脱敏，绝不回显未公开的权威事件标题、
隐藏事件 ID 或其他 NPC 未透露的私密认知内容。
"""

from __future__ import annotations

from typing import Any

from ...errors import ApiError

_SAFE_DETAIL_KEYS = frozenset(
    {
        "field",
        "channel",
        "option_kind",
        "outcome",
        "reason_code",
        "npc_id",
        "player_location",
        "npc_location",
        "expected_revision",
        "current_revision",
        "expected_world_revision",
        "current_world_revision",
        "expected_world_minute",
        "current_world_minute",
        "limit",
        "max_units",
    }
)


class EncounterError(ApiError):
    """玩家主动查证与人物交谈模块业务异常。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details: dict[str, Any] | None = None
        if details:
            safe_details = {k: v for k, v in details.items() if k in _SAFE_DETAIL_KEYS}
            if not safe_details:
                safe_details = None
        super().__init__(status=status, code=code, message=message, details=safe_details)
