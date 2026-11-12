import asyncio
import copy
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from app.m3 import knowledge, world_analysis as analysis
from app.m3.world_analysis.adapter import adapt_draft_to_domain_proposals


@pytest.mark.parametrize('channel', ['observed', 'notice', 'rumor'])
def test_concrete_group_can_share_a_message_in_validation_and_writes(channel):
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        knowledge.create_instance_fixture(conn, 'inst', world_minute=10)
        for holder in ('npc_a', 'npc_b', 'player'):
            knowledge.register_holder(conn, 'inst', holder, name=holder, location='药圃')
        knowledge.register_event_stub(conn, 'inst', 'evt_garden', title='后台事件', description='后台尚未证实的秘密原因', created_minute=0)
        records = [{'holder_id': holder, 'knowledge_type': 'rumor' if channel == 'rumor' else 'confirmed',
                    'source_event_id': 'evt_garden', 'channel': channel, 'learned_minute': 5,
                    'content': '我们听到有人在谈论王城的走私调查。'}
                   for holder in ('npc_a', 'npc_b', 'player')]
        snapshot = knowledge.read_knowledge_snapshot(conn, 'inst')
        checked = knowledge.validate_proposal({'transmissions': records}, snapshot)
        assert len(checked.transmissions) == 3
        assert len(knowledge.record_transmission(conn, 'inst', records)) == 3
        news = knowledge.project_player_news(conn, 'inst')
        assert len(news['items']) == 1
        assert '秘密原因' not in json.dumps(news, ensure_ascii=False)
        # Adding a bystander does not change whether the same group is valid.
        knowledge.register_holder(conn, 'inst', 'npc_c', name='不在场者', location='远方')
        assert knowledge.list_known(conn, 'inst', 'npc_c')['items'] == []


@pytest.mark.parametrize('known', [None, []])
def test_event_affecting_an_npc_does_not_automatically_give_them_knowledge(known):
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        analysis.ensure_base_world_tables(conn)
        analysis.seed_skyforge_academy_world(conn)
        extraction = analysis.extract_and_batch_world_sources(conn, analysis.WORLD_B_ID, '天工外院')
        draft = asyncio.run(analysis.analyze_world_initial_situation(extraction, analysis.FakeWorldAnalysisModelClient(), instance_id='knowers'))
        raw = draft.to_dict()
        raw.pop('player_opening_summary', None)
        for npc in raw['npcs']:
            if known is None:
                npc.pop('initial_known_event_ids', None)
            else:
                npc['initial_known_event_ids'] = known
        ctx = analysis.WorldAnalysisValidationContext(world_id=draft.world_id, instance_id=draft.instance_id,
            starting_point=draft.starting_point, allowed_source_ids=draft.allowed_source_ids)
        validated = analysis.validate_analysis_draft(raw, ctx)
        assert all(npc.cause_event_ids for npc in validated.npcs)
        assert all(not npc.initial_known_event_ids for npc in validated.npcs)
        bundle = adapt_draft_to_domain_proposals(validated)
        assert all(r['holder_id'] == 'player' for r in bundle['knowledge_transmissions'])


@pytest.mark.parametrize('group_size,shared,fail_second_batch', [(2, True, False), (7, True, False), (2, False, False), (7, True, True)])
def test_real_journey_start_with_shared_knowledge_saves_restores_and_migrates(tmp_path, monkeypatch, group_size, shared, fail_second_batch):
    models = []
    class Witnesses(analysis.FakeWorldAnalysisModelClient):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def analyze_batch(self, *args, **kwargs):
            self.calls += 1
            raw = await super().analyze_batch(*args, **kwargs)
            event_ids = [event['event_id'] for event in raw['events']]
            for i, event in enumerate(raw['events']):
                event.update(visibility='public', player_known_at_start=shared, player_channel='rumor',
                    player_knowledge_type='rumor', player_rumor_title=f'药圃风声{i}',
                    player_rumor_content=f'你在药圃听到关于{event["title"]}的议论，尚不知背后原因。')
            base = raw['npcs'][0]
            raw['npcs'] = [{**copy.deepcopy(base), 'npc_id': f'npc_witness_{i}', 'name': f'药圃工人{i}',
                            'role': f'杂役{i}', 'relationships': [],
                            'initial_known_event_ids': event_ids if shared else []}
                           for i in range(group_size)]
            raw.pop('player_opening_summary', None)
            return raw
    def factory(**kwargs):
        model = Witnesses()
        models.append(model)
        return model
    monkeypatch.setattr(analysis, 'OpenAIWorldAnalysisModelClient', factory)
    upstream_calls = []
    def upstream(request):
        upstream_calls.append(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'character': {'name': '杂役', 'location': '药圃'}, 'starts_at': '1000-01-01T08:00',
        })}}]})
    store = ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    session = store.create('https://api.deepseek.com', 'deepseek-flash', 'test-key')['session_id']
    with TestClient(create_app(Settings(db_path=tmp_path/'phone.db'), narration_store=store)) as client:
        world = client.post('/api/v1/worlds', json={'name': '修院'}).json()['id']
        doc = client.post(f'/api/v1/worlds/{world}/documents', files={'file': ('设定.txt', '修院有一处药圃，工人们在此劳作，羽母管理药圃。'.encode(), 'text/plain')}).json()['id']
        payload = {'mode': 'live', 'session_id': session, 'name': '修院生活', 'description': '我和两个工人在药圃听见王城查走私的传闻。',
                   'request_id': 'garden-start', 'selected_source_ids': ['doc:'+doc]}
        if fail_second_batch:
            record = knowledge.record_transmission
            writes = []
            def failing_record(conn, instance, facts, **kwargs):
                result = record(conn, instance, facts, **kwargs)
                writes.append(len(facts))
                if len(writes) == 2:
                    raise knowledge.KnowledgeError(422, 'INVALID_GENERATED_FACT', 'irrecoverable generated fact')
                return result
            monkeypatch.setattr(knowledge, 'record_transmission', failing_record)
            failure = client.post(f'/api/v1/worlds/{world}/journey-start', json=payload)
            assert failure.status_code == 422, failure.text
            assert failure.json()['error']['details']['model_output_issue'] is True
            assert len(writes) == 2
            assert client.get(f'/api/v1/worlds/{world}/journeys').json()['items'] == []
            with sqlite3.connect(tmp_path/'phone.db') as conn:
                for table in ('world_instances', 'world_events', 'm3_npcs', 'm3_knowledge_facts', 'journey_start_requests'):
                    assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
            monkeypatch.setattr(knowledge, 'record_transmission', record)
        response = client.post(f'/api/v1/worlds/{world}/journey-start', json=payload)
        assert response.status_code == 201, response.text
        run = response.json()
        assert client.post(f'/api/v1/worlds/{world}/journey-start', json=payload).json()['id'] == run['id']
        expected_calls = 2 if fail_second_batch else 1
        assert len(upstream_calls) == expected_calls and len(models) == expected_calls
        assert all(model.calls == 1 for model in models)
        original_news = client.get(f"/api/v1/adventures/{run['id']}/world-news").json()['items']
        assert bool(original_news) is shared
        auto = client.get(f"/api/v1/journeys/{run['id']}/saves").json()['items'][0]
        loaded = client.post(f"/api/v1/journeys/{run['id']}/load/{auto['id']}", json={'request_id': 'garden-load'})
        assert loaded.status_code == 201, loaded.text
        restored_news = client.get(f"/api/v1/adventures/{loaded.json()['id']}/world-news").json()['items']
        assert [n['content'] for n in restored_news] == [n['content'] for n in original_news]
        exported = client.get(f'/api/v1/worlds/{world}/transfer', params={'part': 'zip'})
        migrated = client.post('/api/v1/world-transfers', files=[('files', ('world.zip', exported.content, 'application/zip'))])
        assert migrated.status_code == 201, migrated.text
        copied = client.get(f"/api/v1/worlds/{migrated.json()['id']}/adventures").json()['items']
        assert copied
        for copied_run in copied:
            news = client.get(f"/api/v1/adventures/{copied_run['id']}/world-news").json()['items']
            assert [n['content'] for n in news] == [n['content'] for n in original_news]
