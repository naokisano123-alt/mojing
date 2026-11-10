"""把三个模块接到核心适配层上的薄包装（仅 DSH 维护，不改模块内部）。

模块签名与契约略有差异：它们的 `restore(conn, target_instance_id, snapshot)`
是位置参数，适配层统一成 `restore(conn, snapshot, *, new_instance_id, world_id)`。
"""

from __future__ import annotations

from typing import Any

from .. import events, knowledge


class EventsAdapter:
    name = "events"

    def snapshot(self, conn: Any, instance_id: str) -> dict[str, Any]:
        return events.snapshot(conn, instance_id)

    def restore(self, conn: Any, snapshot: Any, *, new_instance_id: str, world_id: str) -> None:
        from ..portability.service import _extract_event_snapshot_source_ids, discover_world_source_catalog

        catalog = discover_world_source_catalog(conn, world_id)
        refs = set(_extract_event_snapshot_source_ids(snapshot))
        if refs & catalog["deleted_historical_sources"].keys():
            allowed = set(catalog["active_sources"]) | set(catalog["deleted_historical_sources"])
            events.restore(conn, new_instance_id, snapshot, allowed_source_ids=allowed)
        else:
            events.restore(conn, new_instance_id, snapshot)


class KnowledgeAdapter:
    name = "knowledge"

    def snapshot(self, conn: Any, instance_id: str) -> dict[str, Any]:
        return knowledge.snapshot(conn, instance_id)

    def restore(self, conn: Any, snapshot: Any, *, new_instance_id: str, world_id: str) -> None:
        knowledge.restore(conn, new_instance_id, snapshot)

    def list_known(
        self,
        conn: Any,
        instance_id: str,
        holder_id: str,
        *,
        before: str | int | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        page = knowledge.list_known(
            conn, instance_id, holder_id, before=before, limit=limit
        )
        return {"items": list(page.get("items", [])), "next_before": page.get("next_before")}


def builtin_adapters() -> list[Any]:
    adapters: list[Any] = [EventsAdapter(), KnowledgeAdapter()]
    try:
        from ..encounters import EncountersAdapter

        adapters.append(EncountersAdapter())
    except Exception:
        pass
    try:
        from ..npcs import NpcsAdapter

        adapters.append(NpcsAdapter())
    except Exception:
        pass
    return adapters