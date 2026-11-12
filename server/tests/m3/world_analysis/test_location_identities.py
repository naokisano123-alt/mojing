import asyncio
import copy
import json
import sqlite3
from dataclasses import replace

import pytest
from app.m3 import world_analysis as analysis
from app.m3.world_analysis.errors import AnalysisValidationError, CrossInstanceAnalysisError, SourceFabricatedError


def sample():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    analysis.ensure_base_world_tables(conn)
    analysis.seed_skyforge_academy_world(conn)
    extraction = analysis.extract_and_batch_world_sources(conn, analysis.WORLD_B_ID, '天工外院')
    draft = asyncio.run(analysis.analyze_world_initial_situation(extraction, analysis.FakeWorldAnalysisModelClient(), instance_id='location-test'))
    raw = draft.to_dict();raw.pop('player_opening_summary',None)
    ctx = analysis.WorldAnalysisValidationContext(world_id=analysis.WORLD_B_ID,instance_id='location-test',starting_point=draft.starting_point,allowed_source_ids=frozenset(extraction['allowed_source_ids']))
    return conn,raw,ctx


def test_distinct_locations_with_same_model_id_get_unique_stable_ids_and_keep_names():
    conn,raw,ctx=sample()
    try:
        base=raw['locations'][0]
        raw['locations']=[{**base,'location_id':'loc_capital_gate','name':name} for name in ('天工外院','王城外围旅店','王城正门')]
        validated=analysis.validate_analysis_draft(raw,ctx)
        assert [p.name for p in validated.locations]==['天工外院','王城外围旅店','王城正门']
        ids=[p.location_id for p in validated.locations]
        assert len(set(ids))==3 and ids[0]=='loc_capital_gate'
        assert all(len(i)<=100 for i in ids)
        assert all(p.source_ids==validated.locations[0].source_ids for p in validated.locations)
        second=analysis.validate_analysis_draft(raw,ctx)
        assert ids==[p.location_id for p in second.locations]
        assert [p.location_id for p in analysis.validate_analysis_draft(validated.to_dict(),ctx).locations]==ids
        assert len([n for n in validated.identity_resolution_log if n['action']=='reassigned_location_id'])==2
    finally:
        conn.close()


def test_duplicate_name_keeps_source_evidence_conflicting_description_and_private_visibility():
    conn,raw,ctx=sample()
    try:
        base=raw['locations'][0]
        raw['locations']=[{**base,'location_id':'loc_a','name':'天工外院','description':'甲版本的说明','visibility':'public'},
                          {**base,'location_id':'loc_b','name':' 天工外院 ','description':'乙版本的不同说明','visibility':'secret'}]
        validated=analysis.validate_analysis_draft(raw,ctx)
        assert len(validated.locations)==1
        assert validated.locations[0].description=='甲版本的说明'
        assert validated.locations[0].visibility=='secret'
        audit=next(n for n in validated.identity_resolution_log if n['action']=='merged_duplicate_location')
        assert audit['incoming_description']=='乙版本的不同说明' and audit['incoming_source_ids']
        assert '乙版本的不同说明' not in json.dumps(validated.player_opening_summary,ensure_ascii=False)
    finally:
        conn.close()


def test_synthetic_start_location_cannot_reuse_existing_model_id():
    conn,raw,ctx=sample()
    try:
        base=raw['locations'][0]
        raw['locations']=[{**base,'location_id':'loc_starting_point','name':'王城正门'}]
        validated=analysis.validate_analysis_draft(raw,ctx)
        assert len(validated.locations)==2
        assert len({p.location_id for p in validated.locations})==2
        assert validated.locations[0].name==ctx.starting_point.location
    finally:
        conn.close()


def test_duplicate_location_normalization_never_bypasses_source_instance_or_length_checks():
    conn,raw,ctx=sample()
    try:
        base=raw['locations'][0]
        raw['locations']=[base,copy.deepcopy(base)]
        raw['locations'][1]['source_ids']=['chunk:invented']
        with pytest.raises(SourceFabricatedError): analysis.validate_analysis_draft(raw,ctx)
        raw['locations'][1]={**base,'location_id':'loc_other-instance-01'}
        with pytest.raises(CrossInstanceAnalysisError): analysis.validate_analysis_draft(raw,replace(ctx,other_instance_ids=frozenset({'other-instance-01'})))
        raw['locations'][1]={**base,'location_id':'x'*101}
        with pytest.raises(AnalysisValidationError): analysis.validate_analysis_draft(raw,ctx)
    finally:
        conn.close()


def test_model_batches_with_repeated_location_ids_still_initialize_and_migrate(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    from app.model_gateway import ModelSessionStore

    class RepeatedIds(analysis.FakeWorldAnalysisModelClient):
        async def analyze_batch(self,*args,**kwargs):
            raw=await super().analyze_batch(*args,**kwargs)
            base=raw['locations'][0]
            raw['locations']=[{**base,'location_id':'loc_capital_gate','name':name} for name in (base['name'],'王城外围旅店','王城正门')]
            raw['locations'].append({**raw['locations'][-1],'location_id':'same-place-again','description':'正门另一份记录'})
            raw.pop('player_opening_summary',None)
            return raw
    monkeypatch.setattr(analysis,'OpenAIWorldAnalysisModelClient',lambda **kwargs:RepeatedIds())
    calls=[]
    def provider(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'character':{'name':'旅人','location':'旅店柜台'},'starts_at':'1000-01-01T08:00'})}}]})
    store=ModelSessionStore(http_transport=httpx.MockTransport(provider))
    session=store.create('https://api.deepseek.com','deepseek-flash','test-key')['session_id']
    with TestClient(create_app(Settings(db_path=tmp_path/'phone.db'),narration_store=store)) as client:
        world=client.post('/api/v1/worlds',json={'name':'王城'}).json()['id']
        document=client.post(f'/api/v1/worlds/{world}/documents',files={'file':('王城.txt','王城正门连接外围旅店。伊妲是王城旅店掌柜。'.encode(),'text/plain')}).json()['id']
        payload={'mode':'live','session_id':session,'name':'求职','description':'我带着推荐信来到王城，在旅店柜台前等候。','request_id':'location-start','selected_source_ids':['doc:'+document]}
        started=client.post(f'/api/v1/worlds/{world}/journey-start',json=payload)
        assert started.status_code==201,started.text
        run=started.json()
        assert client.post(f'/api/v1/worlds/{world}/journey-start',json=payload).json()['id']==run['id']
        assert len(calls)==1
        auto=client.get(f"/api/v1/journeys/{run['id']}/saves").json()['items'][0]
        restored=client.post(f"/api/v1/journeys/{run['id']}/load/{auto['id']}",json={'request_id':'reload'})
        assert restored.status_code==201,restored.text
        zipped=client.get(f'/api/v1/worlds/{world}/transfer',params={'part':'zip'})
        imported=client.post('/api/v1/world-transfers',files=[('files',('world.zip',zipped.content,'application/zip'))])
        assert imported.status_code==201,imported.text
        assert len(client.get(f"/api/v1/worlds/{imported.json()['id']}/journeys").json()['items'])==1


def test_batch_duplicate_preserves_variants_and_cannot_declassify_a_private_place():
    conn, raw, ctx = sample()
    try:
        extraction = analysis.extract_and_batch_world_sources(conn, analysis.WORLD_B_ID, '天工外院')
        batch = extraction['batches'][0]
        base = {**raw['locations'][0], 'name':'王城正门', 'source_ids':[batch.input_range.source_ids[0]]}
        first = {'locations':[{**base,'location_id':'loc_capital_gate','description':'甲版本','visibility':None}]}
        second = {'locations':[{**base,'location_id':'loc_capital_gate','description':'乙版本','visibility':'secret'}]}
        merged = analysis.merge_batch_extractions([first,second],[batch,batch],world_id=ctx.world_id,instance_id=ctx.instance_id,starting_point=ctx.starting_point)
        assert len(merged['locations'])==1 and merged['locations'][0]['visibility']=='secret'
        note = next(n for n in merged['identity_resolution_log'] if n['action']=='merged_batch_location')
        assert note['retained_description']=='甲版本' and note['incoming_description']=='乙版本'
        second['locations'][0]['visibility']={}
        with pytest.raises(AnalysisValidationError):
            analysis.merge_batch_extractions([first,second],[batch,batch],world_id=ctx.world_id,instance_id=ctx.instance_id,starting_point=ctx.starting_point)
    finally:
        conn.close()
