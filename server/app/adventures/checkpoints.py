"""Immutable checkpoints, restored into new independent narrative journals."""
import json
import re
import time
from uuid import uuid4

from fastapi import Query
from ..db import db
from ..errors import ApiError
from . import memory, service
from ..m3.core import adapters, service as instance_service

SCHEMA = '''
CREATE TABLE IF NOT EXISTS adventure_checkpoints (
 id TEXT PRIMARY KEY, adventure_id TEXT NOT NULL REFERENCES adventures(id),
 request_id TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER NOT NULL,
 character_json TEXT, mode TEXT NOT NULL, created_at REAL NOT NULL,
 instance_snapshot TEXT,
 UNIQUE(adventure_id,request_id)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON adventure_checkpoints(adventure_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_restore_request ON adventures(origin_checkpoint_id,restore_request_id);
'''


def validate_input(name, request_id):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ApiError(422, 'VALIDATION_ERROR', '名称须为1–80字。')
    try:
        name.encode('utf-16-le')
    except UnicodeEncodeError:
        raise ApiError(422, 'VALIDATION_ERROR', '名称包含不完整字符。') from None
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
        raise ApiError(422, 'VALIDATION_ERROR', '请求标识格式无效。')
    return name.strip()


def checkpoint_dict(row):
    result = dict(row)
    result['character'] = json.loads(result.pop('character_json') or 'null')
    result.pop('request_id', None)
    result.pop('instance_snapshot', None)
    return result


def create_checkpoint(conn, adventure_id, name, request_id, revision):
    name = validate_input(name, request_id)
    service.revision_number(revision)
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    run = service.get_adventure(conn, adventure_id)
    old = conn.execute('SELECT * FROM adventure_checkpoints WHERE adventure_id=? AND request_id=?', (adventure_id, request_id)).fetchone()
    if old:
        if old['name'] != name or old['revision'] != revision:
            raise ApiError(409, 'REQUEST_CONFLICT', '同一请求不能用于不同的存档。')
        return checkpoint_dict(old)
    if not request_id.startswith('autosave_'):
        from . import journeys
        journeys.ensure_members(conn, run['world_id'])
        journey_id = conn.execute('SELECT journey_id FROM journey_members WHERE adventure_id=?', (adventure_id,)).fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM journey_save_slots WHERE journey_id=? AND kind='manual'", (journey_id,)).fetchone()[0]
        if count >= 20:
            raise ApiError(409, 'SAVE_LIMIT', '手动存档最多20个，请先删除一个存档。')
    if run['revision'] != revision:
        raise ApiError(409, 'ADVENTURE_CONFLICT', '记录已更新，请刷新后再存档。')
    id = str(uuid4())
    cursor = memory.cursor(conn, adventure_id)
    instance = instance_service.ensure_for_adventure(conn, adventure_id)
    snapshot = instance_service.snapshot(conn, instance['id'])
    snapshot['domains'] = adapters.snapshot_all(conn, instance['id'])
    instance_snapshot = json.dumps(snapshot, ensure_ascii=False)
    conn.execute('''INSERT INTO adventure_checkpoints
                 (id,adventure_id,request_id,name,revision,character_json,mode,created_at,memory_cursor,instance_snapshot)
                 VALUES(?,?,?,?,?,?,?,?,?,?)''',
                 (id, adventure_id, request_id, name, revision, json.dumps(run['character'], ensure_ascii=False),
                  run['mode'], time.time(), cursor, instance_snapshot))
    return checkpoint_dict(conn.execute('SELECT * FROM adventure_checkpoints WHERE id=?', (id,)).fetchone())


def restore(conn, checkpoint_id, name, request_id):
    name = validate_input(name, request_id)
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    checkpoint = conn.execute('SELECT * FROM adventure_checkpoints WHERE id=?', (checkpoint_id,)).fetchone()
    if not checkpoint:
        raise ApiError(404, 'CHECKPOINT_NOT_FOUND', '存档不存在。')
    source = service.get_adventure(conn, checkpoint['adventure_id'])
    old = conn.execute('SELECT * FROM adventures WHERE origin_checkpoint_id=? AND restore_request_id=?', (checkpoint_id, request_id)).fetchone()
    if old:
        if old['name'] != name:
            raise ApiError(409, 'REQUEST_CONFLICT', '重复恢复不能改变分支名称。')
        return service.run_dict(old)
    # Every inherited turn is immutable and belongs to this checkpoint's prefix.
    count = conn.execute('SELECT COUNT(*) FROM adventure_turns WHERE adventure_id=? AND sequence<=?', (source['id'], checkpoint['revision'])).fetchone()[0]
    if count != checkpoint['revision']:
        raise ApiError(409, 'CHECKPOINT_HISTORY_INVALID', '存档历史不完整，不能安全恢复。')
    id = str(uuid4())
    conn.execute('''INSERT INTO adventures(id,world_id,name,mode,revision,created_at,character_json,
                    origin_checkpoint_id,restore_request_id,opening_json)
                    SELECT ?,world_id,?,?,?,?,?,?,?,opening_json FROM adventures WHERE id=?''',
                 (id, name, checkpoint['mode'], checkpoint['revision'], time.time(),
                  checkpoint['character_json'], checkpoint_id, request_id, source['id']))
    if checkpoint['instance_snapshot']:
        snapshot = json.loads(checkpoint['instance_snapshot'])
        restored = instance_service.restore(
            conn,
            snapshot,
            adventure_id=id,
            world_id=source['world_id'],
        )
        adapters.restore_all(
            conn,
            snapshot.get('domains', {}),
            new_instance_id=restored['id'],
            world_id=source['world_id'],
        )
    else:
        instance_service.ensure_for_adventure(conn, id)
    conn.execute("INSERT INTO adventure_turns SELECT ?,sequence,'inherited:'||sequence,action,result,created_at FROM adventure_turns WHERE adventure_id=? AND sequence<=?",
                 (id, source['id'], checkpoint['revision']))
    copied = conn.execute("""INSERT INTO adventure_memory_events
                 SELECT ?,sequence,turn,origin,change_json FROM adventure_memory_events
                 WHERE adventure_id=? AND sequence<=?""", (id, source['id'], checkpoint['memory_cursor']))
    if copied.rowcount != checkpoint['memory_cursor']:
        raise ApiError(409, 'CHECKPOINT_HISTORY_INVALID', '存档日志不完整，不能安全恢复。')
    return service.get_adventure(conn, id)


def register_checkpoint_routes(app, cfg):
    @app.get('/api/v1/adventures/{adventure_id}/checkpoints')
    def list_checkpoints(adventure_id: str, offset: int = Query(0, ge=0, le=9223372036854775807)):
        with db(cfg.db_path) as conn:
            service.get_adventure(conn, adventure_id)
            return {'items': [checkpoint_dict(r) for r in conn.execute("SELECT id,adventure_id,name,revision,character_json,mode,created_at,memory_cursor FROM adventure_checkpoints WHERE adventure_id=? AND (request_id NOT LIKE 'autosave_%' OR id IN (SELECT checkpoint_id FROM journey_save_slots WHERE kind='manual')) ORDER BY created_at DESC,id LIMIT 30 OFFSET ?", (adventure_id, offset))]}

    @app.post('/api/v1/adventures/{adventure_id}/checkpoints', status_code=201)
    def save(adventure_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            return create_checkpoint(conn, adventure_id, payload.get('name'), payload.get('request_id'), payload.get('expected_revision'))

    @app.post('/api/v1/checkpoints/{checkpoint_id}/restore', status_code=201)
    def fork(checkpoint_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            return restore(conn, checkpoint_id, payload.get('name'), payload.get('request_id'))
