"""Analyze imported material, then apply the accepted opening in one transaction."""

import hashlib
import json
from dataclasses import replace
from uuid import uuid4

from fastapi import Request, Response

from ..db import db
from ..errors import ApiError
from ..m3 import world_analysis as analysis
from ..m3.encounters.service import list_player_world_news
from ..m3.core import service as instances
from ..m3.world_analysis.extractor import discover_world_source_ownership
from ..narration.lifecycle import await_request_work
from . import scenario, service
from .state import StateError

_PREFIX = '/api/v1/worlds/{world_id}/world-start'
_START_TIME = '1000-01-01T08:00'


def _world(conn, world_id):
    service.world_active(conn, world_id)
    return conn.execute('SELECT name, description FROM worlds WHERE id=?', (world_id,)).fetchone()


def _materials(conn, world_id):
    rows = conn.execute("""
        SELECT 'doc:' || id AS source_id, 'document' AS kind, filename AS title,
               text_length AS char_count, chunk_count FROM documents
        WHERE world_id=? AND deleted_at IS NULL AND status='ready' AND chunk_count>0
        UNION ALL
        SELECT 'entry:' || id, 'lore_entry', title, length(summary), 0 FROM lore_entries
        WHERE world_id=? AND deleted_at IS NULL AND status='confirmed'
        ORDER BY title, source_id
    """, (world_id, world_id))
    return [{**dict(row), 'world_id': world_id, 'selected_by_default': True} for row in rows]


def _selection(payload):
    selected = payload.get('selected_source_ids', [])
    if not isinstance(selected, list) or len(selected) > 1000 or any(
        not isinstance(s, str) or not 5 < len(s) <= 160 or not s.startswith(('doc:', 'entry:'))
        for s in selected
    ):
        raise ApiError(422, 'WORLD_START_SOURCE_INVALID', '请选择有效的世界资料。')
    return list(dict.fromkeys(selected))


def _extract(conn, world_id, point, selected):
    return analysis.extract_and_batch_world_sources(
        conn, world_id, point,
        selected_source_ids=[sid for sid in selected if sid.startswith('entry:')],
        selected_document_ids=[sid[4:] for sid in selected if sid.startswith('doc:')],
        raise_on_empty=True,
    )


def _fingerprint(extraction):
    batches = [batch.to_dict() for batch in extraction['batches']]
    return hashlib.sha256(json.dumps(batches, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _opening(payload, intro, shortcut=""):
    try:
        return scenario.normalize({
            'name': payload.get('adventure_name'),
            'starts_at': payload.get('starts_at'),
            'intro': intro,
            'shortcut': shortcut,
            'character': {'name': payload.get('character_name'), 'location': payload.get('location')},
        })
    except StateError as exc:
        raise ApiError(422, 'WORLD_START_POINT_INVALID', str(exc)) from None


def _point(payload, *, quick=False):
    if not isinstance(payload, dict):
        raise ApiError(422, 'WORLD_START_POINT_INVALID', '请填写开局地点、日期和角色。')
    point = {key: payload.get(key) for key in ('adventure_name', 'starts_at', 'location', 'character_name')}
    if quick:
        for key, default in {'starts_at': _START_TIME, 'location': '起点', 'character_name': '旅人'}.items():
            point[key] = point[key] or default
    opening = _opening(point, '旅程开始。')
    return {
        'adventure_name': opening['name'], 'starts_at': opening['starts_at'],
        'location': opening['character']['location'], 'character_name': opening['character']['name'],
    }


def _mode(payload):
    mode = payload.get('mode', 'fake')
    if mode not in ('fake', 'live'):
        raise ApiError(422, 'WORLD_START_MODE_INVALID', '请选择模拟或真实模型旅程。')
    return mode


def _session(payload, get_store):
    session_id = payload.get('session_id')
    if not isinstance(session_id, str) or not session_id:
        raise ApiError(409, 'MODEL_SESSION_EXPIRED', '请先建立模型会话。')
    return get_store().get_client(session_id)


def _load_draft(conn, world_id, draft_id):
    _world(conn, world_id)
    row = conn.execute("""
        SELECT * FROM m3_world_analysis_drafts WHERE world_id=? AND draft_id=?
    """, (world_id, draft_id)).fetchone()
    if row is None:
        raise ApiError(404, 'WORLD_START_DRAFT_NOT_FOUND', '未找到这份开局草案。')
    return row


def _project_draft(conn, world_id, world_name, draft, point, selected, mode, kind, attempts, extraction):
    materials = {item['source_id']: item for item in _materials(conn, world_id)}
    docs = {f"chunk:{row['id']}": f"doc:{row['document_id']}" for row in conn.execute(
        'SELECT id, document_id FROM chunks WHERE world_id=?', (world_id,),
    )}

    def refs(source_ids):
        ids = dict.fromkeys(docs.get(sid, sid) for sid in source_ids)
        return [{key: materials[sid][key] for key in ('source_id', 'title', 'kind')}
                for sid in ids if sid in materials and sid in selected]

    def origin(grounding):
        return 'canon' if grounding == 'canon' else 'inferred'

    public = draft.player_opening_summary
    places = [
        {'place_id': place.location_id, 'name': place.name, 'summary': place.description,
         'origin': origin(place.grounding_type), 'source_refs': refs(place.source_ids)}
        for place in draft.locations
        if place.visibility == 'public' or (
            place.visibility == 'local_observable' and place.name == point['location']
        )
    ]
    contacts = [
        {'npc_id': npc['npc_id'], 'name': npc['display_name'], 'role': npc['role'],
         'location': npc['location'], 'public_impression': npc['public_demeanor'],
         'contactable': npc['availability'] == 'available', 'known_relation': '',
         'origin': origin(npc['grounding_type']), 'source_refs': refs(npc['source_ids'])}
        for npc in public['present_npcs']
    ]
    facts = [
        {'fact_id': f'opening-fact-{i}', 'title': fact['title'], 'content': fact['summary'],
         'knowledge_type': fact['knowledge_type'], 'channel': fact['channel'], 'source_label': '',
         'origin': origin(fact['grounding_type']), 'source_refs': refs(fact['source_ids'])}
        for i, fact in enumerate(public['known_situations'])
    ]
    summary = [f"你来到{point['location']}。"]
    summary.extend(place['summary'] for place in places if place['name'] == point['location'] and place['summary'])
    summary.extend(f"在这里，你见到{npc['name']}。" for npc in contacts)
    suggestions = [f"和{npc['name']}打招呼，问问这里的近况。" for npc in contacts if npc['contactable']][:3]
    return {
        'draft_id': draft.draft_id, 'world_id': world_id, 'world_name': world_name, 'mode': mode,
        'run_kind': kind, 'attempt_number': attempts, 'selected_source_ids': selected, 'start_point': point,
        'opening_situation_summary': '\n'.join(summary), 'known_places': places, 'known_contacts': contacts,
        'player_known_facts': facts,
        'inferred_notes': [item['name'] for item in (*places, *contacts) if item['origin'] == 'inferred'],
        'conflict_warnings': [
            {'conflict_id': conflict['conflict_id'], 'summary': conflict['summary'],
             'resolution_hint': '已保留两处资料的差异。',
             'source_refs': refs((conflict['primary_source_id'], conflict['conflicting_source_id']))}
            for conflict in public['conflicts']
        ],
        'suggested_first_actions': suggestions, 'committed': False,
        'batch_info': {'total_batches': len(extraction['batches']),
                       'merged_chunks': sum(len(batch.input_range.chunk_ids) for batch in extraction['batches'])},
    }


def _create(conn, world_id, point, mode, summary, row=None):
    intro = summary['opening_situation_summary'] if summary else f"你来到{point['location']}。"
    suggestions = summary['suggested_first_actions'] if summary else []
    opening = _opening(point, intro, suggestions[0] if suggestions else "")
    run = service.create_adventure(
        conn, world_id, point['adventure_name'], mode, opening_snapshot=opening,
        instance_id=row['instance_id'] if row else None,
    )
    return run, opening


def _result(conn, run, opening, summary=None):
    instance = instances.for_adventure(conn, run['id'])
    return {
        'adventure_id': run['id'], 'world_id': run['world_id'], 'world_instance_id': instance['id'],
        'draft_id': summary['draft_id'] if summary else None, 'adventure_name': run['name'], 'mode': run['mode'],
        'run_kind': summary['run_kind'] if summary else 'initial', 'quick_start': summary is None,
        'committed': True, 'starts_at': opening['starts_at'], 'initial_world_minute': instance['world_minute'],
        'world_revision': instance['world_revision'], 'player_character': run['character'],
        'opening_narrative': opening['intro'],
        'initial_player_news': list_player_world_news(conn, instance['id']) if summary else [],
        'bound_source_ids': summary['selected_source_ids'] if summary else [],
        'suggested_actions': summary['suggested_first_actions'] if summary else [],
    }


def register_world_start_routes(app, path, get_store):
    @app.get(_PREFIX)
    def bootstrap(world_id: str):
        with db(path) as conn:
            world = _world(conn, world_id)
            materials = _materials(conn, world_id)
            latest = conn.execute("""
                SELECT player_summary_json FROM m3_world_analysis_drafts
                WHERE world_id=? AND status='ready_to_confirm'
                ORDER BY updated_at DESC, draft_id DESC LIMIT 1
            """, (world_id,)).fetchone()
            draft = json.loads(latest['player_summary_json']) if latest else None
            options = [{
                'start_id': option['id'], 'label': option['name'], 'starts_at': option['starts_at'],
                'location': option['character']['location'], 'character_name': option['character']['name'],
                'character_role': '', 'summary': option['intro'], 'origin': 'canon', 'source_refs': [],
            } for option in scenario.list_openings(conn, world_id)]
            return {
                'world_id': world_id, 'world_name': world['name'], 'world_description': world['description'],
                'materials': materials, 'start_points': options, 'latest_draft': draft,
                'phase': 'ready_to_confirm' if draft else ('waiting' if materials else 'insufficient_materials'),
            }

    @app.post(_PREFIX + '/analyze')
    async def analyze_start(world_id: str, payload: dict, request: Request):
        point = _point(payload.get('start_point'))
        selected = _selection(payload)
        mode = _mode(payload)
        kind = payload.get('run_kind', 'initial')
        if kind not in ('initial', 'retry', 'reopen'):
            raise ApiError(422, 'WORLD_START_KIND_INVALID', '检查点分支请从旅程存档创建。')
        instance_id = str(uuid4())
        draft_id = str(uuid4())
        starting = {**point, 'world_minute': 0, 'run_mode': kind, 'request_id': draft_id}
        with db(path) as conn:
            _world(conn, world_id)
            extraction = _extract(conn, world_id, starting, selected)
            other_instances = [row['id'] for row in conn.execute(
                'SELECT id FROM world_instances WHERE world_id=?', (world_id,),
            )]
            previous_id = payload.get('retry_of_draft_id')
            attempts = 1
            if kind == 'retry' and previous_id:
                if not isinstance(previous_id, str):
                    raise ApiError(422, 'WORLD_START_DRAFT_INVALID', '开局草案标识无效。')
                previous = _load_draft(conn, world_id, previous_id)
                attempts = previous['attempt_count'] + 1
        model = analysis.FakeWorldAnalysisModelClient()
        if mode == 'live':
            client = _session(payload, get_store)
            model = analysis.OpenAIWorldAnalysisModelClient(session_client=client)
        try:
            draft = await await_request_work(request, analysis.analyze_world_initial_situation(
                extraction, model, instance_id=instance_id, other_instance_ids=other_instances,
            ), 120)
        except TimeoutError:
            raise ApiError(504, 'WORLD_START_TIMEOUT', '开局分析超时，请重试或减少资料。') from None
        draft = replace(draft, draft_id=draft_id, request_id=draft_id, status='ready_to_confirm', run_mode=kind)
        fingerprint = _fingerprint(extraction)
        with db(path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            world = _world(conn, world_id)
            summary = _project_draft(conn, world_id, world['name'], draft, point, selected, mode, kind,
                                     attempts, extraction)
            analysis.save_analysis_draft(conn, draft)
            raw = draft.to_dict()
            raw['_sources_fingerprint'] = fingerprint
            conn.execute("""
                UPDATE m3_world_analysis_drafts SET draft_json=?, player_summary_json=?, attempt_count=?
                WHERE draft_id=?
            """, (json.dumps(raw, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), attempts, draft_id))
            return summary

    @app.delete(_PREFIX + '/drafts/{draft_id}', status_code=204)
    def discard(world_id: str, draft_id: str):
        with db(path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = _load_draft(conn, world_id, draft_id)
            if row['status'] == 'applied':
                raise ApiError(409, 'WORLD_START_ALREADY_APPLIED', '已建立的旅程不能作为草案删除。')
            conn.execute('DELETE FROM m3_world_analysis_drafts WHERE draft_id=?', (draft_id,))
        return Response(status_code=204)

    @app.post(_PREFIX + '/drafts/{draft_id}/confirm')
    def confirm(world_id: str, draft_id: str, payload: dict | None = None):
        with db(path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = _load_draft(conn, world_id, draft_id)
            summary = json.loads(row['player_summary_json'])
            if row['status'] == 'applied':
                return summary
            if row['status'] != 'ready_to_confirm':
                raise ApiError(409, 'WORLD_START_DRAFT_NOT_READY', '这份开局草案尚未就绪。')
            point = summary['start_point']
            name = (payload or {}).get('adventure_name')
            if name is not None:
                point = {**point, 'adventure_name': name}
            raw = json.loads(row['draft_json'])
            fingerprint = raw.pop('_sources_fingerprint')
            raw.pop('player_opening_summary', None)
            extraction = _extract(conn, world_id, raw['starting_point'], summary['selected_source_ids'])
            if fingerprint != _fingerprint(extraction):
                raise ApiError(409, 'WORLD_START_SOURCES_CHANGED', '资料已变化，请重新生成开局草案。')
            draft = analysis.validate_analysis_draft(raw, {
                **discover_world_source_ownership(conn, world_id),
                'instance_id': row['instance_id'], 'starting_point': raw['starting_point'],
                'allowed_source_ids': extraction['allowed_source_ids'],
            })
            run, opening = _create(conn, world_id, point, summary['mode'], summary, row)
            analysis.apply_draft_to_instance_in_savepoint(conn, row['instance_id'], draft,
                                                           advance_instance_revision=True)
            result = _result(conn, run, opening, summary)
            conn.execute("""
                UPDATE m3_world_analysis_drafts SET adventure_id=?, player_summary_json=? WHERE draft_id=?
            """, (run['id'], json.dumps(result, ensure_ascii=False), draft_id))
            return result

    @app.post(_PREFIX + '/quick-start', status_code=201)
    def quick_start(world_id: str, payload: dict):
        point = _point(payload, quick=True)
        mode = _mode(payload)
        if mode == 'live':
            _session(payload, get_store)
        with db(path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            run, opening = _create(conn, world_id, point, mode, None)
            return _result(conn, run, opening)
