import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from appmon import dispatcher, pipelines
from appmon.collection import create_plan, work_plans
from appmon.app_collection import report
from appmon.collection_tasks import list_tasks, control_tasks
from appmon.keywords import add_targets
from appmon.intelligence import add_observation_target
from appmon.app_permissions import enqueue
from appmon.db import connect
from appmon.google_rpc import rpc_many
from appmon.stores import Client
from appmon.keyword_stores import SearchResult
from test_monitor import MySQLTestCase
from test_google_batches import response, FIXTURES


class DispatcherTest(MySQLTestCase):
    def setUp(self):
        super().setUp()
        pipelines.save(self.db,self.cfg,'pool',{'enabled':False})

    def test_workers_rotate_ready_types_and_reassign_to_remaining_demand(self):
        m=dict(store='google',country='us',language='en')
        add_targets(self.db,[m|{'keyword':'loan'}])
        add_observation_target(self.db,m|{'kind':'reviews','app_id':'com.example.app'})
        enqueue(self.db,[m|{'app_id':'com.example.app'}])
        def choose(_):
            db=connect(self.cfg,initialize=False)
            try:return dispatcher.choose(db,self.cfg)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=3) as pool:
            lanes=list(pool.map(choose,range(3)))
        self.assertEqual(set(lanes),{'keywords','reviews','permissions'})
        self.db.execute('UPDATE observation_targets SET enabled=0')
        self.db.execute('UPDATE app_permission_targets SET enabled=0')
        self.assertEqual(dispatcher.choose(self.db,self.cfg),'keywords')
        self.db.execute('UPDATE keyword_targets SET enabled=0')
        self.assertIsNone(dispatcher.choose(self.db,self.cfg))

    def test_two_plans_share_one_target_merge_requirements_and_share_result(self):
        base=dict(store='google',country='us',language='en',keywords=['loan'],details=False,name='A')
        a=create_plan(self.db,base|{'keyword_depth':10,'refresh_hours':6})['id'];work_plans(self.db)
        b=create_plan(self.db,base|{'keyword_depth':50,'refresh_hours':24})['id'];work_plans(self.db)
        targets=self.db.execute('SELECT * FROM keyword_targets').fetchall()
        self.assertEqual(len(targets),1)
        self.assertEqual((targets[0]['depth'],targets[0]['refresh_hours']),(50,6))
        with patch('appmon.keyword_stores.KeywordClient.search',return_value=SearchResult([],b'empty','test')):
            result=dispatcher.dispatch_once(self.db,self.cfg,'fixture')
        self.assertEqual(result['processed'],1)
        self.assertEqual(dispatcher.status(self.db)['workers'][0]['name'],'fixture')
        self.assertEqual(report(self.db,a,True)['finished'],1)
        self.assertEqual(report(self.db,b,True)['finished'],1)
        self.assertEqual(report(self.db,a,True)['tasks'][0]['ref'],report(self.db,b,True)['tasks'][0]['ref'])

    def test_one_dispatch_drains_one_full_market_batch_only(self):
        add_targets(self.db,[dict(store='google',country='us',language='en',keyword=f'word {i}') for i in range(45)])
        def send(client,*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            return response(calls,[FIXTURES['search']]*len(calls))
        with patch.object(Client,'request',autospec=True,side_effect=send) as http:
            result=dispatcher.dispatch_once(self.db,self.cfg,'fixture')
        self.assertEqual((result['lane'],result['processed'],http.call_count),('keywords',20,1))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets WHERE due=0').fetchone()['n'],25)

    def test_permission_batch_and_center_controls_preserve_each_target(self):
        enqueue(self.db,[dict(store='google',app_id=f'com.example.app{i}',country='us') for i in range(3)])
        def send(client,*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            return response(calls,[[[['Camera',None,[[None,'take pictures']],None]]]]*len(calls))
        with patch.object(Client,'request',autospec=True,side_effect=send) as http:
            result=dispatcher.dispatch_once(self.db,self.cfg,'fixture')
        self.assertEqual((result['lane'],result['processed'],result['ok'],http.call_count),('permissions',3,3,1))
        tasks=list_tasks(self.db,kind='permissions')
        self.assertEqual(tasks['total'],3)
        control_tasks(self.db,{'scope':'filtered','filters':{'kind':'permissions'},'action':'pause'})
        self.assertEqual(self.db.execute('SELECT sum(enabled) n FROM app_permission_targets').fetchone()['n'],0)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM app_permission_runs').fetchone()['n'],3)

    def test_identical_rpc_is_submitted_once_and_fanned_back_to_all_callers(self):
        client=Client(self.cfg,self.db)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            self.assertEqual(len(calls),1)
            return response(calls,[[['same result']]])
        with patch.object(client,'request',side_effect=send):
            values,_=rpc_many(client,dict(country='us',language='en'),[('test',[1])]*3)
        self.assertEqual(values,[[['same result']]]*3)

    def test_paused_and_live_leases_are_not_claimed_and_global_pause_blocks_dispatch(self):
        from appmon.collection_limits import save
        add_targets(self.db,[dict(store='google',country='us',language='en',keyword='loan')])
        self.db.execute('UPDATE keyword_targets SET lease_until=%s,token=%s',(time.time()+600,'other'))
        self.assertIsNone(dispatcher.choose(self.db,self.cfg))
        self.db.execute('UPDATE keyword_targets SET lease_until=0,token=NULL')
        save(self.db,self.cfg,{'paused':True})
        with patch('appmon.dispatcher.run_batch') as run:
            self.assertEqual(dispatcher.dispatch_once(self.db,self.cfg,'fixture')['processed'],0)
        run.assert_not_called()

    def test_reconciliation_preserves_ids_depth_pause_and_live_leases(self):
        import importlib.util
        from pathlib import Path
        spec=importlib.util.spec_from_file_location('reconcile_collection',Path(__file__).parents[1]/'scripts/reconcile_collection.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from appmon.intelligence import watch
        watch(self.db,dict(store='google',country='us',language='en',app_id='com.example.app'))
        self.db.execute('UPDATE watchlist SET enabled=0')
        self.db.execute('DELETE FROM jobs')
        ids=add_targets(self.db,[dict(store='google',country='us',language='en',keyword=w,depth=70) for w in ['a','b']])['ids']
        self.db.execute("UPDATE keyword_targets SET enabled=0,token='expired',lease_until=1 WHERE id=%s",(ids[0],))
        self.db.execute("UPDATE keyword_targets SET token='live',lease_until=%s WHERE id=%s",(time.time()+600,ids[1]))
        result=module.reconcile(self.db,self.cfg,True,True)
        self.assertEqual(result['changed']['keyword_targets'],1)
        self.assertEqual(result['changed']['missing_jobs'],1)
        rows=self.db.execute('SELECT id,enabled,depth,token FROM keyword_targets ORDER BY id').fetchall()
        self.assertEqual(rows,[dict(id=ids[0],enabled=0,depth=70,token=None),dict(id=ids[1],enabled=1,depth=70,token='live')])
        self.assertEqual(module.reconcile(self.db,self.cfg,True,True)['changed']['missing_jobs'],0)
