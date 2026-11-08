import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import create_app
from app.model_gateway import ModelSessionStore
from app.narration.core import ContextSource, NarrationRequest, validate_draft, DraftError
from app.adventures.state import character, project, StateError


def make_run(client, mode='fake', initial=None):
    world=client.post('/api/v1/worlds',json={'name':'角色世界'}).json()['id']
    run=client.post(f'/api/v1/worlds/{world}/adventures',json={'name':'角色旅程','mode':mode,'character':initial or {}})
    assert run.status_code==201, run.text
    return run.json()


def draft(client, run, key='draft'):
    return client.post(f"/api/v1/adventures/{run['id']}/drafts",json={
        'request_id':key,'expected_revision':run['revision'],'action':'前往港口','session_id':'s',
    })


def test_confirm_applies_once_and_persists_snapshots(client, db_path):
    run=make_run(client, initial={'name':'阿岚','coins':5,'location':'旧街'})
    response=draft(client,run)
    assert response.status_code==200, response.text
    assert response.json()['result']['projected_state']['stamina']==99
    url=f"/api/v1/adventures/{run['id']}"
    assert client.get(url).json()['character']['stamina']==100
    commit=f"{url}/drafts/draft/commit"
    for _ in range(2):
        result=client.post(commit,json={'apply_changes':True})
        assert result.status_code==200, result.text
        assert result.json()['character']['elapsed_minutes']==10
        assert result.json()['character']['stamina']==99
    assert client.post(commit,json={'apply_changes':False}).status_code==409
    with TestClient(create_app(Settings(db_path=db_path))) as restarted:
        detail=restarted.get(url).json()
        assert len(detail['turns'])==1
        saved=detail['turns'][0]['result']
        assert saved['state_before']['stamina']==100
        assert saved['state_after']['stamina']==99
        assert saved['changes_accepted'] is True


def test_default_is_narrative_only(client):
    run=make_run(client)
    assert draft(client,run).status_code==200
    result=client.post(f"/api/v1/adventures/{run['id']}/drafts/draft/commit")
    assert result.status_code==200
    assert result.json()['character']==run['character']


@pytest.mark.parametrize('patch',[{'coins_delta':-1},{'stamina_delta':1},{'spirit_delta':1},
    {'minutes':-1},{'minutes':True},{'coins_delta':.5},{'coins_delta':1001},
    {'name':'另一个人'},{'inventory':['神器']},{'location':''},None])
def test_invalid_proposals_rejected_without_clamping(patch):
    state=character({})
    request=NarrationRequest('行动',character=state)
    with pytest.raises(DraftError) as error:
        validate_draft({'narrative':'正文','references':[],'proposed_changes':patch},request)
    assert error.value.code=='MODEL_STATE_INVALID'


@pytest.mark.parametrize('initial',[{'coins':-1},{'coins':True},{'stamina':101},{'name':''},{'other':1}])
def test_initial_state_validation(client, initial):
    world=client.post('/api/v1/worlds',json={'name':'世界'}).json()['id']
    response=client.post(f'/api/v1/worlds/{world}/adventures',json={'name':'旅程','mode':'fake','character':initial})
    assert response.status_code==422


def test_state_and_proposal_delivered_through_live_gateway(db_path):
    captured=[]
    def upstream(request):
        prompt=json.loads(request.content)['messages'][1]['content']
        captured.append(prompt)
        changes={'coins_delta':-3,'minutes':20,'location':'茶馆'}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'narrative':'你付了茶钱，走进茶馆。','references':[],'proposed_changes':changes,
        })}}]})
    store=ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    sid=store.create('https://model.invalid','test','secret')['session_id']
    with TestClient(create_app(Settings(db_path=db_path),narration_store=store)) as client:
        run=make_run(client,'live',{'coins':5})
        result=client.post(f"/api/v1/adventures/{run['id']}/drafts",json={
            'request_id':'live','expected_revision':0,'action':'买茶','session_id':sid,
        })
        assert result.status_code==200,result.text
        assert '当前角色状态' in captured[0]
        committed=client.post(f"/api/v1/adventures/{run['id']}/drafts/live/commit",json={'apply_changes':True})
        assert committed.json()['character']['coins']==2
        assert committed.json()['character']['location']=='茶馆'
        run=committed.json()
        invalid=client.post(f"/api/v1/adventures/{run['id']}/drafts",json={
            'request_id':'too-expensive','expected_revision':1,'action':'再次买茶','session_id':sid,
        })
        assert invalid.status_code==502
        detail=client.get(f"/api/v1/adventures/{run['id']}").json()
        assert detail['revision']==1 and detail['character']['coins']==2


def test_migration_keeps_old_run_without_invented_character(client, db_path):
    run=make_run(client)
    with sqlite3.connect(db_path) as conn:
        conn.execute('ALTER TABLE adventures DROP COLUMN character_json')
    init_db(db_path); init_db(db_path)
    response=draft(client,run)
    assert response.status_code==200,response.text
    assert response.json()['result']['proposed_changes'] is None
    result=client.post(f"/api/v1/adventures/{run['id']}/drafts/draft/commit")
    assert result.status_code==200 and result.json()['character'] is None


def test_two_state_proposals_cannot_charge_the_same_revision_twice(client):
    from concurrent.futures import ThreadPoolExecutor
    run=make_run(client)
    assert draft(client,run,'a').status_code==200
    assert draft(client,run,'b').status_code==200
    def confirm(key):
        return client.post(f"/api/v1/adventures/{run['id']}/drafts/{key}/commit",json={'apply_changes':True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(confirm,['a','b']))
    assert sorted(r.status_code for r in responses)==[200,409]
    detail=client.get(f"/api/v1/adventures/{run['id']}").json()
    assert detail['character']['stamina']==99
    assert detail['character']['elapsed_minutes']==10
    assert len(detail['turns'])==1


def test_invalid_confirmation_choice_is_not_coerced(client):
    run=make_run(client)
    assert draft(client,run).status_code==200
    result=client.post(f"/api/v1/adventures/{run['id']}/drafts/draft/commit",json={'apply_changes':1})
    assert result.status_code==422
    detail=client.get(f"/api/v1/adventures/{run['id']}").json()
    assert detail['revision']==0 and detail['character']['stamina']==100
