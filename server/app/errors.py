"""契约 §2.6 的统一错误形状：{"error": {"code", "message", "details"}}。"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """业务错误。status 为 HTTP 码，code 为契约第 6 节的机器码。"""

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
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}
