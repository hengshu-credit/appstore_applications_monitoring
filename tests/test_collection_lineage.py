import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon.collection_lineage import report,record_dispatch
from appmon.db import add_apps, enqueue
from appmon.intelligence import watch
from appmon.keywords import add_targets
from appmon.app_permissions import enqueue as permissions
from appmon.stores import Client
from appmon.web import create_app
from appmon.dispatcher import dispatch_once
from appmon import pipelines
from test_monitor import OceanBaseTestCase


class CollectionLineageTest(OceanBaseTestCase):
    def test_three_tasks_one_http_are_counted_on_the_correct_edges(self):
        from test_google_batches import response
        pipelines.save(self.db,self.cfg,'pool',{'enabled':False})
        permissions(self.db,[dict(store='google',country='us',app_id=f'com.example.app{i}') for i in range(3)])
        def send(session,method,url,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            return response(calls,[[[['Camera',None,[[None,'take pictures']],None]]]]*len(calls))
        with patch('requests.Session.request',autospec=True,side_effect=send):
            self.assertEqual(dispatch_once(self.db,self.cfg,'fixture')['ok'],3)
        graph=report(self.db,self.cfg)
        edges={(e['source'],e['target']):e for e in graph['edges']}
        self.assertEqual(edges['q:permissions','d:permissions']['count'],3)
        self.assertEqual(edges['d:permissions','s:google']['count'],1)
        self.assertEqual(edges['s:google','out:permissions']['count'],3)
        self.assertEqual(next(n['value'] for n in graph['nodes'] if n['id']=='s:google'),3)

    def test_queue_scope_pool_exclusion_and_persisted_results_are_distinct(self):
        now=time.time();market=dict(store='google',country='us',language='en')
        watch(self.db,market|{'app_id':'com.example.watch'})
        add_apps(self.db,[('google','com.example.pool',None,None)],'test');enqueue(self.db,['us'],'en')
        self.db.execute('UPDATE watchlist SET enabled=0')
        target=add_targets(self.db,[market|{'keyword':'loan'}])['ids'][0]
        self.db.execute('''INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,requested_depth,returned_count,coverage)
            VALUES(%s,%s,'ok','google','v1',50,17,'first_response'),(%s,%s,'error','google','v1',50,NULL,'first_response'),(%s,%s,'ok','google','v1',50,999,'first_response')''',
            (target,now,target,now,target,now-86400*2))
        permissions(self.db,[market|{'app_id':'com.example.watch'}])
        permission=self.db.execute('SELECT id FROM app_permission_targets').fetchone()['id']
        self.db.execute("INSERT INTO app_permission_runs(target_id,observed_at,outcome,payload) VALUES(%s,%s,'ok',%s)",
            (permission,now,json.dumps({'Camera':['one','two'],'Phone':['three']})))
        result=report(self.db,self.cfg)
        nodes={n['id']:n for n in result['nodes']}
        self.assertEqual((nodes['q:details']['value'],nodes['q:pool']['value']),(1,1))
        self.assertEqual(nodes['q:details']['states'],{'paused':1})
        self.assertEqual(nodes['out:keywords']['value'],2)
        self.assertEqual(nodes['out:keywords']['returned_records'],17)
        self.assertEqual(nodes['out:permissions']['value'],1)
        self.assertEqual(nodes['s:google']['value'],3)
        self.assertEqual(nodes['s:google']['returned_records'],20)
        self.assertIsNone(nodes['s:google']['requests'])
        self.assertEqual(next(e['label'] for e in result['edges'] if e['source']=='d:keywords' and e['target']=='s:google'),'')
        self.assertEqual(next(r for r in nodes['s:google']['records'] if r['kind']=='keywords')['errors'],1)
        self.assertEqual({e['tasks'] for e in result['edges'] if e['source']=='d:keywords' and e['target']=='s:google'},{1})
        apple={n['id']:n for n in report(self.db,self.cfg,'apple')['nodes']}
        self.assertNotIn('s:google',apple);self.assertEqual(apple['q:keywords']['value'],0)

    def test_one_http_counter_per_transport_call_and_no_sensitive_request_data(self):
        client=Client(self.cfg,self.db)
        client.lineage_lane="keywords"
        record_dispatch(self.db,'keywords','google',8)
        with patch.object(client.session,'get',return_value=SimpleNamespace(status_code=200)):
            client.get('google','https://play.google.com/store/apps/details?token=very-secret',{'id':'hidden-app'})
        result=report(self.db,self.cfg)
        google=next(n for n in result['nodes'] if n['id']=='s:google')
        self.assertEqual(google['requests'],1)
        self.assertEqual(next(n['value'] for n in result['nodes'] if n['id']=='d:keywords'),8)
        self.assertEqual(next(e['label'] for e in result['edges'] if e['source']=='q:keywords' and e['target']=='d:keywords'),'8 次任务消费')
        self.assertEqual(next(e['label'] for e in result['edges'] if e['source']=='timer' and e['target']=='q:keywords'),'')
        self.assertEqual(next(e['label'] for e in result['edges'] if e['source']=='d:keywords' and e['target']=='s:google'),'1 次 HTTP')
        self.assertIsNotNone(result['http_since'])
        recorded=str(self.db.execute("SELECT * FROM collection_logs WHERE resource='http'").fetchall())
        self.assertNotIn('very-secret',recorded);self.assertNotIn('hidden-app',recorded)
        self.assertNotIn('https://',recorded)

    def test_lineage_api_is_guarded_cached_and_validates_scope(self):
        client=TestClient(create_app(self.cfg));path='/api/research/collection/lineage'
        self.assertEqual(client.get(path).status_code,401);self.login_admin(client)
        result=client.get(path);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(client.get(path).json()['at'],result.json()['at'])
        self.assertEqual(client.get(path+'?hours=999').status_code,400)
        self.assertEqual(client.get(path+'?store=invalid').status_code,400)
        graph=result.json();ids={n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))
        self.assertEqual(len(ids),len(graph['nodes']))
