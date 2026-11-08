import json
from dataclasses import asdict
from .state import character, project, StateError
import re
import time
from uuid import uuid4

from ..errors import ApiError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS adventures (
 id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id),
 name TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_adventures_world ON adventures(world_id, created_at);
CREATE TABLE IF NOT EXISTS adventure_drafts (
 id TEXT NOT NULL, adventure_id TEXT NOT NULL REFERENCES adventures(id),
 action TEXT NOT NULL, base_revision INTEGER NOT NULL, status TEXT NOT NULL,
 created_at REAL NOT NULL, result TEXT, PRIMARY KEY(adventure_id,id)
);
CREATE TABLE IF NOT EXISTS adventure_turns (
 adventure_id TEXT NOT NULL REFERENCES adventures(id), sequence INTEGER NOT NULL,
 draft_id TEXT NOT NULL, action TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(adventure_id,sequence), UNIQUE(adventure_id,draft_id)
);
'''


def world_active(conn, world_id):
    if not conn.execute('SELECT id FROM worlds WHERE id=? AND deleted_at IS NULL', (world_id,)).fetchone():
        raise ApiError(404, 'WORLD_NOT_FOUND', '世界不存在或已删除。')


def get_adventure(conn, adventure_id):
    row = conn.execute('SELECT * FROM adventures WHERE id=?', (adventure_id,)).fetchone()
    if row is None:
        raise ApiError(404, 'ADVENTURE_NOT_FOUND', '冒险记录不存在。')
    world_active(conn, row['world_id'])
    return run_dict(row)


def run_dict(row):
    value = dict(row)
    value.pop('restore_request_id', None)
    value['character'] = json.loads(value.pop('character_json', None) or 'null')
    return value


def create_adventure(conn, world_id, name, mode, initial_character=None):
    world_active(conn, world_id)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or mode not in ('fake', 'live'):
        raise ApiError(422, 'VALIDATION_ERROR', '请填写1–80字的名称并选择生成模式。')
    try:
        state = asdict(character(initial_character if initial_character is not None else {}))
    except (StateError, TypeError) as exc:
        raise ApiError(422, 'CHARACTER_INVALID', str(exc)) from None
    id = str(uuid4())
    conn.execute('INSERT INTO adventures(id,world_id,name,mode,created_at,character_json) VALUES(?,?,?,?,?,?)',
                 (id, world_id, name.strip(), mode, time.time(), json.dumps(state, ensure_ascii=False)))
    return get_adventure(conn, id)


def revision_number(value):
    if type(value) is not int or value < 0:
        raise ApiError(422, 'VALIDATION_ERROR', 'expected_revision必须为非负整数。')
    return value


def get_draft(conn, adventure_id, id):
    get_adventure(conn, adventure_id)
    row = conn.execute('SELECT * FROM adventure_drafts WHERE adventure_id=? AND id=?', (adventure_id, id)).fetchone()
    if row is None:
        raise ApiError(404, 'DRAFT_NOT_FOUND', '未找到该草案，请重新生成。')
    value = dict(row)
    # A crashed process must not leave an unresolvable pending request forever.
    if value['status'] == 'pending' and time.time() - value['created_at'] >= 90:
        conn.execute("UPDATE adventure_drafts SET status='failed' WHERE adventure_id=? AND id=? AND status='pending'", (adventure_id, id))
        value['status'] = 'failed'
    value['result'] = json.loads(value['result']) if value['result'] else None
    return value


def reserve(conn, adventure_id, id, action, revision):
    revision_number(revision)
    if not isinstance(id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', id):
        raise ApiError(422, 'VALIDATION_ERROR', '请求标识格式无效。')
    # Serialize only the short reservation, never a model/network wait.
    conn.execute('BEGIN IMMEDIATE')
    run = get_adventure(conn, adventure_id)
    old = conn.execute('SELECT id FROM adventure_drafts WHERE adventure_id=? AND id=?', (adventure_id, id)).fetchone()
    if old:
        draft = get_draft(conn, adventure_id, id)
        if draft['action'] != action or draft['base_revision'] != revision:
            raise ApiError(409, 'REQUEST_CONFLICT', '同一请求标识不能用于不同的行动。')
        return draft, False
    if run['revision'] != revision:
        raise ApiError(409, 'ADVENTURE_CONFLICT', '记录已更新，请刷新后重新生成。')
    conn.execute('INSERT INTO adventure_drafts(id,adventure_id,action,base_revision,status,created_at) VALUES(?,?,?,?,?,?)',
                 (id, adventure_id, action, revision, 'pending', time.time()))
    return get_draft(conn, adventure_id, id), True


def finish(conn, adventure_id, id, result):
    get_adventure(conn, adventure_id)
    cur = conn.execute("UPDATE adventure_drafts SET status='ready',result=? WHERE adventure_id=? AND id=? AND status='pending' AND created_at>?",
                       (json.dumps(result, ensure_ascii=False), adventure_id, id, time.time() - 90))
    if cur.rowcount != 1:
        raise ApiError(409, 'DRAFT_EXPIRED', '草案已过期或取消，请重新生成。')
    return get_draft(conn, adventure_id, id)


def fail(conn, adventure_id, id):
    conn.execute("UPDATE adventure_drafts SET status='failed' WHERE adventure_id=? AND id=? AND status='pending'", (adventure_id, id))


def commit(conn, adventure_id, id, apply_changes=False):
    if type(apply_changes) is not bool:
        raise ApiError(422, 'VALIDATION_ERROR', 'apply_changes须为布尔值。')
    conn.execute('BEGIN IMMEDIATE')
    run = get_adventure(conn, adventure_id)
    draft = get_draft(conn, adventure_id, id)
    if draft['status'] == 'committed':
        saved = conn.execute('SELECT result FROM adventure_turns WHERE adventure_id=? AND draft_id=?', (adventure_id,id)).fetchone()
        if json.loads(saved['result']).get('changes_accepted', False) != apply_changes:
            raise ApiError(409, 'COMMIT_CONFLICT', '重复确认不能改变已保存的状态选择。')
        return run  # Lost response retries never append another turn.
    if draft['status'] != 'ready':
        raise ApiError(409, 'DRAFT_NOT_READY', '草案尚未就绪或已失败。')
    result = dict(draft['result'])
    before = run['character']
    if result.get('state_before') != before:
        raise ApiError(409, 'STATE_CONFLICT', '角色状态已变化，请重新生成。')
    after = before
    if apply_changes:
        if before is None or result.get('proposed_changes') is None:
            raise ApiError(422, 'STATE_PROPOSAL_MISSING', '本草案没有可采用的状态提议。')
        try:
            after = asdict(project(character(before), result['proposed_changes']))
        except StateError as exc:
            raise ApiError(422, 'STATE_INVALID', str(exc)) from None
    result.update(state_before=before, state_after=after, changes_accepted=apply_changes)
    cur = conn.execute('UPDATE adventures SET revision=revision+1,character_json=? WHERE id=? AND revision=?',
                       (json.dumps(after, ensure_ascii=False) if after else None, adventure_id, draft['base_revision']))
    if cur.rowcount != 1:
        raise ApiError(409, 'ADVENTURE_CONFLICT', '已有另一回合被确认，请重新生成。')
    conn.execute('INSERT INTO adventure_turns VALUES(?,?,?,?,?,?)',
                 (adventure_id, run['revision'] + 1, id, draft['action'], json.dumps(result, ensure_ascii=False), time.time()))
    conn.execute("UPDATE adventure_drafts SET status='committed' WHERE adventure_id=? AND id=?", (adventure_id, id))
    return get_adventure(conn, adventure_id)


def turns(conn, adventure_id, before=None, limit=30):
    get_adventure(conn, adventure_id)
    rows = conn.execute('SELECT * FROM adventure_turns WHERE adventure_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?',
                        (adventure_id, before if before is not None else 9223372036854775807, limit)).fetchall()
    return [{**dict(r), 'result': json.loads(r['result'])} for r in reversed(rows)]
