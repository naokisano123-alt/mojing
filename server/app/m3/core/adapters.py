"""跨模块适配层：事件 / 脉动 / 知识三个包的统一插槽。

集成约定（契约 §快照、重启与误差边界）：
- 各模块自己的快照是 JSON，带内部版本；
- 检查点在`instance_snapshot.domains`里保存各模块快照；
- 分支/导入新建实例时，`restore` 由集成方调用，模块负责在自己的表里按新实例重建。

模块未合并时注册表为空：读取路由返回 503 M3_NOT_READY，检查点只存实例根。
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol, runtime_checkable

from ...errors import ApiError


@runtime_checkable
class DomainAdapter(Protocol):
    name: str

    def snapshot(self, conn: Any, instance_id: str) -> dict[str, Any]: ...

    def restore(
        self,
        conn: Any,
        snapshot: dict[str, Any],
        *,
        new_instance_id: str,
        world_id: str,
    ) -> None: ...


_REGISTRY: dict[str, Any] = {}


def register_adapter(adapter: Any) -> None:
    """注册一个模块适配器；重复注册以最后一次为准（便于测试注入 Fake）。"""
    _REGISTRY[getattr(adapter, "name", "")] = adapter


def unregister_adapter(name: str) -> None:
    _REGISTRY.pop(name, None)


def clear_adapters() -> None:
    _REGISTRY.clear()


def get_adapter(name: str) -> Any | None:
    return _REGISTRY.get(name)


def adapters() -> list[Any]:
    return [value for key, value in _REGISTRY.items() if key]


def snapshot_all(conn: Any, instance_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for adapter in adapters():
        try:
            result[adapter.name] = adapter.snapshot(conn, instance_id)
        except ApiError:
            raise
        except Exception:
            # 模块快照失败不能悄悄吞掉：检查点宁可失败也不用半个快照
            raise ApiError(500, "INSTANCE_SNAPSHOT_FAILED", f"{adapter.name} 快照失败。")
    return result


def restore_all(
    conn: Any,
    domain_snapshots: dict[str, Any],
    *,
    new_instance_id: str,
    world_id: str,
) -> None:
    for name, payload in (domain_snapshots or {}).items():
        adapter = _REGISTRY.get(name)
        if adapter is None:
            continue
        adapter.restore(conn, payload, new_instance_id=new_instance_id, world_id=world_id)


def load_builtin_adapters() -> None:
    """尝试加载真实模块；不存在则保持空注册表（不报错）。"""
    try:
        from . import integration

        for adapter in integration.builtin_adapters():
            register_adapter(adapter)
    except Exception:
        pass
    try:
        module = importlib.import_module("app.m3.events")
        factory = getattr(module, "adapter", None)
        if factory is not None:
            register_adapter(factory())
    except Exception:
        pass
    try:
        module = importlib.import_module("app.m3.knowledge")
        factory = getattr(module, "adapter", None)
        if factory is not None:
            register_adapter(factory())
    except Exception:
        pass