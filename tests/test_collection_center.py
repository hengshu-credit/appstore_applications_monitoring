import time
from unittest.mock import patch
import pymysql
from appmon import bulk_controls
from appmon.collection import preview_plan, create_plan, work_plans
from appmon.collection_tasks import list_tasks, edit_task, control_tasks
from appmon import pipelines
from appmon.db import claim, connect
from appmon.intelligence import watch, add_observation_target, work_watchlist
from appmon.keywords import add_targets
from appmon.monitor import save_result, work
from appmon.stores import Result
from test_monitor import OceanBaseTestCase


class CollectionCenterTest(OceanBaseTestCase):
    def test_card_success_coverage_is_separate_from_status_and_survives_state_drilldown(self):
        self.seed()
        self.db.execute("UPDATE keyword_targets SET latest_success_id=99,failures=2,last_error='HTTP 429'")
        add_targets(self.db,[dict(store='google',country='th',language='th',keyword='pending')])
        result=list_tasks(self.db,kind='keywords',state='error')
        card=next(c for c in result['categories'] if c['kind']=='keywords')
        self.assertEqual((result['total'],card['total'],card['collected']),(1,2,1))
        self.assertEqual(card['summary']['pending'],1)
        self.db.execute("UPDATE keyword_targets SET enabled=0 WHERE latest_success_id=99")
        card=next(c for c in list_tasks(self.db,kind='keywords')['categories'] if c['kind']=='keywords')
        self.assertEqual((card['collected'],card['summary']['paused']),(1,1))
        self.assertLess(abs(result['observed_at']-time.time()),10)

    def test_cross_page_control_snapshots_filter_and_handles_all_resources(self):
        self.seed()
        add_targets(self.db,[dict(store='apple',country='us',language='en',keyword=f'word {i}') for i in range(125)])
        result=control_tasks(self.db,{'scope':'filtered','filters':{'store':'apple','state':'pending'},'action':'pause'})
        self.assertEqual(result['updated'],125)
        self.assertEqual(list_tasks(self.db,state='paused')['total'],125)
        self.assertEqual(list_tasks(self.db,store='google',state='pending')['total'],3)
        self.assertEqual(control_tasks(self.db,{'scope':'all','action':'resume'})['updated'],128)
        self.assertEqual(list_tasks(self.db,state='paused')['total'],0)
        for value in ({'scope':'all','action':'refresh'}, {'scope':'filtered','action':'pause','filters':{'state':'typo'}},
                      {'scope':'all','action':'pause','filters':{'store':'apple'}}, {'scope':'unknown','action':'pause'}):
            with self.assertRaises(ValueError):control_tasks(self.db,value)

    def seed(self):
        m=dict(store='google',country='th',language='th')
        watch(self.db,m|{'app_id':'com.example.app'})
        add_targets(self.db,[m|{'keyword':'เงินกู้'}])
        add_observation_target(self.db,m|{'kind':'reviews','app_id':'com.example.app'})
        return m

    def test_task_search_finds_review_and_detail_tasks_by_application_name(self):
        self.seed()
        self.db.execute("UPDATE apps SET name='Быстрые займы' WHERE app_id='com.example.app'")
        for q in ('займ','EXAMPLE'):
            result=list_tasks(self.db,q=q)
            self.assertEqual(result['total'],2)
            self.assertEqual({r['kind'] for r in result['items']},{'reviews','details'})

    def test_background_pause_snapshots_filter_and_resumes_after_chunk_failure(self):
        self.seed()
        add_targets(self.db,[dict(store='apple',country='us',language='en',keyword=f'match {i}') for i in range(125)])
        request = dict(action='pause',scope='filtered',background=True,
                       filters=dict(store='apple',kind='keywords',q='match',state='pending'))
        op = control_tasks(self.db,request)
        with patch.object(bulk_controls,'CHUNK_SIZE',50):
            self.assertTrue(bulk_controls.step(self.db))  # Capture all three pages.
        self.db.execute("UPDATE keyword_targets SET due=%s WHERE store='apple'",(time.time()+3600,))
        add_targets(self.db,[dict(store='apple',country='us',language='en',keyword='match later')])
        self.assertTrue(bulk_controls.step(self.db))
        self.assertEqual(bulk_controls.recent(self.db)[0]['completed'],49)  # First page also held the Google keyword.
        # Fail after task updates but before commit. Logs and progress must roll back together.
        execute = self.db.execute
        def interrupt(sql,args=None):
            if sql.startswith('DELETE FROM collection_bulk_chunks'):
                raise pymysql.OperationalError(1213,'simulated deadlock')
            return execute(sql,args)
        with patch.object(self.db,'execute',side_effect=interrupt), self.assertLogs('appmon.bulk_controls',level='ERROR'):
            for _ in range(3):
                self.assertFalse(bulk_controls.step(self.db))
        self.assertEqual(bulk_controls.recent(self.db)[0]['status'],'error')
        self.assertEqual(list_tasks(self.db,state='paused')['total'],49)
        other = connect(self.cfg)
        try:
            bulk_controls.retry(other,op['id'])
            while bulk_controls.step(other): pass
        finally:
            other.close()
        result = bulk_controls.recent(self.db)[0]
        self.assertEqual((result['status'],result['completed'],result['total']),('complete',125,125))
        self.assertEqual(list_tasks(self.db,state='paused')['total'],125)
        self.assertEqual(list_tasks(self.db,state='pending')['total'],4)
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM collection_logs WHERE event='pause'").fetchone()['n'],125)

    def test_background_all_controls_every_configured_resource_and_leases(self):
        self.seed()
        self.db.execute("INSERT INTO app_permission_targets(store,app_id,country) VALUES('google','com.example.app','th')")
        self.db.execute("""INSERT INTO app_icons(store,app_id,country,language,requested) VALUES
            ('apple','123','us','en',1),('apple','456','us','en',0)""")
        self.db.execute("""INSERT INTO collection_source_tasks(store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at)
            VALUES('apple','','us','en','aso_import','test','{}','key','test','1',0)""")
        self.db.execute("INSERT INTO app_media_assets(store,url_hash,source_url,label) VALUES('apple','hash','url','test')")
        self.db.execute("UPDATE jobs SET token='old',lease_until=%s",(time.time()+600,))
        for action,state in [('pause','paused'),('resume','pending')]:
            control_tasks(self.db,dict(scope='all',action=action,background=True))
            while bulk_controls.step(self.db): pass
            self.assertEqual(list_tasks(self.db,state=state)['total'],7)
            self.assertEqual(self.db.execute('SELECT manual_paused FROM app_media_assets').fetchone()['manual_paused'],action=='pause')
            self.assertEqual(self.db.execute('SELECT token,lease_until FROM jobs').fetchone(),dict(token=None,lease_until=0))
            self.assertEqual(self.db.execute('SELECT enabled FROM app_icons WHERE requested=0').fetchone()['enabled'],1)
            self.assertTrue(pipelines.settings(self.db,self.cfg,'pool')['enabled'])

    def test_registry_filters_paginates_and_distinguishes_paused_and_failed(self):
        self.seed()
        self.assertEqual(list_tasks(self.db,limit=2)['total'],3)
        self.assertTrue(list_tasks(self.db,limit=2)['has_more'])
        self.assertEqual(len(list_tasks(self.db,limit=2,page=2)['items']),1)
        self.assertEqual(list_tasks(self.db,kind='keywords',q='เงิน')['total'],1)
        self.assertEqual(list_tasks(self.db,country='vn')['total'],0)
        self.db.execute("UPDATE keyword_targets SET failures=2,last_error='HTTP 429'")
        row=list_tasks(self.db,state='error')['items'][0]
        control_tasks(self.db,{'action':'pause','tasks':[{'resource':row['resource'],'id':row['id']}]})
        self.assertEqual(list_tasks(self.db,state='error')['total'],0)
        self.assertEqual(list_tasks(self.db,state='paused')['total'],1)

    def test_edit_preserves_identity_history_and_pause(self):
        self.seed();row=list_tasks(self.db,kind='keywords')['items'][0]
        self.db.execute("UPDATE keyword_targets SET enabled=0,latest_success_id=99,token='old',lease_until=%s",(time.time()+60,))
        edit_task(self.db,'keywords',row['id'],{'refresh_hours':6,'depth':25})
        saved=self.db.execute('SELECT * FROM keyword_targets').fetchone()
        self.assertEqual((saved['keyword'],saved['enabled'],saved['latest_success_id']),('เงินกู้',0,99))
        self.assertEqual((saved['refresh_hours'],saved['depth'],saved['token']),(6,25,None))
        with self.assertRaises(ValueError):edit_task(self.db,'keywords',row['id'],{'refresh_hours':6,'depth':201})

    def test_batch_is_atomic_and_paused_tasks_cannot_refresh(self):
        self.seed();row=list_tasks(self.db,kind='keywords')['items'][0]
        ref={'resource':'keywords','id':row['id']}
        with self.assertRaises(ValueError):control_tasks(self.db,{'action':'pause','tasks':[ref,{'resource':'watchlist','id':999}]})
        self.assertEqual(self.db.execute('SELECT enabled FROM keyword_targets').fetchone()['enabled'],1)
        control_tasks(self.db,{'action':'pause','tasks':[ref]})
        with self.assertRaises(ValueError):control_tasks(self.db,{'action':'refresh','tasks':[ref]})
        control_tasks(self.db,{'action':'resume','tasks':[ref]})
        control_tasks(self.db,{'action':'refresh','tasks':[ref]})
        self.assertLess(self.db.execute('SELECT due FROM keyword_targets').fetchone()['due'],0)

    def test_full_pool_worker_cannot_bypass_watchlist_pause_and_stale_result_rejected(self):
        m=self.seed();row=list_tasks(self.db,kind='details')['items'][0]
        self.assertIsNone(claim(self.db,['th'],'th',60))
        # In-flight priority worker result must be discarded after pause.
        self.db.execute("UPDATE jobs SET token='claimed',lease_until=%s",(time.time()+60,))
        control_tasks(self.db,{'action':'pause','tasks':[{'resource':'watchlist','id':row['id']}]})
        self.assertFalse(save_result(self.db,self.cfg,m|{'app_id':'com.example.app','token':'claimed'},Result('ok',{'name':'stale'})))
        self.assertIsNone(claim(self.db,['th'],'th',60))
        self.assertEqual(work_watchlist(self.cfg,self.db,1)['processed'],0)

    def test_plan_depth_and_chart_collection_are_applied(self):
        value=dict(store='apple',country='th',language='th',keywords=['loan'],app_ids=['123'],details=True,
                   reviews=True,chart=True,featured=True,collection='top-paid',keyword_depth=25,depth=150,refresh_hours=6)
        self.assertEqual(preview_plan(value)['total'],5)
        create_plan(self.db,value);self.assertEqual(work_plans(self.db)['status'],'complete')
        self.assertEqual(self.db.execute('SELECT depth FROM keyword_targets').fetchone()['depth'],25)
        row=self.db.execute("SELECT * FROM observation_targets WHERE kind='chart'").fetchone()
        self.assertEqual((row['collection'],row['depth'],row['refresh_hours']),('top-paid',150,6))

    def test_pipeline_settings_are_shared_validated_and_gate_running_worker(self):
        other=connect(self.cfg)
        try:
            pipelines.save(self.db,self.cfg,'pool',{'enabled':False,'refresh_hours':6,'countries':['TH','vn'],'language':'vi'})
            enabled,cfg=pipelines.configured(other,self.cfg,'pool')
            self.assertFalse(enabled);self.assertEqual(cfg['countries'],['th','vn'])
            self.assertEqual(work(cfg,other,5,managed=True)['processed'],0)
            with self.assertRaises(ValueError):pipelines.save(self.db,self.cfg,'pool',{'countries':['bad']})
            self.assertFalse(pipelines.settings(other,self.cfg,'pool')['enabled'])
        finally:other.close()
