import asyncio
import json
import sqlite3
from dataclasses import replace
import pytest
from app.m3 import world_analysis as analysis
from app.m3.world_analysis.adapter import adapt_draft_to_domain_proposals

OPENING = '导师塞拉斯将一卷密封的羊皮纸交到你手中，叮嘱你务必送到矮人之城的大厅，交给石须长老。你还不知道羊皮纸中封印着旧王的灵魂。'
KNOWN = '导师塞拉斯将一卷密封的羊皮纸交到你手中，叮嘱你务必送到矮人之城的大厅，交给石须长老'
HIDDEN = '羊皮纸中封印着旧王的灵魂'


def sample():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    analysis.ensure_base_world_tables(conn)
    analysis.seed_skyforge_academy_world(conn)
    extraction = analysis.extract_and_batch_world_sources(conn, analysis.WORLD_B_ID, '天工外院')
    draft = asyncio.run(analysis.analyze_world_initial_situation(extraction, analysis.FakeWorldAnalysisModelClient(), instance_id='private-test'))
    raw = draft.to_dict()
    raw.pop('player_opening_summary', None)
    point = replace(draft.starting_point, player_description=OPENING)
    raw['starting_point'] = point.to_dict()
    raw['events'][0].update(title='送达羊皮纸', description=f'送信委托背后，{HIDDEN}。', visibility='secret', player_known_at_start=True,
                            player_rumor_title='旧王灵魂的秘密', player_rumor_content=f'{KNOWN}。{HIDDEN}。', player_known_basis=KNOWN)
    ctx = analysis.WorldAnalysisValidationContext(world_id=analysis.WORLD_B_ID, instance_id='private-test', starting_point=point, allowed_source_ids=frozenset(extraction['allowed_source_ids']))
    return conn, raw, ctx


def test_private_commission_is_known_but_hidden_reason_is_never_projected_or_marked_complete():
    conn, raw, ctx = sample()
    try:
        draft = analysis.validate_analysis_draft(raw, ctx)
        event = draft.events[0]
        assert event.visibility == 'secret' and event.player_known_at_start
        assert event.player_rumor_content == KNOWN
        assert event.status == 'active'
        assert HIDDEN in event.description
        projection = json.dumps(draft.player_opening_summary, ensure_ascii=False)
        assert KNOWN in projection and HIDDEN not in projection
        assert '旧王灵魂的秘密' not in projection
        bundle = adapt_draft_to_domain_proposals(draft)
        player = [r for r in bundle['knowledge_transmissions'] if r['holder_id'] == 'player']
        assert any(r['content'] == KNOWN and r['channel'] == 'observed' for r in player)
        assert all(HIDDEN not in r['content'] for r in player)
        restored = analysis.validate_analysis_draft(draft.to_dict(), ctx)
        assert restored.events[0].player_known_basis == KNOWN
    finally:
        conn.close()


@pytest.mark.parametrize('basis', ['', HIDDEN, '你还不知道' + HIDDEN, '来自另一份世界资料的秘密'])
def test_unproven_or_explicitly_unknown_secret_is_withheld_without_blocking_opening(basis):
    conn, raw, ctx = sample()
    try:
        raw['events'][0]['player_known_basis'] = basis
        draft = analysis.validate_analysis_draft(raw, ctx)
        event = draft.events[0]
        assert event.visibility == 'secret' and not event.player_known_at_start
        assert not event.player_known_basis and not event.player_rumor_content
        assert HIDDEN not in json.dumps(draft.player_opening_summary, ensure_ascii=False)
        assert any(r['action'] == 'withheld_ungrounded_player_knowledge' for r in draft.identity_resolution_log)
    finally:
        conn.close()


def test_private_commission_survives_real_start_routes_and_transfer(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    from app.model_gateway import ModelSessionStore

    class SecretCommission(analysis.FakeWorldAnalysisModelClient):
        async def analyze_batch(self, *args, **kwargs):
            raw = await super().analyze_batch(*args, **kwargs)
            first = raw['events'][0]
            first.update(title='送达羊皮纸', description=HIDDEN, visibility='secret', player_known_at_start=True,
                         player_known_basis=KNOWN, player_rumor_title='旧王灵魂的秘密', player_rumor_content=HIDDEN)
            for npc in raw['npcs']:
                # All concrete actors may know the backstage event, while the player
                # only knows the commission in their opening, not its hidden reason.
                npc['initial_known_event_ids'] = [first['event_id']]
            raw.pop('player_opening_summary', None)
            return raw
    monkeypatch.setattr(analysis, 'OpenAIWorldAnalysisModelClient', lambda **kwargs: SecretCommission())
    def model(request):
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'character':{'name':'学徒','location':'矮人之城大厅'},'starts_at':'1000-01-01T08:00',
        })}}]})
    store = ModelSessionStore(http_transport=httpx.MockTransport(model))
    session = store.create('https://api.deepseek.com','deepseek-flash','test-key')['session_id']
    with TestClient(create_app(Settings(db_path=tmp_path/'phone.db'), narration_store=store)) as client:
        world = client.post('/api/v1/worlds',json={'name':'双城'}).json()['id']
        doc = client.post(f'/api/v1/worlds/{world}/documents',files={'file':('设定.txt','矮人之城大厅由石须长老管理。塞拉斯是魔法学院导师。'.encode(),'text/plain')}).json()['id']
        started = client.post(f'/api/v1/worlds/{world}/journey-start',json={
            'mode':'live','session_id':session,'name':'送信','description':OPENING,'request_id':'secret-start','selected_source_ids':['doc:'+doc],
        })
        assert started.status_code == 201, started.text
        run = started.json()
        news = client.get(f"/api/v1/adventures/{run['id']}/world-news").json()['items']
        assert any(item['content']==KNOWN for item in news)
        assert all(HIDDEN not in item['content'] for item in news)
        exported = client.get(f'/api/v1/worlds/{world}/transfer',params={'part':'zip'})
        migrated = client.post('/api/v1/world-transfers',files=[('files',('world.zip',exported.content,'application/zip'))])
        assert migrated.status_code == 201, migrated.text
        copied = client.get(f"/api/v1/worlds/{migrated.json()['id']}/adventures").json()['items'][0]
        news = client.get(f"/api/v1/adventures/{copied['id']}/world-news").json()['items']
        assert any(item['content']==KNOWN for item in news)
        assert all(HIDDEN not in item['content'] for item in news)
