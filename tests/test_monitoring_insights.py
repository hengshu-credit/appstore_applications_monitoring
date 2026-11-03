import time
import unittest
from appmon import app_metrics, app_discovery, availability
from appmon.db import add_apps, dump
from appmon.keywords import add_targets
from appmon.keyword_stores import SearchResult
from appmon.stores import Result, FetchError
from test_monitor import MySQLTestCase


class MonitoringRulesTest(unittest.TestCase):
    def test_disappearance_rejects_failures_depth_changes_and_rushed_rechecks(self):
        now=time.time()
        rows=[dict(id=i,source='test',parser_version='v1',coverage='first_response',requested_depth=100,
                   outcome='ok',position=10 if i==0 else None,returned_count=100,observed_at=now-10800+i*3600) for i in range(3)]
        self.assertTrue(app_metrics.disappearance(rows,now,24))
        for field,value in [('outcome','error'),('returned_count',9),('source','other'),('parser_version','v2'),('requested_depth',50),('position',20),('observed_at',rows[1]['observed_at']+5)]:
            changed=[dict(r) for r in rows];changed[-1][field]=value
            self.assertFalse(app_metrics.disappearance(changed,now,24),field)
        self.assertFalse(app_metrics.disappearance(rows,now+86400,24))

    def test_store_url_is_parsed_without_fetching_user_hosts(self):
        self.assertEqual(app_discovery.identity('apple','https://apps.apple.com/cn/app/name/id123?l=zh'),'123')
        self.assertEqual(app_discovery.identity('google','https://play.google.com/store/apps/details?id=com.example.app'),'com.example.app')
        for url in ['https://apps.apple.com.evil.example/app/id123','http://apps.apple.com/app/id123','https://user@apps.apple.com/app/id123','https://127.0.0.1/id123','https://apps.apple.com:8443/id123']:
            with self.assertRaises(ValueError):app_discovery.identity('apple',url)


class MonitoringDataTest(MySQLTestCase):
    def test_availability_snapshots_are_scoped_throttled_and_do_not_backfill(self):
        now=time.time();m=dict(store='apple',country='us',language='en')
        add_apps(self.db,[('apple','123','Example',None)],'test')
        self.db.execute('''INSERT INTO listings(store,app_id,country,language,state,checked_at,misses)
            VALUES('apple','123','us','en','suspected_missing',%s,1)''',(now,))
        self.assertEqual(availability.record_daily(self.db,now),1)
        self.assertEqual(availability.record_daily(self.db,now+60),0)
        r=availability.report(self.db,m)
        self.assertEqual(len(r['history']),1)
        self.assertEqual(r['counts'][0]['state'],'suspected_missing')
        self.assertEqual(len(r['pending']),1)
        self.assertEqual(availability.report(self.db,m|{'country':'gb'})['history'],[])
        self.db.execute("UPDATE listings SET state='available',misses=0")
        availability.record_daily(self.db,now+3601)
        r=availability.report(self.db,m)
        self.assertEqual(r['history'][-1]['counts']['available']['count'],1)
        self.assertEqual(r['pending'],[])

    def test_preview_does_not_import_and_import_is_verified_and_idempotent(self):
        m=dict(store='apple',country='us',language='en')
        class Search:
            def search(self,t):return SearchResult([dict(app_id='123',name='Test')],b'raw','test')
        preview=app_discovery.search(self.db,self.cfg,m|{'query':'example'},Search())
        self.assertFalse(preview['items'][0]['in_library'])
        self.assertEqual(self.db.execute('SELECT count(*) n FROM apps').fetchone()['n'],0)
        class Missing:
            def fetch(self,m):return Result('missing')
        with self.assertRaises(ValueError):app_discovery.import_app(self.db,self.cfg,m|{'app_id':'123'},Missing())
        self.assertEqual(self.db.execute('SELECT count(*) n FROM apps').fetchone()['n'],0)
        class Details:
            def fetch(self,m):return Result('ok',dict(name='Verified',category_id='6005'),b'raw')
        first=app_discovery.import_app(self.db,self.cfg,m|{'app_id':'123'},Details())
        again=app_discovery.import_app(self.db,self.cfg,m|{'app_id':'123'},Details())
        self.assertEqual((first['added'],again['added']),(1,0))
        self.assertEqual(first['collection']['id'],again['collection']['id'])

    def test_market_scoped_metrics_exclude_stale_and_failed_coverage(self):
        m=dict(store='apple',app_id='123',country='us',language='en');now=time.time()
        add_apps(self.db,[('apple','123','Example',None)],'test')
        add_targets(self.db,[m|{'keyword':w} for w in ('one','two','three')]+[m|{'country':'gb','keyword':'foreign'}])
        targets=self.db.execute('SELECT * FROM keyword_targets ORDER BY id').fetchall()
        for t in targets:
            for i in range(3):
                at=now-10800+i*3600;position=2 if i==0 or t['keyword']=='one' else None
                self.db.execute('''INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,requested_depth,returned_count,coverage)
                    VALUES(%s,%s,'ok','test','v1',100,100,'first_response')''',(t['id'],at))
                run=self.db.cursor.lastrowid
                if position:self.db.execute('INSERT INTO keyword_ranks VALUES(%s,%s,%s,%s)',(run,'123',position,'Example'))
        r=app_metrics.report(self.db,m)
        self.assertEqual(r['coverage'],dict(tracked=3,fresh=3,covered=1,top3=1,top10=1))
        self.assertEqual(len(r['series']),3)
        self.assertEqual(r['signals'],[])
        self.assertFalse(r['downloads']['available'])
        self.db.execute('UPDATE keyword_targets SET failures=1 WHERE keyword=%s',('one',))
        r=app_metrics.report(self.db,m)
        self.assertEqual(r['coverage']['covered'],0)
        self.assertEqual(r['coverage']['fresh'],2)
        self.assertTrue(r['coverage_daily'])
        with self.assertRaises(ValueError):app_metrics.report(self.db,m|{'days':0})
