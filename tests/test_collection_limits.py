from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace
from appmon import collection_limits as limits
from appmon.db import connect
from appmon.stores import Client, FetchError
from test_monitor import MySQLTestCase


class CollectionLimitsTest(MySQLTestCase):
    def test_waiting_request_is_cancelled_when_task_pauses(self):
        from appmon.keywords import add_targets
        from appmon.task_logs import execution
        from appmon.collection_tasks import control_tasks
        add_targets(self.db,[dict(store='google',country='us',language='en',keyword='test')])
        identifier=self.db.execute('SELECT id FROM keyword_targets').fetchone()['id']
        client=Client(self.cfg,self.db)
        limits.admission(self.db,self.cfg,'google')
        def pause(_):
            control_tasks(self.db,{'action':'pause','tasks':[dict(resource='keywords',id=identifier)]})
        with execution(self.db,client,'keywords',identifier,'test'):
            with patch('appmon.stores.time.sleep',side_effect=pause),patch.object(client.session,'get') as send:
                with self.assertRaises(FetchError):client.get('google','https://example.com')
                send.assert_not_called()
        self.assertIsNone(client.request_guard)

    def test_parallel_workers_share_both_store_budget_and_live_settings(self):
        limits.save(self.db,self.cfg,{'global_interval_seconds':10})
        def attempt(store):
            db=connect(self.cfg,initialize=False)
            try: return limits.admission(db,self.cfg,store)
            finally: db.close()
        with patch('appmon.collection_limits.time.time',return_value=100):
            with ThreadPoolExecutor(max_workers=4) as pool:
                result=list(pool.map(attempt,['google','apple','google','apple']))
        self.assertEqual(sorted(result),[0,10,10,10])
        limits.save(self.db,self.cfg,{'paused':True})
        self.assertIsNone(attempt('apple'))
        for value in (0,float('nan'),float('inf'),True,90000):
            with self.assertRaises(ValueError):limits.save(self.db,self.cfg,{'global_interval_seconds':value})

    def test_cooldown_rechecks_waiters_without_prebooking_slots(self):
        with patch('appmon.collection_limits.time.time',return_value=100):
            self.assertEqual(limits.admission(self.db,self.cfg,'google'),0)
            self.assertGreater(limits.admission(self.db,self.cfg,'apple'),0)
            self.assertEqual(limits.cooldown(self.db,self.cfg,'google',1800),1800)
        with patch('appmon.collection_limits.time.time',return_value=106):
            self.assertEqual(limits.admission(self.db,self.cfg,'apple'),1794)
        with patch('appmon.collection_limits.time.time',return_value=1900):
            self.assertEqual(limits.admission(self.db,self.cfg,'apple'),0)

    def test_paused_client_sends_no_request_and_429_cools_other_store(self):
        client=Client(self.cfg,self.db)
        limits.save(self.db,self.cfg,{'paused':True})
        with patch.object(client.session,'get') as send:
            with self.assertRaises(FetchError):client.get('google','https://example.com')
            send.assert_not_called()
        limits.save(self.db,self.cfg,{'paused':False})
        with patch.object(client.session,'get',return_value=SimpleNamespace(status_code=429,headers={'Retry-After':'1800'})):
            with self.assertRaises(FetchError) as caught:client.get('google','https://example.com')
        self.assertEqual(caught.exception.retry_after,1800)
        self.assertGreater(limits.admission(self.db,self.cfg,'apple'),1790)
