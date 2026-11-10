"""M3 第四轮：从资料建立初始局势的公共插槽。

真实的「资料分析 → 草案 → 确认建立旅程」由 Marcin 的 `m3/world_analysis` 实现；
这里只定义集成层调用的协议、注册表和 503 未就绪语义，保证：

- 分析必须由玩家显式发起（路由只暴露 POST）；
- 草案未确认前不写正式事件/人物/知识/时间（由 provider 保证，集成层只把确认包在一个事务里）；
- provider 缺失时返回 503 M4_NOT_READY，绝不伪造成功。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ...errors import ApiError


@runtime_checkable
class WorldAnalysisProvider(Protocol):
    name: str

    def analyze(self, conn: Any, world_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """生成未确认的开局草案（只读资料，不写正式状态）。"""

    def get_draft(self, conn: Any, draft_id: str) -> dict[str, Any] | None:
        """读取草案摘要；不存在返回 None。"""

    def confirm(self, conn: Any, draft_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """在调用方事务内确认草案并建立旅程状态。"""


_REGISTRY: dict[str, Any] = {}


def register_provider(provider: Any) -> None:
    _REGISTRY[getattr(provider, "name", "")] = provider


def clear_providers() -> None:
    _REGISTRY.clear()


def get_provider() -> Any | None:
    return next(iter(_REGISTRY.values()), None)


def require_provider() -> Any:
    provider = get_provider()
    if provider is None:
        raise ApiError(503, "M4_NOT_READY", "世界开局分析尚未接入（缺少 world_analysis 模块）。")
    return provider
