import time
import json
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from appmon.db import add_apps, enqueue, claim_details, connect
from appmon.monitor import work, save_result
from appmon.intelligence import watch, work_watchlist
from appmon.collection_tasks import list_tasks, control_tasks
from appmon.keywords import add_targets, work_keywords
from appmon.stores import Client, Result, FetchError
from appmon import ad_sources, collection_limits as limits, pipelines, source_tasks
from test_monitor import OceanBaseTestCase


class CollectionBatchTest(OceanBaseTestCase):
    def seed(self, size=105):
        add_apps(self.db,[('apple',str(1000+i),None,None) for i in range(size)],'test')
        enqueue(self.db,['us'],'en')

    @staticmethod
    def response(ids):
        data = {'resultCount':len(ids),'results':[dict(trackId=int(i),wrapperType='software',trackName='App '+i) for i in reversed(ids)]}
        return SimpleNamespace(status_code=200,content=json.dumps(data).encode(),json=lambda:data)

    def test_105_apps_use_one_http_call_and_preserve_each_identity(self):
        self.seed()
        client=Client(self.cfg,self.db)
        with patch.object(client,'get',side_effect=lambda store,url,params,headers:self.response(params['id'].split(','))) as send:
            self.assertEqual(work(self.cfg,self.db,105,client)['ok'],105)
        self.assertEqual([len(c.args[2]['id'].split(',')) for c in send.call_args_list],[105])
        rows=self.db.execute('SELECT app_id,payload FROM listings').fetchall()
        self.assertEqual(len(rows),105)
        self.assertTrue(all(json.loads(r['payload'])['name']=='App '+r['app_id'] for r in rows))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM jobs WHERE token IS NOT NULL').fetchone()['n'],0)

    def test_larger_lookup_keeps_all_fields_and_reuses_unchanged_content(self):
        from appmon import monitor
        from appmon.content_store import put,load_many
        self.seed(105)
        def read(store,url,params,headers):return self.response(params['id'].split(','))
        cfg=self.cfg | {'apple_lookup_batch_size':50}
        client=Client(cfg,self.db)
        try:
            with patch.object(client,'get',side_effect=read) as old:
                self.assertEqual(work(cfg,self.db,105,client)['ok'],105)
            self.assertEqual([len(c.args[2]['id'].split(',')) for c in old.call_args_list],[50,50,5])
        finally:client.session.close()
        before=self.db.execute('SELECT app_id,payload,digest,state FROM listings ORDER BY app_id').fetchall()
        self.db.execute('UPDATE jobs SET due=0')
        client=Client(self.cfg,self.db)
        try:
            with patch.object(client,'get',side_effect=read) as current,patch.object(monitor,'put',wraps=put) as write_body,patch.object(
                    monitor,'load_many',wraps=load_many) as read_body,patch.object(monitor,'raw_save',wraps=monitor.raw_save) as raw:
                self.assertEqual(work(self.cfg,self.db,105,client)['ok'],105)
            current.assert_called_once();raw.assert_called_once();write_body.assert_not_called();read_body.assert_not_called()
        finally:client.session.close()
        self.assertEqual(self.db.execute('SELECT app_id,payload,digest,state FROM listings ORDER BY app_id').fetchall(),before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM snapshots').fetchone()['n'],105)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM requests').fetchone()['n'],210)

    def test_raw_object_shared_only_with_eligible_members_in_the_same_response(self):
        from appmon import app_groups,monitor
        self.seed(4)
        group=app_groups.create(self.db,{'name':'raw-scope'})
        app_groups.add_members(self.db,group['id'],{'members':[dict(store='apple',app_id=str(i),country='gb',language='en') for i in (1000,1002)]})
        cfg=self.cfg|{'raw_response_scope':'groups'}
        client=Client(cfg,self.db)
        try:
            with patch.object(client,'get',side_effect=lambda store,url,p,headers:self.response(p['id'].split(','))) as remote,patch.object(monitor,'raw_save',wraps=monitor.raw_save) as archive:
                self.assertEqual(work(cfg,self.db,4,client)['ok'],4)
            remote.assert_called_once();archive.assert_called_once()
        finally:client.session.close()
        rows=self.db.execute("SELECT app_id,raw_path FROM snapshots WHERE country='us' ORDER BY app_id").fetchall()
        self.assertEqual([r['app_id'] for r in rows if r['raw_path']],['1000','1002'])
        self.assertEqual(rows[0]['raw_path'],rows[2]['raw_path'])

    def test_partial_response_retries_single_missing_without_false_delisting(self):
        self.seed(3);client=Client(self.cfg,self.db)
        with patch.object(client,'get',return_value=self.response(['1000','1002'])):
            result=work(self.cfg,self.db,3,client)
        self.assertEqual((result['ok'],result['error'],result['missing']),(2,1,0))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM listings').fetchone()['n'],2)
        self.db.execute("UPDATE jobs SET due=0 WHERE app_id='1001'")
        with patch.object(client,'get',return_value=self.response([])) as send:
            self.assertEqual(work(self.cfg,self.db,3,client)['missing'],1)
        self.assertEqual(send.call_args.args[2]['id'],'1001')

    def test_batch_claims_isolate_markets_workers_and_paused_watches(self):
        self.seed(60);enqueue(self.db,['gb'],'en')
        watch(self.db,dict(store='apple',app_id='1000',country='us',language='en'))
        self.db.execute('UPDATE watchlist SET enabled=0')
        def claim_one(_):
            db=connect(self.cfg,initialize=False)
            try:return claim_details(db,['us','gb'],'en',600,50)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            batches=list(pool.map(claim_one,range(2)))
        keys=[(j['app_id'],j['country']) for b in batches for j in b]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertNotIn(('1000','us'),keys)
        self.assertTrue(all(len({j['country'] for j in b})==1 and len(b)<=50 for b in batches))

    def test_large_detail_save_only_renews_uncommitted_live_leases(self):
        from appmon.detail_batches import renew_pending_leases
        self.seed(5)
        jobs=claim_details(self.db,['us'],'en',600,5,now=100)
        self.assertTrue(all(j['lease_until']==700 for j in jobs))
        self.db.execute("UPDATE jobs SET token=NULL,lease_until=0 WHERE app_id='1000'")
        self.db.execute("UPDATE jobs SET token='replacement',lease_until=900 WHERE app_id='1002'")
        self.db.execute("UPDATE jobs SET lease_until=300 WHERE app_id='1003'")
        renew_pending_leases(self.db,jobs[1:],400,600)
        rows=self.db.execute('SELECT lease_until FROM jobs ORDER BY app_id').fetchall()
        self.assertEqual([r['lease_until'] for r in rows],[0,1000,900,300,1000])

    def test_detail_claim_applies_learned_limit_only_to_its_storefront(self):
        from appmon.batch_limits import BatchWindow,window_key
        self.seed(12);enqueue(self.db,['gb'],'en')
        window=BatchWindow()
        window.too_large(window_key({'country':'us','language':'en'},('apple-lookup',)),8)
        us=claim_details(self.db,['us'],'en',600,12,batch_window=window)
        gb=claim_details(self.db,['gb'],'en',600,12,batch_window=window)
        self.assertEqual((len(us),len(gb)),(4,12))
        self.assertTrue(all(j['country']=='us' for j in us))
        self.assertTrue(all(j['country']=='gb' for j in gb))

    def test_watch_batch_pause_invalidates_only_its_result_and_keeps_intervals(self):
        for i,hours in [(1000,6),(1001,48)]:
            watch(self.db,dict(store='apple',app_id=str(i),country='us',language='en',refresh_hours=hours))
        row=list_tasks(self.db)['items'][0]
        client=Client(self.cfg,self.db)
        def response(*args,**kwargs):
            control_tasks(self.db,{'action':'pause','tasks':[{'resource':'watchlist','id':row['id']}]})
            return self.response(['1000','1001'])
        before=time.time()
        with patch.object(client,'get',side_effect=response) as send:
            result=work_watchlist(self.cfg,self.db,2,client)
        self.assertEqual((send.call_count,result['ok'],result['stale']),(1,1,1))
        remaining=self.db.execute('SELECT j.due,w.refresh_hours FROM jobs j JOIN watchlist w USING(store,app_id,country,language) WHERE w.enabled=1').fetchone()
        self.assertAlmostEqual(remaining['due']-before,remaining['refresh_hours']*3600,delta=10)

    def test_pause_while_waiting_prevents_batch_network_send(self):
        self.seed(3);client=Client(self.cfg,self.db)
        limits.save(self.db,self.cfg,{'global_interval_seconds':30})
        limits.admission(self.db,self.cfg,'apple')
        with patch('appmon.stores.time.sleep',side_effect=lambda _:limits.save(self.db,self.cfg,{'paused':True})),patch.object(client.session,'get') as send:
            work(self.cfg,self.db,3,client)
        send.assert_not_called()
        self.assertEqual(self.db.execute('SELECT count(*) n FROM listings').fetchone()['n'],0)

    def test_categories_keep_all_types_and_states_when_list_is_drilled_down(self):
        add_targets(self.db,[dict(store='google',country='us',language='en',keyword=f'word {i}') for i in range(3)])
        self.db.execute('UPDATE keyword_targets SET enabled=0 ORDER BY id LIMIT 1')
        second=self.db.execute('SELECT id FROM keyword_targets ORDER BY id LIMIT 1 OFFSET 1').fetchone()['id']
        self.db.execute('UPDATE keyword_targets SET failures=2 WHERE id=%s',(second,))
        source_states = [
            ('aso_popularity','pending',1,0), ('aso_import','paused',0,0), ('aso_report','done',1,None),
            ('ad_creatives','pending',1,0), ('ad_rankings','paused',0,0), ('ad_apple','done',1,None),
            ('ad_google','pending',1,0), ('ad_meta','paused',0,0), ('ad_tiktok','done',1,None),
            ('ad_reddit','pending',1,0),
        ]
        self.db.executemany('''INSERT INTO collection_source_tasks
            (store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at,enabled,due)
            VALUES('apple','','us','en',%s,%s,'{}',%s,'test',%s,0,%s,%s)''',
            [(kind,kind,'0'*64,kind,enabled,due) for kind,_,enabled,due in source_states if kind not in ad_sources.KINDS])
        market = dict(store='apple',app_id='123',country='de',language='de')
        for kind,_,enabled,due in source_states:
            if kind in ad_sources.KINDS:
                receipt = source_tasks.enqueue(self.db,kind,market,source='app-ads',submitted_by=1)
                self.db.execute('UPDATE collection_source_tasks SET enabled=%s,due=%s WHERE id=%s',
                                (enabled,due,receipt['id']))
        result=list_tasks(self.db,kind='keywords',state='error')
        self.assertEqual(result['total'],1)
        self.assertCountEqual([c['kind'] for c in result['categories']],
            ['details','keywords','chart','reviews','featured','icons','screenshots','permissions',
             *[kind for kind,_,_,_ in source_states]])
        c=next(c for c in result['categories'] if c['kind']=='keywords')
        self.assertEqual((c['total'],c['summary']['paused'],c['summary']['error'],c['summary']['pending']),(3,1,1,1))
        for kind,state,_,_ in source_states:
            with self.subTest(kind=kind):
                category=next(c for c in result['categories'] if c['kind']==kind)
                self.assertEqual(category['total'],1)
                self.assertEqual(category['summary'],
                    {'paused':0,'running':0,'error':0,'pending':0,'scheduled':0,'done':0}|{state:1})
        completed=list_tasks(self.db,kind='aso_report',state='done',store='apple')
        self.assertEqual(completed['total'],1)
        self.assertEqual([(row['resource'],row['kind'],row['task_state']) for row in completed['items']],
            [('sources','aso_report','done')])
        google=list_tasks(self.db,store='google')
        self.assertEqual(google['total'],3)
        self.assertTrue(all(c['total']==0 for c in google['categories'] if c['kind'].startswith('aso_')))
        self.assertEqual(sum(c['total'] for c in list_tasks(self.db,country='th')['categories']),0)
        # A formerly implicit ad receipt can still be enabled on disk during an
        # upgrade. Both the category counts and the drill-down must show it paused.
        legacy = self.db.execute("""INSERT INTO collection_source_tasks
            (store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at,enabled,due)
            SELECT store,app_id,country,language,kind,label,payload,batch_key,'collection-plan','old-plan:0',created_at,1,0
            FROM collection_source_tasks WHERE kind='ad_creatives' AND source='app-ads'""").lastrowid
        paused = list_tasks(self.db,kind='ad_creatives',state='paused')
        self.assertEqual([(r['id'],r['task_state']) for r in paused['items']],[(legacy,'paused')])
        summary = next(c['summary'] for c in paused['categories'] if c['kind']=='ad_creatives')
        self.assertEqual((summary['pending'],summary['paused']),(1,1))

    def test_bulk_interval_and_retry_are_set_based_and_keep_history_and_pause(self):
        add_targets(self.db,[dict(store='google',country='us',language='en',keyword=f'word {i}') for i in range(150)])
        self.db.execute('UPDATE keyword_targets SET failures=1,latest_success_id=123')
        self.db.execute('UPDATE keyword_targets SET enabled=0 ORDER BY id LIMIT 1')
        with patch.object(self.db,'execute',wraps=self.db.execute) as calls:
            result=control_tasks(self.db,{'scope':'filtered','filters':{'kind':'keywords'},'action':'interval','refresh_hours':168})
        self.assertEqual(result['updated'],150)
        self.assertLess(calls.call_count,20)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets WHERE refresh_hours=168 AND latest_success_id=123 AND due>%s',(time.time()+167*3600,)).fetchone()['n'],150)
        result=control_tasks(self.db,{'scope':'filtered','filters':{'kind':'keywords'},'action':'retry'})
        self.assertEqual(result['updated'],149)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets WHERE due=0').fetchone()['n'],149)
        self.assertEqual(list_tasks(self.db,state='paused')['total'],1)

    def test_budget_is_shared_atomic_resets_daily_and_does_not_claim_backlog(self):
        limits.save(self.db,self.cfg,{'daily_request_limit':2})
        with patch('appmon.collection_limits.time.time',return_value=100):
            self.assertEqual(limits.admission(self.db,self.cfg,'apple'),0)
        with patch('appmon.collection_limits.time.time',return_value=110):
            self.assertEqual(limits.admission(self.db,self.cfg,'google'),0)
        with patch('appmon.collection_limits.time.time',return_value=120):
            self.assertEqual(limits.admission(self.db,self.cfg,'apple'),86400-120)
            self.seed(3)
            self.assertEqual(work(self.cfg,self.db,3)['processed'],0)
            self.assertEqual(self.db.execute('SELECT sum(failures) n FROM jobs').fetchone()['n'],0)
        with patch('appmon.collection_limits.time.time',return_value=86401):
            self.assertEqual(limits.admission(self.db,self.cfg,'apple'),0)
        for value in (-1,True,1.5):
            with self.assertRaises(ValueError):limits.save(self.db,self.cfg,{'daily_request_limit':value})

    def test_pool_summary_includes_queue_and_excludes_priority_without_claims(self):
        self.seed(3);enqueue(self.db,['gb'],'en')
        watch(self.db,dict(store='apple',app_id='1000',country='us',language='en'))
        for app_id,country in [('1000','us'),('1001','us'),('1001','gb')]:
            self.db.execute("INSERT INTO listings(store,app_id,country,language,state,checked_at,last_success) VALUES('apple',%s,%s,'en','available',10,10)",(app_id,country))
        pool=pipelines.report(self.db,self.cfg)[0]
        self.assertEqual((pool['total'],pool['summary']['pending'],pool['summary']['out_of_scope']),(5,2,3))
        self.assertEqual(pool['collected'],2)
