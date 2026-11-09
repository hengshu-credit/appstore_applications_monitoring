import base64
import concurrent.futures
import json
import os
import time
from pathlib import Path
from unittest.mock import patch
from appmon import keyword_discovery as kd, charts, global_charts, apple_popularity as ap, keyword_reports as kr, growth
from appmon.db import connect
from appmon.keywords import add_targets
from appmon.intelligence import add_observation_target
from appmon.collection import work_plans
from test_monitor import OceanBaseTestCase
from test_discovery import workbook_bytes
from appmon.keyword_stores import parse_apple


class DiscoveryReviewTest(OceanBaseTestCase):
    def setUp(self):
        super().setUp();self.m=dict(store='google',country='us',language='en')

    def candidate(self,source):
        kd.import_candidates(self.db,{'markets':[self.m],'words':['chat'],'source':source,'source_period':'test'})

    def chart(self,store,country,category,at):
        target=add_observation_target(self.db,dict(store=store,country=country,language='en',kind='chart',category=category))['id']
        self.db.execute("INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count) VALUES(%s,%s,'ok','fixture','v1','returned_chart_range',1)",(target,at))
        run=self.db.cursor.lastrowid
        self.db.execute('INSERT INTO observation_items VALUES(%s,%s,1,%s)',(run,'123' if store=='apple' else 'com.example.app',json.dumps({'name':'Test'})))
        self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,target))
        return target

    def test_shared_candidate_sources_create_one_target_even_with_concurrent_requests(self):
        self.candidate('one');self.candidate('two')
        candidates=kd.candidates(self.db,self.m)
        self.assertEqual((candidates['total'],candidates['source_records']),(1,2))
        ids=[r['id'] for r in self.db.execute('SELECT id FROM keyword_candidates').fetchall()]
        def track():
            db=connect(self.cfg,initialize=False)
            try:return kd.track(db,{'ids':ids})
            finally:db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:track(),range(2)))
        self.assertEqual(sum(r['targets'] for r in results),1)
        self.assertTrue(all(r['unique_targets']==1 for r in results))
        self.db.execute('UPDATE keyword_targets SET enabled=0,depth=7,refresh_hours=9')
        result=kd.track(self.db,{'ids':ids,'depth':100,'refresh_hours':24})
        row=self.db.execute('SELECT enabled,depth,refresh_hours FROM keyword_targets').fetchone()
        self.assertEqual((row['enabled'],row['depth'],row['refresh_hours']),(0,7,9));self.assertEqual(result['existing'],1)

    def test_coverage_paginates_all_apps_and_keywords_without_changing_denominator(self):
        now=time.time()
        for term in ('a','b','c'):
            target=add_targets(self.db,[self.m|{'keyword':term,'depth':10}])['ids'][0]
            self.db.execute("INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,requested_depth,returned_count,coverage) VALUES(%s,%s,'ok','fixture','v1',10,2,'first_response')",(target,now))
            run=self.db.cursor.lastrowid
            for pos,app in enumerate(('com.a.app','com.b.app'),1):self.db.execute('INSERT INTO keyword_ranks VALUES(%s,%s,%s,%s)',(run,app,pos,app))
            self.db.execute('UPDATE keyword_targets SET latest_success_id=%s WHERE id=%s',(run,target))
        r=kd.coverage(self.db,self.m,'com.a.app',app_offset=1,keyword_offset=2,app_keyword_offset=2,limit=1)
        self.assertEqual(r['apps'][0]['app_id'],'com.b.app');self.assertEqual(r['keywords'][0]['keyword'],'c')
        self.assertEqual(r['app_keywords'][0]['keyword'],'c');self.assertEqual(r['stats']['tracked'],3)
        self.assertEqual(r['pages']['apps']['total'],2);self.assertFalse(r['pages']['keywords']['has_more'])
        filtered=kd.coverage(self.db,self.m,q='b',limit=1)
        self.assertEqual(filtered['pages']['keywords']['total'],1);self.assertEqual(filtered['apps'][0]['covered'],3)

    def test_historical_alias_does_not_choose_a_target_created_in_the_future(self):
        now=time.time()-100
        old=self.chart('google','us','',now-86400)
        self.chart('google','us','APPLICATION',now)
        r=charts.view(self.db,'google','us','en',as_of=now-100)
        self.assertEqual(r['target']['id'],old);self.assertEqual(len(r['items']),1)

    def test_pending_global_plan_is_idempotent_and_keeps_edits_made_after_queueing(self):
        target=add_observation_target(self.db,self.m|{'kind':'chart','depth':20})['id']
        spec={'store':'google','countries':'us,jp','collections':'top-free'}
        first=global_charts.create_plan(self.db,spec)
        second=global_charts.create_plan(self.db,spec|{'countries':'jp,us'})
        self.assertEqual(first['id'],second['id']);self.assertTrue(second['reused'])
        self.db.execute('UPDATE observation_targets SET enabled=0,depth=13,refresh_hours=12 WHERE id=%s',(target,))
        work_plans(self.db,20)
        r=self.db.execute('SELECT enabled,depth,refresh_hours FROM observation_targets WHERE id=%s',(target,)).fetchone()
        self.assertEqual((r['enabled'],r['depth'],r['refresh_hours']),(0,13,12))

    def test_rising_board_without_comparable_baseline_is_not_reported_ready(self):
        self.chart('apple','us','',time.time()-60)
        r=global_charts.report(self.db,'apple','us','trending')
        self.assertEqual(r['fresh_boards'],0)
        self.assertEqual(r['countries'][0]['boards'][0]['quality'],'insufficient_history')

    def test_official_popularity_preserves_rank_period_scope_and_requires_authorization(self):
        spec={'store':'apple','country':'us','language':'en','genre':'FINANCE','period':'weekly','start':'2025-01-05','limit':20}
        rows=[dict(week='2025-01-05',countryOrRegion='US',genre='FINANCE',searchTerm='budget',rankInGenre=3,searchPopularity1to100=88)]
        with patch.dict(os.environ,{'APPLE_ADS_ACCESS_TOKEN':'','APPLE_ADS_AD_ACCOUNT_ID':''}),patch('appmon.apple_popularity.requests.post') as post:
            with self.assertRaises(ValueError):ap.fetch(self.cfg,self.db,spec)
            post.assert_not_called()
        r=ap.ingest(self.cfg,self.db,spec,{'result':{'rows':rows}})
        observed=kd.signals(self.db,spec)
        self.assertEqual(observed['items'][0]['position'],3);self.assertEqual(observed['items'][0]['score'],88)
        self.assertIn('2025-01-05/2025-01-11',observed['selected']['score_unit'])
        with self.assertRaises(ValueError):ap.ingest(self.cfg,self.db,spec,{'result':{'rows':[rows[0]|{'countryOrRegion':'JP'}]}})
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_signal_runs').fetchone()['n'],1)
        kd.promote_signals(self.db,r['id']);self.assertEqual(kd.candidates(self.db,spec)['total'],1)

    def test_report_raw_file_is_repaired_atomically_and_rejections_are_viewable(self):
        raw=workbook_bytes([['\u200bhidden',1,1,1]])
        value={'store':'apple','app_id':'123','country':'cn','language':'zh-cn','content_base64':base64.b64encode(raw).decode()}
        result=kr.import_report(self.cfg,self.db,value)
        from appmon.objects import for_config
        objects=for_config(self.cfg)
        ref=self.db.execute('SELECT raw_path FROM keyword_reports WHERE id=%s',(result['id'],)).fetchone()['raw_path']
        objects.client.put_object(Bucket=objects.bucket,Key=objects.key(ref),Body=b'partial')
        self.assertTrue(kr.import_report(self.cfg,self.db,value)['duplicate']);self.assertEqual(objects.get(ref),raw)
        rejected=kr.rejections(self.db,result['id']);self.assertEqual(rejected['total'],1)
        self.assertEqual(rejected['items'][0]['payload']['values'][0],'\u200bhidden')
        kr.promote(self.db,{'report_id':result['id'],'sheet':'ASO关键词','source_rows':[3]})
        identifier=self.db.execute('SELECT id FROM keyword_candidates LIMIT 1').fetchone()['id']
        source=kd.candidate_sources(self.db,identifier)['items'][0]
        self.assertEqual(source['source_app_id'],'123');self.assertIsNotNone(source['report_observed_at'])

    def test_apple_names_come_from_matching_metadata_without_changing_search_order(self):
        data={'bubbles':[{'results':[{'id':'2'},{'id':'1'}]}],
            'storePlatformData':{'native-search-lockup-search':{'meta':{'storefront':{'cc':'US'}},
                'results':{'1':{'id':1,'name':'First'},'2':{'id':2,'name':'Second','artwork':[{'url':'https://example.com/icon.png'}]}}}}}
        apps=parse_apple(data,'us')
        self.assertEqual([(a['app_id'],a['name']) for a in apps],[('2','Second'),('1','First')])
        self.assertEqual(apps[0]['icon'],'https://example.com/icon.png')
        from appmon.stores import FetchError
        with self.assertRaises(FetchError):parse_apple(data,'jp')

    def test_metric_replay_preserves_original_time_and_never_overwrites_newer_observation(self):
        at=time.time()-3*86400;m=dict(store='apple',app_id='123',country='us',language='en')
        self.db.execute('INSERT INTO snapshots(store,app_id,country,language,observed_at,digest,raw_path,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
            (*m.values(),at,'a'*64,'missing.gz',json.dumps({'name':'Old','ratings':5})))
        with patch('requests.get') as request:
            first=growth.backfill(self.cfg,self.db);request.assert_not_called()
        self.assertEqual(first['written'],1)
        row=self.db.execute('SELECT * FROM app_metrics_daily').fetchone();self.assertEqual(row['observed_at'],at)
        growth.record(self.db,m,{'name':'New','ratings':9},at+1,'new.gz')
        second=growth.backfill(self.cfg,self.db);self.assertEqual(second['skipped_newer_or_equal'],1)
        self.assertEqual(self.db.execute('SELECT ratings FROM app_metrics_daily').fetchone()['ratings'],9)
