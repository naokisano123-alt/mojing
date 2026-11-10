"""M3.2 内存模型会话绑定与在线活动租约管理。

契约要求（M3_WAVE1_API.md & M3_WAVE2_API.md）：
1. 世界模拟可独立连接（independent）或复用当前叙事会话（reuse_narration），也可注入 Fake 客户端（fake）。
2. 绑定关系只保存在内存中，严禁将 API Key 或 session_id 写入 SQLite、日志、用量、快照或导出文件。
3. 复用模式只在内存中引用有效叙事会话，不改变叙事会话的所有权；关闭脉动、解绑脉动或退出绝不删除被引用的叙事会话。
4. 在线活动租约（ActivityLeaseRegistry）：前台可见旅程触发续期，离开或切后台立即失效，心跳缺失由可配置短租约兜底；离线或租约过期时不发起付费模型请求。
5. 代次计数（generation）：每次绑定变更或离线状态切换均递增代次，供 Worker 提交前第二道门拦截旧代次在途响应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable

import httpx

from app.m3.pulse.client import (
    FakePulseModelClient,
    OpenAIPulseModelClient,
    PulseModelClientProtocol,
)
from app.m3.pulse.errors import PulseError
from app.m3.pulse.types import SessionMode
from app.model_gateway.client import OpenAIModelClient
from app.model_gateway.errors import GatewayError
from app.model_gateway.store import ModelSessionStore
from app.model_gateway.url import validate_and_normalize_base_url


def _default_now() -> float:
    return time.time()


@dataclass
class _PulseBinding:
    """单个世界实例的内存模型绑定（严禁序列化落盘）。"""

    instance_id: str
    mode: SessionMode
    generation: int = 1
    model_hint: str | None = None
    last_error_code: str | None = None
    # session_id 显式标记 repr=False，防止在日志或 repr 中外泄
    session_id: str | None = field(default=None, repr=False)
    fake_client: PulseModelClientProtocol | None = field(default=None, repr=False)


class ActivityLeaseRegistry:
    """内存级前台在线活动租约注册表（在线闸门）。

    用于判断玩家当前是否在线活跃：
    - 前台可见旅程调用 touch(instance_id) 续期活动租约；
    - 玩家主动离开、切后台或切旅程可调用 mark_offline(instance_id) 立即失效；
    - 心跳缺失由可配置短租约（default_ttl_seconds）自动过期兜底；
    - 维护实例级活动代次（generation）：在同一活跃租约内续期保持代次不变；
      一旦发生离线（显式 mark_offline 或 TTL 超时过期）及重新上线，代次递增，阻止跨离线周期的旧在途请求提交。
    """

    DEFAULT_TTL_SECONDS: float = 45.0

    def __init__(
        self,
        *,
        default_ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.default_ttl_seconds = float(default_ttl_seconds)
        self._clock = clock or _default_now
        self._leases: dict[str, float] = {}
        self._generations: dict[str, int] = {}

    def _now(self, override: float | None = None) -> float:
        return float(override) if override is not None else self._clock()

    def touch(
        self,
        instance_id: str,
        *,
        ttl_seconds: float | None = None,
        now: float | None = None,
        exclusive: bool = False,
    ) -> float:
        """刷新指定实例的在线活动租约到期时间戳。

        - 若 exclusive=True（如手机端从旧旅程切换到新可见旅程），自动使其余实例的活动租约立即失效；
        - 若该实例此前处于离线或已过期状态，开始新在线周期时递增代次；若已在有效期内续期，则保持同一代次。
        """
        current = self._now(now)
        if exclusive:
            for other_id in list(self._leases.keys()):
                if other_id != instance_id:
                    self.mark_offline(other_id, now=current)

        was_active = self.is_active(instance_id, now=current)
        if not was_active:
            self._generations[instance_id] = self._generations.get(instance_id, 0) + 1

        ttl = float(ttl_seconds) if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = current + max(1.0, ttl)
        self._leases[instance_id] = expires_at
        return expires_at

    def mark_offline(self, instance_id: str, *, now: float | None = None) -> None:
        """显式标记实例离线（离开页面、切后台或断网时立即失效并推进代次）。"""
        _ = self._now(now)
        if instance_id in self._leases:
            self._leases.pop(instance_id, None)
            self._generations[instance_id] = self._generations.get(instance_id, 0) + 1
        else:
            self._generations.setdefault(instance_id, 0)

    def is_active(self, instance_id: str, *, now: float | None = None) -> bool:
        """检查实例当前是否处于有效在线活动租约内（只读检查，若已超时则回收并推进代次）。"""
        current = self._now(now)
        expires_at = self._leases.get(instance_id)
        if expires_at is None:
            return False
        if current >= expires_at:
            self._leases.pop(instance_id, None)
            self._generations[instance_id] = self._generations.get(instance_id, 0) + 1
            return False
        return True

    def generation_for(self, instance_id: str, *, now: float | None = None) -> int:
        """获取指定实例当前的在线活动代次（先核验租约是否已自然过期）。"""
        self.is_active(instance_id, now=now)
        return self._generations.get(instance_id, 0)

    def remaining_ttl(self, instance_id: str, *, now: float | None = None) -> float:
        """返回指定实例剩余的在线租约秒数（离线时返回 0.0，不续期）。"""
        current = self._now(now)
        if not self.is_active(instance_id, now=current):
            return 0.0
        expires_at = self._leases.get(instance_id, current)
        return max(0.0, expires_at - current)

    def clear(self) -> None:
        self._leases.clear()
        self._generations.clear()


class PulseSessionRouter:
    """世界脉动内存会话路由器（管理独立会话池与叙事会话复用）。"""

    def __init__(
        self,
        *,
        narration_store: ModelSessionStore | None = None,
        pulse_store: ModelSessionStore | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 60.0,
        allowed_base_urls: set[str] | frozenset[str] | None = None,
        require_https: bool = False,
    ) -> None:
        self._narration_store = narration_store
        self._pulse_store = pulse_store or ModelSessionStore(
            http_transport=http_transport,
            timeout_seconds=timeout_seconds,
        )
        self._http_transport = http_transport
        self._timeout_seconds = timeout_seconds
        self._allowed_base_urls = (
            {validate_and_normalize_base_url(u) for u in allowed_base_urls}
            if allowed_base_urls is not None
            else None
        )
        self._require_https = bool(require_https)
        self._bindings: dict[str, _PulseBinding] = {}
        self._generations: dict[str, int] = {}

    def __repr__(self) -> str:
        return f"<PulseSessionRouter bound_instances={len(self._bindings)}>"

    @property
    def pulse_store(self) -> ModelSessionStore:
        return self._pulse_store

    @property
    def narration_store(self) -> ModelSessionStore | None:
        return self._narration_store

    def set_narration_store(self, narration_store: ModelSessionStore | None) -> None:
        """供宿主在运行时延迟注入前台叙事 ModelSessionStore。"""
        self._narration_store = narration_store

    def _bump_generation(self, instance_id: str) -> int:
        gen = self._generations.get(instance_id, 0) + 1
        self._generations[instance_id] = gen
        return gen

    def generation_for(self, instance_id: str) -> int:
        return self._generations.get(instance_id, 0)

    def _validate_url_policy(self, base_url: str) -> str:
        normalized = validate_and_normalize_base_url(base_url)
        if self._require_https and not normalized.startswith("https://"):
            raise PulseError(
                "MODEL_URL_HTTPS_REQUIRED",
                "托管模式下模型接口地址必须使用 HTTPS。",
                status=422,
            )
        if self._allowed_base_urls is not None and normalized not in self._allowed_base_urls:
            raise PulseError(
                "MODEL_URL_NOT_ALLOWED",
                "该模型服务商地址不在允许列表中。",
                status=403,
            )
        return normalized

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
        连接操作本身不启用脉动、不发起付费测试请求。
        """
        try:
            normalized_url = self._validate_url_policy(base_url)
            created = self._pulse_store.create(base_url=normalized_url, model=model, api_key=api_key)
        except GatewayError as exc:
            raise PulseError(exc.code, exc.message, status=exc.status) from None

        self._cleanup_owned_session(instance_id)
        sid = str(created["session_id"])
        gen = self._bump_generation(instance_id)
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="independent",
            generation=gen,
            model_hint=created.get("model"),
            last_error_code=None,
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
        sid = pulse_session_id.strip()
        try:
            client = self._pulse_store.get_client(sid)
        except GatewayError as exc:
            raise PulseError(exc.code, exc.message, status=exc.status) from None

        self._cleanup_owned_session(instance_id)
        gen = self._bump_generation(instance_id)
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="independent",
            generation=gen,
            model_hint=client.model,
            last_error_code=None,
            session_id=sid,
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
        - 关闭脉动、解绑或退出时，绝不删除 narration_store 中的会话。
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
        gen = self._bump_generation(instance_id)
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="reuse_narration",
            generation=gen,
            model_hint=client.model,
            last_error_code=None,
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
        model_name = str(getattr(fake, "model", "fake-pulse-v1"))
        gen = self._bump_generation(instance_id)
        self._bindings[instance_id] = _PulseBinding(
            instance_id=instance_id,
            mode="fake",
            generation=gen,
            model_hint=model_name,
            last_error_code=None,
            fake_client=fake,
        )
        return {
            "instance_id": instance_id,
            "mode": "fake",
            "model": model_name,
        }

    def mark_auth_failed(self, instance_id: str, *, error_code: str = "MODEL_AUTH_FAILED") -> None:
        """当后台请求遭遇 401/403 认证失败时失效当前脉动绑定，要求用户重新连接，绝不静默重试或切换 Key。"""
        binding = self._bindings.get(instance_id)
        if binding is None:
            return
        if binding.mode == "independent" and binding.session_id:
            self._pulse_store.delete(binding.session_id)
        binding.session_id = None
        binding.last_error_code = error_code
        self._bump_generation(instance_id)

    def unbind(self, instance_id: str) -> None:
        """解除指定实例的模型会话绑定。

        - 若为 independent 模式，同时从 pulse_store 删除其独占会话；
        - 若为 reuse_narration 模式，仅移除脉动侧内存引用，绝不删除 narration_store 中的原叙事会话。
        """
        self._cleanup_owned_session(instance_id)
        self._bindings.pop(instance_id, None)
        self._bump_generation(instance_id)

    def _cleanup_owned_session(self, instance_id: str) -> None:
        existing = self._bindings.get(instance_id)
        if existing is not None and existing.mode == "independent" and existing.session_id:
            self._pulse_store.delete(existing.session_id)

    def resolve_client(
        self,
        instance_id: str,
        *,
        touch_ttl: bool = True,
    ) -> tuple[PulseModelClientProtocol, SessionMode]:
        """解析指定实例当前可用的模型客户端。

        若未绑定、服务重启后内存丢失、认证失败或底层会话已过期，抛出 PulseError，
        供 Worker 将任务安全转入 waiting_for_model，绝不静默回退到其他会话或 Fake。
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

        if binding.last_error_code:
            raise PulseError(
                code=binding.last_error_code,
                message="模型会话已失效或认证失败，请重新连接。",
                status=409,
            )

        if binding.mode == "independent":
            if not binding.session_id:
                binding.last_error_code = "MODEL_SESSION_EXPIRED"
                raise PulseError("MODEL_SESSION_EXPIRED", "独立脉动模型会话已失效，请重新连接。", status=409)
            try:
                gw_client = self._peek_or_get_client(self._pulse_store, binding.session_id, touch_ttl=touch_ttl)
            except GatewayError as exc:
                binding.session_id = None
                binding.last_error_code = exc.code
                self._bump_generation(instance_id)
                raise PulseError(exc.code, exc.message, status=exc.status) from None
            binding.model_hint = gw_client.model
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
                binding.last_error_code = "MODEL_SESSION_EXPIRED"
                raise PulseError("MODEL_SESSION_EXPIRED", "复用的叙事模型会话不可用，请重新连接。", status=409)
            try:
                gw_client = self._peek_or_get_client(
                    self._narration_store,
                    binding.session_id,
                    touch_ttl=touch_ttl,
                )
            except GatewayError as exc:
                # 叙事会话已过期或被前台删除，标记脉动绑定失效并等待用户重连（绝不静默切换）
                binding.session_id = None
                binding.last_error_code = exc.code
                self._bump_generation(instance_id)
                raise PulseError(exc.code, exc.message, status=exc.status) from None
            binding.model_hint = gw_client.model
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

    @staticmethod
    def _peek_or_get_client(
        store: ModelSessionStore,
        session_id: str,
        *,
        touch_ttl: bool,
    ) -> Any:
        """从 ModelSessionStore 获取客户端；当 touch_ttl=False（如只读状态查询）时不延长会话过期时间。"""
        if touch_ttl:
            return store.get_client(session_id)

        sessions_map = getattr(store, "_sessions", None)
        clock_fn = getattr(store, "_clock", None)
        if isinstance(sessions_map, dict) and callable(clock_fn):
            now = clock_fn()
            sess = sessions_map.get(session_id)
            if sess is None:
                raise GatewayError("MODEL_SESSION_EXPIRED", "模型会话不存在或已失效，请重新配置。", status=409)
            if sess.expires_at is not None and now >= sess.expires_at:
                sessions_map.pop(session_id, None)
                raise GatewayError("MODEL_SESSION_EXPIRED", "模型会话已过期，请重新配置。", status=409)
            return OpenAIModelClient(
                base_url=sess.base_url,
                model=sess.model,
                api_key=sess.api_key,
                transport=store.http_transport,
                timeout_seconds=store.timeout_seconds,
            )
        return store.get_client(session_id)

    def is_ready(self, instance_id: str) -> bool:
        """只读检查指定实例的模型会话是否有效可用（不刷新会话 TTL）。"""
        return bool(self.status_for(instance_id).get("ready"))

    def status_for(self, instance_id: str) -> dict[str, Any]:
        """返回脱敏的会话绑定状态（只读、不续期 TTL，绝不包含 session_id 或 api_key）。"""
        binding = self._bindings.get(instance_id)
        if binding is None:
            return {
                "mode": "unbound",
                "ready": False,
                "model": None,
                "reason_code": "MODEL_SESSION_UNBOUND",
            }
        if binding.mode == "fake":
            return {
                "mode": "fake",
                "ready": True,
                "model": getattr(binding.fake_client, "model", "fake-pulse-v1"),
                "reason_code": None,
            }
        try:
            client, mode = self.resolve_client(instance_id, touch_ttl=False)
            return {
                "mode": mode,
                "ready": True,
                "model": client.model,
                "reason_code": None,
            }
        except PulseError as exc:
            return {
                "mode": binding.mode,
                "ready": False,
                "model": binding.model_hint,
                "reason_code": exc.code,
            }

    def clear(self) -> None:
        """清空脉动自有绑定与独立会话池（绝不清空复用的 narration_store）。"""
        self._bindings.clear()
        self._generations.clear()
        self._pulse_store.clear()
