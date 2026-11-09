import json
from .state import StateError, character

from fastapi import Query, Request, Response

from ..db import db
from ..errors import ApiError
from ..model_gateway.errors import GatewayError
from ..narration.core import DraftError, FakeModelClient, HistoryTurn, NarrationRequest, generate_draft, validate_request
from ..narration.lifecycle import await_request_work
from ..narration_context.types import ContextError, safe_u16_truncate
from . import item_descriptions, memory, scenario, service


def register_routes(app, cfg, get_store, get_builder, pulse_worker):
    from ..m3.core import pulse_preview, service as instances
    from ..m3.pulse.queue import resume_waiting_jobs
    from .checkpoints import register_checkpoint_routes
    register_checkpoint_routes(app, cfg)

    @app.get('/api/v1/worlds/{world_id}/openings')
    def list_openings(world_id: str):
        with db(cfg.db_path) as conn:
            service.world_active(conn, world_id)
            return {'items': scenario.list_openings(conn, world_id)}

    @app.get('/api/v1/worlds/{world_id}/opening-settings')
    def opening_settings(world_id: str):
        with db(cfg.db_path) as conn:
            service.world_active(conn, world_id)
            return {'items': scenario.list_openings(conn, world_id, author=True)}

    @app.post('/api/v1/worlds/{world_id}/opening-settings', status_code=201)
    def create_opening(world_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            service.world_active(conn, world_id)
            return scenario.save_opening(conn, world_id, payload)

    @app.put('/api/v1/worlds/{world_id}/opening-settings/{opening_id}')
    def update_opening(world_id: str, opening_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            service.world_active(conn, world_id)
            return scenario.save_opening(conn, world_id, payload, opening_id)

    @app.delete('/api/v1/worlds/{world_id}/opening-settings/{opening_id}', status_code=204)
    def delete_opening(world_id: str, opening_id: str):
        with db(cfg.db_path) as conn:
            service.world_active(conn, world_id)
            conn.execute("DELETE FROM world_openings WHERE world_id=? AND id=?", (world_id, opening_id))
            return Response(status_code=204)

    @app.get('/api/v1/worlds/{world_id}/adventures')
    def list_adventures(world_id: str, offset: int = Query(0, ge=0, le=9223372036854775807)):
        with db(cfg.db_path) as conn:
            service.world_active(conn, world_id)
            return {'items': [service.run_dict(r) for r in conn.execute(
                'SELECT * FROM adventures WHERE world_id=? ORDER BY created_at DESC,id LIMIT 30 OFFSET ?', (world_id, offset))]}

    @app.post('/api/v1/worlds/{world_id}/adventures', status_code=201)
    def create_adventure(world_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            return service.create_adventure(conn, world_id, payload.get('name'), payload.get('mode', 'fake'),
                                            payload.get('character'), payload.get('opening_id'))

    @app.get('/api/v1/adventures/{adventure_id}')
    def detail(adventure_id: str, before: int | None = Query(None, ge=1, le=9223372036854775807)):
        with db(cfg.db_path) as conn:
            conn.execute("BEGIN")
            run = service.get_adventure(conn, adventure_id)
            latest = conn.execute("SELECT id FROM adventure_drafts WHERE adventure_id=? AND base_revision=? AND status IN ('pending','ready') ORDER BY created_at DESC,id DESC LIMIT 1", (adventure_id, run['revision'])).fetchone()
            return {**run, 'turns': service.turns(conn, adventure_id, before),
                    'item_descriptions': item_descriptions.for_character(conn, run['world_id'], run['character']),
                    'memory': memory.view(memory.replay(conn, adventure_id)),
                    'latest_draft': service.get_draft(conn, adventure_id, latest['id']) if latest else None}

    @app.patch('/api/v1/adventures/{adventure_id}/memory')
    def correct_memory(adventure_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            run = service.get_adventure(conn, adventure_id)
            snapshot = memory.replay(conn, adventure_id)
            if (type(payload.get('expected_cursor')) is not int
                    or payload['expected_cursor'] != snapshot['cursor']):
                raise ApiError(409, 'MEMORY_CONFLICT', '日志已更新，请刷新记录后再更正。')
            try:
                change = memory.correction(snapshot, payload)
            except StateError as exc:
                raise ApiError(422, 'MEMORY_INVALID', str(exc)) from None
            memory.append(conn, adventure_id, run['revision'], snapshot['cursor'], 'correction', [change])
            return memory.view(memory.replay(conn, adventure_id))

    @app.patch('/api/v1/adventures/{adventure_id}/item-description')
    def edit_item_description(adventure_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            return item_descriptions.write(conn, service.get_adventure(conn, adventure_id), payload)

    @app.get('/api/v1/adventures/{adventure_id}/drafts/{draft_id}')
    def draft_status(adventure_id: str, draft_id: str):
        with db(cfg.db_path) as conn:
            return service.get_draft(conn, adventure_id, draft_id)

    @app.delete('/api/v1/adventures/{adventure_id}/drafts/{draft_id}', status_code=204)
    def discard_draft(adventure_id: str, draft_id: str):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            draft = service.get_draft(conn, adventure_id, draft_id)
            if draft['status'] == 'committed':
                raise ApiError(409, 'DRAFT_COMMITTED', '已确认回合不能当作草案删除。')
            conn.execute("UPDATE adventure_drafts SET status='discarded' WHERE adventure_id=? AND id=?", (adventure_id, draft_id))
        return Response(status_code=204)

    @app.post('/api/v1/adventures/{adventure_id}/drafts/{draft_id}/commit')
    async def commit_draft(adventure_id: str, draft_id: str, payload: dict | None = None):
        active_instance = None
        with db(cfg.db_path) as conn:
            run = service.commit(conn, adventure_id, draft_id, (payload or {}).get('apply_changes', False))
            if run['mode'] == 'fake':
                instance = instances.for_adventure(conn, adventure_id)
                config = conn.execute(
                    'SELECT enabled,paused FROM pulse_instance_configs WHERE instance_id=?', (instance['id'],)
                ).fetchone()
                if config and config['enabled'] and not config['paused']:
                    active_instance = instance['id']
                    resume_waiting_jobs(conn, active_instance)
        if active_instance:
            pulse_preview.activate(pulse_worker, active_instance)
        return run

    @app.post('/api/v1/adventures/{adventure_id}/drafts')
    async def prepare(adventure_id: str, payload: dict, http_request: Request):
        action = payload.get('action')
        try:
            validate_request(NarrationRequest(action=action))
        except DraftError as exc:
            raise ApiError(422, exc.code, str(exc))
        draft_id = payload.get('request_id')
        with db(cfg.db_path) as conn:
            reserved, fresh = service.reserve(conn, adventure_id, draft_id, action, payload.get('expected_revision'),
                                               payload.get('advance_minutes'))
        if not fresh:
            if reserved['status'] in ('ready', 'committed'):
                return reserved
            raise ApiError(409, 'DRAFT_PENDING' if reserved['status'] == 'pending' else 'DRAFT_FAILED',
                           '该请求正在生成或已失败，请查询状态；重新生成需使用新的请求标识。')
        try:
            with db(cfg.db_path) as conn:
                conn.execute('BEGIN')  # Coherent short snapshot of revision, lore and history.
                run = service.get_adventure(conn, adventure_id)
                if run['revision'] != reserved['base_revision']:
                    raise ApiError(409, 'ADVENTURE_CONFLICT', '记录已更新，请刷新后重新生成。')
                world = conn.execute('SELECT name, description FROM worlds WHERE id=?', (run['world_id'],)).fetchone()
                raw_opening = conn.execute('SELECT opening_json FROM adventures WHERE id=?', (adventure_id,)).fetchone()[0]
                opening = json.loads(raw_opening) if raw_opening else None
                bundle = get_builder()(conn, run['world_id'], action)
                recent = service.turns(conn, adventure_id, limit=6)
                snapshot = memory.replay(conn, adventure_id)
                history = tuple(HistoryTurn(
                    safe_u16_truncate(t['action'], 500),
                    safe_u16_truncate(t['result']['narrative'], 1150 if t['result'].get('clock', {}).get('events') else 1400)
                    + safe_u16_truncate(
                        '【事件】' + '；'.join(f"{event['title']}：{event['description']}" for event in t['result']['clock']['events'])
                        if t['result'].get('clock', {}).get('events') else '', 250,
                    ),
                ) for t in recent)
                facts, excerpts = memory.context(
                    conn, adventure_id, snapshot, action, (run['character'] or {}).get('location', ''),
                    [f"{turn.action} {turn.narrative}" for turn in history], recent[0]['sequence'] if recent else 1,
                )
                notes = item_descriptions.for_character(conn, run['world_id'], run['character'])
            if run['mode'] == 'live':
                session_id = payload.get('session_id')
                if not isinstance(session_id, str) or not session_id:
                    raise ApiError(409, 'MODEL_SESSION_EXPIRED', '请先建立模型会话。')
                client = get_store().get_client(session_id)
            else:
                client = FakeModelClient()
            state = character(run['character']) if run['character'] is not None else None
            intro = safe_u16_truncate(f"{world['name']}\n{world['description']}", 1300)
            requested = reserved['advance_minutes']
            clock = None
            clock_max = None
            if opening:
                intro += '\n开局：' + safe_u16_truncate(opening['intro'], 1000)
                intro += '\n当前世界时间：' + scenario.world_time(opening, state.elapsed_minutes)
                if requested is not None:
                    clock = scenario.plan(opening, state.elapsed_minutes, requested)
                else:
                    available = min(1440, 1_000_000_000 - state.elapsed_minutes)
                    clock_max = scenario.plan(opening, state.elapsed_minutes, available)['elapsed_minutes']
            request = NarrationRequest(action, tuple(bundle.sources), history, state, world_intro=intro, suggest_actions=True,
                                       item_descriptions=item_descriptions.for_prompt(notes, action),
                                       adventure_memory=facts, memory_excerpts=excerpts,
                                       memory_items=tuple(snapshot["items"].values()),
                                       clock=clock, clock_max_minutes=clock_max)
            draft = await await_request_work(http_request, generate_draft(request, client), 60)
            result = {'request_id': draft_id, 'mode': run['mode'], 'narrative': draft.narrative,
                      'references': list(draft.references), 'sources': list(bundle.metadata),
                      'suggestions': list(draft.suggestions),
                      'state_before': run['character'], 'proposed_changes': draft.proposed_changes,
                      'projected_state': draft.projected_state, 'memory_cursor': snapshot['cursor'],
                      'memory_proposal': list(draft.memory_changes)}
            if opening and clock is None and draft.projected_state is not None:
                advanced = draft.projected_state['elapsed_minutes'] - state.elapsed_minutes
                if advanced:
                    clock = scenario.plan(opening, state.elapsed_minutes, advanced)
            if clock:
                result['clock'] = clock
                result['clock_required'] = requested is not None or bool(clock['events'])
            with db(cfg.db_path) as conn:
                return service.finish(conn, adventure_id, draft_id, result)
        except BaseException as exc:
            with db(cfg.db_path) as conn:
                service.fail(conn, adventure_id, draft_id)
            if isinstance(exc, (GatewayError, ContextError)):
                raise ApiError(exc.status, exc.code, exc.message) from None
            if isinstance(exc, DraftError):
                raise ApiError(502, exc.code, str(exc)) from None
            if isinstance(exc, TimeoutError):
                raise ApiError(504, 'MODEL_TIMEOUT', '生成超时，未写入回合。') from None
            raise
