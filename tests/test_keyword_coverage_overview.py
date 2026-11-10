import time
import pymysql
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon import auth, keyword_discovery as kd
from appmon.keywords import add_targets, claim_target, save_search
from appmon.keyword_stores import SearchResult
from appmon.web import create_app
from test_monitor import OceanBaseTestCase


class KeywordCoverageOverviewTest(OceanBaseTestCase):
    def test_disk_capacity_error_reports_actionable_message_without_internal_details(self):
        client = TestClient(create_app(self.cfg))
        self.login_admin(client)
        with patch.object(kd,'monitoring_overview',side_effect=pymysql.OperationalError(4184,'private server address')):
            response=client.get('/api/discovery/keywords/overview')
        self.assertEqual(response.status_code,503)
        self.assertIn('扩容 OceanBase',response.json()['detail'])
        self.assertNotIn('private',response.text)

    def test_empty_overview_requires_keyword_read_access(self):
        client = TestClient(create_app(self.cfg))
        path = '/api/discovery/keywords/overview'
        self.assertEqual(client.get(path).status_code, 401)
        self.assertEqual(auth.requirements(path, 'GET'), ['keywords:read'])
        self.login_admin(client)
        response = client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['items'], [])
        self.assertTrue(all(value == 0 for value in result['totals'].values()))
        self.assertEqual(result['scope'], 'all_stores_countries_languages')

    def test_country_language_counts_deduplicate_stores_and_sources_without_merging_countries(self):
        en = dict(store='google', country='us', language='en')
        markets = [en, en | {'country':'gb'}, en | {'store':'apple'}]
        for source in ('one', 'two'):
            kd.import_candidates(self.db, dict(markets=markets, words=['loan','Loan','unused'], source=source))
        for language,country,word in [('th','th','สินเชื่อ'),('zh-cn','cn','贷款'),('zh-tw','tw','贷款')]:
            kd.import_candidates(self.db, dict(markets=[en | dict(language=language,country=country)], words=[word]))
        specs = [m | {'keyword':'loan'} for m in markets] + [en | {'keyword':word} for word in ('Loan','paused','stale','depth','error','never')]
        now = time.time()-10
        for spec in specs:
            identifier = add_targets(self.db, [spec | {'depth':10}])['ids'][0]
            if spec['keyword'] != 'never':
                target = claim_target(self.db, 60, now=now, target_id=identifier)
                save_search(self.db,self.cfg,target,SearchResult([],b'fixture','fixture'),now=now)
            if spec['country']=='gb' or spec['keyword']=='paused':
                self.db.execute('UPDATE keyword_targets SET enabled=0 WHERE id=%s',(identifier,))
            if spec['store']=='apple' or spec['keyword']=='error':
                self.db.execute('UPDATE keyword_targets SET failures=1 WHERE id=%s',(identifier,))
            if spec['keyword']=='depth':
                self.db.execute('UPDATE keyword_targets SET depth=20 WHERE id=%s',(identifier,))
            if spec['keyword']=='stale':
                self.db.execute('UPDATE keyword_runs SET observed_at=%s WHERE target_id=%s',(now-3*86400,identifier))
        result = kd.monitoring_overview(self.db)
        rows = {(r['country'],r['language']):r for r in result['items']}
        self.assertEqual(set(rows), {('us','en'),('gb','en'),('th','th'),('cn','zh-cn'),('tw','zh-tw')})
        self.assertEqual({k:rows['us','en'][k] for k in ('library_words','monitored_words','enabled_words','paused_words','fresh_words','tasks','fresh_tasks')},
                         dict(library_words=8,monitored_words=7,enabled_words=6,paused_words=1,fresh_words=2,tasks=8,fresh_tasks=2))
        self.assertEqual({k:rows['gb','en'][k] for k in ('library_words','monitored_words','enabled_words','paused_words','tasks')},
                         dict(library_words=3,monitored_words=1,enabled_words=0,paused_words=1,tasks=1))
        for key in [('th','th'),('cn','zh-cn'),('tw','zh-tw')]:
            self.assertEqual(rows[key]['library_words'],1)
            self.assertEqual(rows[key]['monitored_words'],0)
            self.assertEqual(rows[key]['tasks'],0)
        self.assertEqual(result['totals']['library_words'],14)
        self.assertEqual(result['totals']['languages'],4)
        self.assertEqual(result['totals']['monitoring_languages'],1)
        self.assertEqual(result['totals']['monitored_words'],8)
        self.assertEqual(result['totals']['countries'],5)
        self.assertEqual(result['totals']['monitoring_countries'],2)
        self.assertEqual(result['totals']['tasks'],9)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_targets').fetchone()['n'],9)
