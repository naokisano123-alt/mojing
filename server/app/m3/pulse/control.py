"""M3.2 世界脉动手机端控制契约实现、跨模块回调组合器与 Fake 领域测试底座。

按照 docs/contracts/M3_WAVE2_API.md 与 docs/collaboration/M3_WAVE2_GEMINI_LI.md 交付：
1. 手机端四项控制入口（既可传入 adventure_id，也可在独立单测中直接传入 instance_id）：
   - get_control_status (对应 transport.getStatus(adventureId))
   - connect_pulse_session (对应 transport.connect(adventureId, config))
   - set_pulse_enabled (对应 transport.setEnabled(adventureId, enabled))
   - set_pulse_activity (对应 transport.setActivity(adventureId, active))
2. 跨模块回调组合器 build_combined_pulse_callbacks：
   将事件模块（operations）与知识传播模块（transmissions）组合进 PulseWorker 的三阶段回调。
3. 独立 Fake 领域回调底座 FakePulseDomainHarness：
   在不依赖外部真实模块修改的前提下，用 SQLite 完整模拟事件账本（Canon 资料、到期事件、版本与因果）与知识传播（持有人、转述链校验、原子回滚）。
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import secrets
import sqlite3
import time
from typing import Any, Callable, ContextManager

from app.m3.pulse.budget import (
    PulseBudgetManager,
    advance_pulse_time_anchor,
    configure_instance_pulse,
    get_or_init_instance_config,
)
from app.m3.pulse.errors import PulseError, assert_no_credentials
from app.m3.pulse.observability import get_pulse_status
from app.m3.pulse.queue import (
    cancel_instance_jobs,
    enqueue,
    resume_waiting_jobs,
)
from app.m3.pulse.schema import ensure_pulse_schema
from app.m3.pulse.session import ActivityLeaseRegistry, PulseSessionRouter
from app.m3.pulse.types import (
    PulsePublicStatusDTO,
    PulseTrigger,
)
from app.m3.pulse.worker import (
    ProposalApplier,
    ProposalValidator,
    PulseWorker,
    SnapshotReader,
    schedule_on_turn_commit,
    validate_pulse_envelope,
)


def _now(override: float | None = None) -> float:
    return float(override) if override is not None else time.time()


def resolve_adventure_or_instance(
    conn: sqlite3.Connection,
    adventure_or_instance_id: str,
) -> dict[str, Any]:
    """解析旅程或实例标识，校验旅程与世界有效性。

    返回 {"instance_id", "adventure_id", "world_id", "adventure_mode", "adventure_revision", "world_minute", "world_revision"}。
    """
    if not isinstance(adventure_or_instance_id, str) or not adventure_or_instance_id.strip():
        raise PulseError("VALIDATION_ERROR", "旅程或实例标识不能为空。", status=422)
    target = adventure_or_instance_id.strip()
    conn.row_factory = sqlite3.Row
    ensure_pulse_schema(conn, ensure_instance_table=False)

    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    # 1. 若存在 adventures 表，优先按 adventure_id 解析并核对世界未删除
    if "adventures" in tables:
        adv_row = conn.execute(
            "SELECT * FROM adventures WHERE id = ?",
            (target,),
        ).fetchone()
        if adv_row is not None:
            world_id = str(adv_row["world_id"])
            if "worlds" in tables:
                world_cols = {
                    str(c[1]) for c in conn.execute("PRAGMA table_info(worlds)").fetchall()
                }
                if "deleted_at" in world_cols:
                    w_row = conn.execute(
                        "SELECT id FROM worlds WHERE id = ? AND deleted_at IS NULL",
                        (world_id,),
                    ).fetchone()
                else:
                    w_row = conn.execute("SELECT id FROM worlds WHERE id = ?", (world_id,)).fetchone()
                if w_row is None:
                    raise PulseError("WORLD_NOT_FOUND", "世界不存在或已删除。", status=404)

            inst_row = conn.execute(
                "SELECT id, world_id, adventure_id, world_minute, revision FROM world_instances WHERE adventure_id = ?",
                (target,),
            ).fetchone()
            if inst_row is None:
                raise PulseError("INSTANCE_NOT_FOUND", "该旅程尚未关联世界实例。", status=404)

            adv_keys = adv_row.keys()
            adv_mode = str(adv_row["mode"]) if "mode" in adv_keys and adv_row["mode"] else "live"
            adv_rev = int(adv_row["revision"]) if "revision" in adv_keys and adv_row["revision"] is not None else 0
            return {
                "instance_id": str(inst_row["id"]),
                "adventure_id": target,
                "world_id": str(inst_row["world_id"]),
                "adventure_mode": adv_mode,
                "adventure_revision": adv_rev,
                "world_minute": int(inst_row["world_minute"]),
                "world_revision": int(inst_row["revision"]),
            }

    # 2. 按 world_instances.id 或 world_instances.adventure_id 解析（兼容独立模块单测）
    inst_row = conn.execute(
        """
        SELECT id, world_id, adventure_id, world_minute, revision
        FROM world_instances
        WHERE id = ? OR adventure_id = ?
        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (target, target, target),
    ).fetchone()
    if inst_row is None:
        if "adventures" in tables:
            raise PulseError("ADVENTURE_NOT_FOUND", "冒险记录不存在。", status=404)
        raise PulseError("INSTANCE_NOT_FOUND", "未找到对应的世界实例。", status=404)

    adv_id = str(inst_row["adventure_id"] or target)
    adv_mode = "live"
    adv_rev = int(inst_row["revision"])
    if "adventures" in tables and inst_row["adventure_id"]:
        adv_row = conn.execute(
            "SELECT * FROM adventures WHERE id = ?",
            (inst_row["adventure_id"],),
        ).fetchone()
        if adv_row is not None:
            adv_keys = adv_row.keys()
            if "mode" in adv_keys and adv_row["mode"]:
                adv_mode = str(adv_row["mode"])
            if "revision" in adv_keys and adv_row["revision"] is not None:
                adv_rev = int(adv_row["revision"])

    return {
        "instance_id": str(inst_row["id"]),
        "adventure_id": adv_id,
        "world_id": str(inst_row["world_id"]),
        "adventure_mode": adv_mode,
        "adventure_revision": adv_rev,
        "world_minute": int(inst_row["world_minute"]),
        "world_revision": int(inst_row["revision"]),
    }


def get_control_status(
    conn: sqlite3.Connection,
    adventure_or_instance_id: str,
    *,
    session_router: PulseSessionRouter,
    activity_registry: ActivityLeaseRegistry | None = None,
    budget_manager: PulseBudgetManager | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """对应手机端 getStatus(adventureId) 契约。

    - 只读操作，绝不续期活动租约或模型会话 TTL；
    - 返回 enabled, mode, state, model, reason_code 与当游戏日用量 usage（区分实测/估算、可能计费与模拟用量）；
    - 严禁包含 API Key、session_id、隐藏事件或任务负载。
    """
    current = _now(now)
    resolved = resolve_adventure_or_instance(conn, adventure_or_instance_id)
    inst_id = resolved["instance_id"]

    full_status = get_pulse_status(
        conn,
        inst_id,
        session_router=session_router,
        activity_registry=activity_registry,
        budget_manager=budget_manager,
        now=current,
    )
    day_usage = full_status["current_day_usage"]
    dto = PulsePublicStatusDTO(
        available=True,
        enabled=bool(full_status["enabled"]) and not bool(full_status["paused"]),
        paused=bool(full_status["paused"]),
        mode=full_status["mode"],
        state=full_status["state"],
        model=full_status["model"],
        reason_code=full_status["reason_code"],
        world_minute=int(full_status["world_minute"]),
        world_revision=int(full_status["world_revision"]),
        current_game_day=int(day_usage["game_day"]),
        usage={
            "game_day": int(day_usage["game_day"]),
            "actual_tokens": int(day_usage["actual_tokens"]),
            "estimated_tokens": int(day_usage["estimated_tokens"]),
            "potentially_billed_tokens": int(day_usage["potentially_billed_tokens"]),
            "potentially_billed_calls": int(day_usage["potentially_billed_calls"]),
            "billed_calls": int(day_usage["billed_calls"]),
            "timeout_maybe_billed_calls": int(day_usage["timeout_maybe_billed_calls"]),
            "timeout_maybe_billed_tokens": int(day_usage["timeout_maybe_billed_tokens"]),
            "simulated_calls": int(day_usage["simulated_calls"]),
            "simulated_tokens": int(day_usage["simulated_tokens"]),
            "aborted_unbilled_calls": int(day_usage["aborted_unbilled_calls"]),
            "reserved_calls": int(day_usage["reserved_calls"]),
            "reserved_tokens": int(day_usage["reserved_tokens"]),
            "daily_call_budget": int(day_usage["daily_call_budget"]),
            "daily_token_budget": int(day_usage["daily_token_budget"]),
            "remaining_calls": int(day_usage["remaining_calls"]),
            "remaining_tokens": int(day_usage["remaining_tokens"]),
        },
    ).to_dict()
    assert_no_credentials(dto, context="pulse_control_status")
    return dto


def connect_pulse_session(
    conn: sqlite3.Connection,
    adventure_or_instance_id: str,
    config: Mapping[str, Any],
    *,
    session_router: PulseSessionRouter,
    activity_registry: ActivityLeaseRegistry | None = None,
    budget_manager: PulseBudgetManager | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """对应手机端 connect(adventureId, config) 契约。

    - 独立模式提交 mode='independent', base_url, model, api_key；
    - 复用模式提交 mode='reuse_narration', narration_session_id；
    - Fake 旅程支持 mode='fake'（正式 live 旅程禁止绑定 fake）；
    - 只绑定有效内存会话，连接本身不启用脉动（enabled 保持原值）、不发起付费测试请求；
    - 若该实例脉动原本已处于开启状态（例如重启或过期后重连），重连以当前已确认分钟锚定并唤醒等待任务。
    """
    if not isinstance(config, Mapping):
        raise PulseError("VALIDATION_ERROR", "连接配置必须是 JSON 对象。", status=422)

    current = _now(now)
    resolved = resolve_adventure_or_instance(conn, adventure_or_instance_id)
    inst_id = resolved["instance_id"]
    adv_mode = resolved["adventure_mode"]
    current_min = resolved["world_minute"]

    mode = str(config.get("mode") or "").strip()
    if mode == "independent":
        if adv_mode == "fake":
            raise PulseError(
                "PULSE_LIVE_FORBIDDEN_IN_FAKE",
                "模拟旅程仅使用无费用演示脉动，不绑定真实付费模型会话。",
                status=409,
            )
        base_url = str(config.get("base_url") or "")
        model = str(config.get("model") or "")
        api_key = str(config.get("api_key") or "")
        session_router.bind_independent(
            inst_id,
            base_url=base_url,
            model=model,
            api_key=api_key,
        )
    elif mode == "reuse_narration":
        if adv_mode == "fake":
            raise PulseError(
                "PULSE_LIVE_FORBIDDEN_IN_FAKE",
                "模拟旅程仅使用无费用演示脉动，不复用付费叙事会话。",
                status=409,
            )
        narration_sid = str(config.get("narration_session_id") or "")
        session_router.bind_reuse_narration(inst_id, narration_sid)
    elif mode == "fake":
        if adv_mode == "live" and not bool(config.get("allow_fake_in_live")):
            raise PulseError(
                "PULSE_FAKE_FORBIDDEN_IN_LIVE",
                "正式旅程不允许绑定模拟客户端。",
                status=409,
            )
        session_router.bind_fake(inst_id)
    else:
        raise PulseError(
            "INVALID_SESSION_MODE",
            "mode 仅支持 independent、reuse_narration 或 fake。",
            status=422,
        )

    cfg = get_or_init_instance_config(conn, inst_id, now=current)
    # 若脉动已处于启用状态（如过期或重启后重连），以当前已确认分钟锚定并唤醒 waiting_for_model 任务
    if bool(cfg["enabled"]) and not bool(cfg["paused"]):
        advance_pulse_time_anchor(
            conn,
            inst_id,
            world_minute=current_min,
            cancel_pending_advance=True,
            reason_code="PULSE_RECONNECTED_SKIP_HISTORY",
            reason_summary="重连模型会话时以当前已确认分钟锚定，不补演断连期间的历史。",
            now=current,
        )
        resume_waiting_jobs(conn, inst_id, now=current)

    return get_control_status(
        conn,
        adventure_or_instance_id,
        session_router=session_router,
        activity_registry=activity_registry,
        budget_manager=budget_manager,
        now=current,
    )


def _has_existing_events_or_seed_job(conn: sqlite3.Connection, instance_id: str) -> bool:
    """检查实例是否已有权威事件或已存在活跃/已完成的 seed 任务。"""
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for tbl in ("world_events", "m3_pulse_fake_events"):
        if tbl in tables:
            row = conn.execute(
                f"SELECT 1 FROM {tbl} WHERE instance_id = ? LIMIT 1",
                (instance_id,),
            ).fetchone()
            if row is not None:
                return True

    job_row = conn.execute(
        """
        SELECT 1 FROM pulse_jobs
        WHERE instance_id = ?
          AND kind = 'seed'
          AND status IN ('pending', 'running', 'waiting_for_model', 'completed')
        LIMIT 1
        """,
        (instance_id,),
    ).fetchone()
    return job_row is not None


def set_pulse_enabled(
    conn: sqlite3.Connection,
    adventure_or_instance_id: str,
    enabled: bool,
    *,
    session_router: PulseSessionRouter,
    activity_registry: ActivityLeaseRegistry | None = None,
    budget_manager: PulseBudgetManager | None = None,
    auto_enqueue_seed: bool = True,
    require_session: bool = False,
    unbind_on_disable: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """对应手机端 setEnabled(adventureId, enabled) 契约。

    - 用户显式启停；开关与会话分离，关闭时保留叙事会话及已有历史；
    - 开关切换递增实例代次（generation），关闭时取消排队与在途任务，阻止慢响应提交；
    - 启用时锚定当前已确认游戏分钟，不补演关闭期间的历史；若实例尚未初始化事件线则自动登记 seed 任务。
    """
    if type(enabled) is not bool:
        raise PulseError("VALIDATION_ERROR", "enabled 必须是布尔值。", status=422)

    current = _now(now)
    resolved = resolve_adventure_or_instance(conn, adventure_or_instance_id)
    inst_id = resolved["instance_id"]
    adv_id = resolved["adventure_id"]
    adv_mode = resolved["adventure_mode"]
    adv_rev = resolved["adventure_revision"]
    current_min = resolved["world_minute"]
    current_rev = resolved["world_revision"]

    if enabled:
        # Fake 旅程保留既有无费用入口
        sess_status = session_router.status_for(inst_id)
        if adv_mode == "fake" and sess_status["mode"] == "unbound":
            session_router.bind_fake(inst_id)
            sess_status = session_router.status_for(inst_id)

        if require_session and not bool(sess_status["ready"]):
            code = str(sess_status.get("reason_code") or "MODEL_SESSION_UNBOUND")
            raise PulseError(code, "启用世界脉动前请先连接有效的模型会话。", status=409)

        configure_instance_pulse(
            conn,
            inst_id,
            enabled=True,
            paused=False,
            now=current,
        )
        if auto_enqueue_seed and not _has_existing_events_or_seed_job(conn, inst_id):
            enqueue(
                conn,
                PulseTrigger(
                    instance_id=inst_id,
                    kind="seed",
                    trigger_adventure_id=adv_id,
                    trigger_revision=adv_rev,
                    base_world_revision=current_rev,
                    target_world_minute=current_min,
                    idempotency_key=f"seed:{inst_id}:init:{secrets.token_hex(4)}",
                    coalesce=False,
                    now=current,
                ),
            )
        if bool(sess_status["ready"]) and (
            activity_registry is None or activity_registry.is_active(inst_id, now=current)
        ):
            resume_waiting_jobs(conn, inst_id, now=current)
    else:
        configure_instance_pulse(
            conn,
            inst_id,
            enabled=False,
            paused=False,
            now=current,
        )
        cancel_instance_jobs(
            conn,
            inst_id,
            include_running=True,
            reason_code="PULSE_DISABLED",
            reason_summary="用户已关闭世界脉动，排队与在途任务已取消。",
            now=current,
        )
        if unbind_on_disable:
            session_router.unbind(inst_id)

    return get_control_status(
        conn,
        adventure_or_instance_id,
        session_router=session_router,
        activity_registry=activity_registry,
        budget_manager=budget_manager,
        now=current,
    )


def set_pulse_activity(
    conn: sqlite3.Connection,
    adventure_or_instance_id: str,
    active: bool,
    *,
    session_router: PulseSessionRouter,
    activity_registry: ActivityLeaseRegistry,
    budget_manager: PulseBudgetManager | None = None,
    ttl_seconds: float | None = None,
    exclusive: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    """对应手机端 setActivity(adventureId, active) 契约。

    - active=True：从当前可见旅程续期在线租约（exclusive=True 时使其他旅程租约立即失效）；
      前台恢复不推进世界时间，并以当前已确认分钟锚定，不补演离线期间事件；
    - active=False：切后台、离开或断网立即使在线租约失效并推进时间锚点。
    """
    if type(active) is not bool:
        raise PulseError("VALIDATION_ERROR", "active 必须是布尔值。", status=422)

    current = _now(now)
    resolved = resolve_adventure_or_instance(conn, adventure_or_instance_id)
    inst_id = resolved["instance_id"]
    current_min = resolved["world_minute"]

    if active:
        was_active = activity_registry.is_active(inst_id, now=current)
        activity_registry.touch(
            inst_id,
            ttl_seconds=ttl_seconds,
            now=current,
            exclusive=exclusive,
        )
        if not was_active:
            advance_pulse_time_anchor(
                conn,
                inst_id,
                world_minute=current_min,
                cancel_pending_advance=True,
                reason_code="PULSE_FOREGROUND_RESUMED_ANCHOR",
                reason_summary="前台恢复时锚定当前已确认世界时间，不补演离线期间事件。",
                now=current,
            )
            cfg = get_or_init_instance_config(conn, inst_id, now=current)
            if bool(cfg["enabled"]) and not bool(cfg["paused"]) and session_router.is_ready(inst_id):
                resume_waiting_jobs(conn, inst_id, now=current)
    else:
        activity_registry.mark_offline(inst_id, now=current)
        advance_pulse_time_anchor(
            conn,
            inst_id,
            world_minute=current_min,
            cancel_pending_advance=True,
            reason_code="CLIENT_LEFT_OR_BACKGROUND",
            reason_summary="玩家离开或切入后台，推进时间锚并取消未执行的排队推进任务。",
            now=current,
        )

    return get_control_status(
        conn,
        adventure_or_instance_id,
        session_router=session_router,
        activity_registry=activity_registry,
        budget_manager=budget_manager,
        now=current,
    )


def build_combined_pulse_callbacks(
    *,
    events_snapshot_reader: SnapshotReader,
    events_validator: Callable[[dict[str, Any], dict[str, Any]], Any],
    events_applier: Callable[[sqlite3.Connection, str, Any, dict[str, Any]], dict[str, Any] | None],
    knowledge_validator: Callable[[list[dict[str, Any]], dict[str, Any]], Any] | None = None,
    knowledge_applier: Callable[[sqlite3.Connection, str, Any, dict[str, Any]], Any] | None = None,
) -> tuple[SnapshotReader, ProposalValidator, ProposalApplier]:
    """将事件模块与知识模块的回调组合为 PulseWorker 的 (read_snapshot, validate_proposal, apply_proposal)。"""

    def combined_snapshot_reader(
        conn: sqlite3.Connection,
        instance_id: str,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        snap = dict(events_snapshot_reader(conn, instance_id, job) or {})
        snap.setdefault("instance_id", instance_id)
        snap.setdefault("job_kind", job["kind"])
        if "due_events" in snap and isinstance(snap["due_events"], (list, tuple)):
            snap.setdefault(
                "allowed_event_ids",
                [
                    str(ev["event_id"])
                    for ev in snap["due_events"]
                    if isinstance(ev, Mapping) and "event_id" in ev
                ],
            )
        return snap

    def combined_validator(proposal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        envelope = validate_pulse_envelope(proposal, context)
        ops = envelope.get("operations", [])
        transmissions = envelope.get("transmissions", [])

        events_payload: dict[str, Any] = {
            "instance_id": context["instance_id"],
            "mode": context["kind"],
            "operations": ops,
        }
        if "summary" in envelope and envelope["summary"]:
            events_payload["summary"] = envelope["summary"]

        validated_events = events_validator(events_payload, context) if ops else None
        validated_transmissions: Any = transmissions
        if transmissions and knowledge_validator is not None:
            validated_transmissions = knowledge_validator(transmissions, context)

        return {
            "kind": context["kind"],
            "validated_events": validated_events,
            "validated_transmissions": validated_transmissions,
            "raw_operations": ops,
            "raw_transmissions": transmissions,
        }

    def combined_applier(
        conn: sqlite3.Connection,
        instance_id: str,
        validated: Any,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        val_dict = validated if isinstance(validated, Mapping) else {}
        val_events = val_dict.get("validated_events")
        val_trans = val_dict.get("validated_transmissions")

        events_result: dict[str, Any] | None = None
        if val_events is not None:
            events_result = events_applier(conn, instance_id, val_events, job)

        trans_result: Any = None
        if val_trans and knowledge_applier is not None:
            trans_result = knowledge_applier(conn, instance_id, val_trans, job)

        return {
            "events": events_result,
            "transmissions": trans_result,
        }

    return combined_snapshot_reader, combined_validator, combined_applier


class FakePulseDomainHarness:
    """用于独立单测与运行期接线演练的 Fake 事件 + 知识传播领域底座（基于 SQLite 表与 SAVEPOINT）。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS m3_pulse_fake_sources (
        world_id   TEXT NOT NULL,
        source_id  TEXT NOT NULL,
        text       TEXT NOT NULL,
        PRIMARY KEY (world_id, source_id)
    );

    CREATE TABLE IF NOT EXISTS m3_pulse_fake_events (
        instance_id        TEXT NOT NULL,
        event_id           TEXT NOT NULL,
        kind               TEXT NOT NULL,
        title              TEXT NOT NULL,
        description        TEXT NOT NULL,
        location           TEXT NOT NULL,
        scope              TEXT NOT NULL,
        stage              TEXT NOT NULL,
        status             TEXT NOT NULL,
        created_minute     INTEGER NOT NULL,
        updated_minute     INTEGER NOT NULL,
        next_update_minute INTEGER,
        cause_event_ids    TEXT NOT NULL DEFAULT '[]',
        source_ids         TEXT NOT NULL DEFAULT '[]',
        revision           INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (instance_id, event_id)
    );

    CREATE TABLE IF NOT EXISTS m3_pulse_fake_knowledge (
        instance_id      TEXT NOT NULL,
        fact_id          TEXT NOT NULL,
        holder_id        TEXT NOT NULL,
        knowledge_type   TEXT NOT NULL,
        title            TEXT NOT NULL,
        content          TEXT NOT NULL,
        source_event_id  TEXT NOT NULL,
        source_person_id TEXT,
        channel          TEXT NOT NULL,
        learned_minute   INTEGER NOT NULL,
        confidence       REAL NOT NULL DEFAULT 0.8,
        PRIMARY KEY (instance_id, fact_id)
    );
    """

    def __init__(self) -> None:
        self._event_seq = 0
        self._fact_seq = 0

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        for stmt in self.SCHEMA.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)

    def add_canon_source(
        self,
        conn: sqlite3.Connection,
        world_id: str,
        source_id: str,
        text: str = "北境小镇与灰岩边关贸易设定。",
    ) -> None:
        self.ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO m3_pulse_fake_sources(world_id, source_id, text) VALUES (?, ?, ?)",
            (world_id, source_id, text),
        )

    def seed_holder_knowledge(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        *,
        holder_id: str,
        source_event_id: str,
        learned_minute: int,
        title: str = "已知事件",
        content: str = "NPC 已获知该事件。",
        channel: str = "observed",
        knowledge_type: str = "confirmed",
    ) -> str:
        self.ensure_schema(conn)
        self._fact_seq += 1
        fid = f"ffact_{self._fact_seq}"
        conn.execute(
            """
            INSERT INTO m3_pulse_fake_knowledge(
                instance_id, fact_id, holder_id, knowledge_type, title, content,
                source_event_id, source_person_id, channel, learned_minute, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1.0)
            """,
            (instance_id, fid, holder_id, knowledge_type, title, content, source_event_id, channel, learned_minute),
        )
        return fid

    def read_snapshot(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        job: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_schema(conn)
        inst = conn.execute(
            "SELECT id, world_id, world_minute, revision FROM world_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if inst is None:
            raise PulseError("INSTANCE_NOT_FOUND", "世界实例不存在。", status=404)

        world_id = str(inst["world_id"])
        world_minute = int(inst["world_minute"])
        world_rev = int(inst["revision"])

        sources = [
            {"source_id": str(r[0]), "text": str(r[1])}
            for r in conn.execute(
                "SELECT source_id, text FROM m3_pulse_fake_sources WHERE world_id = ? ORDER BY source_id ASC",
                (world_id,),
            ).fetchall()
        ]
        allowed_source_ids = [s["source_id"] for s in sources]

        if job["kind"] == "seed":
            if not sources:
                return {
                    "instance_id": instance_id,
                    "world_minute": world_minute,
                    "world_revision": world_rev,
                    "blocked": True,
                    "reason_code": "PULSE_SOURCE_REQUIRED",
                    "reason_summary": "当前世界尚无已授权的 Canon 设定资料，无法生成初始事件线。",
                    "sources": [],
                    "allowed_source_ids": [],
                }
            return {
                "instance_id": instance_id,
                "world_minute": world_minute,
                "world_revision": world_rev,
                "sources": sources,
                "allowed_source_ids": allowed_source_ids,
            }

        due_rows = conn.execute(
            """
            SELECT event_id, kind, title, description, location, scope, stage, status,
                   created_minute, updated_minute, next_update_minute, cause_event_ids, source_ids, revision
            FROM m3_pulse_fake_events
            WHERE instance_id = ?
              AND status IN ('active', 'stalled')
              AND next_update_minute IS NOT NULL
              AND next_update_minute <= ?
            ORDER BY next_update_minute ASC, event_id ASC
            LIMIT 8
            """,
            (instance_id, world_minute),
        ).fetchall()

        due_events = [
            {
                "event_id": str(r[0]),
                "kind": str(r[1]),
                "title": str(r[2]),
                "description": str(r[3]),
                "location": str(r[4]),
                "scope": str(r[5]),
                "stage": str(r[6]),
                "status": str(r[7]),
                "created_minute": int(r[8]),
                "updated_minute": int(r[9]),
                "next_update_minute": int(r[10]) if r[10] is not None else None,
                "cause_event_ids": json.loads(r[11] or "[]"),
                "source_ids": json.loads(r[12] or "[]"),
                "revision": int(r[13]),
            }
            for r in due_rows
        ]
        for ev in due_events:
            for sid in ev["source_ids"]:
                if sid not in allowed_source_ids:
                    allowed_source_ids.append(sid)

        if not due_events:
            return {
                "instance_id": instance_id,
                "world_minute": world_minute,
                "world_revision": world_rev,
                "skip": True,
                "reason_code": "NO_DUE_EVENTS",
                "reason_summary": "当前已确认时间无到期事件，跳过模型调用。",
                "due_events": [],
                "allowed_source_ids": allowed_source_ids,
                "allowed_event_ids": [],
            }

        return {
            "instance_id": instance_id,
            "world_minute": world_minute,
            "world_revision": world_rev,
            "due_events": due_events,
            "allowed_source_ids": allowed_source_ids,
            "allowed_event_ids": [ev["event_id"] for ev in due_events],
        }

    def validate_events(self, payload: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        ops = payload.get("operations", [])
        mode = payload.get("mode", context.get("kind", "advance"))
        if mode == "seed" and not (3 <= len(ops) <= 6):
            raise PulseError(
                "SEED_THREAD_COUNT_INVALID",
                f"Seed 必须生成 3～6 条初始主要事件线，实际为 {len(ops)} 条。",
                status=422,
            )
        for idx, op in enumerate(ops):
            op_type = op.get("op")
            if op_type not in ("create", "update"):
                raise PulseError("EVENT_OP_INVALID", f"operations[{idx}].op 非法。", status=422)
            if op_type == "update":
                if not op.get("event_id") or type(op.get("expected_event_revision")) is not int:
                    raise PulseError(
                        "EVENT_UPDATE_FIELDS_REQUIRED",
                        f"operations[{idx}] 更新操作必须提供 event_id 与 expected_event_revision。",
                        status=422,
                    )
        return list(ops)

    def validate_knowledge(
        self,
        transmissions: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        target_min = int(context.get("target_world_minute", 0))
        for idx, tr in enumerate(transmissions):
            channel = tr.get("channel")
            if channel not in ("observed", "notice", "conversation", "rumor"):
                raise PulseError("INVALID_CHANNEL", f"transmissions[{idx}].channel 非法。", status=422)
            ktype = tr.get("knowledge_type", "rumor")
            if ktype not in ("confirmed", "suspected", "rumor", "mistaken"):
                raise PulseError("INVALID_KNOWLEDGE_TYPE", f"transmissions[{idx}].knowledge_type 非法。", status=422)
            learned_min = tr.get("learned_minute", target_min)
            if type(learned_min) is not int or learned_min < 0 or learned_min > target_min:
                raise PulseError(
                    "FUTURE_TRANSMISSION_FORBIDDEN",
                    f"transmissions[{idx}].learned_minute 超出当前已确认世界时间。",
                    status=422,
                )
            if channel == "conversation" and not tr.get("source_person_id"):
                raise PulseError(
                    "SOURCE_PERSON_REQUIRED",
                    f"transmissions[{idx}] conversation 渠道必须提供转述人 source_person_id。",
                    status=422,
                )
        return list(transmissions)

    def apply_events(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        validated_ops: list[dict[str, Any]],
        job: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_schema(conn)
        commit_min = int(job["target_world_minute"])
        applied: list[dict[str, Any]] = []

        for op in validated_ops:
            if op["op"] == "create":
                self._event_seq += 1
                eid = f"fevt_{self._event_seq}"
                kind = str(op.get("kind", "thread"))
                title = str(op.get("title", "未命名事件"))
                desc = str(op.get("description", title))
                loc = str(op.get("location", "未知地点"))
                scope = str(op.get("scope", "local"))
                stage = str(op.get("stage", "initial"))
                status = str(op.get("status", "active"))
                next_min = op.get("next_update_minute", commit_min + 60)
                causes = list(op.get("cause_event_ids") or [])
                sources = list(op.get("source_ids") or [])
                conn.execute(
                    """
                    INSERT INTO m3_pulse_fake_events(
                        instance_id, event_id, kind, title, description, location, scope,
                        stage, status, created_minute, updated_minute, next_update_minute,
                        cause_event_ids, source_ids, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        instance_id,
                        eid,
                        kind,
                        title,
                        desc,
                        loc,
                        scope,
                        stage,
                        status,
                        commit_min,
                        commit_min,
                        next_min,
                        json.dumps(causes, ensure_ascii=False),
                        json.dumps(sources, ensure_ascii=False),
                    ),
                )
                applied.append({"event_id": eid, "op": "create", "revision": 1})
            else:
                eid = str(op["event_id"])
                exp_rev = int(op["expected_event_revision"])
                row = conn.execute(
                    "SELECT * FROM m3_pulse_fake_events WHERE instance_id = ? AND event_id = ?",
                    (instance_id, eid),
                ).fetchone()
                if row is None:
                    raise PulseError("EVENT_NOT_FOUND", f"事件 {eid} 不存在。", status=404)
                if int(row["revision"]) != exp_rev:
                    raise PulseError("EVENT_REVISION_CONFLICT", f"事件 {eid} 版本冲突。", status=409)
                new_rev = exp_rev + 1
                new_stage = str(op.get("stage", row["stage"]))
                new_status = str(op.get("status", row["status"]))
                next_min = op.get("next_update_minute", row["next_update_minute"])
                conn.execute(
                    """
                    UPDATE m3_pulse_fake_events
                    SET stage = ?, status = ?, updated_minute = ?, next_update_minute = ?, revision = ?
                    WHERE instance_id = ? AND event_id = ? AND revision = ?
                    """,
                    (new_stage, new_status, commit_min, next_min, new_rev, instance_id, eid, exp_rev),
                )
                applied.append({"event_id": eid, "op": "update", "revision": new_rev})

        return {"events": applied}

    def apply_knowledge(
        self,
        conn: sqlite3.Connection,
        instance_id: str,
        validated_transmissions: list[dict[str, Any]],
        job: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_schema(conn)
        commit_min = int(job["target_world_minute"])
        applied: list[dict[str, Any]] = []

        for tr in validated_transmissions:
            src_eid = str(tr["source_event_id"])
            ev_row = conn.execute(
                "SELECT event_id, created_minute FROM m3_pulse_fake_events WHERE instance_id = ? AND event_id = ?",
                (instance_id, src_eid),
            ).fetchone()
            if ev_row is None:
                raise PulseError("SOURCE_EVENT_NOT_FOUND", f"传播引用的来源事件 {src_eid} 不存在。", status=422)

            learned_min = int(tr.get("learned_minute", commit_min))
            speaker = tr.get("source_person_id")
            if speaker:
                speaker_row = conn.execute(
                    """
                    SELECT learned_minute FROM m3_pulse_fake_knowledge
                    WHERE instance_id = ? AND holder_id = ? AND source_event_id = ?
                    ORDER BY learned_minute ASC LIMIT 1
                    """,
                    (instance_id, str(speaker), src_eid),
                ).fetchone()
                if speaker_row is None or int(speaker_row[0]) > learned_min:
                    raise PulseError(
                        "RELAY_HOLDER_UNKNOWN",
                        f"转述人 {speaker} 在第 {learned_min} 分钟前尚未获知事件 {src_eid}。",
                        status=422,
                    )

            self._fact_seq += 1
            fid = f"ffact_{self._fact_seq}"
            holder = str(tr["holder_id"])
            ktype = str(tr.get("knowledge_type", "rumor"))
            title = str(tr.get("title", "世界消息"))
            content = str(tr.get("content", title))
            channel = str(tr["channel"])
            conf = float(tr.get("confidence", 0.8))

            conn.execute(
                """
                INSERT INTO m3_pulse_fake_knowledge(
                    instance_id, fact_id, holder_id, knowledge_type, title, content,
                    source_event_id, source_person_id, channel, learned_minute, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    fid,
                    holder,
                    ktype,
                    title,
                    content,
                    src_eid,
                    str(speaker) if speaker else None,
                    channel,
                    learned_min,
                    conf,
                ),
            )
            applied.append({"fact_id": fid, "holder_id": holder, "source_event_id": src_eid})

        return {"transmissions": applied}

    def callbacks(self) -> tuple[SnapshotReader, ProposalValidator, ProposalApplier]:
        return build_combined_pulse_callbacks(
            events_snapshot_reader=self.read_snapshot,
            events_validator=self.validate_events,
            events_applier=self.apply_events,
            knowledge_validator=self.validate_knowledge,
            knowledge_applier=self.apply_knowledge,
        )


class PulseRuntimeController:
    """面向 DeepSeekHarness 路由挂载与手机端契约联调的运行期控制器。

    保证锁内只做短数据库事务，网络与纯解析在事务外。
    """

    def __init__(
        self,
        db_factory: Callable[[], ContextManager[sqlite3.Connection]],
        *,
        worker: PulseWorker,
    ) -> None:
        self._db_factory = db_factory
        self.worker = worker

    @property
    def session_router(self) -> PulseSessionRouter:
        return self.worker.session_router

    @property
    def activity_registry(self) -> ActivityLeaseRegistry:
        return self.worker.activity_registry

    @property
    def budget_manager(self) -> PulseBudgetManager:
        return self.worker.budget_manager

    def get_status(self, adventure_id: str, *, now: float | None = None) -> dict[str, Any]:
        """transport.getStatus(adventureId)：只读查询，绝不续期活动租约。"""
        with self._db_factory() as conn:
            return get_control_status(
                conn,
                adventure_id,
                session_router=self.session_router,
                activity_registry=self.activity_registry,
                budget_manager=self.budget_manager,
                now=now,
            )

    def connect(
        self,
        adventure_id: str,
        config: Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """transport.connect(adventureId, config)：绑定独立或复用会话，不自动开启开关、不发付费测试请求。"""
        with self._db_factory() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            return connect_pulse_session(
                conn,
                adventure_id,
                config,
                session_router=self.session_router,
                activity_registry=self.activity_registry,
                budget_manager=self.budget_manager,
                now=now,
            )

    def set_enabled(
        self,
        adventure_id: str,
        enabled: bool,
        *,
        auto_enqueue_seed: bool = True,
        require_session: bool = False,
        unbind_on_disable: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        """transport.setEnabled(adventureId, enabled)：显式启停脉动。"""
        with self._db_factory() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            return set_pulse_enabled(
                conn,
                adventure_id,
                enabled,
                session_router=self.session_router,
                activity_registry=self.activity_registry,
                budget_manager=self.budget_manager,
                auto_enqueue_seed=auto_enqueue_seed,
                require_session=require_session,
                unbind_on_disable=unbind_on_disable,
                now=now,
            )

    def set_activity(
        self,
        adventure_id: str,
        active: bool,
        *,
        ttl_seconds: float | None = None,
        exclusive: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        """transport.setActivity(adventureId, active)：前台可见旅程续期或离开立即失效。"""
        with self._db_factory() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            return set_pulse_activity(
                conn,
                adventure_id,
                active,
                session_router=self.session_router,
                activity_registry=self.activity_registry,
                budget_manager=self.budget_manager,
                ttl_seconds=ttl_seconds,
                exclusive=exclusive,
                now=now,
            )

    def on_turn_committed(
        self,
        conn: sqlite3.Connection,
        *,
        instance_id: str,
        adventure_id: str,
        adventure_revision: int,
        world_revision: int | None = None,
        world_minute: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """在前台已确认回合的短事务中调用，结合当前会话与在线状态决定入队或仅推进时间锚。"""
        return schedule_on_turn_commit(
            conn,
            instance_id=instance_id,
            adventure_id=adventure_id,
            adventure_revision=adventure_revision,
            world_revision=world_revision,
            world_minute=world_minute,
            kind="advance",
            session_router=self.session_router,
            activity_registry=self.activity_registry,
            now=now,
        )
