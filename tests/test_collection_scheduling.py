import json,time
from types import SimpleNamespace
from unittest.mock import patch
import pymysql
from appmon import collection_limits,dispatcher,pipelines
from appmon.db import add_apps,enqueue,connect
from appmon.keywords import add_targets
from appmon.monitor import work
from appmon.stores import Client
from test_monitor import OceanBaseTestCase


class CollectionSchedulingTest(OceanBaseTestCase):
    def test_selected_mixed_market_controls_and_dispatch_to_completion(self):
        from fastapi.testclient import TestClient
        from appmon.web import create_app
        from appmon.intelligence import watch
        from appmon.app_permissions import enqueue as permissions, PermissionClient, PermissionResult
        from appmon.keyword_stores import KeywordClient, SearchResult
        from appmon.stores import Result
        from contextlib import ExitStack

        app_id='com.case.focus'
        scope=dict(store='google',country='kz',language='kk')
        watch(self.db,scope|{'app_id':app_id})
        permissions(self.db,[scope|{'app_id':app_id}])
        add_targets(self.db,[scope|{'keyword':app_id},scope|{'keyword':'ordinary'}])
        self.db.executemany("""INSERT INTO app_icons(store,app_id,country,language,requested)
            VALUES('google',%s,%s,%s,1)""",[(app_id,'kz','kk'),(app_id,'kz','en'),(app_id,'us','en'),('com.ordinary','de','de')])
        # Ensure there is ordinary work in a competing queue AND the same queue.
        watch(self.db,dict(store='apple',app_id='100',country='us',language='en'))
        self.db.execute("UPDATE jobs SET failures=1,due=1 WHERE app_id='100'")
        self.db.execute("UPDATE keyword_targets SET due=1 WHERE keyword='ordinary'")
        self.db.executemany("INSERT INTO schedules VALUES(%s,%s)",[('claim:details',4),('claim:keyword_targets',3)])
        api=TestClient(create_app(self.cfg))
        self.login_admin(api)
        path='/api/research/collection/tasks'
        def selected():
            response=api.get(path,params={'q':app_id})
            self.assertEqual(response.status_code,200,response.text)
            return response.json()['items']
        refs=[{'resource':r['resource'],'id':r['id']} for r in selected()]
        self.assertEqual(len(refs),6)
        def control(action,**extra):
            result=api.post(path+'/batch',json=dict(action=action,tasks=refs,**extra))
            self.assertEqual(result.status_code,200,result.text)
            return result.json()
        self.assertEqual(control('pause')['updated'],6)
        self.assertTrue(all(r['task_state']=='paused' for r in selected()))
        self.assertEqual(control('interval',refresh_hours=12)['updated'],6)
        self.assertTrue(all(r['refresh_hours']==12 and not r['enabled'] and
                            abs(r['due']-time.time()-12*3600)<10 for r in selected()))
        self.assertEqual(control('resume')['updated'],6)
        self.assertTrue(all(r['task_state']=='scheduled' for r in selected()))
        collection_limits.save(self.db,self.cfg,{'paused':True})
        self.assertEqual(control('refresh')['waiting_for_resume'],6)
        self.assertTrue(all(r['due']<0 for r in selected()))
        self.assertIsNone(dispatcher.choose(self.db,self.cfg))
        collection_limits.save(self.db,self.cfg,{'paused':False})
        # Use real dispatcher, claims, saves and leases; replace only source responses.
        def details(rows):
            self.assertTrue(all(r['app_id']==app_id for r in rows))
            return [Result('ok',{'name':app_id,'icon':'https://is1.mzstatic.com/focus.png'}) for _ in rows]
        cfg=self.cfg|{'google_rpc_batch_size':1}
        with ExitStack() as mocks:
            mocks.enter_context(patch.object(Client,'fetch_many',side_effect=details))
            mocks.enter_context(patch.object(KeywordClient,'search_many',return_value=[SearchResult([],b'','test')]))
            mocks.enter_context(patch.object(PermissionClient,'collect_many',return_value=[PermissionResult({},b'')]))
            mocks.enter_context(patch('appmon.icon_batches.download',side_effect=lambda *a,**k:dict(
                fetched_at=time.time(),file_path='fixture',mime='image/png')))
            for _ in range(10):
                if all(r['success_at'] for r in selected()):break
                result=dispatcher.dispatch_once(self.db,cfg,'manual-test')
                self.assertGreater(result['ok']+result.get('prepared',0),0,result)
        self.assertTrue(all(r['task_state']=='scheduled' and r['success_at'] for r in selected()))
        self.assertEqual(self.db.execute("SELECT due FROM keyword_targets WHERE keyword='ordinary'").fetchone()['due'],1)
        self.assertEqual(self.db.execute("SELECT due FROM jobs WHERE app_id='100'").fetchone()['due'],1)
        self.assertIsNone(self.db.execute("SELECT checked_at FROM app_icons WHERE app_id='com.ordinary'").fetchone()['checked_at'])

    def test_selected_retry_only_queues_eligible_failures_and_preserves_live_leases(self):
        from appmon.collection_tasks import control_tasks
        from appmon.target_batches import claim_targets
        ids=add_targets(self.db,[dict(store='google',country='us',language='en',keyword=f'word{i}') for i in range(5)])['ids']
        self.db.execute('UPDATE keyword_targets SET due=123,failures=2')
        self.db.execute('UPDATE keyword_targets SET failures=0 WHERE id=%s',(ids[1],))
        self.db.execute('UPDATE keyword_targets SET enabled=0 WHERE id=%s',(ids[2],))
        self.db.execute("UPDATE keyword_targets SET token='active',lease_until=%s WHERE id=%s",(time.time()+500,ids[3]))
        result=control_tasks(self.db,dict(action='retry',tasks=[dict(resource='keywords',id=i) for i in ids[:4]]))
        self.assertEqual((result['updated'],result['running'],result['skipped']),(1,1,2))
        self.assertEqual([r['id'] for r in claim_targets(self.db,'keyword_targets',600,1)],[ids[0]])
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets WHERE due=123').fetchone()['n'],4)
        self.assertEqual(self.db.execute('SELECT token FROM keyword_targets WHERE id=%s',(ids[3],)).fetchone()['token'],'active')

    def test_filtered_retry_requires_country_and_language_on_the_same_media_reference(self):
        from appmon import collection_tasks
        assets=[]
        for label in ['matches','other']:
            cursor=self.db.execute("""INSERT INTO app_media_assets(store,url_hash,source_url,label,due,failures)
                VALUES('google',%s,'url',%s,123,1)""",(label,label))
            assets.append(cursor.lastrowid)
        self.db.executemany("""INSERT INTO app_media_refs(store,app_id,country,language,kind,position,asset_id)
            VALUES('google',%s,%s,%s,'screenshots',0,%s)""",[
            ('one','kz','en',assets[0]),('two','kz','kk',assets[1]),('three','us','en',assets[1])])
        filters=dict(kind='screenshots',country='kz',language='en',state='error')
        rows=collection_tasks.list_tasks(self.db,**filters)['items']
        self.assertEqual([r['id'] for r in rows],assets[:1])
        result=collection_tasks.control_tasks(self.db,dict(scope='filtered',action='retry',filters=filters))
        self.assertEqual(result['updated'],1)
        self.assertEqual([r['due'] for r in self.db.execute('SELECT due FROM app_media_assets ORDER BY id').fetchall()],[0,123])

    def test_manual_keyword_refresh_precedes_import_backlog_and_preserves_active_lease(self):
        from appmon.collection_tasks import control_tasks
        from appmon.target_batches import claim_targets
        spec=dict(store='google',country='us',language='en')
        ids=add_targets(self.db,[spec|{'keyword':f'word{i}'} for i in range(25)])['ids']
        selected=ids[-1]
        ref={'resource':'keywords','id':selected}
        control_tasks(self.db,{'action':'refresh','tasks':[ref]})
        rows=claim_targets(self.db,'keyword_targets',600,1)
        self.assertEqual([r['id'] for r in rows],[selected])
        before=self.db.execute('SELECT * FROM keyword_targets WHERE id=%s',(selected,)).fetchone()
        control_tasks(self.db,{'action':'refresh','tasks':[ref]})
        self.assertEqual(self.db.execute('SELECT * FROM keyword_targets WHERE id=%s',(selected,)).fetchone(),before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_targets WHERE due=0').fetchone()['n'],24)

    def test_queue_pages_past_locked_prefix_and_returns_complete_leased_rows(self):
        from appmon.db import claim_details
        add_apps(self.db,[('apple',str(i),None,None) for i in range(1000,1080)],'fixture')
        enqueue(self.db,['us'],'en')
        holder=connect(self.cfg,initialize=False)
        try:
            holder.begin()
            holder.execute("SELECT app_id FROM jobs WHERE app_id<='1069' FOR UPDATE").fetchall()
            rows=claim_details(self.db,['us'],'en',600,limit=5)
            self.assertEqual([r['app_id'] for r in rows],['1070','1071','1072','1073','1074'])
            self.assertTrue(all(r['failures']==0 and r['last_error'] is None and r['token'] for r in rows))
            self.assertEqual(len({r['token'] for r in rows}),1)
        finally:holder.rollback();holder.close()

    def test_queue_index_upgrade_is_idempotent_and_preserves_jobs(self):
        add_apps(self.db,[('apple','1000',None,None)],'fixture');enqueue(self.db,['us'],'en')
        before=self.db.execute('SELECT * FROM jobs').fetchall()
        self.db.execute('CREATE INDEX jobs_due ON jobs(due,lease_until)')
        self.db.execute('DROP INDEX jobs_dispatch ON jobs')
        timeout=self.db.execute('SELECT @@ob_query_timeout value').fetchone()['value']
        for _ in range(2):
            upgraded=connect(self.cfg,initialize=True)
            self.assertEqual(upgraded.execute('SELECT @@ob_query_timeout value').fetchone()['value'],timeout)
            upgraded.close()
        indexes=self.db.execute('SHOW KEYS FROM jobs').fetchall()
        self.assertEqual([r['Column_name'] for r in indexes if r['Key_name']=='jobs_dispatch'],
                         ['due','store','app_id','country','language','lease_until'])
        self.assertFalse(any(r['Key_name']=='jobs_due' for r in indexes))
        self.assertEqual(self.db.execute('SELECT * FROM jobs').fetchall(),before)

    def test_batch_locks_exact_cross_store_identities_and_rolls_back_together(self):
        from appmon.collection_batch import persist_batch,revision
        add_apps(self.db,[(store,appid,None,None) for store in ('apple','google') for appid in ('1000','2000')],'fixture')
        observer=connect(self.cfg,initialize=False)
        keys=[('google','2000'),('apple','1000'),('apple','1000')]
        def save(writer):
            with observer:
                available=observer.execute('SELECT store,app_id FROM apps ORDER BY store,app_id FOR UPDATE SKIP LOCKED').fetchall()
                self.assertEqual([(r['store'],r['app_id']) for r in available],[('apple','2000'),('google','1000')])
            for store,appid in set(keys):
                writer.execute('UPDATE apps SET name=%s WHERE store=%s AND app_id=%s',('updated',store,appid))
        before=revision(self.db)
        try:
            def fail(writer):
                save(writer)
                raise RuntimeError('reject entire batch')
            with self.assertRaisesRegex(RuntimeError,'reject entire batch'):persist_batch(self.db,fail,keys)
            self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM apps WHERE name IS NOT NULL').fetchone()['n'],0)
            self.assertEqual(revision(self.db),before)
            persist_batch(self.db,save,keys)
            self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM apps WHERE name='updated'").fetchone()['n'],2)
            self.assertEqual(revision(self.db),before+1)
        finally:observer.close()

    def test_focus_lanes_get_priority_without_starving_or_idling_the_pool(self):
        add_apps(self.db,[('apple','1000',None,None)],'fixture');enqueue(self.db,['us'],'en')
        pipelines.save(self.db,self.cfg,'pool',{'enabled':True})
        add_targets(self.db,[dict(store='google',country='us',language='en',keyword='focus')])
        cfg=self.cfg|{'collection_priority_mode':'focus'}
        self.assertEqual([dispatcher.choose(self.db,cfg) for _ in range(10)],['keywords']*9+['pool'])
        self.db.execute('UPDATE keyword_targets SET enabled=0')
        self.assertEqual(dispatcher.choose(self.db,cfg),'pool')

    def test_request_slots_are_fair_and_a_cooling_store_does_not_block_the_other(self):
        self.db.execute("INSERT INTO schedules VALUES('rate:global',110)")
        with patch('appmon.collection_limits.time.time',return_value=100):
            self.assertEqual(collection_limits.admission(self.db,self.cfg,'apple',ticket='a'),10)
            self.assertEqual(collection_limits.admission(self.db,self.cfg,'google',ticket='b'),10)
        with patch('appmon.collection_limits.time.time',return_value=110):
            self.assertGreater(collection_limits.admission(self.db,self.cfg,'google',ticket='b'),0)
            self.assertEqual(collection_limits.admission(self.db,self.cfg,'apple',ticket='a'),0)
        with patch('appmon.collection_limits.time.time',return_value=115):
            self.assertEqual(collection_limits.admission(self.db,self.cfg,'google',ticket='b'),0)
        self.db.execute("UPDATE schedules SET next_at=200 WHERE name='rate:apple'")
        with patch('appmon.collection_limits.time.time',return_value=120):
            self.assertGreater(collection_limits.admission(self.db,self.cfg,'apple',ticket='c'),0)
            self.assertEqual(collection_limits.admission(self.db,self.cfg,'google',ticket='d'),0)
        collection_limits.withdraw(self.db,'c')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_request_waiters').fetchone()['n'],0)

    def test_deadlock_retries_only_the_transaction_without_double_counting_or_refetching(self):
        add_apps(self.db,[('apple',str(i),None,None) for i in range(1000,1003)],'fixture');enqueue(self.db,['us'],'en')
        client=Client(self.cfg,self.db)
        payload={'resultCount':3,'results':[dict(trackId=i,wrapperType='software',trackName=str(i),description='complete') for i in range(1000,1003)]}
        response=SimpleNamespace(status_code=200,json=lambda:payload,content=json.dumps(payload).encode())
        execute=self.db.execute;failed=[]
        def conflict(sql,args=None):
            if not failed and sql.startswith('INSERT INTO listings') and args[1]=='1001':
                failed.append(True);raise pymysql.OperationalError(1213,'fixture deadlock')
            return execute(sql,args)
        try:
            with patch.object(client,'get',return_value=response) as remote,patch.object(self.db,'execute',side_effect=conflict):
                result=work(self.cfg,self.db,3,client)
            remote.assert_called_once();self.assertEqual(failed,[True]);self.assertEqual((result['processed'],result['ok']),(3,3))
            for table in ('requests','listings','snapshots'):
                self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM '+table).fetchone()['n'],3)
        finally:client.session.close()

    def test_large_response_commits_bounded_chunks_and_retries_only_uncommitted_chunk(self):
        add_apps(self.db,[('apple',str(i),None,None) for i in range(1000,1210)],'fixture');enqueue(self.db,['us'],'en')
        cfg=self.cfg|{'apple_lookup_batch_size':210};client=Client(cfg,self.db);observer=connect(cfg,initialize=False)
        data={'resultCount':210,'results':[dict(trackId=i,wrapperType='software',trackName=str(i)) for i in range(1000,1210)]}
        response=SimpleNamespace(status_code=200,json=lambda:data,content=json.dumps(data).encode())
        execute=self.db.execute;committed=[]
        def conflict(sql,args=None):
            if not committed and sql.startswith('INSERT INTO listings') and args[1]=='1150':
                committed.append(observer.execute('SELECT COUNT(*) n FROM requests').fetchone()['n'])
                raise pymysql.OperationalError(1213,'fixture second-chunk conflict')
            return execute(sql,args)
        try:
            with patch.object(client,'get',return_value=response) as remote,patch.object(self.db,'execute',side_effect=conflict):result=work(cfg,self.db,210,client)
            remote.assert_called_once();self.assertEqual(committed,[100]);self.assertEqual((result['processed'],result['ok']),(210,210))
            self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM snapshots').fetchone()['n'],210)
        finally:client.session.close();observer.close()
