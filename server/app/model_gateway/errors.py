"""M2-A 模型网关领域异常定义。"""

from __future__ import annotations

from typing import Any

from app.errors import ApiError


class GatewayError(ApiError):
    """模型网关领域异常，自动兼容 FastAPI 统一错误处理。"""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 502,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status=status, code=code, message=message, details=details)

    def __repr__(self) -> str:
        return f"<GatewayError code={self.code!r} status={self.status} message={self.message!r}>"
