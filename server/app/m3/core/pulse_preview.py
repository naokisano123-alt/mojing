"""Connect pulse controls and worker callbacks for simulated and live journeys."""

from uuid import uuid4

from ...adventures import service as adventures
from ...db import db
from ...errors import ApiError
from .. import events, knowledge, npcs
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


def _adventure_mode(conn, instance_id):
    row = conn.execute(
        "SELECT a.mode FROM adventures a JOIN world_instances i ON i.adventure_id = a.id WHERE i.id=?",
        (instance_id,),
    ).fetchone()
    if row is None:
        return None
    return row[0] if not hasattr(row, "keys") else row["mode"]


def create_worker(path, **session_options):
    from ..pulse.control import build_combined_pulse_callbacks
    from ..events import (
        apply_proposal as events_apply,
        read_snapshot as read_event_snapshot,
        validate_proposal as events_validate,
    )
    from ..knowledge import (
        apply_proposal as knowledge_apply,
        validate_proposal as knowledge_validate,
    )

    def events_read(conn, instance_id, job):
        snapshot = read_event_snapshot(
            conn, instance_id, job, seed_char_budget=2400, advance_char_budget=1400,
            max_due_events=2, max_causal_neighbors=2,
        )
        allowed_sources = set(snapshot.get("selected_source_ids", ()))
        for event in (*snapshot.get("due_events", ()), *snapshot.get("causal_neighborhood", ())):
            allowed_sources.update(event["source_ids"])
        snapshot["allowed_source_ids"] = sorted(allowed_sources)
        snapshot["use_event_prompts"] = True
        return snapshot

    callbacks = dict(
        events_snapshot_reader=events_read,
        events_validator=events_validate,
        events_applier=events_apply,
        knowledge_validator=knowledge_validate,
        knowledge_applier=knowledge_apply,
    )

    combined_read, combined_validate, combined_apply = build_combined_pulse_callbacks(**callbacks)
    npc_read, npc_validate, npc_apply = npcs.build_npc_pulse_callbacks(
        **callbacks, budget=npcs.DepthBudget(max_total_npcs=3),
    )

    def read_dispatch(conn, instance_id, job):
        instance = instances.get(conn, instance_id)
        adventures.world_active(conn, instance["world_id"])
        if _adventure_mode(conn, instance_id) == "fake":
            return {**_read_snapshot(conn, instance_id, job), "journey_mode": "fake"}
        if job["kind"] == "advance":
            snapshot = npc_read(conn, instance_id, job)
            if snapshot.get("due_npcs"):
                facts = {}
                for npc in snapshot["due_npcs"]:
                    npc_id = npc["npc_id"]
                    facts[npc_id] = [dict(row) for row in conn.execute("""
                        SELECT fact_id, knowledge_type, source_event_id, substr(content, 1, 110) AS content
                        FROM m3_knowledge_facts
                        WHERE instance_id=? AND holder_id=? AND status='active' AND learned_minute<=?
                        ORDER BY holder_seq DESC LIMIT 3
                    """, (instance_id, npc_id, snapshot["world_minute"]))]
                known_events = {
                    npc["npc_id"]: sorted(set(npc["cause_event_ids"]) | {
                        fact["source_event_id"] for fact in facts[npc["npc_id"]]
                    })
                    for npc in snapshot["due_npcs"]
                }
                return {
                    **snapshot, "use_npc_prompts": True, "known_facts_by_npc": facts,
                    "allowed_fact_ids_by_npc": {
                        npc_id: [row["fact_id"] for row in rows] for npc_id, rows in facts.items()
                    },
                    "known_event_ids_by_npc": known_events,
                }
        return combined_read(conn, instance_id, job)

    def validate_dispatch(proposal, context):
        if context["snapshot"].get("journey_mode") == "fake":
            return proposal
        if context["snapshot"].get("use_npc_prompts"):
            return {**npc_validate(proposal, context), "npc_cycle": True}
        return combined_validate(proposal, context)

    def apply_dispatch(conn, instance_id, proposal, job):
        adventures.world_active(conn, instances.get(conn, instance_id)["world_id"])
        if _adventure_mode(conn, instance_id) == "fake":
            return _apply(conn, instance_id, proposal, job)
        if proposal.get("npc_cycle"):
            return npc_apply(conn, instance_id, proposal, job)
        return combined_apply(conn, instance_id, proposal, job)

    return PulseWorker(
        lambda: db(path),
        session_router=PulseSessionRouter(**session_options),
        snapshot_reader=read_dispatch,
        proposal_validator=validate_dispatch,
        proposal_applier=apply_dispatch,
        # 为初始化的三条事件预留完整 JSON；仍受每轮/每日总预算约束。
        default_max_output_tokens=2000,
    )


def register_routes(app, path, worker, get_narration_store):
    prefix = "/api/v1/adventures/{adventure_id}/world-pulse"
    from ..pulse import control as pulse_control

    def _status(conn, adventure_id):
        return pulse_control.get_control_status(
            conn,
            adventure_id,
            session_router=worker.session_router,
            activity_registry=worker.activity_registry,
            budget_manager=worker.budget_manager,
        )

    @app.get(prefix)
    def status(adventure_id: str):
        with db(path) as conn:
            adventures.get_adventure(conn, adventure_id)
            return _status(conn, adventure_id)

    @app.post(prefix + "/connect")
    async def connect(adventure_id: str, payload: dict):
        with db(path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = adventures.get_adventure(conn, adventure_id)
            if run["mode"] == "fake":
                raise ApiError(409, "PULSE_LIVE_ONLY", "模拟旅程不需要连接模型会话。")
            mode = payload.get("mode")
            if mode == "independent" and not all(
                isinstance(payload.get(key), str) and payload[key].strip()
                for key in ("base_url", "model", "api_key")
            ):
                raise ApiError(422, "VALIDATION_ERROR", "请填写模型地址、名称和 API Key。")
            if mode == "reuse_narration":
                if not isinstance(payload.get("narration_session_id"), str):
                    raise ApiError(422, "VALIDATION_ERROR", "叙事会话标识须为字符串。")
                worker.session_router.set_narration_store(get_narration_store())
            if mode not in ("independent", "reuse_narration"):
                raise ApiError(422, "INVALID_SESSION_MODE", "请使用独立配置或复用叙事会话。")
            result = pulse_control.connect_pulse_session(
                conn,
                adventure_id,
                payload,
                session_router=worker.session_router,
                activity_registry=worker.activity_registry,
                budget_manager=worker.budget_manager,
            )
        if result["enabled"]:
            worker.start()
        return result

    @app.put(prefix)
    async def set_enabled(adventure_id: str, payload: dict):
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            raise ApiError(422, "VALIDATION_ERROR", "enabled 须为布尔值。")
        with db(path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = adventures.get_adventure(conn, adventure_id)
            if run["mode"] == "fake":
                result = _legacy_fake_enable(conn, adventure_id, enabled)
            else:
                result = pulse_control.set_pulse_enabled(
                    conn,
                    adventure_id,
                    enabled,
                    session_router=worker.session_router,
                    activity_registry=worker.activity_registry,
                    budget_manager=worker.budget_manager,
                    require_session=True,
                )
        if result["enabled"]:
            if run["mode"] == "fake":
                worker.activity_registry.touch(result["instance_id"])
            worker.start()
        return result

    @app.post(prefix + "/activity")
    async def activity(adventure_id: str, payload: dict):
        active = payload.get("active")
        if type(active) is not bool:
            raise ApiError(422, "VALIDATION_ERROR", "active 须为布尔值。")
        with db(path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            adventures.get_adventure(conn, adventure_id)
            result = pulse_control.set_pulse_activity(
                conn,
                adventure_id,
                active,
                session_router=worker.session_router,
                activity_registry=worker.activity_registry,
                budget_manager=worker.budget_manager,
            )
        if active and result["enabled"]:
            worker.start()
        return result

    def _legacy_fake_enable(conn, adventure_id, enabled):
        """模拟旅程保留首轮无费用演示：自定义 Fake 生成器 + 现有 seed 登记。"""
        instance = instances.for_adventure(conn, adventure_id)
        instance_id = instance["id"]
        seeded = conn.execute(
            "SELECT 1 FROM world_events WHERE instance_id=? LIMIT 1", (instance_id,)
        ).fetchone()
        if enabled and not seeded and _source(conn, instance["world_id"]) is None:
            raise ApiError(422, "PULSE_SOURCE_REQUIRED", "请先导入一份资料或启用一条人工设定。")
        run = adventures.get_adventure(conn, adventure_id)
        configure_instance_pulse(conn, instance_id, enabled=enabled, paused=False)
        if enabled and not seeded:
            queued = conn.execute(
                """SELECT 1 FROM pulse_jobs WHERE instance_id=? AND kind='seed'
                   AND status IN ('pending','running','waiting_for_model') LIMIT 1""",
                (instance_id,),
            ).fetchone()
            if not queued:
                schedule_on_turn_commit(
                    conn,
                    instance_id=instance_id,
                    adventure_id=adventure_id,
                    adventure_revision=run["revision"],
                    kind="seed",
                    idempotency_key=f"preview:{uuid4().hex}",
                )
        if enabled:
            worker.session_router.bind_fake(instance_id, FakePulseModelClient(generator=_generate))
            resume_waiting_jobs(conn, instance_id)
        else:
            cancel_instance_jobs(
                conn,
                instance_id,
                include_running=True,
                reason_code="PULSE_DISABLED",
                reason_summary="世界脉动已关闭。",
            )
            worker.session_router.unbind(instance_id)
        return {"available": True, "enabled": enabled, "instance_id": instance_id}


def activate(worker, instance_id):
    worker.session_router.bind_fake(instance_id, FakePulseModelClient(generator=_generate))
    worker.activity_registry.touch(instance_id)
    worker.start()
