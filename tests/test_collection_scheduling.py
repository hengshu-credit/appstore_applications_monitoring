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
