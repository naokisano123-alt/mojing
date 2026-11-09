"""M3.2 世界脉动（World Pulse）领域异常与敏感凭据脱敏工具。"""

from __future__ import annotations

import re
from typing import Any

from app.errors import ApiError

# 严禁进入 SQLite 队列、日志、快照或错误详情的敏感键名
_FORBIDDEN_CREDENTIAL_KEYS = frozenset({
    "api_key",
    "apikey",
    "secret_key",
    "access_token",
    "refresh_token",
    "authorization",
    "x-api-key",
    "session_id",
    "model_session_id",
    "narration_session_id",
    "pulse_session_id",
    "headers",
})

# 匹配常见密钥、Bearer 令牌与查询串凭据的脱敏正则
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|session[_-]?id)\s*[:=]\s*['\"]?[^\s,'\"};]+"),
    re.compile(r"https?://[^/\s:@]+:[^/\s:@]+@[^\s]+"),
)


def sanitize_text(text: str | None, *, max_length: int = 240) -> str:
    """对错误摘要、日志文本执行严格脱敏与长度截断，确保不暴露 Key 或会话 ID。"""
    if not text or not isinstance(text, str):
        return ""
    cleaned = text.strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3] + "..."
    return cleaned


def contains_forbidden_credential(value: Any, *, extra_secrets: tuple[str, ...] = ()) -> bool:
    """递归检查任意结构是否包含敏感键名或已知明文密钥。"""
    if isinstance(value, dict):
        for k, v in value.items():
            key_norm = str(k).strip().lower()
            if key_norm in _FORBIDDEN_CREDENTIAL_KEYS:
                return True
            if contains_forbidden_credential(v, extra_secrets=extra_secrets):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(contains_forbidden_credential(item, extra_secrets=extra_secrets) for item in value)
    if isinstance(value, str):
        for secret in extra_secrets:
            if secret and len(secret) >= 4 and secret in value:
                return True
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                return True
    return False


def assert_no_credentials(value: Any, *, context: str = "payload") -> None:
    """断言待持久化的数据不含任何 API Key、会话 ID 或认证请求头。"""
    if contains_forbidden_credential(value):
        raise PulseError(
            code="PULSE_CREDENTIAL_STORAGE_FORBIDDEN",
            message=f"{context} 中禁止包含 API Key、模型会话 ID 或密钥请求头。",
            status=422,
        )


class PulseError(ApiError):
    """世界脉动模块统一异常，所有消息自动经过脱敏处理。"""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_message = sanitize_text(message, max_length=300)
        safe_details: dict[str, Any] | None = None
        if details:
            safe_details = {}
            for k, v in details.items():
                if str(k).strip().lower() in _FORBIDDEN_CREDENTIAL_KEYS:
                    continue
                safe_details[k] = sanitize_text(v) if isinstance(v, str) else v
        super().__init__(status=status, code=code, message=safe_message, details=safe_details)

    def __repr__(self) -> str:
        return f"<PulseError code={self.code!r} status={self.status} message={self.message!r}>"
