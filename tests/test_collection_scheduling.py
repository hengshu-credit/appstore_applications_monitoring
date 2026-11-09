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
