import asyncio
from dataclasses import asdict
from .state import character, project

from fastapi import Query, Request, Response

from ..db import db
from ..errors import ApiError
from ..model_gateway.errors import GatewayError
from ..narration.core import DraftError, FakeModelClient, HistoryTurn, NarrationRequest, generate_draft, validate_request
from ..narration.lifecycle import await_request_work
from ..narration_context.types import ContextError, safe_u16_truncate
from . import service


def register_routes(app, cfg, get_store, get_builder):
    from .checkpoints import register_checkpoint_routes
    register_checkpoint_routes(app, cfg)

    @app.get('/api/v1/worlds/{world_id}/adventures')
    def list_adventures(world_id: str, offset: int = Query(0, ge=0, le=9223372036854775807)):
        with db(cfg.db_path) as conn:
            service.world_active(conn, world_id)
            return {'items': [service.run_dict(r) for r in conn.execute(
                'SELECT * FROM adventures WHERE world_id=? ORDER BY created_at DESC,id LIMIT 30 OFFSET ?', (world_id, offset))]}

    @app.post('/api/v1/worlds/{world_id}/adventures', status_code=201)
    def create_adventure(world_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            return service.create_adventure(conn, world_id, payload.get('name'), payload.get('mode', 'fake'), payload.get('character'))

    @app.get('/api/v1/adventures/{adventure_id}')
    def detail(adventure_id: str, before: int | None = Query(None, ge=1, le=9223372036854775807)):
        with db(cfg.db_path) as conn:
            run = service.get_adventure(conn, adventure_id)
            latest = conn.execute("SELECT id FROM adventure_drafts WHERE adventure_id=? AND base_revision=? AND status IN ('pending','ready') ORDER BY created_at DESC,id DESC LIMIT 1", (adventure_id, run['revision'])).fetchone()
            return {**run, 'turns': service.turns(conn, adventure_id, before),
                    'latest_draft': service.get_draft(conn, adventure_id, latest['id']) if latest else None}

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
    def commit_draft(adventure_id: str, draft_id: str, payload: dict | None = None):
        with db(cfg.db_path) as conn:
            return service.commit(conn, adventure_id, draft_id, (payload or {}).get('apply_changes', False))

    @app.post('/api/v1/adventures/{adventure_id}/drafts')
    async def prepare(adventure_id: str, payload: dict, http_request: Request):
        action = payload.get('action')
        try:
            validate_request(NarrationRequest(action=action))
        except DraftError as exc:
            raise ApiError(422, exc.code, str(exc))
        draft_id = payload.get('request_id')
        with db(cfg.db_path) as conn:
            reserved, fresh = service.reserve(conn, adventure_id, draft_id, action, payload.get('expected_revision'))
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
                bundle = get_builder()(conn, run['world_id'], action)
                recent = service.turns(conn, adventure_id, limit=6)
            history = tuple(HistoryTurn(safe_u16_truncate(t['action'], 500), safe_u16_truncate(t['result']['narrative'], 1400)) for t in recent)
            if run['mode'] == 'live':
                session_id = payload.get('session_id')
                if not isinstance(session_id, str) or not session_id:
                    raise ApiError(409, 'MODEL_SESSION_EXPIRED', '请先建立模型会话。')
                client = get_store().get_client(session_id)
            else:
                client = FakeModelClient()
            state = character(run['character']) if run['character'] is not None else None
            intro = safe_u16_truncate(f"{world['name']}\n{world['description']}", 1300)
            request = NarrationRequest(action, tuple(bundle.sources), history, state, world_intro=intro, suggest_actions=True)
            draft = await await_request_work(http_request, generate_draft(request, client), 60)
            result = {'request_id': draft_id, 'mode': run['mode'], 'narrative': draft.narrative,
                      'references': list(draft.references), 'sources': list(bundle.metadata),
                      'suggestions': list(draft.suggestions),
                      'state_before': run['character'], 'proposed_changes': draft.proposed_changes,
                      'projected_state': asdict(project(state, draft.proposed_changes)) if draft.proposed_changes is not None else None}
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
