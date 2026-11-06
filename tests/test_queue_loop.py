import json
import time
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from appmon import collection, dispatcher, pipelines
from appmon.app_collection import report
from appmon.collection_batch import revision
from appmon.db import connect
from appmon.keywords import add_targets
from appmon.stores import Client
from appmon.web import create_app
from test_google_batches import response, FIXTURES
from test_monitor import MySQLTestCase


class QueueLoopTest(MySQLTestCase):
    def setUp(self):
        super().setUp()
        pipelines.save(self.db,self.cfg,'pool',{'enabled':False})

    def tasks(self, count=3):
        return [dict(kind='keywords',store='google',country='us',language='en',keyword=f'word {i}',depth=10) for i in range(count)]

    def response(self, client, *args, **kwargs):
        calls=json.loads(kwargs['data']['f.req'])[0]
        return response(calls,[FIXTURES['search']]*len(calls))

    def test_concurrent_duplicate_submissions_return_one_receipt(self):
        def submit(_):
            db=connect(self.cfg,initialize=False)
            try:return collection.enqueue(db,dict(source='crm',request_id='same',tasks=self.tasks()))
            finally:db.close()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(submit,range(3)))
        self.assertEqual(len({r['id'] for r in results}),1)
        self.assertEqual(sum(not r['reused'] for r in results),1)

    def test_multiple_producers_share_targets_one_request_and_one_database_commit(self):
        a=collection.enqueue(self.db,dict(source='crm',request_id='daily-1',tasks=self.tasks()))
        duplicate=collection.enqueue(self.db,dict(source='crm',request_id='daily-1',tasks=self.tasks()))
        self.assertEqual(a['id'],duplicate['id']);self.assertTrue(duplicate['reused'])
        b=collection.enqueue(self.db,dict(source='timer',request_id='daily-1',tasks=self.tasks()))
        collection.work_plans(self.db);collection.work_plans(self.db)
        before=revision(self.db)
        observer=connect(self.cfg,initialize=False)
        original=self.db.executemany
        writes=[]
        def observe(sql,values):
            if sql.startswith('INSERT INTO keyword_ranks'):
                writes.append(len(values))
                self.assertEqual(observer.execute('SELECT COUNT(*) n FROM keyword_runs').fetchone()['n'],0)
                self.assertEqual(revision(observer),before)
            return original(sql,values)
        try:
            with patch.object(Client,'request',autospec=True,side_effect=self.response) as http,patch.object(self.db,'executemany',side_effect=observe),patch.object(self.db.connection,'commit',wraps=self.db.connection.commit) as commit:
                counts=dispatcher.dispatch_once(self.db,self.cfg,'test')
            self.assertEqual(counts['ok'],3);self.assertEqual(http.call_count,1)
            self.assertEqual(commit.call_count,3)  # Scheduling, claim, atomic result batch.
            self.assertEqual(writes,[10,10,10])
            self.assertEqual(observer.execute('SELECT COUNT(*) n FROM keyword_ranks').fetchone()['n'],30)
            self.assertEqual(revision(observer),before+1)
        finally:observer.close()
        self.assertEqual(report(self.db,a['id'],True)['finished'],3)
        self.assertEqual(report(self.db,b['id'],True)['finished'],3)

    def test_partial_database_failure_rolls_back_results_and_leaves_all_unfinished_for_retry(self):
        submission=collection.enqueue(self.db,dict(source='crm',request_id='failure',tasks=self.tasks()))
        collection.work_plans(self.db);before=revision(self.db)
        original=self.db.executemany;calls=0
        def fail_second(sql,values):
            nonlocal calls
            if sql.startswith('INSERT INTO keyword_ranks'):
                calls+=1
                if calls==2:raise RuntimeError('disk write failed')
            return original(sql,values)
        with patch.object(Client,'request',autospec=True,side_effect=self.response),patch.object(self.db,'executemany',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'disk write'):
                dispatcher.dispatch_once(self.db,self.cfg,'test')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_runs').fetchone()['n'],0)
        self.assertEqual(revision(self.db),before)
        self.assertEqual(report(self.db,submission['id'],True)['finished'],0)
        self.db.execute('UPDATE keyword_targets SET lease_until=0')
        with patch.object(Client,'request',autospec=True,side_effect=self.response):
            self.assertEqual(dispatcher.dispatch_once(self.db,self.cfg,'test')['ok'],3)
        self.assertEqual(report(self.db,submission['id'],True)['finished'],3)

    def test_http_failures_can_rejoin_batch_but_parser_failures_retry_individually(self):
        add_targets(self.db,[{k:v for k,v in t.items() if k!='kind'} for t in self.tasks()])
        self.db.execute("UPDATE keyword_targets SET failures=1,last_error='HTTP 429'")
        with patch.object(Client,'request',autospec=True,side_effect=self.response) as http:
            self.assertEqual(dispatcher.dispatch_once(self.db,self.cfg,'test')['processed'],3)
        self.assertEqual(http.call_count,1)
        from appmon.target_batches import claim_targets
        self.db.execute("UPDATE keyword_targets SET due=0,failures=1,last_error='Google batch omitted this target; retry separately'")
        self.assertEqual(len(claim_targets(self.db,'keyword_targets',600,20)),1)

    def test_unified_api_validates_entire_submission_and_enforces_idempotency_and_scope(self):
        api=TestClient(create_app(self.cfg));url='/api/research/collection/enqueue'
        value=dict(source='billing',request_id='invoice-1',tasks=self.tasks())
        self.assertEqual(api.post(url,json=value).status_code,401);self.login_admin(api)
        bad=value|{'tasks':self.tasks()+[dict(kind='permissions',store='apple',app_id='123',country='us',language='en')]}
        self.assertEqual(api.post(url,json=bad).status_code,400)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_plans').fetchone()['n'],0)
        created=api.post(url,json=value);self.assertEqual(created.status_code,200,created.text)
        self.assertTrue(api.post(url,json=value).json()['reused'])
        self.assertEqual(api.post(url,json=value|{'tasks':self.tasks(1)}).status_code,400)
        collection.work_plans(self.db)
        progress=api.get(f"/api/research/plans/{created.json()['id']}/collection")
        self.assertEqual(progress.status_code,200,progress.text)
        self.assertEqual(len(api.get('/api/research/collection/activity?store=google').json()['plans']),1)
        self.assertEqual(len(api.get('/api/research/collection/activity?store=apple').json()['plans']),0)
        self.assertEqual(api.get('/api/research/collection/revision').json()['revision'],revision(self.db))

    def test_icons_permissions_and_paused_targets_share_normal_queue_submission(self):
        tasks=[dict(kind=k,store='google',app_id='com.example.app',country='us',language='en') for k in ['permissions','icons']]
        job=collection.enqueue(self.db,dict(source='ui',tasks=tasks));collection.work_plans(self.db)
        result=report(self.db,job['id'],True)
        self.assertEqual([t['ref']['resource'] for t in result['tasks']],['permissions','icons'])
        self.db.execute('UPDATE app_permission_targets SET enabled=0')
        self.db.execute('UPDATE app_icons SET enabled=0')
        collection.enqueue(self.db,dict(source='timer',tasks=tasks));collection.work_plans(self.db)
        self.assertIsNone(dispatcher.choose(self.db,self.cfg))

    def test_submission_requeues_a_successful_target_once_without_resetting_backoff(self):
        add_targets(self.db,[{k:v for k,v in t.items() if k!='kind'} for t in self.tasks(1)])
        self.db.execute('UPDATE keyword_targets SET due=%s',(time.time()+86400,))
        collection.enqueue(self.db,dict(source='timer',tasks=self.tasks(1)));collection.work_plans(self.db)
        self.assertEqual(self.db.execute('SELECT due FROM keyword_targets').fetchone()['due'],0)
        self.db.execute("UPDATE keyword_targets SET due=12345678999,failures=1,last_error='HTTP 429'")
        collection.enqueue(self.db,dict(source='timer',tasks=self.tasks(1)));collection.work_plans(self.db)
        self.assertEqual(self.db.execute('SELECT due FROM keyword_targets').fetchone()['due'],12345678999)
