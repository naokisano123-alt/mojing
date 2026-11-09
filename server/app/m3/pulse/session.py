"""M3.2 内存模型会话绑定与在线活动租约管理。

契约要求：
1. 世界模拟可独立连接（independent）或复用当前叙事会话（reuse_narration），也可注入 Fake 客户端（fake）。
2. 绑定关系只保存在内存中，严禁将 API Key 或 session_id 写入 SQLite、日志或快照。
3. 复用模式只在内存中引用有效叙事会话，不改变叙事会话的所有权；关闭脉动或解绑脉动绝不删除被引用的叙事会话。
4. 在线活动租约（ActivityLeaseRegistry）：离线或租约过期时不发起付费模型请求。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable

import httpx

from app.m3.pulse.client import (
    FakePulseModelClient,
    OpenAIPulseModelClient,
    PulseModelClientProtocol,
)
from app.m3.pulse.errors import PulseError
from app.m3.pulse.types import SessionMode
from app.model_gateway.errors import GatewayError
from app.model_gateway.store import ModelSessionStore


def _default_now() -> float:
    return time.time()


@dataclass
class _PulseBinding:
    """单个世界实例的内存模型绑定（严禁序列化落盘）。"""

    instance_id: str
    mode: SessionMode
    # session_id 显式标记 repr=False，防止在日志或 repr 中外泄
    session_id: str | None = field(default=None, repr=False)
    fake_client: PulseModelClientProtocol | None = field(default=None, repr=False)


class ActivityLeaseRegistry:
    """内存级前台在线活动租约注册表。

    用于判断玩家当前是否在线活跃：
    - 前台请求或心跳调用 touch(instance_id) 续期活动租约；
    - 玩家主动离开或切后台可调用 mark_offline(instance_id)；
    - 后台 Worker 在发起付费模型请求及提交前检查 is_active(instance_id)，离线时暂停发请求。
    """

    DEFAULT_TTL_SECONDS: float = 180.0

    def __init__(
        self,
        *,
        default_ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.default_ttl_seconds = float(default_ttl_seconds)
        self._clock = clock or _default_now
        self._leases: dict[str, float] = {}

    def touch(self, instance_id: str, *, ttl_seconds: float | None = None) -> float:
        """刷新指定实例的在线活动租约到期时间戳。"""
        now = self._clock()
        ttl = float(ttl_seconds) if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = now + max(1.0, ttl)
        self._leases[instance_id] = expires_at
        return expires_at

    def mark_offline(self, instance_id: str) -> None:
        """显式标记实例离线。"""
        self._leases.pop(instance_id, None)

    def is_active(self, instance_id: str, *, now: float | None = None) -> bool:
        """检查实例当前是否处于有效在线活动租约内。"""
        current = float(now) if now is not None else self._clock()
        expires_at = self._leases.get(instance_id)
        if expires_at is None:
            return False
        if current >= expires_at:
            self._leases.pop(instance_id, None)
            return False
        return True

    def clear(self) -> None:
        self._leases.clear()


class PulseSessionRouter:
    """世界脉动内存会话路由器（管理独立会话池与叙事会话复用）。"""

    def __init__(
        self,
        *,
        narration_store: ModelSessionStore | None = None,
        pulse_store: ModelSessionStore | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._narration_store = narration_store
        self._pulse_store = pulse_store or ModelSessionStore(
            http_transport=http_transport,
            timeout_seconds=timeout_seconds,
        )
        self._http_transport = http_transport
        self._timeout_seconds = timeout_seconds
        self._bindings: dict[str, _PulseBinding] = {}

    def __repr__(self) -> str:
        return f"<PulseSessionRouter bound_instances={len(self._bindings)}>"

    @property
    def pulse_store(self) -> ModelSessionStore:
        return self._pulse_store

    @property
    def narration_store(self) -> ModelSessionStore | None:
        return self._narration_store

    def bind_independent(
        self,
        instance_id: str,
        *,
        base_url: str,
        model: str,
        api_key: str,
    ) -> dict[str, str | None]:
        """为指定实例创建并绑定独立的世界脉动内存模型会话。

        若该实例此前已绑定过独立会话，先安全释放旧的独立会话（绝不影响叙事会话池）。
        """
        self._cleanup_owned_session(instance_id)
        created = self._pulse_store.create(base_url=base_url, model=model, api_key=api_key)
        sid = str(created["session_id"])
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="independent",
            session_id=sid,
        )
        return {
            "instance_id": instance_id,
            "mode": "independent",
            "model": created.get("model"),
            "expires_at": created.get("expires_at"),
        }

    def bind_independent_session_id(self, instance_id: str, pulse_session_id: str) -> dict[str, str]:
        """将已存在于 pulse_store 中的独立会话绑定到指定实例。"""
        if not isinstance(pulse_session_id, str) or not pulse_session_id.strip():
            raise PulseError("MODEL_SESSION_INVALID", "脉动模型会话标识不能为空。", status=422)
        # 先验证会话有效
        client = self._pulse_store.get_client(pulse_session_id.strip())
        self._cleanup_owned_session(instance_id)
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="independent",
            session_id=pulse_session_id.strip(),
        )
        return {
            "instance_id": instance_id,
            "mode": "independent",
            "model": client.model,
        }

    def bind_reuse_narration(self, instance_id: str, narration_session_id: str) -> dict[str, str]:
        """在内存中绑定复用现有的前台叙事模型会话。

        - 只保存内存引用，不转移叙事会话所有权；
        - 若此前该实例持有独立脉动会话，先清理独立脉动会话；
        - 关闭脉动或解绑时，绝不删除 narration_store 中的会话。
        """
        if self._narration_store is None:
            raise PulseError(
                code="NARRATION_STORE_UNAVAILABLE",
                message="当前未注入前台叙事会话存储，无法复用叙事会话。",
                status=409,
            )
        if not isinstance(narration_session_id, str) or not narration_session_id.strip():
            raise PulseError("MODEL_SESSION_INVALID", "叙事模型会话标识不能为空。", status=422)

        sid = narration_session_id.strip()
        try:
            client = self._narration_store.get_client(sid)
        except GatewayError as exc:
            raise PulseError(exc.code, exc.message, status=exc.status) from None

        self._cleanup_owned_session(instance_id)
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="reuse_narration",
            session_id=sid,
        )
        return {
            "instance_id": instance_id,
            "mode": "reuse_narration",
            "model": client.model,
        }

    def bind_fake(
        self,
        instance_id: str,
        client: PulseModelClientProtocol | None = None,
    ) -> dict[str, str]:
        """为指定实例绑定 Fake 模拟客户端（不发外网付费请求）。"""
        self._cleanup_owned_session(instance_id)
        fake = client or FakePulseModelClient()
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="fake",
            fake_client=fake,
        )
        return {
            "instance_id": instance_id,
            "mode": "fake",
            "model": getattr(fake, "model", "fake-pulse-v1"),
        }

    def unbind(self, instance_id: str) -> None:
        """解除指定实例的模型会话绑定。

        - 若为 independent 模式，同时从 pulse_store 删除其独占会话；
        - 若为 reuse_narration 模式，仅移除内存引用，绝不删除 narration_store 中的原叙事会话。
        """
        self._cleanup_owned_session(instance_id)
        self._bindings.pop(instance_id, None)

    def _cleanup_owned_session(self, instance_id: str) -> None:
        existing = self._bindings.get(instance_id)
        if existing is not None and existing.mode == "independent" and existing.session_id:
            self._pulse_store.delete(existing.session_id)

    def resolve_client(self, instance_id: str) -> tuple[PulseModelClientProtocol, SessionMode]:
        """解析指定实例当前可用的模型客户端。

        若未绑定、服务重启后内存丢失或底层会话已过期，抛出 PulseError(code="MODEL_SESSION_EXPIRED" / "MODEL_SESSION_UNBOUND")，
        供 Worker 将任务安全转入 waiting_for_model。
        """
        binding = self._bindings.get(instance_id)
        if binding is None or binding.mode == "unbound":
            raise PulseError(
                code="MODEL_SESSION_UNBOUND",
                message="世界脉动尚未绑定模型会话（或服务重启后需重新连接）。",
                status=409,
            )

        if binding.mode == "fake":
            return binding.fake_client or FakePulseModelClient(), "fake"

        if binding.mode == "independent":
            if not binding.session_id:
                raise PulseError("MODEL_SESSION_EXPIRED", "独立脉动模型会话已失效。", status=409)
            try:
                gw_client = self._pulse_store.get_client(binding.session_id)
            except GatewayError as exc:
                self._bindings.pop(instance_id, None)
                raise PulseError(exc.code, exc.message, status=exc.status) from None
            return (
                OpenAIPulseModelClient(
                    base_url=gw_client.base_url,
                    model=gw_client.model,
                    api_key=gw_client._api_key,
                    transport=gw_client.transport or self._http_transport,
                    timeout_seconds=gw_client.timeout_seconds,
                    max_response_bytes=gw_client.max_response_bytes,
                ),
                "independent",
            )

        if binding.mode == "reuse_narration":
            if self._narration_store is None or not binding.session_id:
                raise PulseError("MODEL_SESSION_EXPIRED", "复用的叙事模型会话不可用。", status=409)
            try:
                gw_client = self._narration_store.get_client(binding.session_id)
            except GatewayError as exc:
                # 叙事会话已过期或被前台主动删除，移除绑定引用并通知等待重连
                self._bindings.pop(instance_id, None)
                raise PulseError(exc.code, exc.message, status=exc.status) from None
            return (
                OpenAIPulseModelClient(
                    base_url=gw_client.base_url,
                    model=gw_client.model,
                    api_key=gw_client._api_key,
                    transport=gw_client.transport or self._http_transport,
                    timeout_seconds=gw_client.timeout_seconds,
                    max_response_bytes=gw_client.max_response_bytes,
                ),
                "reuse_narration",
            )

        raise PulseError("MODEL_SESSION_UNBOUND", "未知的脉动模型会话模式。", status=409)

    def status_for(self, instance_id: str) -> dict[str, object]:
        """返回脱敏的会话绑定状态（绝不包含 session_id 或 api_key）。"""
        binding = self._bindings.get(instance_id)
        if binding is None:
            return {"mode": "unbound", "ready": False, "model": None}
        if binding.mode == "fake":
            return {
                "mode": "fake",
                "ready": True,
                "model": getattr(binding.fake_client, "model", "fake-pulse-v1"),
            }
        try:
            client, mode = self.resolve_client(instance_id)
            return {"mode": mode, "ready": True, "model": client.model}
        except PulseError:
            return {"mode": binding.mode, "ready": False, "model": None}

    def clear(self) -> None:
        """清空脉动自有绑定与独立会话池（绝不清空复用的 narration_store）。"""
        self._bindings.clear()
        self._pulse_store.clear()
