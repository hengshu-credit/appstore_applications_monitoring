import time
from unittest.mock import patch
from appmon import app_icons,icon_batches,app_groups
from appmon.db import connect
from appmon.task_logs import Execution,BatchProgress
from test_monitor import MySQLTestCase
import test_request_coalescing as helpers


class CollectionCostTest(MySQLTestCase):
    def icon(self,app_id,country='us',hours=168):
        app_icons.remember(self.db,'apple',app_id,country,'en','https://is1.mzstatic.com/shared.png')
        self.db.execute("UPDATE app_icons SET requested=1,refresh_hours=%s WHERE app_id=%s AND country=%s",(hours,app_id,country))

    def test_later_market_reuses_fresh_bytes_without_extending_observation_time(self):
        client=helpers.IconBatchTest.client(self)
        self.icon('100');self.assertEqual(app_icons.work_icons(self.cfg,self.db,20,client,max_requests=1)['ok'],1)
        observed=self.db.execute('SELECT checked_at FROM app_icons').fetchone()['checked_at']
        self.icon('101','gb')
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,20,client,max_requests=1)['ok'],1)
        self.assertEqual(client.downloads,1)
        self.assertEqual(self.db.execute("SELECT checked_at FROM app_icons WHERE app_id='101'").fetchone()['checked_at'],observed)
        # A stricter receipt may not reuse an older result just because a peer accepts it.
        self.db.execute('UPDATE icon_downloads SET fetched_at=%s',(time.time()-3600,))
        self.icon('102','gb',0.25)
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,20,client,max_requests=1)['ok'],1)
        self.assertEqual(client.downloads,2)

    def test_scope_scan_does_not_hold_icon_lock_against_detail_writer(self):
        self.icon('100')
        group=app_groups.create(self.db,{'name':'focus'})
        app_groups.add_members(self.db,group['id'],{'members':[dict(store='apple',app_id='100',country='us',language='en')]})
        writer=connect(self.cfg,initialize=False);writer.execute('SET SESSION innodb_lock_wait_timeout=1')
        original=self.db.execute;checked=[]
        def execute(sql,args=None):
            cursor=original(sql,args)
            if not checked and sql.startswith('SELECT') and 'FROM app_icons' in sql:
                checked.append(True)
                writer.execute("UPDATE app_icons SET name='updated during scope resolution' WHERE app_id='100'")
            return cursor
        try:
            with patch.object(self.db,'execute',side_effect=execute):
                rows=icon_batches.claim(self.db,self.cfg|{'media_scope':'groups_and_current_charts'},20,helpers.IconBatchTest.client(self))
            self.assertEqual(len(rows),1);self.assertEqual(checked,[True])
            self.assertEqual(rows[0]['name'],'updated during scope resolution')
        finally:writer.close()

    def test_wait_logs_are_sampled_and_pool_progress_is_written_once_per_batch(self):
        logs=[Execution(self.db,'pipelines','pool') for _ in range(50)]
        progress=BatchProgress(logs)
        with patch('appmon.task_logs.time.monotonic',return_value=100):
            for _ in range(100):progress('rate_wait','waiting')
        with patch('appmon.task_logs.time.monotonic',return_value=131):progress('rate_wait','still waiting')
        progress('request','one actual request');progress('response','one response')
        events=self.db.execute('SELECT event,COUNT(*) n FROM collection_logs GROUP BY event').fetchall()
        self.assertEqual({r['event']:r['n'] for r in events},{'rate_wait':2,'request':1,'response':1})

    def test_reconciliation_includes_media_and_one_off_receipts_without_resetting_history(self):
        import importlib.util
        from pathlib import Path
        spec=importlib.util.spec_from_file_location('cost_reconcile',Path(__file__).parents[1]/'scripts/reconcile_collection.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.db.execute("INSERT INTO app_media_assets(store,url_hash,source_url,label,enabled,manual_paused,token,lease_until,failures,last_error,checked_at,file_path) VALUES('apple','hash','https://is1.mzstatic.com/x.png','image',0,1,'expired',1,2,'previous error',10,'icons/old.png')")
        self.db.execute("""INSERT INTO collection_source_tasks(store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at)
            VALUES('apple','100','us','en','aso_report','archived receipt','{}','key','fixture','receipt',1)""")
        self.db.execute("UPDATE collection_source_tasks SET enabled=0,token='expired',lease_until=1,success_at=10,result='{}',failures=1")
        before_media=self.db.execute('SELECT * FROM app_media_assets').fetchone()
        before_receipt=self.db.execute('SELECT * FROM collection_source_tasks').fetchone()
        result=module.reconcile(self.db,self.cfg,True,quiescent=True)
        self.assertEqual((result['changed']['app_media_assets'],result['changed']['collection_source_tasks']),(1,1))
        for table,before in [('app_media_assets',before_media),('collection_source_tasks',before_receipt)]:
            after=self.db.execute('SELECT * FROM '+table).fetchone()
            self.assertIsNone(after.pop('token'));self.assertEqual(after.pop('lease_until'),0)
            before.pop('token');before.pop('lease_until');self.assertEqual(after,before)

    def test_invalid_legacy_icon_address_remains_eligible_for_metadata_repair(self):
        self.icon('100')
        self.db.execute("UPDATE app_icons SET source_url='https://invalid.example/image',failures=2,last_error='old invalid source'")
        client=helpers.IconBatchTest.client(self)
        result=app_icons.work_icons(self.cfg,self.db,20,client,max_requests=1)
        self.assertEqual(result['prepared'],1)
        self.assertEqual(client.lookups,[1])
        self.assertEqual(self.db.execute('SELECT source_url FROM app_icons').fetchone()['source_url'],'https://is1.mzstatic.com/shared.png')
