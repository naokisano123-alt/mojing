import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from app.adventures.state import character, project, StateError


def run(client):
    world=client.post('/api/v1/worlds',json={'name':'物品世界'}).json()['id']
    result=client.post(f'/api/v1/worlds/{world}/adventures',json={
        'name':'背包旅程','mode':'live','character':{'coins':10,'inventory':[{'name':'干粮','quantity':2}]},
    })
    assert result.status_code==201,result.text
    return result.json()


def prepare(client,run,key='bag'):
    return client.post(f"/api/v1/adventures/{run['id']}/drafts",json={
        'request_id':key,'expected_revision':0,'action':'买一袋干粮','session_id':'s',
    })


def test_inventory_and_coins_commit_once_as_a_unit(db_path):
    prompts=[]
    def upstream(request):
        prompts.append(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'narrative':'你付出3铜币，得到一袋干粮。','references':[],
            'proposed_changes':{'coins_delta':-3,'inventory_changes':[{'name':'干粮','delta':1}]},
        })}}]})
    store=ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    sid=store.create('https://model.invalid','test','secret')['session_id']
    with TestClient(create_app(Settings(db_path=db_path),narration_store=store)) as client:
        journal=run(client)
        response=client.post(f"/api/v1/adventures/{journal['id']}/drafts",json={
            'request_id':'buy','expected_revision':0,'action':'买干粮','session_id':sid,
        })
        assert response.status_code==200,response.text
        assert '干粮' in prompts[0] and 'inventory' in prompts[0]
        detail=f"/api/v1/adventures/{journal['id']}"
        assert client.get(detail).json()['character']['coins']==10
        for _ in range(2):
            result=client.post(detail+'/drafts/buy/commit',json={'apply_changes':True})
            assert result.status_code==200,result.text
            assert result.json()['character']['coins']==7
            assert result.json()['character']['inventory']==[{'name':'干粮','quantity':3}]
        saved=client.get(detail).json()['turns'][0]['result']
        assert saved['state_before']['inventory'][0]['quantity']==2
        assert saved['state_after']['inventory'][0]['quantity']==3
    with TestClient(create_app(Settings(db_path=db_path))) as restarted:
        assert restarted.get(detail).json()['character']['inventory'][0]['quantity']==3


@pytest.mark.parametrize('changes', [None,{},[{'name':'干粮','delta':-3}],
    [{'name':'陌生物品','delta':-1}],[{'name':'干粮','delta':True}],
    [{'name':'干粮','delta':1.5}],[{'name':'干粮','delta':0}],
    [{'name':'干粮','delta':100}],[{'name':'干粮','delta':1,'script':'run'}],
    [{'name':'干粮','delta':1},{'name':' 干粮 ','delta':-1}],
    [{'name':'坏\n名称','delta':1}]])
def test_bad_inventory_proposal_refuses_entire_projection(changes):
    state=character({'coins':10,'inventory':[{'name':'干粮','quantity':2}]})
    with pytest.raises(StateError):
        project(state,{'coins_delta':-3,'inventory_changes':changes})
    assert state.coins==10 and state.inventory[0].quantity==2


def test_consumption_zero_removes_stack_and_capacity_is_checked_after_all_changes():
    state=character({'inventory':[{'name':f'物品{i}','quantity':1} for i in range(40)]})
    with pytest.raises(StateError):
        project(state,{'inventory_changes':[{'name':'新增','delta':1}]})
    result=project(state,{'inventory_changes':[{'name':'新增','delta':1},{'name':'物品0','delta':-1}]})
    assert len(result.inventory)==40
    assert '物品0' not in {item.name for item in result.inventory}
    state=character({'inventory':[{'name':'干粮','quantity':9999}]})
    with pytest.raises(StateError):
        project(state,{'inventory_changes':[{'name':'干粮','delta':1}]})


@pytest.mark.parametrize('items',[[{'name':'干粮','quantity':0}], [{'name':'干粮','quantity':True}],
    [{'name':'é','quantity':1},{'name':'e\u0301','quantity':1}],
    [{'name':f'物品{i}','quantity':1} for i in range(41)]])
def test_initial_inventory_is_strict(items):
    with pytest.raises(StateError):
        character({'inventory':items})


def test_concurrent_item_spending_only_applies_one_draft(tmp_path):
    class Store:
        def get_client(self,session): return self
        async def generate(self,request):
            return {'narrative':'吃下一份干粮。','references':[],
                    'proposed_changes':{'inventory_changes':[{'name':'干粮','delta':-1}]}}
    with TestClient(create_app(Settings(db_path=tmp_path/'race.db'),narration_store=Store())) as client:
        journal=run(client)
        assert prepare(client,journal,'a').status_code==200
        assert prepare(client,journal,'b').status_code==200
        def confirm(key):
            return client.post(f"/api/v1/adventures/{journal['id']}/drafts/{key}/commit",json={'apply_changes':True})
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(confirm,['a','b']))
        assert sorted(r.status_code for r in result)==[200,409]
        detail=client.get(f"/api/v1/adventures/{journal['id']}").json()
        assert detail['character']['inventory'][0]['quantity']==1 and detail['revision']==1


def test_invalid_item_change_prevents_coin_and_turn_commit(tmp_path):
    class Store:
        def get_client(self,session): return self
        async def generate(self,request):
            return {'narrative':'无效提议。','references':[], 'proposed_changes':{
                'coins_delta':-3,'inventory_changes':[{'name':'干粮','delta':-99}],
            }}
    with TestClient(create_app(Settings(db_path=tmp_path/'invalid.db'),narration_store=Store())) as client:
        journal=run(client)
        response=prepare(client,journal)
        assert response.status_code==502
        assert response.json()['error']['code']=='MODEL_STATE_INVALID'
        detail=client.get(f"/api/v1/adventures/{journal['id']}").json()
        assert detail['character']['coins']==10
        assert detail['character']['inventory'][0]['quantity']==2
        assert detail['revision']==0 and detail['turns']==[]
