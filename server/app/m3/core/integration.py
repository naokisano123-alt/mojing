"""把三个模块接到核心适配层上的薄包装（仅 DSH 维护，不改模块内部）。

模块签名与契约略有差异：它们的 `restore(conn, target_instance_id, snapshot)`
是位置参数，适配层统一成 `restore(conn, snapshot, *, new_instance_id, world_id)`。
"""

from __future__ import annotations

from typing import Any


class EventsAdapter:
    name = "events"

    def __init__(self) -> None:
        from ...m3 import events

        self._events = events

    def snapshot(self, conn: Any, instance_id: str) -> dict[str, Any]:
        return self._events.snapshot(conn, instance_id)

    def restore(self, conn: Any, snapshot: Any, *, new_instance_id: str, world_id: str) -> None:
        self._events.restore(conn, new_instance_id, snapshot)


class KnowledgeAdapter:
    name = "knowledge"

    def __init__(self) -> None:
        from ...m3 import knowledge

        self._knowledge = knowledge

    def snapshot(self, conn: Any, instance_id: str) -> dict[str, Any]:
        return self._knowledge.snapshot(conn, instance_id)

    def restore(self, conn: Any, snapshot: Any, *, new_instance_id: str, world_id: str) -> None:
        self._knowledge.restore(conn, new_instance_id, snapshot)

    def list_known(
        self,
        conn: Any,
        instance_id: str,
        holder_id: str,
        *,
        before: str | int | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        page = self._knowledge.list_known(
            conn, instance_id, holder_id, before=before, limit=limit
        )
        return {"items": list(page.get("items", [])), "next_before": page.get("next_before")}


def builtin_adapters() -> list[Any]:
    adapters: list[Any] = []
    try:
        adapters.append(EventsAdapter())
    except Exception:
        pass
    try:
        adapters.append(KnowledgeAdapter())
    except Exception:
        pass
    return adapters
