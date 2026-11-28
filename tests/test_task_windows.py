from unittest.mock import patch

from appmon import collection, task_windows
from appmon.app_collection import report
from appmon.collection_tasks import control_tasks
from appmon.keywords import add_targets, claim_target
from test_monitor import OceanBaseTestCase


class TaskWindowTest(OceanBaseTestCase):
    market = dict(store='google', country='us', language='en')

    def words(self, *names):
        values = [self.market | dict(keyword=name, refresh_hours=1) for name in names]
        return add_targets(self.db, values)['ids']

    def policy(self, ids, minutes, at=1000):
        refs = [{'resource':'keywords','id':i} for i in ids]
        with patch('time.time', return_value=at):
            control_tasks(self.db, dict(action='validity',tasks=refs,validity_minutes=minutes))

    def due(self, identifier):
        return self.db.execute('SELECT due FROM keyword_targets WHERE id=%s',(identifier,)).fetchone()['due']

    def test_cutoff_skips_request_then_promotes_once_at_next_cycle(self):
        first, second, ordinary = self.words('first','second','ordinary')
        for identifier, due in [(first,900),(second,950)]:
            self.db.execute('UPDATE keyword_targets SET due=%s WHERE id=%s',(due,identifier))
        self.policy([first,second],10)
        self.assertIsNone(claim_target(self.db,60,1600,first))
        self.assertEqual(self.due(first),4600)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_runs').fetchone()['n'],0)
        task_windows.advance(self.db,1601)
        self.assertIsNone(claim_target(self.db,60,4599,first))
        task_windows.advance(self.db,4600)
        self.assertLess(self.due(first),self.due(second))
        self.assertLess(self.due(second),self.due(ordinary))
        slot = self.due(first)
        task_windows.advance(self.db,4600+3600*4)
        self.assertEqual(self.due(first),slot)
        self.assertLess(self.due(first),self.due(second))
        self.assertEqual(claim_target(self.db,60,4600+3600*4)['id'],first)

    def test_filtered_window_rollover_and_live_claim(self):
        first, second = self.words('first','second')
        with patch('time.time', return_value=1000):
            control_tasks(self.db,dict(action='validity',scope='filtered',filters={'kind':'keywords'},validity_minutes=0))
        token = claim_target(self.db,7200,1001,first)['token']
        task_windows.advance(self.db,4600)
        self.assertEqual(self.db.execute('SELECT token FROM keyword_targets WHERE id=%s',(first,)).fetchone()['token'],token)
        self.assertLess(self.due(second),0)
        self.assertEqual(claim_target(self.db,60,4600)['id'],second)
        with patch('time.time', return_value=4600):
            control_tasks(self.db,dict(action='validity',scope='all',validity_minutes=None))
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_task_windows WHERE validity_minutes>=0').fetchone()['n'],0)

    def test_replacement_keeps_position_and_latest_parameters(self):
        first, second = self.words('first','second')
        self.db.execute('UPDATE keyword_targets SET due=100 WHERE id=%s',(first,))
        self.db.execute('UPDATE keyword_targets SET due=101 WHERE id=%s',(second,))
        task = self.market | dict(kind='keywords',keyword='first',depth=80)
        enqueue = lambda key, value: collection.enqueue(self.db, dict(source='timer',request_id=key,tasks=[value]))
        old = enqueue('older',task)
        self.assertEqual(collection.work_plans(self.db)['error'],0)
        latest = enqueue('latest',task | dict(depth=12))
        self.assertEqual(collection.work_plans(self.db)['error'],0)
        self.assertEqual(report(self.db,old['id'],True)['state'],'expired')
        self.assertEqual(enqueue('older',task)['status'],'expired')
        self.assertEqual(report(self.db,latest['id'],True)['state'],'running')
        self.assertEqual(self.due(first),100)
        claimed = claim_target(self.db,60)
        self.assertEqual((claimed['id'],claimed['depth']),(first,12))

    def test_older_deferred_plan_cannot_replace_page_submission(self):
        from appmon.collection_batch import PAGE_DUE
        task = self.market | dict(kind='keywords',keyword='shared',depth=80)
        old = collection.enqueue(self.db,dict(source='timer',request_id='1',tasks=[task]))
        target = add_targets(self.db,[task | dict(depth=14)],initial_due=PAGE_DUE)['ids'][0]
        self.assertEqual(collection.work_plans(self.db)['error'],0)
        self.assertEqual(report(self.db,old['id'],True)['state'],'expired')
        self.assertEqual(claim_target(self.db,60,target_id=target)['depth'],14)

    def test_only_newest_receipt_is_pending_and_existing_page_order_is_retained(self):
        from appmon import source_tasks
        from appmon.collection_batch import PAGE_DUE
        spec=dict(store='apple',country='us',language='en',genre='FINANCE',period='weekly',start='2025-01-05')
        first=source_tasks.enqueue(self.db,'aso_popularity',spec,due=PAGE_DUE)
        second=source_tasks.enqueue(self.db,'aso_popularity',spec|{'limit':17},due=PAGE_DUE)
        current=source_tasks.enqueue(self.db,'aso_popularity',spec,due=0)
        self.assertEqual(current['id'],first['id'])
        self.assertNotEqual(current['request_id'],first['request_id'])
        self.assertEqual(source_tasks.receipt(self.db,first['id'],first['source'],first['request_id'])['status'],'expired')
        self.assertEqual(source_tasks.receipt(self.db,current['id'],current['source'],current['request_id'])['status'],'pending')
        queued=self.db.execute('SELECT id FROM collection_source_tasks WHERE due IS NOT NULL ORDER BY due,id').fetchall()
        self.assertEqual([r['id'] for r in queued],[first['id'],second['id']])
        # A finished result retained on an explicitly requeued receipt is
        # history. Only an unfinished pagination cursor protects active work.
        self.db.execute("UPDATE collection_source_tasks SET success_at=1,result='{}' WHERE id=%s",(first['id'],))
        again=source_tasks.enqueue(self.db,'aso_popularity',spec,due=PAGE_DUE)
        self.assertEqual(again['id'],current['id'])
        self.assertEqual(source_tasks.receipt(self.db,current['id'],current['source'],current['request_id'])['status'],'expired')

    def test_filtered_chart_deadline_matches_individual_configuration(self):
        from appmon.charts import daily_slot
        from appmon.intelligence import add_observation_target
        at=1728000000
        chart=self.market | dict(kind='chart',collection='top-free',refresh_hours=24)
        add_observation_target(self.db,chart)
        with patch('time.time', return_value=at):
            control_tasks(self.db,dict(action='validity',scope='filtered',filters={'kind':'chart'},validity_minutes=0))
        expected=daily_slot('google','us','top-free',at)+86400
        w=self.db.execute('SELECT next_round,check_at FROM collection_task_windows').fetchone()
        self.assertEqual(w,dict(next_round=expected,check_at=expected))

    def test_latest_plan_receipt_survives_delayed_overlapping_plan(self):
        task=self.market | dict(kind='keywords',keyword='shared',depth=14)
        first=collection.enqueue(self.db,dict(tasks=[task],source='one',request_id='1'))
        collection.work_plans(self.db)
        delayed=collection.enqueue(self.db,dict(tasks=[task|{'depth':80}],source='two',request_id='2'))
        current=collection.enqueue(self.db,dict(tasks=[task],source='one',request_id='3'))
        self.assertEqual(current['id'],first['id'])
        self.assertEqual(collection.enqueue(self.db,dict(tasks=[task],source='one',request_id='1'))['status'],'expired')
        self.assertEqual(collection.work_plans(self.db)['error'],0)
        self.assertEqual(report(self.db,delayed['id'],True)['state'],'expired')
        self.assertEqual(claim_target(self.db,60)['depth'],14)

    def test_deleted_target_does_not_block_later_deadlines(self):
        first,second=self.words('removed','waiting')
        self.policy([first,second],0)
        self.db.execute('DELETE FROM keyword_targets WHERE id=%s',(first,))
        task_windows.advance(self.db,4600,limit=1)
        task_windows.advance(self.db,4600,limit=1)
        self.assertLess(self.due(second),0)
        self.assertEqual(self.db.execute('SELECT target_id FROM collection_task_windows').fetchall(),[{'target_id':second}])

    def test_default_enrollment_preserves_explicit_opt_out_and_resumes_by_id(self):
        first,second,third=self.words('first','disabled','third')
        self.policy([second],None)
        task_windows.enroll_defaults(self.db,1000,limit=2)
        task_windows.enroll_defaults(self.db,1005,limit=2)
        task_windows.advance(self.db,4605)
        self.assertLess(self.due(first),0)
        self.assertEqual(self.due(second),0)
        self.assertLess(self.due(third),0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM collection_task_windows').fetchone()['n'],3)

    def test_unexpanded_replacement_precedes_unrelated_plan_and_keeps_old_receipt(self):
        task=self.market | dict(kind='keywords',keyword='first',depth=80)
        def submit(key,value):
            return collection.enqueue(self.db,dict(source='timer',request_id=key,tasks=[value]))
        first=submit('v1',task)
        later=submit('other',task|{'keyword':'second'})
        latest=submit('v2',task|{'depth':12})
        self.assertEqual(first['id'],latest['id'])
        self.assertEqual(submit('v1',task)['status'],'expired')
        self.assertEqual(collection.work_plans(self.db)['id'],latest['id'])
        self.assertEqual(collection.work_plans(self.db)['id'],later['id'])
        self.assertEqual(claim_target(self.db,60)['depth'],12)
