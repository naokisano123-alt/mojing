import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore


def setup_run(client, mode='fake'):
    world = client.post('/api/v1/worlds', json={'name': '旅程世界', 'description': '群岛被常年的潮雾包围。'}).json()['id']
    run = client.post(f'/api/v1/worlds/{world}/adventures', json={'name': '港口旅程', 'mode': mode}).json()
    return world, run


def prepare(client, run, key='request-1', revision=0, action='走向灯塔'):
    return client.post(f"/api/v1/adventures/{run['id']}/drafts", json={
        'request_id': key, 'expected_revision': revision, 'action': action, 'session_id': 'session',
    })


def confirm(client, run, key='request-1'):
    return client.post(f"/api/v1/adventures/{run['id']}/drafts/{key}/commit")


def test_explicit_confirmation_idempotency_and_restart(client, db_path):
    _, run = setup_run(client)
    assert prepare(client, run).status_code == 200
    assert client.get(f"/api/v1/adventures/{run['id']}").json()['turns'] == []
    assert confirm(client, run).json()['revision'] == 1
    assert confirm(client, run).json()['revision'] == 1
    with TestClient(create_app(Settings(db_path=db_path))) as restarted:
        detail = restarted.get(f"/api/v1/adventures/{run['id']}").json()
        assert detail['revision'] == 1
        assert len(detail['turns']) == 1
        assert detail['turns'][0]['action'] == '走向灯塔'


def test_conflicting_confirmation_and_request_key(client):
    _, run = setup_run(client)
    assert prepare(client, run, 'a').status_code == 200
    assert prepare(client, run, 'a', action='改写行动').status_code == 409
    assert prepare(client, run, 'b').status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda key: confirm(client, run, key), ['a', 'b']))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(client.get(f"/api/v1/adventures/{run['id']}").json()['turns']) == 1


def test_live_receives_only_confirmed_history_and_cached_retry(db_path):
    prompts = []

    def model(request):
        body = json.loads(request.content)
        prompts.append(body['messages'][1]['content'])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'narrative': '灯塔守望者为你打开门。', 'references': [],
                                   'suggestions': [' 问候守望者 ', '问候守望者', 0]}),
        }}]})

    store = ModelSessionStore(http_transport=httpx.MockTransport(model))
    origin = 'https://adventure.example.com'
    settings = Settings(db_path=db_path, public_origin=origin, allowed_model_base_urls=('https://model.invalid',))
    with TestClient(create_app(settings, narration_store=store), headers={'Origin': origin}) as client:
        session = client.post('/api/v1/model-sessions', json={
            'base_url': 'https://model.invalid/', 'model': 'test', 'api_key': 'secret',
        }).json()
        _, run = setup_run(client, 'live')
        def draft(key, rev, action):
            return client.post(f"/api/v1/adventures/{run['id']}/drafts", json={
                'request_id': key, 'expected_revision': rev, 'action': action, 'session_id': session['session_id'],
            })
        first = draft('a', 0, '敲门')
        assert first.status_code == 200
        assert first.json()['result']['suggestions'] == ['问候守望者']
        assert draft('a', 0, '敲门').status_code == 200
        assert len(prompts) == 1
        assert '已确认的历史剧情' not in prompts[0]
        assert '群岛被常年的潮雾包围。' in prompts[0]
        assert confirm(client, run, 'a').status_code == 200
        assert draft('b', 1, '向守望者问好').status_code == 200
        assert '灯塔守望者为你打开门。' in prompts[-1]
        assert '已确认的历史剧情' in prompts[-1]
        recorded = client.get(f"/api/v1/adventures/{run['id']}").json()
        assert recorded['revision'] == 1
        assert recorded['turns'][-1]['result']['suggestions'] == ['问候守望者']


def test_discarded_or_failed_draft_cannot_commit(client, db_path):
    _, run = setup_run(client)
    assert prepare(client, run).status_code == 200
    assert client.delete(f"/api/v1/adventures/{run['id']}/drafts/request-1").status_code == 204
    assert confirm(client, run).status_code == 409
    assert client.get(f"/api/v1/adventures/{run['id']}").json()['latest_draft'] is None
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO adventure_drafts VALUES(?,?,?,?,?,?,?)", ('expired', run['id'], '旧行动', 0, 'pending', 0, None))
    assert client.get(f"/api/v1/adventures/{run['id']}/drafts/expired").json()['status'] == 'failed'
    assert prepare(client, run, 'expired', action='旧行动').status_code == 409
    assert prepare(client, run, 'new').status_code == 200


def test_deleted_world_blocks_history_and_mutations(client):
    world, run = setup_run(client)
    assert prepare(client, run).status_code == 200
    assert client.delete('/api/v1/worlds/' + world).status_code == 204
    assert client.get(f"/api/v1/adventures/{run['id']}").status_code == 404
    assert confirm(client, run).status_code == 404


def test_failure_records_no_turn_and_duplicate_pending_calls_once(tmp_path):
    class Store:
        calls = 0
        def get_client(self, session):
            return self
        async def generate(self, request):
            self.calls += 1
            await asyncio.sleep(.03)
            return {'narrative': '结果', 'references': []}
    store = Store()
    app = create_app(Settings(db_path=tmp_path/'pending.db'), narration_store=store)
    with TestClient(app) as client:
        _, run = setup_run(client, 'live')

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            url = f"/api/v1/adventures/{run['id']}/drafts"
            body = {'request_id': 'same', 'expected_revision': 0, 'action': '行动', 'session_id': 's'}
            responses = await asyncio.gather(client.post(url, json=body), client.post(url, json=body))
            assert sorted(r.status_code for r in responses) == [200, 409]
            assert store.calls == 1
            assert (await client.post(url, json=body)).status_code == 200
            assert store.calls == 1
    asyncio.run(exercise())


@pytest.mark.parametrize('revision', [True, -1, '0'])
def test_invalid_revision_rejected(client, revision):
    _, run = setup_run(client)
    assert prepare(client, run, revision=revision).status_code == 422


def test_disconnected_generation_fails_reservation_without_committing(tmp_path):
    async def exercise():
        started, closed = asyncio.Event(), asyncio.Event()
        class Store:
            def get_client(self, session):
                return self
            async def generate(self, request):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    closed.set()
        path = tmp_path/'disconnect.db'
        app = create_app(Settings(db_path=path), narration_store=Store())
        with TestClient(app) as client:
            _, run = setup_run(client, 'live')
        body = json.dumps({'request_id':'cancelled', 'action':'行动', 'expected_revision':0, 'session_id':'s'}).encode()
        sent=False
        async def receive():
            nonlocal sent
            if not sent:
                sent=True
                return {'type':'http.request','body':body,'more_body':False}
            await started.wait()
            return {'type':'http.disconnect'}
        messages=[]
        async def send(message):
            messages.append(message)
        url=f"/api/v1/adventures/{run['id']}/drafts"
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http',
               'path':url,'raw_path':url.encode(),'query_string':b'','root_path':'',
               'headers':[(b'content-type',b'application/json')],'server':('test',80),'client':('test',123)}
        await asyncio.wait_for(app(scope,receive,send),2)
        assert closed.is_set()
        assert next(m['status'] for m in messages if m['type']=='http.response.start') == 499
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM adventure_turns').fetchone()[0] == 0
            assert conn.execute('SELECT status FROM adventure_drafts').fetchone()[0] == 'failed'
            assert conn.execute('SELECT revision FROM adventures').fetchone()[0] == 0
    asyncio.run(exercise())


def test_hosted_writes_require_own_origin_and_model_url_allowlist(db_path):
    origin = 'https://adventure.example.com'
    settings = Settings(db_path=db_path, public_origin=origin, allowed_model_base_urls=('https://model.invalid/v1',))
    with TestClient(create_app(settings)) as client:
        for headers in ({}, {'Origin': 'https://other.example.com'}):
            assert client.post('/api/v1/worlds', json={'name': '世界'}, headers=headers).status_code == 403
        assert client.get('/docs').status_code == 404
        assert client.get('/api/v1/worlds').json()['total'] == 0
        headers = {'Origin': origin}
        for url in ('https://127.0.0.1/v1', 'https://model.invalid/other', 'http://model.invalid/v1'):
            response = client.post('/api/v1/model-sessions', headers=headers, json={
                'base_url': url, 'model': 'test', 'api_key': 'secret',
            })
            assert response.status_code == 422
            assert response.json()['error']['code'] == 'MODEL_ENDPOINT_NOT_ALLOWED'
        response = client.post('/api/v1/model-sessions', headers=headers, json={
            'base_url': 'https://model.invalid/v1/', 'model': 'test', 'api_key': 'secret',
        })
        assert response.status_code == 201
        assert response.headers['cache-control'] == 'no-store'
        assert 'secret' not in response.text
