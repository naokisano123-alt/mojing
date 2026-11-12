"""M3 第四轮（Wave 4）AI 初始化草案适配层与草案状态管理。

契约依据（`docs/collaboration/M3_WAVE4_GEMINI_MARCIN.md` & `docs/contracts/M3_WAVE4_API.md`）：
1. 设计可直接接现有事件（`m3/events`）、NPC（`m3/npcs`）、知识账本（`m3/knowledge`）的结构化草案适配层 `adapt_draft_to_domain_proposals(draft)`。
2. 初始人物的动作安排使用 Li 可以直接读取的实例内身份（`npc_id`、`target_npc_id`）和时间语义（`world_minute`、`duration_minutes`、`next_due_minute`、`plan_kind`、`action_kind`）。
3. 区分首次分析（`initial`）、失败重试（`retry`）、重开新局（`reopen`）与检查点分支（`branch`）。
4. 未确认草案（`waiting` / `analyzing` / `ready_to_confirm` / `failed_retryable`）绝不写入正式事件、人物、知识与时间；确认后可在调用方事务（`SAVEPOINT`）内原子写入三大账本并绑定来源。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .errors import (
    AnalysisStateConflictError,
    AnalysisValidationError,
    CrossInstanceAnalysisError,
    CrossWorldSourceError,
)
from .extractor import safe_u16_truncate
from .schema import ensure_world_analysis_schema
from .types import (
    ValidatedWorldAnalysisDraft,
    WorldAnalysisValidationContext,
)
from .validator import validate_analysis_draft


def make_event_id_factory(event_ids: list[str] | tuple[str, ...]) -> Callable[[], str]:
    """生成供 `events.apply_proposal(..., id_factory=...)` 使用的确定性事件 ID 分配器。

    确保 `world_events` 落库后的 `event_id` 与 NPC 的 `cause_event_ids` 及知识账本的 `source_event_id` 严格一致。
    """
    iterator = iter(event_ids)

    def _next_id() -> str:
        try:
            return next(iterator)
        except StopIteration:
            return f"evt_{uuid4().hex[:10]}"

    return _next_id


def adapt_draft_to_domain_proposals(
    draft: ValidatedWorldAnalysisDraft | Mapping[str, Any],
    context: WorldAnalysisValidationContext | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """将已校验的 `ValidatedWorldAnalysisDraft` 转换为可直接传给 `events`、`npcs`、`knowledge` 的标准提议载荷。"""
    if not isinstance(draft, ValidatedWorldAnalysisDraft):
        if context is None:
            raise AnalysisValidationError(
                "CONTEXT_REQUIRED",
                "当传入原始字典草案时，必须提供 WorldAnalysisValidationContext 以完成校验后再适配。",
            )
        validated = validate_analysis_draft(draft, context)
    else:
        validated = draft

    instance_id = validated.instance_id
    world_minute = validated.world_minute
    base_rev = validated.base_world_revision

    # 1. 适配 m3/events Seed 提议
    event_ops: list[dict[str, Any]] = []
    event_id_seq: list[str] = []
    for ev in validated.events:
        event_id_seq.append(ev.event_id)
        reason_prefix = (
            f"[资料依据:{','.join(ev.source_ids)}]"
            if ev.grounding_type == "canon"
            else f"[旅程内补写:{ev.supplement_reason or '动态局势推演'}]"
        )
        op_dict: dict[str, Any] = {
            "op": "create",
            "kind": "thread",
            "title": ev.title,
            "description": ev.description,
            "location": ev.location,
            "scope": ev.scope,
            "participants": list(ev.participants),
            "stage": ev.stage,
            "status": "active",
            "next_update_minute": ev.next_update_minute,
            "cause_event_ids": [],
            "source_ids": list(ev.source_ids),
            "reason": safe_u16_truncate(f"{reason_prefix} 初始化主线「{ev.title}」。", 280),
        }
        if ev.consequences:
            op_dict["consequences"] = [dict(c) for c in ev.consequences]
        event_ops.append(op_dict)

    event_seed_proposal: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": "seed",
        "kind": "seed",
        "world_minute": world_minute,
        "target_world_minute": world_minute,
        "base_world_revision": base_rev,
        "world_revision": base_rev,
        "summary": validated.summary,
        "operations": event_ops,
    }

    # 2. 适配 m3/npcs Seed 提议与 Li 的初始人物状态结构
    npc_ops: list[dict[str, Any]] = []
    li_npc_states: list[dict[str, Any]] = []

    for npc in validated.npcs:
        display_role = (
            f"{npc.role}（{npc.homonym_disambiguator}）"
            if npc.homonym_disambiguator and npc.homonym_disambiguator not in npc.role
            else npc.role
        )
        grounding_tag = (
            f"资料依据({','.join(npc.source_ids)})"
            if npc.grounding_type == "canon"
            else f"旅程内补写({npc.supplement_reason})"
        )
        npc_op: dict[str, Any] = {
            "op": "seed_npc",
            "npc_id": npc.npc_id,
            "name": npc.name,
            "role": display_role,
            "faction": npc.faction,
            "home_location": npc.home_location,
            "location": npc.location,
            "current_activity": npc.current_activity,
            "action_kind": npc.action_kind,
            "motivation": npc.motivation,
            "goal": npc.goal,
            "depth_tier": npc.depth_tier,
            "depth_reason": f"开局起点「{validated.starting_point.location}」初始化分级（{npc.depth_tier}）",
            "status": "active",
            "availability": npc.availability,
            "duration_minutes": npc.duration_minutes,
            "next_due_minute": npc.next_due_minute,
            "cause_event_ids": list(npc.cause_event_ids),
            "cause_fact_ids": [],
            "source_ids": list(npc.source_ids),
            "reason": safe_u16_truncate(f"[{grounding_tag}] 初始化人物 {npc.name} ({npc.plan_kind})。", 280),
        }
        if npc.action_kind == "travel":
            npc_op["from_location"] = npc.from_location or npc.home_location
            npc_op["to_location"] = npc.to_location or npc.location
        elif npc.action_kind == "conversation_plan":
            npc_op["target_npc_id"] = npc.target_npc_id
            npc_op["meeting_location"] = npc.meeting_location or npc.location
            npc_op["conversation_topic"] = npc.conversation_topic
        elif npc.action_kind == "shift_change":
            npc_op["from_shift"] = "开局准备"
            npc_op["to_shift"] = npc.shift_name or "当值守备"
            npc_op["shift_name"] = npc.shift_name or "当值守备"
        elif npc.action_kind == "task_progress":
            npc_op["task_id"] = npc.task_id or f"task_{npc.npc_id}_init"
            npc_op["task_title"] = npc.task_title or npc.current_activity
            npc_op["task_stage"] = npc.task_stage or "筹备阶段"
            npc_op["task_progress_percent"] = (
                npc.task_progress_percent if npc.task_progress_percent is not None else 20
            )
            npc_op["task_status"] = "active"

        npc_ops.append(npc_op)

        li_npc_states.append(
            {
                "instance_id": instance_id,
                "npc_id": npc.npc_id,
                "name": npc.name,
                "homonym_disambiguator": npc.homonym_disambiguator,
                "aliases": list(npc.aliases),
                "role": display_role,
                "faction": npc.faction,
                "home_location": npc.home_location,
                "location": npc.location,
                "present_at_start": npc.present_at_start,
                "plan_kind": npc.plan_kind,
                "action_kind": npc.action_kind,
                "current_activity": npc.current_activity,
                "motivation": npc.motivation,
                "goal": npc.goal,
                "depth_tier": npc.depth_tier,
                "availability": npc.availability,
                "world_minute": world_minute,
                "duration_minutes": npc.duration_minutes,
                "next_due_minute": npc.next_due_minute,
                "target_npc_id": npc.target_npc_id,
                "conversation_topic": npc.conversation_topic,
                "from_location": npc.from_location,
                "to_location": npc.to_location,
                "shift_name": npc.shift_name,
                "task_id": npc.task_id,
                "cause_event_ids": list(npc.cause_event_ids),
                "known_event_ids": list(npc.initial_known_event_ids),
                "relationships": [r.to_dict() for r in npc.relationships],
                "grounding_type": npc.grounding_type,
                "source_ids": list(npc.source_ids),
                "supplement_reason": npc.supplement_reason,
            }
        )

    npc_seed_proposal: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": "seed",
        "kind": "seed",
        "world_minute": world_minute,
        "target_world_minute": world_minute,
        "base_world_revision": base_rev,
        "world_revision": base_rev,
        "idempotency_key": f"init_npcs_{validated.draft_id}",
        "summary": f"初始化「{validated.starting_point.location}」及关联区域共 {len(npc_ops)} 名 NPC 状态与到期日程。",
        "operations": npc_ops,
    }

    # 3. 适配 m3/knowledge 持有人与初始认知记录（严格限制玩家未获知信息）
    knowledge_holders: list[dict[str, Any]] = [
        {
            "instance_id": instance_id,
            "holder_id": "player",
            "name": validated.starting_point.character_name,
            "role": "player",
            "location": validated.starting_point.location,
            "created_at_minute": world_minute,
        }
    ]
    for npc in validated.npcs:
        knowledge_holders.append(
            {
                "instance_id": instance_id,
                "holder_id": npc.npc_id,
                "name": npc.name,
                "role": npc.role,
                "location": npc.location,
                "created_at_minute": world_minute,
            }
        )

    events_by_id = {ev.event_id: ev for ev in validated.events}
    knowledge_transmissions: list[dict[str, Any]] = []

    for npc in validated.npcs:
        for k_idx, ev_id in enumerate(npc.initial_known_event_ids):
            ev = events_by_id.get(ev_id)
            if ev is None:
                continue
            fact_id = f"fact_init_{npc.npc_id}_{k_idx + 1}"
            knowledge_transmissions.append(
                {
                    "op": "record",
                    "fact_id": fact_id,
                    "holder_id": npc.npc_id,
                    "knowledge_type": "confirmed",
                    "channel": "observed",
                    "source_event_id": ev.event_id,
                    "learned_minute": world_minute,
                    "confidence": 0.95,
                    "status": "active",
                    "title": safe_u16_truncate(f"{npc.name}掌握的局势：{ev.title}", 80),
                    "content": safe_u16_truncate(
                        f"作为{npc.role}，{npc.name}知悉：{ev.description}",
                        360,
                    ),
                    "reason": f"开局初始化人物 {npc.npc_id} 的已知背景事实。",
                }
            )

    for p_idx, ev in enumerate(validated.events):
        if not ev.player_known_at_start or not ev.player_rumor_content:
            continue
        p_channel = ev.player_channel if ev.player_channel in ("observed", "notice", "rumor") else "rumor"
        p_ktype = ev.player_knowledge_type if ev.player_knowledge_type in ("confirmed", "suspected", "rumor") else "rumor"
        p_title = ev.player_rumor_title or f"{ev.location}坊间风声"
        p_content = ev.player_rumor_content
        knowledge_transmissions.append(
            {
                "op": "record",
                "fact_id": f"fact_init_player_{p_idx + 1}",
                "holder_id": "player",
                "knowledge_type": p_ktype,
                "channel": p_channel,
                "source_event_id": ev.event_id,
                "learned_minute": world_minute,
                "confidence": 0.9 if p_ktype == "confirmed" else 0.65,
                "status": "active",
                "title": safe_u16_truncate(p_title, 80),
                "content": safe_u16_truncate(p_content, 360),
                "reason": ("玩家确认的开局亲历或被告知事实，仅写入玩家认知。" if ev.player_known_basis else
                           f"玩家在起点「{validated.starting_point.location}」开局可获知的公开信息或本地传闻。"),
            }
        )

    return {
        "draft_id": validated.draft_id,
        "world_id": validated.world_id,
        "instance_id": instance_id,
        "world_minute": world_minute,
        "base_world_revision": base_rev,
        "run_mode": validated.run_mode,
        "event_seed_proposal": event_seed_proposal,
        "event_id_sequence": tuple(event_id_seq),
        "make_event_id_factory": lambda: make_event_id_factory(event_id_seq),
        "npc_seed_proposal": npc_seed_proposal,
        "li_npc_initial_states": li_npc_states,
        "knowledge_holders": knowledge_holders,
        "knowledge_transmissions": knowledge_transmissions,
        "allowed_event_ids": tuple(event_id_seq),
        "player_opening_summary": dict(validated.player_opening_summary),
        "supplement_ledger": [dict(x) for x in validated.supplement_ledger],
        "identity_resolution_log": [dict(x) for x in validated.identity_resolution_log],
        "conflicts": [c.to_dict() for c in validated.conflicts],
    }


def save_analysis_draft(
    conn: sqlite3.Connection,
    draft: ValidatedWorldAnalysisDraft,
    *,
    adventure_id: str | None = None,
) -> dict[str, Any]:
    """将校验通过但尚未确认的草案暂存到 `m3_world_analysis_drafts`（不修改正式事件、NPC、知识与实例时间）。"""
    ensure_world_analysis_schema(conn)
    now = time.time()
    existing = conn.execute(
        """
        SELECT draft_id, status, analysis_fingerprint, draft_json, player_summary_json,
               attempt_count, run_mode, starting_location, world_minute
        FROM m3_world_analysis_drafts
        WHERE world_id = ? AND instance_id = ? AND request_id = ?
        """,
        (draft.world_id, draft.instance_id, draft.request_id),
    ).fetchone()

    if existing is not None:
        old_draft_id = str(existing[0])
        old_status = str(existing[1])
        old_fp = str(existing[2] or "")
        old_draft_json = str(existing[3])
        old_summary_json = str(existing[4])
        old_attempts = int(existing[5] or 1)
        old_start_loc = str(existing[7] or "")
        old_world_min = int(existing[8] or 0)

        if (
            old_start_loc != draft.starting_point.location
            or old_world_min != draft.world_minute
            or (old_fp and old_fp != draft.analysis_fingerprint)
        ):
            raise AnalysisStateConflictError(
                "DUPLICATE_ANALYSIS_CONFLICT",
                f"请求标识 {draft.request_id!r} 已被用于不同的起点或资料集合，请更换 request_id。",
                {
                    "request_id": draft.request_id,
                    "existing_draft_id": old_draft_id,
                },
            )
        if old_status in ("ready_to_confirm", "applied") and draft.run_mode != "retry":
            return {
                "draft_id": old_draft_id,
                "world_id": draft.world_id,
                "instance_id": draft.instance_id,
                "status": old_status,
                "run_mode": str(existing[6]),
                "request_id": draft.request_id,
                "attempt_count": old_attempts,
                "idempotent_replay": True,
                "draft": json.loads(old_draft_json),
                "player_opening_summary": json.loads(old_summary_json),
            }
        new_attempts = old_attempts + 1
        effective_mode = "retry" if old_status == "failed_retryable" else draft.run_mode
        conn.execute(
            """
            UPDATE m3_world_analysis_drafts
            SET status = ?,
                run_mode = ?,
                analysis_fingerprint = ?,
                draft_json = ?,
                player_summary_json = ?,
                error_code = NULL,
                error_message = NULL,
                attempt_count = ?,
                updated_at = ?
            WHERE draft_id = ?
            """,
            (
                draft.status,
                effective_mode,
                draft.analysis_fingerprint,
                json.dumps(draft.to_dict(), ensure_ascii=False),
                json.dumps(draft.player_opening_summary, ensure_ascii=False),
                new_attempts,
                now,
                old_draft_id,
            ),
        )
        return {
            "draft_id": old_draft_id,
            "world_id": draft.world_id,
            "instance_id": draft.instance_id,
            "status": draft.status,
            "run_mode": effective_mode,
            "request_id": draft.request_id,
            "attempt_count": new_attempts,
            "idempotent_replay": False,
            "draft": draft.to_dict(),
            "player_opening_summary": dict(draft.player_opening_summary),
        }

    conn.execute(
        """
        INSERT INTO m3_world_analysis_drafts (
            draft_id, world_id, instance_id, adventure_id, request_id, run_mode,
            status, starting_location, world_minute, base_world_revision,
            analysis_fingerprint, parent_draft_id, branch_from_checkpoint_id,
            draft_json, player_summary_json, attempt_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            draft.draft_id,
            draft.world_id,
            draft.instance_id,
            adventure_id,
            draft.request_id,
            draft.run_mode,
            draft.status,
            draft.starting_point.location,
            draft.world_minute,
            draft.base_world_revision,
            draft.analysis_fingerprint,
            draft.starting_point.parent_draft_id,
            draft.starting_point.branch_from_checkpoint_id,
            json.dumps(draft.to_dict(), ensure_ascii=False),
            json.dumps(draft.player_opening_summary, ensure_ascii=False),
            now,
            now,
        ),
    )
    return {
        "draft_id": draft.draft_id,
        "world_id": draft.world_id,
        "instance_id": draft.instance_id,
        "status": draft.status,
        "run_mode": draft.run_mode,
        "request_id": draft.request_id,
        "attempt_count": 1,
        "idempotent_replay": False,
        "draft": draft.to_dict(),
        "player_opening_summary": dict(draft.player_opening_summary),
    }


def record_failed_analysis_attempt(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    instance_id: str,
    request_id: str,
    starting_location: str,
    world_minute: int = 0,
    base_world_revision: int = 0,
    analysis_fingerprint: str = "",
    error_code: str = "MODEL_ANALYSIS_FAILED",
    error_message: str = "AI 初始局势分析失败，可重试。",
    draft_id: str | None = None,
) -> dict[str, Any]:
    """记录失败的分析请求状态（`status='failed_retryable'`），确保未确认或失败时绝不污染正式领域表，且后续可重试。"""
    ensure_world_analysis_schema(conn)
    now = time.time()
    existing = conn.execute(
        """
        SELECT draft_id, attempt_count FROM m3_world_analysis_drafts
        WHERE world_id = ? AND instance_id = ? AND request_id = ?
        """,
        (world_id, instance_id, request_id),
    ).fetchone()
    if existing is not None:
        did = str(existing[0])
        attempts = int(existing[1] or 1) + 1
        conn.execute(
            """
            UPDATE m3_world_analysis_drafts
            SET status = 'failed_retryable',
                run_mode = 'retry',
                error_code = ?,
                error_message = ?,
                attempt_count = ?,
                updated_at = ?
            WHERE draft_id = ?
            """,
            (error_code, error_message, attempts, now, did),
        )
    else:
        did = draft_id or f"wad_fail_{uuid4().hex[:10]}"
        attempts = 1
        conn.execute(
            """
            INSERT INTO m3_world_analysis_drafts (
                draft_id, world_id, instance_id, request_id, run_mode, status,
                starting_location, world_minute, base_world_revision, analysis_fingerprint,
                error_code, error_message, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'initial', 'failed_retryable', ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                did,
                world_id,
                instance_id,
                request_id,
                starting_location,
                world_minute,
                base_world_revision,
                analysis_fingerprint,
                error_code,
                error_message,
                now,
                now,
            ),
        )
    return {
        "draft_id": did,
        "world_id": world_id,
        "instance_id": instance_id,
        "request_id": request_id,
        "status": "failed_retryable",
        "retryable": True,
        "attempt_count": attempts,
        "error_code": error_code,
        "error_message": error_message,
    }


def apply_draft_to_instance_in_savepoint(
    conn: sqlite3.Connection,
    instance_id: str,
    draft: ValidatedWorldAnalysisDraft,
    *,
    advance_instance_revision: bool = False,
) -> dict[str, Any]:
    """在 `SAVEPOINT` 内将确认后的初始化草案原子写入 `m3/events`、`m3/npcs` 和 `m3/knowledge`（不自行 commit）。"""
    from .. import events, knowledge, npcs

    ensure_world_analysis_schema(conn)
    if draft.instance_id != instance_id:
        raise CrossInstanceAnalysisError(instance_id, draft.instance_id, draft.instance_id)

    sp_name = f"sp_m3_world_analysis_apply_{uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        inst_row = conn.execute(
            "SELECT world_id, world_minute, revision FROM world_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if inst_row is None:
            raise AnalysisValidationError(
                "INSTANCE_NOT_FOUND",
                f"目标世界实例 {instance_id!r} 不存在。",
                status=404,
            )
        db_world_id = str(inst_row[0] or "")
        if db_world_id and db_world_id != draft.world_id:
            raise CrossWorldSourceError(
                db_world_id,
                f"world:{draft.world_id}",
                draft.world_id,
                f"草案所属世界 ({draft.world_id}) 与目标实例所属世界 ({db_world_id}) 不匹配。",
            )

        existing_row = conn.execute(
            """
            SELECT draft_id, status, draft_json
            FROM m3_world_analysis_drafts
            WHERE draft_id = ? OR (world_id = ? AND instance_id = ? AND request_id = ?)
            """,
            (draft.draft_id, draft.world_id, instance_id, draft.request_id),
        ).fetchone()
        if existing_row is not None and str(existing_row[1]) == "applied":
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            return {
                "draft_id": str(existing_row[0]),
                "world_id": draft.world_id,
                "instance_id": instance_id,
                "status": "applied",
                "idempotent_replay": True,
                "player_opening_summary": {**draft.player_opening_summary, "status": "applied"},
            }

        bundle = adapt_draft_to_domain_proposals(draft)

        ev_res = events.apply_proposal(
            conn,
            instance_id,
            bundle["event_seed_proposal"],
            world_minute=draft.world_minute,
            allowed_source_ids=draft.allowed_source_ids,
            id_factory=bundle["make_event_id_factory"](),
        )

        npc_res = npcs.apply_proposal(
            conn,
            instance_id,
            bundle["npc_seed_proposal"],
            player_location=draft.starting_point.location,
            allowed_source_ids=draft.allowed_source_ids,
            advance_world_revision=False,
        )

        for holder in bundle["knowledge_holders"]:
            knowledge.register_holder(
                conn,
                instance_id,
                holder["holder_id"],
                name=holder["name"],
                role=holder["role"],
                location=holder["location"],
                created_at_minute=holder["created_at_minute"],
            )
        know_res = knowledge.record_transmission(
            conn,
            instance_id,
            bundle["knowledge_transmissions"],
            allowed_event_ids=bundle["allowed_event_ids"],
        )
        recorded_count = len(getattr(know_res, "applied", None) or bundle["knowledge_transmissions"])

        if advance_instance_revision:
            conn.execute(
                "UPDATE world_instances SET revision = revision + 1 WHERE id = ?",
                (instance_id,),
            )

        now = time.time()
        applied_summary = {**draft.player_opening_summary, "status": "applied"}
        if existing_row is not None:
            target_draft_id = str(existing_row[0])
            conn.execute(
                """
                UPDATE m3_world_analysis_drafts
                SET status = 'applied',
                    player_summary_json = ?,
                    updated_at = ?,
                    applied_at = ?
                WHERE draft_id = ?
                """,
                (
                    json.dumps(applied_summary, ensure_ascii=False),
                    now,
                    now,
                    target_draft_id,
                ),
            )
        else:
            target_draft_id = draft.draft_id
            conn.execute(
                """
                INSERT INTO m3_world_analysis_drafts (
                    draft_id, world_id, instance_id, request_id, run_mode, status,
                    starting_location, world_minute, base_world_revision, analysis_fingerprint,
                    draft_json, player_summary_json, attempt_count, created_at, updated_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    target_draft_id,
                    draft.world_id,
                    instance_id,
                    draft.request_id,
                    draft.run_mode,
                    draft.starting_point.location,
                    draft.world_minute,
                    draft.base_world_revision,
                    draft.analysis_fingerprint,
                    json.dumps(draft.to_dict(), ensure_ascii=False),
                    json.dumps(applied_summary, ensure_ascii=False),
                    now,
                    now,
                    now,
                ),
            )

        result_payload = {
            "draft_id": target_draft_id,
            "world_id": draft.world_id,
            "instance_id": instance_id,
            "status": "applied",
            "idempotent_replay": False,
            "created_event_ids": ev_res.get("created_event_ids", []),
            "created_npc_ids": npc_res.get("created_npc_ids", []),
            "recorded_fact_count": recorded_count,
            "player_opening_summary": applied_summary,
        }
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        return result_payload
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise
