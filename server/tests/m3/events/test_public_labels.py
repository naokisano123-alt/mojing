import json
import sqlite3
import pytest
from app.m3.events import apply_proposal, get_event, snapshot, restore
from app.m3.events.errors import EventValidationError
from app.m3.events.schema import ensure_events_schema
from app.m3.pulse.observability import get_pulse_status


def seed(labels=('躁动的狮群', '城内契约', '无声的潮汐')):
    return {'mode': 'seed', 'operations': [{
        'op': 'create', 'kind': 'thread', 'title': f'秘密计划{i}', 'description': '尚未公开的幕后行动',
        'public_label': label, 'location': '密室', 'scope': 'local', 'next_update_minute': 60,
        'source_ids': ['chunk:canon'],
    } for i, label in enumerate(labels)]}


def test_labels_survive_advance_and_restore_without_exposing_titles():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        ensure_events_schema(conn)
        for instance in ('original', 'branch'):
            conn.execute('INSERT INTO world_instances(id,world_id,world_minute,revision) VALUES (?,?,0,0)', (instance, 'world'))
        result = apply_proposal(conn, 'original', seed(), allowed_source_ids={'chunk:canon'})
        event_id = result['created_event_ids'][0]
        assert get_event(conn, 'original', event_id)['public_label'] == '躁动的狮群'
        apply_proposal(conn, 'original', {'mode': 'advance', 'operations': [{
            'op': 'update', 'event_id': event_id, 'expected_event_revision': 1,
            'stage': '酝酿', 'next_update_minute': 120,
        }]}, allowed_source_ids={'chunk:canon'})
        restored = restore(conn, 'branch', snapshot(conn, 'original'), allowed_source_ids={'chunk:canon'})
        assert next(e for e in restored['events'] if e['event_id'] == event_id)['public_label'] == '躁动的狮群'
        public = get_pulse_status(conn, 'branch')['simulation']
        assert public['initialized']
        assert {item['label'] for item in public['events']} == {'躁动的狮群', '城内契约', '无声的潮汐'}
        assert '秘密计划' not in json.dumps(public, ensure_ascii=False)
        assert '密室' not in json.dumps(public, ensure_ascii=False)
        conn.execute("UPDATE world_events SET status='completed',next_update_minute=NULL WHERE instance_id='branch'")
        assert get_pulse_status(conn, 'branch')['simulation'] == {'initialized': True, 'events': [], 'updated_minute': 0}


def test_old_database_adds_labels_idempotently():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        ensure_events_schema(conn)
        conn.execute('ALTER TABLE world_events DROP COLUMN public_label')
        ensure_events_schema(conn)
        ensure_events_schema(conn)
        assert 'public_label' in {row[1] for row in conn.execute('PRAGMA table_info(world_events)')}
        conn.execute("INSERT INTO world_instances(id,world_id,world_minute,revision) VALUES ('old','w',0,0)")
        old_seed = seed()
        for operation in old_seed['operations']:
            operation.pop('public_label')
        created = apply_proposal(conn, 'old', old_seed, allowed_source_ids={'chunk:canon'})
        public = get_pulse_status(conn, 'old')['simulation']
        assert all(item['label'] == '未明的动向' for item in public['events'])
        assert '秘密计划' not in json.dumps(public, ensure_ascii=False)
        event_id = created['created_event_ids'][0]
        apply_proposal(conn, 'old', {'mode': 'advance', 'operations': [{
            'op': 'update', 'event_id': event_id, 'expected_event_revision': 1,
            'public_label': '旧城微澜', 'next_update_minute': 120,
        }]}, allowed_source_ids={'chunk:canon'})
        assert get_event(conn, 'old', event_id)['public_label'] == '旧城微澜'


@pytest.mark.parametrize('label', ['', 12, '长' * 25])
def test_invalid_labels_fail_atomically(label):
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        ensure_events_schema(conn)
        conn.execute("INSERT INTO world_instances(id,world_id,world_minute,revision) VALUES ('i','w',0,0)")
        with pytest.raises(EventValidationError) as failure:
            apply_proposal(conn, 'i', seed((label,)), allowed_source_ids={'chunk:canon'})
        assert failure.value.code == 'VALIDATION_ERROR'
        assert conn.execute('SELECT COUNT(*) FROM world_events').fetchone()[0] == 0
