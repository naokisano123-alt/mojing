"""内存会话存储与生命周期管理（ModelSessionStore）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import secrets
from typing import Any, Callable

import httpx

from app.model_gateway.client import OpenAIModelClient
from app.model_gateway.errors import GatewayError
from app.model_gateway.url import validate_and_normalize_base_url


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc_iso(dt: datetime) -> str:
    """格式化为标准的 UTC ISO 8601 字符串（...Z）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ModelSession:
    """内存中的模型会话对象。

    Key 字段显式标记 repr=False，杜绝在日志、repr 或调试输出中外泄。
    """

    session_id: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    created_at: datetime
    last_accessed_at: datetime
    expires_at: datetime | None


class ModelSessionStore:
    """模型会话存储，全内存运行，不落盘、不持久化。

    - 30 分钟无操作 TTL；
    - 最多 8 个活跃会话；
    - secrets 加密随机 ID；
    - get / test 成功时自动刷新 TTL；
    - delete 幂等；过期会话在获取时删除并返回 409。
    """

    DEFAULT_TTL_SECONDS: int = 1800  # 30 分钟
    MAX_SESSIONS: int = 8

    def __init__(
        self,
        ttl_seconds: int | None = DEFAULT_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
        clock: Callable[[], datetime] | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock or _utc_now
        self.http_transport = http_transport
        self.timeout_seconds = timeout_seconds
        self._sessions: dict[str, ModelSession] = {}

    def _purge_expired(self, now: datetime) -> None:
        """清理所有已过期的死会话。"""
        expired_ids = [
            sid for sid, s in self._sessions.items() if s.expires_at is not None and now >= s.expires_at
        ]
        for sid in expired_ids:
            self._sessions.pop(sid, None)

    def create(self, base_url: str, model: str, api_key: str) -> dict[str, str | None]:
        """创建新模型会话，返回契约响应字典（不含 API Key）。"""
        now = self._clock()
        self._purge_expired(now)

        if len(self._sessions) >= self.max_sessions:
            raise GatewayError(
                code="MODEL_SESSION_CAPACITY_EXCEEDED",
                message=f"活跃模型会话已达上限（最多 {self.max_sessions} 个），请先删除闲置会话后再试。",
                status=429,
            )

        clean_url = validate_and_normalize_base_url(base_url)
        if not isinstance(model, str) or not model.strip():
            raise GatewayError("INVALID_MODEL", "模型名称不能为空。", status=422)
        if not isinstance(api_key, str) or not api_key.strip():
            raise GatewayError("INVALID_API_KEY", "API Key 不能为空。", status=422)

        session_id = secrets.token_urlsafe(24)
        expires_at = now + timedelta(seconds=self.ttl_seconds) if self.ttl_seconds is not None else None

        session = ModelSession(
            session_id=session_id,
            base_url=clean_url,
            model=model.strip(),
            api_key=api_key.strip(),
            created_at=now,
            last_accessed_at=now,
            expires_at=expires_at,
        )
        self._sessions[session_id] = session

        return {
            "session_id": session.session_id,
            "model": session.model,
            "expires_at": _format_utc_iso(session.expires_at) if session.expires_at is not None else None,
        }

    def delete(self, session_id: str) -> None:
        """删除会话，保证幂等。"""
        self._sessions.pop(session_id, None)

    def get_client(self, session_id: str) -> OpenAIModelClient:
        """获取模型客户端，校验 TTL 并刷新过期时间。

        若会话不存在或已过期，删除并报 409 MODEL_SESSION_EXPIRED。
        返回的客户端不暴露 repr 中的 Key。
        """
        now = self._clock()
        session = self._sessions.get(session_id)
        if session is None:
            raise GatewayError(
                code="MODEL_SESSION_EXPIRED",
                message="模型会话不存在或已失效，请重新配置。",
                status=409,
            )

        if session.expires_at is not None and now >= session.expires_at:
            self._sessions.pop(session_id, None)
            raise GatewayError(
                code="MODEL_SESSION_EXPIRED",
                message="模型会话已过期，请重新配置。",
                status=409,
            )

        # 刷新 TTL
        session.last_accessed_at = now
        if self.ttl_seconds is not None:
            session.expires_at = now + timedelta(seconds=self.ttl_seconds)

        return OpenAIModelClient(
            base_url=session.base_url,
            model=session.model,
            api_key=session.api_key,
            transport=self.http_transport,
            timeout_seconds=self.timeout_seconds,
        )

    async def test(self, session_id: str) -> dict[str, Any]:
        """对指定会话进行快速连通性测试，成功返回 {"ok": True, "model": ...}。"""
        client = self.get_client(session_id)
        return await client.test()

    def clear(self) -> None:
        """清空所有会话，供应用关闭时清理资源。"""
        self._sessions.clear()

    @property
    def active_count(self) -> int:
        """返回未过期的活跃会话数。"""
        now = self._clock()
        self._purge_expired(now)
        return len(self._sessions)
