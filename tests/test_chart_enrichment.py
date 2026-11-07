import json,time
from unittest.mock import patch
from appmon import chart_enrichment as enrichment, app_groups, storage_policy
from appmon.catalog import CATALOG,APPLE_COUNTRIES
from appmon.db import dump
from test_monitor import MySQLTestCase


class ChartEnrichmentTest(MySQLTestCase):
    def prepare(self,rows,run=1):
        target=self.db.execute("SELECT id FROM observation_targets WHERE kind='chart' LIMIT 1").fetchone()
        if not target:
            self.db.execute("INSERT INTO observation_targets(kind,store,app_id,country,language,collection,category,depth) VALUES('chart','google','','us','en','top-free','FINANCE',200)")
            target={'id':self.db.cursor.lastrowid}
        self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,target['id']))
        self.db.executemany('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',
            [(run,appid,pos,dump({'app_id':appid,'name':name,'icon':'https://play-lh.googleusercontent.com/'+appid})) for appid,pos,name in rows])
        return target['id']

    def enable(self):
        self.db.execute("INSERT INTO collection_controls VALUES('global-top50',%s,%s)",(dump({'enabled':True,'started_at':time.time()}),time.time()))

    def test_global_matrix_covers_configured_countries_categories_and_three_boards(self):
        rows=list(enrichment.matrix())
        self.assertEqual(len(rows),len(set(rows)))
        for store in ('apple','google'):
            selected=[r for r in rows if r[0]==store]
            self.assertEqual({r[3] for r in selected},set(enrichment.COLLECTIONS))
            self.assertEqual({r[4] for r in selected},{''}|{r['id'] for r in CATALOG['categories'][store] if not r['legacy']})
            self.assertEqual({r[1] for r in selected},APPLE_COUNTRIES if store=='apple' else {c['code'] for c in CATALOG['countries']})
        self.assertEqual(len(rows),62625)

    def test_top50_enqueues_every_public_dependency_once_and_excludes_rank51(self):
        self.enable();self.prepare([('com.first',1,'First App'),('com.fifty',50,'Fifty'),('com.outside',51,'Outside')])
        result=enrichment.sync(self.db,self.cfg)
        self.assertEqual(result,{'runs':1,'apps':2})
        for table in ('watchlist','jobs','app_permission_targets','keyword_targets','app_icons','chart_enrichment_apps'):
            self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM '+table).fetchone()['n'],2,table)
        reviews=self.db.execute("SELECT app_id,depth FROM observation_targets WHERE kind='reviews'").fetchall()
        self.assertEqual({r['app_id'] for r in reviews},{'com.first','com.fifty'});self.assertTrue(all(r['depth']==500 for r in reviews))
        self.assertEqual(enrichment.sync(self.db,self.cfg),{'runs':0,'apps':0})
        from appmon.collection_lineage import report
        graph=report(self.db,self.cfg,'google')
        self.assertIn('top50',{node['id'] for node in graph['nodes']})
        self.assertEqual(next(e['count'] for e in graph['edges'] if e['source']=='top50' and e['target']=='q:details'),2)
        cfg=self.cfg|{'media_scope':'groups_and_current_charts'}
        self.assertFalse(storage_policy.media_allowed(self.db,cfg,'google','com.outside'))
        self.assertTrue(storage_policy.media_allowed(self.db,cfg,'google','com.fifty'))

    def test_new_snapshot_preserves_pause_and_reuses_shared_app_tasks(self):
        self.enable();self.prepare([('com.first',1,'First')]);enrichment.sync(self.db,self.cfg)
        group=app_groups.create(self.db,{'name':'测试关注'})
        app_groups.add_members(self.db,group['id'],{'members':[dict(store='google',app_id='com.first',country='us',language='en')]})
        app_groups.control_monitoring(self.db,group['id'],{'action':'pause'})
        self.prepare([('com.first',2,'First'),('com.new',1,'New')],run=2)
        result=enrichment.sync(self.db,self.cfg)
        self.assertEqual(result['apps'],2)
        self.assertEqual(self.db.execute("SELECT enabled FROM watchlist WHERE app_id='com.first'").fetchone()['enabled'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM watchlist').fetchone()['n'],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_targets').fetchone()['n'],2)

    def test_start_is_idempotent_and_preserves_existing_depth_failure_backoff(self):
        self.prepare([('com.first',1,'First')])
        self.db.execute("UPDATE observation_targets SET enabled=0,failures=2,due=%s",(time.time()+600,))
        subset=[('google','us','en','top-free','FINANCE'),('apple','us','en','top-paid','')]
        with patch('appmon.chart_enrichment.matrix',return_value=iter(subset)):
            first=enrichment.start(self.db)
        row=self.db.execute("SELECT enabled,depth,failures,due FROM observation_targets WHERE store='google'").fetchone()
        self.assertEqual((row['enabled'],row['depth'],row['failures']),(1,200,2));self.assertGreater(row['due'],time.time())
        self.db.execute("UPDATE observation_targets SET enabled=0 WHERE store='google'")
        with patch('appmon.chart_enrichment.matrix',return_value=iter(subset)):
            second=enrichment.start(self.db)
        self.assertTrue(second['reused']);self.assertEqual(first['signature'],second['signature'])
        self.assertEqual(self.db.execute("SELECT enabled FROM observation_targets WHERE store='google'").fetchone()['enabled'],0)

    def test_dependency_failure_rolls_back_checkpoint_and_can_resume(self):
        self.enable();self.prepare([('com.first',1,'First')])
        with patch('appmon.chart_enrichment.activate_once',side_effect=ValueError('interrupted')):
            with self.assertRaises(ValueError):enrichment.sync(self.db,self.cfg)
        for table in ('watchlist','jobs','keyword_targets','chart_enrichment_apps','chart_enrichment_runs'):
            self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM '+table).fetchone()['n'],0,table)
        self.assertEqual(enrichment.sync(self.db,self.cfg),{'runs':1,'apps':1})
