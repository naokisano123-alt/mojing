import json
from io import BytesIO
from zipfile import ZipFile
import httpx
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.model_gateway import ModelSessionStore
from test_checkpoints import make_run, advance


def api(world, suffix=''):
    return f'/api/v1/journeys/{world}{suffix}'


def slots(client, folder):
    response = client.get(api(folder, '/saves'))
    assert response.status_code == 200, response.text
    return response.json()['items']


def manual(client, folder, run, key='save', name='进城之前'):
    return client.post(api(folder, '/saves'), json={'name': name, 'adventure_id': run['id'], 'expected_revision': run['revision'], 'request_id': key})


def test_auto_slot_only_tracks_confirmed_progress_and_load_keeps_folder(client):
    world, run = make_run(client)
    auto = slots(client, run['id'])
    assert len(auto) == 1 and auto[0]['kind'] == 'auto' and auto[0]['revision'] == 0
    response = manual(client, run['id'], run)
    assert response.status_code == 201, response.text
    saved = response.json()
    client.post(f"/api/v1/adventures/{run['id']}/drafts", json={'request_id':'draft', 'expected_revision':0, 'action':'走进城市'})
    assert slots(client, run['id'])[0]['revision'] == 0
    progressed = client.post(f"/api/v1/adventures/{run['id']}/drafts/draft/commit", json={'apply_changes':True}).json()
    assert slots(client, run['id'])[0]['revision'] == 1
    assert sum(s['kind'] == 'auto' for s in slots(client, run['id'])) == 1
    loaded = client.post(api(run['id'], f"/load/{saved['id']}"), json={'request_id':'load'}).json()
    assert loaded['revision'] == 0 and loaded['id'] != run['id']
    folders = client.get(f'/api/v1/worlds/{world}/journeys').json()['items']
    assert len(folders) == 1 and folders[0]['active_adventure_id'] == loaded['id']
    assert len(slots(client, run['id'])) == 2
    assert client.get(f"/api/v1/adventures/{progressed['id']}").json()['revision'] == 1
    # Replaying a lost load response cannot duplicate the folder or autosave.
    retry = client.post(api(run['id'], f"/load/{saved['id']}"), json={'request_id':'load'})
    assert retry.status_code == 201 and retry.json()['id'] == loaded['id']
    resumed = advance(client, loaded, 'branch-next')
    assert slots(client, run['id'])[0]['adventure_id'] == resumed['id']


def test_twenty_manual_saves_rename_delete_and_cross_world_rejection(client):
    world, run = make_run(client)
    for n in range(20):
        r = manual(client, run['id'], run, f'save{n}', f'进度{n}')
        assert r.status_code == 201, r.text
    assert manual(client, run['id'], run, 'overflow').json()['error']['code'] == 'SAVE_LIMIT'
    assert manual(client, run['id'], run, 'save0', '进度0').status_code == 201
    assert client.patch(api(run['id']), json={'name':'重命名旅程'}).status_code == 200
    assert client.get(f'/api/v1/worlds/{world}/journeys').json()['items'][0]['name'] == '重命名旅程'
    first = next(s for s in slots(client, run['id']) if s['kind'] == 'manual')
    assert client.delete(api(run['id'], '/saves/' + first['id'])).status_code == 204
    assert len(slots(client, run['id'])) == 20
    assert manual(client, run['id'], run, 'new').status_code == 201
    _, other = make_run(client)
    assert manual(client, run['id'], other).status_code == 409
    auto = slots(client, run['id'])[0]
    assert client.delete(api(run['id'], '/saves/' + auto['id'])).status_code == 409


def test_selected_journey_export_preserves_all_slots_and_can_import_into_same_world(client):
    world, run = make_run(client)
    saved = manual(client, run['id'], run).json()
    progressed = advance(client, run, 'next')
    loaded = client.post(api(run['id'], f"/load/{saved['id']}"), json={'request_id':'load'}).json()
    new = client.post(api(run['id'], f"/load/{saved['id']}"), json={'request_id':'new', 'as_new_journey':True, 'name':'另一段旅程'}).json()
    assert client.patch(f"/api/v1/adventures/{run['id']}/item-description", json={'name':'干粮','description':'旅人自己烘焙的饼干。'}).status_code == 200
    url = f'/api/v1/worlds/{world}/transfer'
    exported = client.get(url, params={'part':'zip', 'journeys':run['id']})
    assert exported.status_code == 200, exported.text
    with ZipFile(BytesIO(exported.content)) as zipped:
        payload = json.loads(zipped.read('saves.json'))
        assert {r['id'] for r in payload['adventures']} == {run['id'], loaded['id']}
        assert new['id'] not in {r['id'] for r in payload['adventures']}
        assert len(payload['journey_metadata']['slots']) == 2
    imported = client.post('/api/v1/world-transfers', files=[('files',('world.zip',exported.content,'application/zip'))])
    assert imported.status_code == 201, imported.text
    copied = imported.json()['id']
    folders = client.get(f'/api/v1/worlds/{copied}/journeys').json()['items']
    assert len(folders) == 1
    copy_slots = slots(client, folders[0]['id'])
    assert len(copy_slots) == 2 and {s['kind'] for s in copy_slots} == {'manual','auto'}
    restored = client.post(api(folders[0]['id'], '/load/' + next(s['id'] for s in copy_slots if s['kind']=='manual')), json={'request_id':'restore-copy'})
    assert restored.status_code == 201, restored.text
    # A same-world import adds folders without adding/replacing world documents.
    same = client.post('/api/v1/world-transfers', params={'target_world_id':world}, files=[('files',('world.zip',exported.content,'application/zip'))])
    assert same.status_code == 201, same.text
    assert same.json()['id'] == world
    assert len(client.get(f'/api/v1/worlds/{world}/journeys').json()['items']) == 3
    assert manual(client, run['id'], run).json()['id'] == saved['id']
    saves_only = client.get(url, params={'part':'saves','journeys':run['id']})
    again = client.post('/api/v1/world-transfers', params={'target_world_id':copied}, files=[('files',('saves.json',saves_only.content,'application/json'))])
    assert again.status_code == 201, again.text
    assert len(client.get(f'/api/v1/worlds/{copied}/journeys').json()['items']) == 2

    assert client.get(f"/api/v1/adventures/{progressed['id']}").json()['revision'] == 1


def test_natural_opening_interpretation_and_options_use_final_edited_description(tmp_path):
    calls = []
    def upstream(request):
        body = json.loads(request.content)
        calls.append(body)
        output = {'options':[{'title':'过客','description':'我提着旧灯来到渡口。'}]}
        if 'item_descriptions' in body['messages'][0]['content']:
            output = {'character':{'name':'黎斯','location':'渡口','coins':7,'inventory':[{'name':'旧灯','quantity':1}], 'condition':'左手有旧伤。'},'item_descriptions':{'旧灯':'母亲留下的铜灯，灯罩有裂纹。'},'starts_at':'1000-01-01T14:00'}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output,ensure_ascii=False)}}]})
    store=ModelSessionStore(http_transport=httpx.MockTransport(upstream))
    session=store.create('https://api.deepseek.com','deepseek-flash','test-key')['session_id']
    with TestClient(create_app(Settings(db_path=tmp_path/'phone.db'),narration_store=store)) as client:
        world=client.post('/api/v1/worlds',json={'name':'新世界','description':'一个河港'}).json()['id']
        body={'mode':'live','session_id':session,'selected_source_ids':[],'description':'我想当旅人'}
        options=client.post(f'/api/v1/worlds/{world}/opening-options',json=body)
        assert options.status_code==200, options.text
        final='我叫黎斯，提着母亲留下的旧灯来到渡口，灯罩有裂纹。左手有旧伤，口袋里有七枚铜币。'
        body.update(name='灯与河', description=final, request_id='start')
        started=client.post(f'/api/v1/worlds/{world}/journey-start',json=body)
        assert started.status_code==201, started.text
        run=started.json()
        assert run['opening']['intro']==final
        assert run['character']['coins']==7 and run['character']['inventory'][0]['name']=='旧灯'
        detail=client.get(f"/api/v1/adventures/{run['id']}").json()
        assert detail['item_descriptions']['旧灯']=='母亲留下的铜灯，灯罩有裂纹。'
        assert final in calls[-1]['messages'][1]['content']
        assert calls[-1]['thinking']=={'type':'disabled'}
        retry=client.post(f'/api/v1/worlds/{world}/journey-start',json=body)
        assert retry.json()['id']==run['id'] and len(calls)==2
        assert slots(client,run['id'])[0]['revision']==0


def test_unified_opening_still_initializes_npcs_events_and_knowledge(client, db_path):
    import sqlite3
    world = client.post('/api/v1/worlds', json={'name':'港城','description':'临海的港城'}).json()['id']
    document = client.post(f'/api/v1/worlds/{world}/documents', files={'file':('港城.txt','雾港临海。阿棠是修补铺掌柜，平日住在雾港。商会最近正在准备货物。'.encode(),'text/plain')})
    assert document.status_code == 201, document.text
    started = client.post(f'/api/v1/worlds/{world}/journey-start', json={'name':'在港口醒来','mode':'fake','description':'我是旅人，来到雾港。','request_id':'start-with-doc','selected_source_ids':['doc:'+document.json()['id']]})
    assert started.status_code == 201, started.text
    run = started.json()
    with sqlite3.connect(db_path) as conn:
        instance = conn.execute('SELECT id FROM world_instances WHERE adventure_id=?',(run['id'],)).fetchone()[0]
        assert conn.execute('SELECT COUNT(*) FROM m3_npcs WHERE instance_id=?',(instance,)).fetchone()[0] > 0
        assert conn.execute('SELECT COUNT(*) FROM world_events WHERE instance_id=?',(instance,)).fetchone()[0] > 0
        auto = conn.execute('SELECT instance_snapshot FROM adventure_checkpoints WHERE id=?',(slots(client,run['id'])[0]['id'],)).fetchone()[0]
        assert json.loads(auto)['domains']['npcs']


def test_edited_world_rejects_same_world_import_and_bad_metadata_rolls_back(client):
    world, run = make_run(client)
    exported = client.get(f'/api/v1/worlds/{world}/transfer',params={'part':'zip'})
    client.patch(f'/api/v1/worlds/{world}',json={'description':'已经修改的世界观版本'})
    rejected = client.post('/api/v1/world-transfers',params={'target_world_id':world},files=[('files',('world.zip',exported.content,'application/zip'))])
    assert rejected.status_code in (409,422)
    assert len(client.get(f'/api/v1/worlds/{world}/journeys').json()['items']) == 1
    with ZipFile(BytesIO(exported.content)) as archive:
        world_bytes = archive.read('world.json')
        saves = json.loads(archive.read('saves.json'))
    saves['journey_metadata']['members'][0]['journey_id']='a-foreign-folder'
    before = client.get('/api/v1/worlds').json()['total']
    result = client.post('/api/v1/world-transfers', files=[('files',('world.json',world_bytes,'application/json')),('files',('saves.json',json.dumps(saves).encode(),'application/json'))])
    assert result.status_code == 422
    assert client.get('/api/v1/worlds').json()['total'] == before
