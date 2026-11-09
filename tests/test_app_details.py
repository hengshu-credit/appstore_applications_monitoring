import time
from decimal import Decimal
from datetime import datetime, timezone

from appmon import app_details, app_groups, auth
from appmon.db import dump
from test_monitor import OceanBaseTestCase


class AppDetailRowsTest(OceanBaseTestCase):
    def test_unregistered_apps_remain_unknown_and_reads_create_no_tasks(self):
        targets=[dict(store='google',app_id='com.example.loan',country=c,language='en') for c in ('th','vn')]
        result=app_details.rows(self.db,self.cfg,targets)['items']
        self.assertEqual([r['country'] for r in result],['th','vn'])
        self.assertTrue(all(r['keyword_count'] is None and r['chart_positions']==[] for r in result))
        for table in ('apps','app_group_members','watchlist','jobs','observation_targets'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) n FROM {table}').fetchone()['n'],0)
        self.assertEqual(auth.requirements('/api/apps/detail-rows','POST'),['apps:read'])
        with self.assertRaises(ValueError):app_details.rows(self.db,self.cfg,targets*101)

    def test_details_share_group_metrics_without_leaking_group_overrides(self):
        from appmon.intelligence import add_observation_target
        from appmon.keywords import add_targets
        target=dict(store='google',app_id='com.example.loan',country='th',language='en')
        group=app_groups.create(self.db,{'name':'test'})
        app_groups.add_members(self.db,group['id'],{'members':[target]})
        now=time.time()
        payload=dict(name='Local Loan',category_id='FINANCE',developer='Loan Inc',developer_id='6942553452222258407',rating=0,price=0,has_iap=False)
        self.db.execute('''INSERT INTO listings(store,app_id,country,language,state,payload,last_success,checked_at)
            VALUES('google','com.example.loan','th','en','available',%s,%s,%s)''',(dump(payload),now,now))
        for category,pos in (('',12),('FINANCE',4),('TOOLS',1)):
            identifier=add_observation_target(self.db,target|dict(kind='chart',collection='top-free',category=category))['id']
            run=self.db.execute('''INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count)
                VALUES(%s,%s,'ok','test','v1','returned_chart_range',20)''',(identifier,now)).lastrowid
            self.db.execute("INSERT INTO observation_items VALUES(%s,'com.example.loan',%s,'{}')",(run,pos))
            self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,identifier))
        word=add_targets(self.db,[target|{'keyword':'loan'}])['ids'][0]
        run=self.db.execute('''INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,requested_depth,returned_count,coverage)
            VALUES(%s,%s,'ok','test','v1',50,20,'first_response')''',(word,now)).lastrowid
        self.db.execute("INSERT INTO keyword_ranks VALUES(%s,'com.example.loan',2,'Loan')",(run,))
        self.db.execute('UPDATE keyword_targets SET latest_success_id=%s WHERE id=%s',(run,word))
        day=datetime.now(timezone.utc).date()
        self.db.execute("INSERT INTO app_metric_facts VALUES('google','com.example.loan','th',%s,'downloads',0,0,'',0)",(day,))
        expected=app_groups.dashboard(self.db,self.cfg,group['id'])['items'][0]
        common,other=app_details.rows(self.db,self.cfg,[target,target|{'country':'vn'}])['items']
        for field in ('developer_id','category','chart_positions','keyword_count','keyword_top3_count','top_keywords','rating','price','has_iap'):
            self.assertEqual(common[field],expected[field],field)
        self.assertEqual(common['metrics'][0]['value'],expected['metrics'][0]['value'])
        self.assertEqual(Decimal(common['metrics'][0]['value']),Decimal(0))
        self.assertIsNone(other['keyword_count'])
        self.assertEqual(other['metrics'],[])
        app_groups.adjust_member(self.db,group['id'],expected['id'],dict(revision=0,status_override='region_unavailable',permission_overrides={'Camera':True}),'tester')
        clean=app_details.rows(self.db,self.cfg,[target])['items'][0]
        self.assertEqual(clean['classification'],'available')
        self.assertEqual(clean['permission_overrides'],{})
