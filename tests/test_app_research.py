import json
import time
import unittest
from datetime import datetime,timezone
from appmon.catalog import public_catalog
from appmon.keywords import validate_target,work_keywords
from appmon.keyword_stores import parse_apple_web,SearchResult
from appmon.stores import FetchError,Result
from appmon.observation_stores import ObservationResult
from appmon.intelligence import add_observation_target,work_observations,work_watchlist
from appmon.collection import work_plans
from appmon.db import add_apps
from appmon import charts,app_collection
from test_monitor import OceanBaseTestCase


class StoreCapabilityTest(unittest.TestCase):
    def test_kazakhstan_is_supported_without_silent_us_fallback(self):
        self.assertEqual(validate_target(dict(store='apple',country='KZ',language='en',keyword='test'))['country'],'kz')
        self.assertEqual(sum(c['apple_supported'] for c in public_catalog()['countries']),175)
        payload={'data':[{'intent':{'$kind':'SearchResultsPageIntent','storefront':'kz'},'data':{'shelves':[
            {'id':'SearchResults.shelfId','items':[{'$kind':'AppSearchResult','lockup':{'adamId':'456','title':'B'}},
                                                {'$kind':'AppSearchResult','lockup':{'adamId':'123','title':'A'}}]}]}}]}
        def html():return '<script type="application/json" id="serialized-server-data">'+json.dumps(payload)+'</script>'
        self.assertEqual([r['app_id'] for r in parse_apple_web(html(),'kz')],['456','123'])
        with self.assertRaises(FetchError):parse_apple_web(html(),'us')
        with self.assertRaises(ValueError):validate_target(dict(store='apple',country='aq',language='en',keyword='test'))

    def test_chart_capabilities_separate_live_sources_and_derived_views(self):
        self.assertEqual(len(charts.capabilities('google')),7)
        self.assertEqual(charts.definition('google','trending')['mode'],'direct')
        self.assertEqual(charts.definition('apple','trending')['mode'],'derived')
        self.assertFalse(charts.definition('apple','new-free')['category_supported'])
        for store in ('apple','google'):self.assertEqual(charts.definition(store,'new-grossing')['mode'],'derived')


class AppResearchTest(OceanBaseTestCase):
    def test_expanded_market_failure_records_actual_web_search_source(self):
        from appmon.keywords import add_targets
        add_targets(self.db,[dict(store='apple',country='kz',language='en',keyword='test')])
        class Broken:
            def search(self,target):raise FetchError('TLS error')
        self.assertEqual(work_keywords(self.cfg,self.db,1,Broken())['error'],1)
        row=self.db.execute('SELECT source,parser_version FROM keyword_runs').fetchone()
        self.assertEqual((row['source'],row['parser_version']),('apple-web-search','apple-web-v1'))

    def snapshot(self,collection,rows,now=None):
        t=add_observation_target(self.db,dict(kind='chart',store='apple',country='kz',language='en',collection=collection))['id']
        now=time.time() if now is None else now
        self.db.execute('''INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count)
            VALUES(%s,%s,'ok','fixture','v1','returned_chart_range',%s)''',(t,now,len(rows)))
        run_id=self.db.cursor.lastrowid
        for n,row in enumerate(rows,1):self.db.execute('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',(run_id,row['app_id'],n,json.dumps(row)))
        self.db.execute('UPDATE observation_targets SET previous_success_id=latest_success_id,latest_success_id=%s WHERE id=%s',(run_id,t))
        return run_id

    def test_derived_rising_chart_uses_real_positive_changes_and_excludes_newcomers(self):
        self.snapshot('top-free',[dict(app_id='111'),dict(app_id='222'),dict(app_id='333')])
        self.snapshot('top-free',[dict(app_id='333'),dict(app_id='444'),dict(app_id='111')])
        result=charts.view(self.db,'apple','kz','en','trending')
        self.assertEqual([r['app_id'] for r in result['items']],['333'])
        self.assertEqual(result['items'][0]['change'],2)
        self.assertEqual(result['definition']['mode'],'derived')
        self.assertEqual(charts.view(self.db,'apple','us','en','trending')['items'],[])

    def test_new_grossing_reports_base_rank_and_unknown_release_dates(self):
        now=time.time();date=lambda days:datetime.fromtimestamp(now-days*86400,timezone.utc).isoformat()
        self.snapshot('top-grossing',[dict(app_id='111',released=date(60)),dict(app_id='222'),dict(app_id='333',released=date(5))],now)
        result=charts.view(self.db,'apple','kz','en','new-grossing')
        self.assertEqual(result['unknown_release_count'],1)
        self.assertEqual([(r['position'],r['base_position']) for r in result['items']],[(1,3)])
        ranking=charts.app_rankings(self.db,'apple','333','kz','en',[''])
        self.assertEqual(next(r for r in ranking if r['collection']=='top-grossing')['position'],3)
        unsupported=charts.view(self.db,'apple','kz','en','new-free','6015')
        self.assertTrue(unsupported['unsupported'])

    def test_one_click_is_idempotent_market_scoped_and_completes_through_workers(self):
        m=dict(store='apple',app_id='123',country='kz',language='en')
        add_apps(self.db,[('apple','123','Example','6015')],'fixture')
        first=app_collection.collect(self.db,m)
        self.assertEqual(app_collection.collect(self.db,m)['id'],first['id'])
        while work_plans(self.db)['status']!='complete':pass
        targets=self.db.execute("SELECT * FROM observation_targets WHERE kind='chart'").fetchall()
        self.assertTrue(any(t['collection']=='new-free' and t['category']=='' for t in targets))
        self.assertFalse(any(t['collection']=='new-free' and t['category']=='6015' for t in targets))
        self.assertTrue(all(t['country']=='kz' for t in targets))
        class Details:
            def fetch(self,job):return Result('ok',{'name':'Example','category':'6015'},b'detail')
        class Observations:
            def fetch_observation(self,t):
                items=[{'id':'r1','rating':5,'content':'Good'}] if t['kind']=='reviews' else [{'id':'123','app_id':'123','name':'Example'}]
                return ObservationResult(items,b'raw','fixture','returned_range')
        class Keywords:
            def search(self,t):return SearchResult([{'app_id':'123','name':'Example'}],b'search','fixture')
        work_watchlist(self.cfg,self.db,5,Details());work_observations(self.cfg,self.db,30,Observations());work_keywords(self.cfg,self.db,5,Keywords())
        done=app_collection.report(self.db,first['id'])
        self.assertEqual(done['state'],'complete');self.assertEqual(done['finished'],done['total'])
        self.assertIsNone(app_collection.latest(self.db,m|{'country':'us'}))
        self.assertNotEqual(app_collection.collect(self.db,m)['id'],first['id'])

    def test_empty_apple_new_chart_category_is_rejected_not_mislabeled(self):
        with self.assertRaisesRegex(ValueError,'总榜'):
            add_observation_target(self.db,dict(kind='chart',store='apple',country='kz',language='en',collection='new-paid',category='6015'))
        derived=add_observation_target(self.db,dict(kind='chart',store='apple',country='kz',language='en',collection='new-grossing'))
        self.assertEqual(self.db.execute('SELECT collection FROM observation_targets WHERE id=%s',(derived['id'],)).fetchone()['collection'],'top-grossing')
