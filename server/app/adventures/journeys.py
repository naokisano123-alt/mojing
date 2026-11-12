"""Journey folders and save slots; immutable journals remain the history engine."""
from uuid import uuid4
from fastapi import Response
from ..db import db
from ..errors import ApiError
from . import checkpoints, service

SCHEMA = '''
CREATE TABLE IF NOT EXISTS journey_members (
 adventure_id TEXT PRIMARY KEY REFERENCES adventures(id), journey_id TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS journey_members_folder ON journey_members(journey_id);
CREATE TABLE IF NOT EXISTS journey_save_slots (
 checkpoint_id TEXT PRIMARY KEY REFERENCES adventure_checkpoints(id),
 journey_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('auto','manual'))
);
CREATE UNIQUE INDEX IF NOT EXISTS journey_single_auto ON journey_save_slots(journey_id) WHERE kind='auto';
'''

def ensure_members(conn, world_id):
    service.world_active(conn, world_id)
    # Legacy branches remain separate journeys, preserving their existing names.
    conn.execute('''INSERT OR IGNORE INTO journey_members(adventure_id,journey_id,active)
                    SELECT id,id,1 FROM adventures WHERE world_id=?''', (world_id,))
    conn.execute('''INSERT OR IGNORE INTO journey_save_slots(checkpoint_id,journey_id,kind)
        SELECT c.id,m.journey_id,'manual' FROM adventure_checkpoints c
        JOIN journey_members m ON m.adventure_id=c.adventure_id
        JOIN adventures a ON a.id=c.adventure_id WHERE a.world_id=?
        AND c.request_id NOT LIKE 'autosave_%' ''', (world_id,))

def folder(conn, journey_id):
    root = service.get_adventure(conn, journey_id)
    ensure_members(conn, root['world_id'])
    member = conn.execute('SELECT journey_id FROM journey_members WHERE adventure_id=?', (journey_id,)).fetchone()
    if member['journey_id'] != journey_id:
        raise ApiError(404, 'JOURNEY_NOT_FOUND', '旅程不存在。')
    return root

def autosave(conn, adventure_id):
    run = service.get_adventure(conn, adventure_id)
    ensure_members(conn, run['world_id'])
    journey = conn.execute('SELECT journey_id FROM journey_members WHERE adventure_id=?', (adventure_id,)).fetchone()[0]
    conn.execute('UPDATE journey_members SET active=(adventure_id=?) WHERE journey_id=?', (adventure_id, journey))
    saved = checkpoints.create_checkpoint(conn, adventure_id, '自动存档', 'autosave_' + uuid4().hex, run['revision'])
    old = conn.execute("SELECT checkpoint_id FROM journey_save_slots WHERE journey_id=? AND kind='auto'", (journey,)).fetchone()
    conn.execute("DELETE FROM journey_save_slots WHERE journey_id=? AND kind='auto'", (journey,))
    conn.execute('INSERT INTO journey_save_slots VALUES(?,?,?)', (saved['id'], journey, 'auto'))
    if old:
        # Keep snapshots referenced by a loaded journal; otherwise reclaim the replaced slot.
        conn.execute('''DELETE FROM adventure_checkpoints WHERE id=? AND NOT EXISTS
            (SELECT 1 FROM adventures WHERE origin_checkpoint_id=?)''', (old[0], old[0]))
    return saved

def list_folders(conn, world_id):
    ensure_members(conn, world_id)
    return [dict(row) for row in conn.execute('''SELECT r.id,r.name,m.adventure_id AS active_adventure_id,
        a.revision,a.mode FROM journey_members m JOIN adventures r ON r.id=m.journey_id
        JOIN adventures a ON a.id=m.adventure_id WHERE r.world_id=? AND m.active=1 ORDER BY r.created_at DESC,r.id''', (world_id,))]

def register_routes(app, cfg):
    @app.get('/api/v1/worlds/{world_id}/journeys')
    def listing(world_id: str):
        with db(cfg.db_path) as conn:
            return {'items': list_folders(conn, world_id)}

    @app.get('/api/v1/journeys/{journey_id}/saves')
    def saves(journey_id: str):
        with db(cfg.db_path) as conn:
            folder(conn, journey_id)
            rows = conn.execute('''SELECT c.*,s.kind FROM journey_save_slots s
                JOIN adventure_checkpoints c ON c.id=s.checkpoint_id WHERE s.journey_id=?
                ORDER BY CASE s.kind WHEN 'auto' THEN 0 ELSE 1 END,c.created_at DESC,c.id''', (journey_id,))
            return {'items': [checkpoints.checkpoint_dict(row) for row in rows]}

    @app.patch('/api/v1/journeys/{journey_id}')
    def rename(journey_id: str, payload: dict):
        name = checkpoints.validate_input(payload.get('name'), 'rename')
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            folder(conn, journey_id)
            conn.execute('''UPDATE adventures SET name=? WHERE id IN
                (SELECT adventure_id FROM journey_members WHERE journey_id=?)''', (name, journey_id))
            return {'id': journey_id, 'name': name}

    @app.post('/api/v1/journeys/{journey_id}/saves', status_code=201)
    def save(journey_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            root = folder(conn, journey_id)
            run = service.get_adventure(conn, payload.get('adventure_id'))
            member = conn.execute('SELECT journey_id FROM journey_members WHERE adventure_id=?', (run['id'],)).fetchone()
            if run['world_id'] != root['world_id'] or not member or member[0] != journey_id:
                raise ApiError(409, 'JOURNEY_MISMATCH', '进度不属于这段旅程。')
            count = conn.execute("SELECT COUNT(*) FROM journey_save_slots WHERE journey_id=? AND kind='manual'", (journey_id,)).fetchone()[0]
            previous = conn.execute('SELECT id FROM adventure_checkpoints WHERE adventure_id=? AND request_id=?', (run['id'], payload.get('request_id'))).fetchone()
            if count >= 20 and not previous:
                raise ApiError(409, 'SAVE_LIMIT', '手动存档最多20个，请先删除一个存档。')
            saved = checkpoints.create_checkpoint(conn, run['id'], payload.get('name'), payload.get('request_id'), payload.get('expected_revision'))
            conn.execute("INSERT OR IGNORE INTO journey_save_slots VALUES(?,?,'manual')", (saved['id'], journey_id))
            return {**saved, 'kind': 'manual'}

    @app.delete('/api/v1/journeys/{journey_id}/saves/{save_id}', status_code=204)
    def delete(journey_id: str, save_id: str):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            folder(conn, journey_id)
            slot = conn.execute('SELECT kind FROM journey_save_slots WHERE checkpoint_id=? AND journey_id=?', (save_id, journey_id)).fetchone()
            if not slot or slot[0] != 'manual':
                raise ApiError(409, 'SAVE_NOT_MANUAL', '只能删除手动存档。')
            conn.execute('DELETE FROM journey_save_slots WHERE checkpoint_id=?', (save_id,))
            # Tombstone the immutable checkpoint so compatibility discovery cannot recreate a deleted slot.
            conn.execute("UPDATE adventure_checkpoints SET request_id=? WHERE id=?", ('autosave_retired_' + uuid4().hex, save_id))
        return Response(status_code=204)

    @app.post('/api/v1/journeys/{journey_id}/load/{save_id}', status_code=201)
    def load(journey_id: str, save_id: str, payload: dict):
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            root = folder(conn, journey_id)
            if not conn.execute('SELECT 1 FROM journey_save_slots WHERE checkpoint_id=? AND journey_id=?', (save_id, journey_id)).fetchone():
                raise ApiError(404, 'SAVE_NOT_FOUND', '存档不属于这段旅程。')
            run = checkpoints.restore(conn, save_id, payload.get('name') or root['name'], payload.get('request_id'))
            if payload.get('as_new_journey') is not True:
                conn.execute('INSERT OR REPLACE INTO journey_members VALUES(?,?,1)', (run['id'], journey_id))
            autosave(conn, run['id'])
            return run

def export_metadata(conn, world_id, allowed):
    ensure_members(conn, world_id)
    return {
        'members': [dict(r) for r in conn.execute('SELECT m.* FROM journey_members m JOIN adventures a ON a.id=m.adventure_id WHERE a.world_id=?', (world_id,)) if r['adventure_id'] in allowed],
        'slots': [dict(r) for r in conn.execute('SELECT s.* FROM journey_save_slots s JOIN adventures a ON a.id=s.journey_id WHERE a.world_id=?', (world_id,)) if r['journey_id'] in allowed],
    }

def import_metadata(conn, metadata, ids, world_id):
    if metadata is None:
        ensure_members(conn, world_id)
        return
    if not isinstance(metadata, dict) or not isinstance(metadata.get('members'), list) or not isinstance(metadata.get('slots'), list):
        raise ValueError('Invalid journey metadata')
    members, slots = metadata['members'], metadata['slots']
    seen, active = set(), set()
    for row in members:
        aid, jid = ids['adventures'][row['adventure_id']], ids['adventures'][row['journey_id']]
        if aid in seen or type(row['active']) is not int or row['active'] not in (0, 1) or (row['active'] and jid in active):
            raise ValueError('Invalid journey membership')
        seen.add(aid)
        if row['active']: active.add(jid)
        conn.execute('INSERT INTO journey_members VALUES(?,?,?)', (aid, jid, row['active']))
    if seen != set(ids['adventures'].values()) or active != {ids['adventures'][r['journey_id']] for r in members}:
        raise ValueError('Incomplete journey metadata')
    for row in slots:
        cid, jid = ids['adventure_checkpoints'][row['checkpoint_id']], ids['adventures'][row['journey_id']]
        cp = conn.execute('SELECT adventure_id FROM adventure_checkpoints WHERE id=?', (cid,)).fetchone()
        member = conn.execute('SELECT journey_id FROM journey_members WHERE adventure_id=?', (cp[0],)).fetchone()
        if not member or member[0] != jid or row['kind'] not in ('auto', 'manual'):
            raise ValueError('Invalid save slot')
        conn.execute('INSERT INTO journey_save_slots VALUES(?,?,?)', (cid, jid, row['kind']))
    roots = {row['journey_id'] for row in members}
    if any(not any(member['adventure_id'] == root and member['journey_id'] == root for member in members) for root in roots):
        raise ValueError('Invalid journey root')
    # Tag only the new copies. Never change idempotency keys of existing local saves.
    for adventure_id in ids['adventures'].values():
        conn.execute("UPDATE adventure_checkpoints SET request_id='autosave_imported_'||id WHERE adventure_id=?", (adventure_id,))
