import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from app.model_gateway.client import OpenAIModelClient
from app.model_gateway.errors import GatewayError
from app.model_gateway.url import validate_and_normalize_base_url
from app.narration import NarrationRequest
from app.narration_context.types import ContextBundle


def test_live_preview_real_store_transport_context_and_no_writes(tmp_path):
    path = tmp_path / 'live.db'
    cited = []

    def upstream(request):
        assert request.headers['Authorization'] == 'Bearer test-secret'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'narrative': '港口晨雾散去。', 'references': cited}),
        }}]})

    store = ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    with TestClient(create_app(Settings(db_path=path), narration_store=store)) as client:
        world = client.post('/api/v1/worlds', json={'name': '港口'}).json()['id']
        doc = client.post(f'/api/v1/worlds/{world}/documents',
                          files={'file': ('harbor.txt', '港口晨雾'.encode())}).json()['id']
        chunk = client.get(f'/api/v1/documents/{doc}/chunks/0').json()['id']
        cited.append('chunk:' + chunk)
        session = client.post('/api/v1/model-sessions', json={
            'base_url': 'https://model.invalid/v1', 'model': 'test', 'api_key': 'test-secret',
        }).json()

        def snapshot():
            with sqlite3.connect(path) as conn:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                return {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}

        before = snapshot()
        response = client.post(f'/api/v1/worlds/{world}/narration-preview', json={
            'action': '查看港口', 'mode': 'live', 'session_id': session['session_id'],
        })
        assert response.status_code == 200, response.text
        assert response.json()['references'] == cited
        assert response.json()['sources'][0]['chunk_id'] == chunk
        assert 'test-secret' not in response.text
        assert snapshot() == before
        cited[:] = ['chunk:not-provided']
        invalid = client.post(f'/api/v1/worlds/{world}/narration-preview', json={
            'action': '查看', 'mode': 'live', 'session_id': session['session_id'],
        })
        assert invalid.status_code == 502
        assert invalid.json()['error']['code'] == 'MODEL_REFERENCE_INVALID'
        assert snapshot() == before


@pytest.mark.parametrize('timeout', [False, True])
def test_asgi_disconnect_or_timeout_closes_upstream(tmp_path, monkeypatch, timeout):
    import app.main as main
    if timeout:
        monkeypatch.setattr(main, 'MODEL_REQUEST_TIMEOUT', 0.02)

    async def exercise():
        started, closed = asyncio.Event(), asyncio.Event()

        async def slow_upstream(request):
            started.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

        store = ModelSessionStore(http_transport=httpx.MockTransport(slow_upstream))
        session = store.create('https://model.invalid/v1', 'test', 'secret')
        app = create_app(Settings(db_path=tmp_path / 'cancel.db'), narration_store=store,
                         context_builder=lambda *args: ContextBundle())
        body = json.dumps({'action': '看看', 'mode': 'live', 'session_id': session['session_id']}).encode()
        sent_body = False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            if timeout:
                await asyncio.Future()
            await started.wait()
            return {'type': 'http.disconnect'}

        messages = []

        async def send(message):
            messages.append(message)

        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'http', 'path': '/api/v1/worlds/w/narration-preview',
                 'raw_path': b'/api/v1/worlds/w/narration-preview', 'query_string': b'',
                 'root_path': '', 'headers': [(b'content-type', b'application/json')],
                 'server': ('test', 80), 'client': ('test', 123)}
        await asyncio.wait_for(app(scope, receive, send), 2)
        assert closed.is_set(), 'Request must await upstream cancellation cleanup'
        assert next(m['status'] for m in messages if m['type'] == 'http.response.start') == (504 if timeout else 499)

    asyncio.run(exercise())


@pytest.mark.parametrize('reason', ['length', 'content_filter', 'tool_calls'])
def test_valid_json_with_unfinished_completion_is_rejected(reason):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'choices': [{
        'finish_reason': reason, 'message': {'content': '{"narrative":"ok","references":[]}'},
    }]}))
    client = OpenAIModelClient('https://model.invalid', 'test', 'secret', transport=transport)
    with pytest.raises(GatewayError):
        asyncio.run(client.generate(NarrationRequest('行动')))
    with pytest.raises(GatewayError):
        asyncio.run(client.test())


def test_connection_test_rejects_empty_choices():
    client = OpenAIModelClient('https://model.invalid', 'test', 'secret',
                              transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': []})))
    with pytest.raises(GatewayError):
        asyncio.run(client.test())


@pytest.mark.parametrize('url', ['https://[bad', 'https://:80', 'https://host:99999',
                                  'https://user@host', 'https://bad host', 'https://host:abc'])
def test_malformed_urls_are_safe_validation_errors(url):
    with pytest.raises(GatewayError) as error:
        validate_and_normalize_base_url(url)
    assert error.value.status == 422

@pytest.mark.parametrize('entries', [False, True])
def test_context_stops_reading_rows_at_source_budget(tmp_path, entries):
    from app.narration_context import build_context
    path = tmp_path / 'bounded.db'
    with TestClient(create_app(Settings(db_path=path))) as client:
        wid = client.post('/api/v1/worlds', json={'name': 'Budget'}).json()['id']
        doc = client.post(f'/api/v1/worlds/{wid}/documents', files={'file': ('a.txt', b'body')}).json()['id']
        if entries:
            for i in range(20):
                assert client.post(f'/api/v1/worlds/{wid}/lore-entries', json={
                    'type': 'rule', 'title': f'Rule {i}', 'status': 'confirmed',
                }).status_code == 201
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if not entries:
            conn.execute('DELETE FROM chunks WHERE document_id=?', (doc,))
            conn.executemany('INSERT INTO chunks(id,document_id,world_id,chunk_index,heading_path,start_char,end_char,text) VALUES(?,?,?,?,?,?,?,?)',
                             [(f'c{i}', doc, wid, i, '[]', i * 10, i * 10 + 4, 'body') for i in range(1000)])
            conn.commit()
        reads = []

        class Cursor:
            def __init__(self):
                self.cursor = conn.cursor()
            def execute(self, *args):
                self.cursor.execute(*args)
                return self
            def fetchone(self):
                return self.cursor.fetchone()
            def fetchall(self):
                raise AssertionError('Context must not materialize all text rows')
            def __iter__(self):
                for row in self.cursor:
                    reads.append(1)
                    yield row

        class Connection:
            def cursor(self):
                return Cursor()

        result = build_context(Connection(), wid, 'Unmatched action ' * 10)
        assert len(result.sources) == 8
        assert len(reads) <= 9
        assert all(m['selection_reason'] == ('confirmed_fallback' if entries else 'document_fallback') for m in result.metadata)
        assert all(not m['truncated'] for m in result.metadata)
