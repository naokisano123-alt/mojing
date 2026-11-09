"""M3.3 人物知识、传播与玩家视角模块异常定义。

所有对外错误消息与 details 均经过脱敏，绝不回显未公开的权威事件标题、
隐藏事件 ID 或其他持有人的私密认知内容，防止通过报错信息探测隐藏事件。
"""

from __future__ import annotations

from typing import Any

from ...errors import ApiError


class KnowledgeError(ApiError):
    """知识与信息传播业务异常。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        # 确保 details 不泄露隐藏事件标题或内部跨实例详情
        safe_details: dict[str, Any] | None = None
        if details:
            safe_details = {
                k: v
                for k, v in details.items()
                if k in {"field", "channel", "expected_revision", "current_revision", "limit"}
            }
            if not safe_details:
                safe_details = None
        super().__init__(status=status, code=code, message=message, details=safe_details)
