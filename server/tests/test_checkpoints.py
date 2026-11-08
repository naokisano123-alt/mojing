import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import create_app
from app.model_gateway import ModelSessionStore


def make_run(client,mode='fake'):
    world=client.post('/api/v1/worlds',json={'name':'分支世界'}).json()['id']
    run=client.post(f'/api/v1/worlds/{world}/adventures',json={'name':'原路线','mode':mode,
        'character':{'coins':10,'inventory':[{'name':'干粮','quantity':3}]}}).json()
    return world,run


def advance(client,run,key,action='行动',session='s'):
    draft=client.post(f"/api/v1/adventures/{run['id']}/drafts",json={'request_id':key,
        'expected_revision':run['revision'],'action':action,'session_id':session})
    assert draft.status_code==200,draft.text
    committed=client.post(f"/api/v1/adventures/{run['id']}/drafts/{key}/commit",json={'apply_changes':True})
    assert committed.status_code==200,committed.text
    return committed.json()


def save(client,run,key='save'):
    result=client.post(f"/api/v1/adventures/{run['id']}/checkpoints",json={'request_id':key,
        'expected_revision':run['revision'],'name':'检查点'})
    assert result.status_code==201,result.text
    return result.json()


def restore(client,checkpoint,key='restore'):
    return client.post(f"/api/v1/checkpoints/{checkpoint['id']}/restore",json={'request_id':key,'name':'新路线'})


def test_initial_checkpoint_restores_character_without_future_turns(client,db_path):
    _,run=make_run(client)
    checkpoint=save(client,run)
    progressed=advance(client,run,'first')
    branch=restore(client,checkpoint).json()
    assert branch['character']==run['character']
    assert branch['revision']==0 and branch['origin_checkpoint_id']==checkpoint['id']
    assert client.get(f"/api/v1/adventures/{branch['id']}").json()['turns']==[]
    assert client.get(f"/api/v1/adventures/{run['id']}").json()['character']==progressed['character']
    with TestClient(create_app(Settings(db_path=db_path))) as restarted:
        assert restarted.get(f"/api/v1/adventures/{branch['id']}").json()['character']==run['character']


def test_live_branch_context_excludes_source_future_and_restores_items(db_path):
    prompts=[]
    def upstream(request):
        prompts.append(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'narrative':f'已确认故事-{len(prompts)}','references':[],'proposed_changes':{
                'coins_delta':-1,'inventory_changes':[{'name':'干粮','delta':-1}]},
        })}}]})
    store=ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    session=store.create('https://model.invalid','test','secret')['session_id']
    with TestClient(create_app(Settings(db_path=db_path),narration_store=store)) as client:
        _,source=make_run(client,'live')
        source=advance(client,source,'a','早期行动',session)
        checkpoint=save(client,source)
        source=advance(client,source,'b','未来路线',session)
        branch=restore(client,checkpoint).json()
        assert branch['character']['coins']==9
        assert branch['character']['inventory'][0]['quantity']==2
        assert branch['revision']==1
        branch=advance(client,branch,'a','分支行动',session)
        assert '早期行动' in prompts[-1] and '已确认故事-1' in prompts[-1]
        assert '未来路线' not in prompts[-1] and '已确认故事-2' not in prompts[-1]
        assert branch['revision']==2
        original=client.get(f"/api/v1/adventures/{source['id']}").json()
        assert original['turns'][-1]['action']=='未来路线'
        assert original['character']==source['character']


def test_save_and_concurrent_restore_are_idempotent(client):
    _,run=make_run(client)
    checkpoint=save(client,run)
    assert save(client,run)['id']==checkpoint['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:restore(client,checkpoint),range(2)))
    assert all(r.status_code==201 for r in responses)
    assert responses[0].json()['id']==responses[1].json()['id']
    conflict=client.post(f"/api/v1/checkpoints/{checkpoint['id']}/restore",json={'name':'另一个名字','request_id':'restore'})
    assert conflict.status_code==409
    advance(client,run,'later')
    stale=client.post(f"/api/v1/adventures/{run['id']}/checkpoints",json={'name':'旧版本','request_id':'new','expected_revision':0})
    assert stale.status_code==409


def test_unconfirmed_drafts_not_copied_and_deleted_world_blocked(client,db_path):
    world,run=make_run(client)
    run=advance(client,run,'a')
    checkpoint=save(client,run)
    assert client.post(f"/api/v1/adventures/{run['id']}/drafts",json={'request_id':'not-confirmed',
        'action':'未确认行动','expected_revision':1}).status_code==200
    branch=restore(client,checkpoint).json()
    detail=client.get(f"/api/v1/adventures/{branch['id']}").json()
    assert detail['latest_draft'] is None and len(detail['turns'])==1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM adventure_drafts WHERE adventure_id=?',(branch['id'],)).fetchone()[0]==0
    assert client.delete('/api/v1/worlds/'+world).status_code==204
    assert restore(client,checkpoint,'another').status_code==404
    assert client.get(f"/api/v1/adventures/{run['id']}/checkpoints").status_code==404


def test_legacy_character_and_migration_are_preserved(client,db_path):
    _,run=make_run(client)
    with sqlite3.connect(db_path) as conn:
        conn.execute('UPDATE adventures SET character_json=NULL WHERE id=?',(run['id'],))
    init_db(db_path);init_db(db_path)
    checkpoint=save(client,run)
    branch=restore(client,checkpoint).json()
    assert branch['character'] is None


def test_incomplete_checkpoint_history_cannot_create_partial_branch(client,db_path):
    _,run=make_run(client)
    run=advance(client,run,'a')
    checkpoint=save(client,run)
    with sqlite3.connect(db_path) as conn:
        conn.execute('DELETE FROM adventure_turns WHERE adventure_id=?',(run['id'],))
    result=restore(client,checkpoint)
    assert result.status_code==409
    with sqlite3.connect(db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM adventures').fetchone()[0]==1
