import base64
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon import source_tasks as queue, collection, collection_tasks, collection_lineage, pipelines
from appmon.dispatcher import dispatch_once
from appmon.web import create_app
from test_monitor import MySQLTestCase
from test_discovery import workbook_bytes


class SourceQueueTest(MySQLTestCase):
    def setUp(self):
        super().setUp()
        pipelines.save(self.db,self.cfg,'pool',{'enabled':False})
        self.spec=dict(store='apple',country='us',language='en',genre='FINANCE',period='weekly',start='2025-01-05',limit=20)
        self.response={'result':{'rows':[dict(week='2025-01-05',countryOrRegion='US',genre='FINANCE',searchTerm='budget',rankInGenre=3,searchPopularity1to100=88)]}}

    def test_invalid_receipts_and_imports_do_not_enter_queue(self):
        for response in ({'result':None},{'result':'invalid'},None):
            with self.assertRaises(ValueError):queue.enqueue(self.db,'aso_import',self.spec|{'response':response})
        with self.assertRaises(ValueError):queue.enqueue(self.db,'aso_popularity',self.spec,request_id=0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_source_tasks').fetchone()['n'],0)

    def test_compatible_one_shot_receipts_exceed_old_fifty_task_limit(self):
        for i in range(70):queue.enqueue(self.db,'aso_popularity',self.spec,request_id=f'large-{i}')
        with patch('appmon.apple_popularity.request_data',return_value=self.response) as fetch:
            result=queue.work(self.db,self.cfg)
        self.assertEqual((result['processed'],result['ok']),(70,70));fetch.assert_called_once()
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_source_fetches').fetchone()['n'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_source_tasks WHERE due IS NULL').fetchone()['n'],70)

    def test_api_only_enqueues_merge_fetch_and_complete_without_rescheduling(self):
        client=TestClient(create_app(self.cfg));self.login_admin(client)
        with patch('appmon.apple_popularity.requests.post') as http:
            a=client.post('/api/discovery/keywords/apple-popularity',json=self.spec|{'request_id':'receipt-a'})
            self.assertEqual(a.status_code,200,a.text);self.assertEqual(a.json()['status'],'pending')
            http.assert_not_called()
        first=a.json()['id']
        duplicate=client.post('/api/discovery/keywords/apple-popularity',json=self.spec|{'request_id':'receipt-a'}).json()
        self.assertEqual(duplicate['id'],first);self.assertTrue(duplicate['reused'])
        second=queue.enqueue(self.db,'aso_popularity',self.spec|{'limit':100})['id']
        third=queue.enqueue(self.db,'aso_popularity',self.spec|{'language':'es','limit':60})['id']
        response=SimpleNamespace(status_code=200,headers={},json=lambda:self.response)
        with patch.dict(os.environ,{'APPLE_ADS_ACCESS_TOKEN':'test-token','APPLE_ADS_AD_ACCOUNT_ID':'123'}),patch('appmon.apple_popularity.requests.post',return_value=response) as fetch:
            result=dispatch_once(self.db,self.cfg,'test')
            self.assertEqual(result['ok'],3);fetch.assert_called_once()
            self.assertEqual(fetch.call_args.kwargs['json']['pagination']['pageSize'],100)
        for identifier in (first,second,third):
            task=queue.receipt(self.db,identifier)
            self.assertEqual(task['status'],'done');self.assertIsNone(task['due']);self.assertEqual(task['result']['returned'],1)
        self.assertEqual(queue.receipt(self.db,first)['result']['id'],queue.receipt(self.db,second)['result']['id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_signals').fetchone()['n'],2)
        self.assertEqual(dispatch_once(self.db,self.cfg,'test')['processed'],0)
        listing=collection_tasks.list_tasks(self.db,kind='aso_popularity',state='done')
        self.assertEqual(listing['total'],3)
        self.assertEqual(next(c['total'] for c in listing['categories'] if c['kind']=='aso_popularity'),3)
        self.assertEqual(next(c['total'] for c in listing['categories'] if c['kind']=='aso_report'),0)
        graph=collection_lineage.report(self.db,self.cfg,'apple')
        self.assertEqual(next(n['value'] for n in graph['nodes'] if n['id']=='s:ads'),3)
        edge=next(e for e in graph['edges'] if e['source']=='d:aso_popularity' and e['target']=='s:ads')
        self.assertEqual(edge['count'],1)

    def test_common_intake_file_import_survives_replay_and_has_no_http(self):
        spec=dict(store='apple',country='cn',language='zh-cn',app_id='123',filename='report.xlsx',content_base64=base64.b64encode(workbook_bytes()).decode())
        request=dict(source='external-system',request_id='batch-a',tasks=[spec|{'kind':'aso_report'}])
        plan=collection.enqueue(self.db,request)
        self.assertEqual(collection.enqueue(self.db,request)['id'],plan['id'])
        collection.work_plans(self.db)
        job=self.db.execute('SELECT id FROM collection_source_tasks').fetchone()['id']
        self.db.execute('UPDATE collection_plans SET status="queued",completed=0')
        collection.work_plans(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_source_tasks').fetchone()['n'],1)
        with patch('requests.Session.request') as http:
            self.assertEqual(dispatch_once(self.db,self.cfg,'test')['ok'],1);http.assert_not_called()
        self.assertGreater(queue.receipt(self.db,job)['result']['rows'],0)
        graph=collection_lineage.report(self.db,self.cfg,'apple')
        self.assertIn('q:aso_report',{n['id'] for n in graph['nodes']})
        self.assertFalse(any(e['source']=='timer' and e['target']=='q:aso_report' for e in graph['edges']))
        edge=next(e for e in graph['edges'] if e['source']=='d:aso_report' and e['target']=='s:aso_file')
        self.assertEqual(edge['count'],1)

    def test_failure_retry_pause_fencing_and_no_period_settings(self):
        task=queue.enqueue(self.db,'aso_popularity',self.spec);identifier=task['id'];ref={'resource':'sources','id':identifier}
        with patch('appmon.apple_popularity.request_data',side_effect=ValueError('temporary failure')):
            self.assertEqual(dispatch_once(self.db,self.cfg,'test')['error'],1)
        self.assertEqual(queue.receipt(self.db,identifier)['status'],'error')
        collection_tasks.control_tasks(self.db,{'tasks':[ref],'action':'refresh'})
        self.db.execute("UPDATE schedules SET next_at=0 WHERE name LIKE 'rate:%'")
        def pause(*args):
            collection_tasks.control_tasks(self.db,{'tasks':[ref],'action':'pause'})
            return self.response
        with patch('appmon.apple_popularity.request_data',side_effect=pause):
            self.assertEqual(dispatch_once(self.db,self.cfg,'test')['ok'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_signals').fetchone()['n'],0)
        self.assertEqual(queue.receipt(self.db,identifier)['status'],'paused')
        with self.assertRaisesRegex(ValueError,'一次性'):
            collection_tasks.control_tasks(self.db,{'tasks':[ref],'action':'interval','refresh_hours':24})
        self.db.execute('UPDATE collection_source_tasks SET enabled=1,failures=3,due=0')
        collection_tasks.control_tasks(self.db,{'scope':'filtered','action':'retry','filters':{'kind':'aso_popularity'}})
        self.assertEqual(queue.receipt(self.db,identifier)['failures'],0)

    def test_atomic_data_and_acknowledgements_rollback_and_expired_lease_recovery(self):
        a=queue.enqueue(self.db,'aso_import',self.spec|{'response':self.response})['id']
        b=queue.enqueue(self.db,'aso_import',self.spec|{'response':self.response})['id']
        self.db.execute('UPDATE collection_source_tasks SET lease_until=%s,token="old"',(time.time()-1,))
        from appmon import source_tasks
        original=source_tasks.emit
        def failing(db,resource,identifier,event,*args,**kwargs):
            if event=='ok' and identifier==b:raise RuntimeError('write failure')
            return original(db,resource,identifier,event,*args,**kwargs)
        with patch('appmon.source_tasks.emit',side_effect=failing):
            self.assertEqual(queue.work(self.db,self.cfg)['error'],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_signals').fetchone()['n'],0)
        self.assertIsNone(queue.receipt(self.db,a)['result'])
        collection_tasks.control_tasks(self.db,{'scope':'all','action':'retry'})
        self.assertEqual(queue.work(self.db,self.cfg)['ok'],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_source_fetches').fetchone()['n'],1)
