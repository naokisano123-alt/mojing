"""Connect the durable pulse queue to events and player knowledge in Fake journeys."""

from uuid import uuid4

from ...adventures import service as adventures
from ...db import db
from ...errors import ApiError
from .. import events, knowledge
from ..pulse.budget import configure_instance_pulse
from ..pulse.client import FakePulseModelClient
from ..pulse.queue import cancel_instance_jobs, resume_waiting_jobs
from ..pulse.session import PulseSessionRouter
from ..pulse.worker import PulseWorker, schedule_on_turn_commit
from . import service as instances


def _source(conn, world_id):
    return conn.execute("""
        SELECT 'entry:' || id AS source_id FROM lore_entries
        WHERE world_id=? AND deleted_at IS NULL AND status='confirmed'
        UNION ALL
        SELECT 'chunk:' || c.id FROM chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.world_id=? AND d.deleted_at IS NULL AND c.chunk_index=0
        ORDER BY source_id LIMIT 1
    """, (world_id, world_id)).fetchone()


def _read_snapshot(conn, instance_id, job):
    instance = instances.get(conn, instance_id)
    source = _source(conn, instance["world_id"]) if job["kind"] == "seed" else None
    thread = conn.execute("""
        SELECT event_id, revision FROM world_events
        WHERE instance_id=? AND kind='thread' AND status='active' AND next_update_minute<=?
        ORDER BY next_update_minute, event_id LIMIT 1
    """, (instance_id, instance["world_minute"])).fetchone()
    return {"source": dict(source) if source else None, "thread": dict(thread) if thread else None}


def _generate(request):
    minute = request.target_world_minute
    operations = []
    if request.kind == "seed":
        source = request.snapshot["source"]
        if source is None:
            raise ApiError(409, "PULSE_SOURCE_REQUIRED", "演示需要一份资料或已启用的设定条目。")
        for index, (title, detail) in enumerate((
            ("旅人交换线索", "旅人开始交换与这份资料相关的见闻。"),
            ("商队核对消息", "商队开始核对外地流传的说法。"),
            ("当地人整理旧闻", "当地人着手整理可以公开张贴的消息。"),
        ), 1):
            operations.append({
                "op": "create", "kind": "thread", "scope": "local", "stage": "开始",
                "title": title, "description": detail, "location": "旅途周边",
                "participants": [], "source_ids": [source["source_id"]],
                "next_update_minute": minute + index * 30,
            })
    elif thread := request.snapshot["thread"]:
        operations.append({
            "op": "update", "event_id": thread["event_id"], "expected_event_revision": thread["revision"],
            "next_update_minute": minute + 60, "stage": "继续追查",
        })
    return {"instance_id": request.instance_id, "mode": request.kind, "operations": operations}


def _apply(conn, instance_id, proposal, _job):
    instance = instances.get(conn, instance_id)
    adventures.world_active(conn, instance["world_id"])
    if not proposal["operations"]:
        return {"applied_operations": 0}
    applied = events.apply_proposal(conn, instance_id, proposal)
    first = applied["events"][0]
    message = (
        f"公告栏贴出新消息，有人开始打听「{first['title']}」。"
        if proposal["mode"] == "seed"
        else f"公告栏又有了新消息，有人仍在追查「{first['title']}」。"
    )
    knowledge.record_transmission(conn, instance_id, {
        "holder_id": "player", "knowledge_type": "rumor", "channel": "notice",
        "title": "公告栏上的传闻",
        "content": f"【脉动演示】{message}",
        "source_event_id": first["event_id"], "learned_minute": instance["world_minute"],
    })
    return {"applied_operations": len(applied["events"])}


def create_worker(path):
    return PulseWorker(
        lambda: db(path), session_router=PulseSessionRouter(),
        snapshot_reader=_read_snapshot, proposal_applier=_apply,
    )


def register_routes(app, path, worker):
    prefix = "/api/v1/adventures/{adventure_id}/world-pulse"

    @app.get(prefix)
    def status(adventure_id: str):
        with db(path) as conn:
            run = adventures.get_adventure(conn, adventure_id)
            instance = instances.for_adventure(conn, adventure_id)
            config = conn.execute(
                "SELECT enabled FROM pulse_instance_configs WHERE instance_id=?", (instance["id"],)
            ).fetchone()
        return {"available": run["mode"] == "fake", "enabled": bool(config and config["enabled"])}

    @app.put(prefix)
    async def set_enabled(adventure_id: str, payload: dict):
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            raise ApiError(422, "VALIDATION_ERROR", "enabled 须为布尔值。")
        with db(path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = adventures.get_adventure(conn, adventure_id)
            if run["mode"] != "fake":
                raise ApiError(409, "PULSE_PREVIEW_ONLY", "世界脉动预览仅支持模拟旅程。")
            instance = instances.for_adventure(conn, adventure_id)
            instance_id = instance["id"]
            if enabled:
                seeded = conn.execute(
                    "SELECT 1 FROM world_events WHERE instance_id=? LIMIT 1", (instance_id,)
                ).fetchone()
                if not seeded and _source(conn, instance["world_id"]) is None:
                    raise ApiError(422, "PULSE_SOURCE_REQUIRED", "请先导入一份资料或启用一条人工设定。")
            configure_instance_pulse(conn, instance_id, enabled=enabled, paused=False)
            if enabled and not seeded:
                queued = conn.execute("""
                    SELECT 1 FROM pulse_jobs WHERE instance_id=? AND kind='seed'
                    AND status IN ('pending','running','waiting_for_model') LIMIT 1
                """, (instance_id,)).fetchone()
                if not queued:
                    schedule_on_turn_commit(
                        conn, instance_id=instance_id, adventure_id=adventure_id,
                        adventure_revision=run["revision"], kind="seed", idempotency_key=f"preview:{uuid4().hex}",
                    )
            if enabled:
                resume_waiting_jobs(conn, instance_id)
            else:
                cancel_instance_jobs(
                    conn, instance_id, include_running=True,
                    reason_code="PULSE_DISABLED", reason_summary="世界脉动预览已关闭。",
                )

        if enabled:
            activate(worker, instance_id)
        else:
            worker.activity_registry.mark_offline(instance_id)
            worker.session_router.unbind(instance_id)
        return {"available": True, "enabled": enabled}


def activate(worker, instance_id):
    worker.session_router.bind_fake(instance_id, FakePulseModelClient(generator=_generate))
    worker.activity_registry.touch(instance_id)
    worker.start()
