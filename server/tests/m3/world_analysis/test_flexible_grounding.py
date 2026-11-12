import asyncio
import copy
import json
import sqlite3
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from app.m3 import world_analysis as analysis
from app.m3.world_analysis.errors import AnalysisValidationError, CrossWorldSourceError, SourceFabricatedError
from app.m3.world_analysis.normalization import normalize_model_sources


class LooseModel(analysis.FakeWorldAnalysisModelClient):
    def __init__(self, source=None):
        super().__init__(); self.source=source; self.requests=0
    async def analyze_batch(self,*args,**kwargs):
        self.requests+=1
        raw=await super().analyze_batch(*args,**kwargs)
        for key in ('locations','events','npcs'):
            raw[key][0].update(grounding_type='canon',source_ids=self.source)
        first=raw['npcs'][0]['npc_id']; second=raw['npcs'][1]['npc_id'] if len(raw['npcs'])>1 else 'player'
        raw['relationships']=[{'from_npc_id':first,'to_target_id':second,'relation_type':f'familiar_{i}','summary':'两人有过往来。','grounding_type':'canon','source_ids':self.source} for i in range(3)]
        raw['relationships'].append({'from_npc_id':first,'to_target_id':'missing-npc','relation_type':'familiar','summary':'可选的未落实关系'})
        raw['relationships'].append({'from_npc_id':first,'to_target_id':second,'relation_type':'familiar'})
        raw['npcs'][0]['relationships']=[{'to_target_id':second,'relation_type':'familiar','summary':'两人曾见过面。','grounding_type':'canon','source_ids':self.source}]
        raw.pop('player_opening_summary',None)
        return raw


@pytest.mark.parametrize('source',[None, [], [''], ['chunk:made-up'], 42])
def test_model_missing_or_unverifiable_sources_become_inference_without_paid_retry(source):
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory=sqlite3.Row
        analysis.ensure_base_world_tables(conn)
        analysis.seed_skyforge_academy_world(conn)
        extraction=analysis.extract_and_batch_world_sources(conn,analysis.WORLD_B_ID,'天工外院')
        model=LooseModel(source)
        draft=asyncio.run(analysis.analyze_world_initial_situation(extraction,model,instance_id='flexible-test'))
        assert model.requests==len(extraction['batches'])
        assert draft.locations[0].grounding_type=='adventure_supplement'
        assert draft.events[0].grounding_type=='adventure_supplement'
        assert draft.npcs[0].grounding_type=='adventure_supplement'
        assert draft.npcs[0].relationships[0].grounding_type=='adventure_supplement'
        assert all(rel.grounding_type=='adventure_supplement' for rel in draft.relationships)
        assert all(rel.to_target_id!='missing-npc' for rel in draft.relationships)
        assert any(n['action']=='normalized_model_grounding' for n in draft.identity_resolution_log)
        assert any(n['action']=='omitted_optional_relationship' for n in draft.identity_resolution_log)
        assert 'chunk:made-up' not in draft.referenced_source_ids
        assert all(sid in extraction['allowed_source_ids'] for sid in draft.referenced_source_ids)
        # Formal proposals continue to require proof when they claim to be canon.
        bad=draft.to_dict();bad.pop('player_opening_summary',None)
        bad['events'][0].update(grounding_type='canon',source_ids=[])
        ctx=analysis.WorldAnalysisValidationContext(world_id=draft.world_id,instance_id=draft.instance_id,starting_point=draft.starting_point,allowed_source_ids=draft.allowed_source_ids)
        with pytest.raises(SourceFabricatedError): analysis.validate_analysis_draft(bad,ctx)


def test_annotation_repair_keeps_valid_citations_and_rejects_real_foreign_world_sources():
    partial={'npcs':[{'name':'伊妲','grounding_type':'canon','source_ids':'entry:local','relationships':[]}], 'events':[]}
    original=copy.deepcopy(partial)
    audit=[]
    cleaned=normalize_model_sources(partial,{'entry:local'},{'entry:foreign':'other-world'},'this-world',audit,0)
    assert cleaned['npcs'][0]['grounding_type']=='canon'
    assert cleaned['npcs'][0]['source_ids']==['entry:local']
    assert partial==original
    partial['npcs'][0]['source_ids']=['entry:foreign']
    with pytest.raises(CrossWorldSourceError):
        normalize_model_sources(partial,{'entry:local'},{'entry:foreign':'other-world'},'this-world',[],0)


def test_production_analysis_keeps_real_cross_world_guard():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory=sqlite3.Row;analysis.ensure_base_world_tables(conn)
        analysis.seed_skyforge_academy_world(conn);analysis.seed_maritime_salt_world(conn)
        extraction=analysis.extract_and_batch_world_sources(conn,analysis.WORLD_B_ID,'天工外院')
        foreign=next(iter(extraction['other_world_source_ids']))
        with pytest.raises(CrossWorldSourceError):
            asyncio.run(analysis.analyze_world_initial_situation(extraction,LooseModel([foreign]),instance_id='foreign-test'))


def test_missing_relationship_provenance_starts_saves_and_migrates(tmp_path,monkeypatch):
    models=[]
    def factory(**kwargs):
        model=LooseModel(['chunk:made-up']);models.append(model);return model
    monkeypatch.setattr(analysis,'OpenAIWorldAnalysisModelClient',factory)
    calls=[]
    def provider(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'character':{'name':'学徒','location':'王城旅店'},'starts_at':'1000-01-01T08:00'})}}]})
    store=ModelSessionStore(http_transport=httpx.MockTransport(provider))
    session=store.create('https://api.deepseek.com','deepseek-flash','test-key')['session_id']
    with TestClient(create_app(Settings(db_path=tmp_path/'phone.db'),narration_store=store)) as client:
        world=client.post('/api/v1/worlds',json={'name':'王城'}).json()['id']
        doc=client.post(f'/api/v1/worlds/{world}/documents',files={'file':('世界.txt','王城旅店由伊妲掌柜经营，石须是大厅长老。'.encode(),'text/plain')}).json()['id']
        payload={'name':'学徒之旅','description':'我带着学院徽章来到王城旅店，想找一份工作。','mode':'live','session_id':session,'request_id':'loose-start','selected_source_ids':['doc:'+doc]}
        response=client.post(f'/api/v1/worlds/{world}/journey-start',json=payload)
        assert response.status_code==201,response.text
        run=response.json()
        assert client.post(f'/api/v1/worlds/{world}/journey-start',json=payload).json()['id']==run['id']
        assert len(calls)==1 and len(models)==1 and models[0].requests==1
        auto=client.get(f"/api/v1/journeys/{run['id']}/saves").json()['items'][0]
        assert client.post(f"/api/v1/journeys/{run['id']}/load/{auto['id']}",json={'request_id':'restore'}).status_code==201
        exported=client.get(f'/api/v1/worlds/{world}/transfer',params={'part':'zip'})
        with ZipFile(BytesIO(exported.content)) as archive:
            assert 'chunk:made-up' not in archive.read('saves.json').decode()
        imported=client.post('/api/v1/world-transfers',files=[('files',('world.zip',exported.content,'application/zip'))])
        assert imported.status_code==201,imported.text


def test_unrecoverable_analysis_error_is_tagged_and_leaves_no_partial_journey(tmp_path,monkeypatch):
    class InvalidModel(analysis.FakeWorldAnalysisModelClient):
        async def analyze_batch(self,*args,**kwargs):
            raise AnalysisValidationError('INVALID_EVENT_STRUCTURE','events[0] is not usable')
    monkeypatch.setattr(analysis,'OpenAIWorldAnalysisModelClient',lambda **kwargs:InvalidModel())
    store=ModelSessionStore(http_transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'character':{'name':'学徒','location':'王城旅店'},'starts_at':'1000-01-01T08:00'})}}]
    })))
    session=store.create('https://api.deepseek.com','deepseek-flash','test-key')['session_id']
    path=tmp_path/'phone.db'
    with TestClient(create_app(Settings(db_path=path),narration_store=store)) as client:
        world=client.post('/api/v1/worlds',json={'name':'王城'}).json()['id']
        doc=client.post(f'/api/v1/worlds/{world}/documents',files={'file':('世界.txt','王城有一间旅店。'.encode(),'text/plain')}).json()['id']
        response=client.post(f'/api/v1/worlds/{world}/journey-start',json={
            'name':'学徒之旅','description':'我来到王城旅店。','mode':'live','session_id':session,
            'request_id':'failed-start','selected_source_ids':['doc:'+doc],
        })
        assert response.status_code==422,response.text
        error=response.json()['error']
        assert error['code']=='INVALID_EVENT_STRUCTURE'
        assert error['details']['stage']=='opening_analysis'
        assert error['details']['model_output_issue'] is True
        assert client.get(f'/api/v1/worlds/{world}/journeys').json()['items']==[]
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM journey_start_requests').fetchone()[0]==0
