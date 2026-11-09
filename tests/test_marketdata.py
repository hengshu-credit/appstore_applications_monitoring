import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from appmon import marketdata_source as af, data_sources
from appmon.web import create_app
from appmon.collection_limits import save as save_limits
from test_monitor import OceanBaseTestCase


class MarketDataTest(OceanBaseTestCase):
    def plan(self, **kwargs):
        value = dict(start_date='2025-01-01', end_date='2025-02-01',
                     stop_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
                     countries=['us'], categories=['71'], products=['123'], daily_charts=False)
        value.update(kwargs)
        return af.create_run(self.db, value)['runs'][0]

    def allow(self):
        self.db.execute('UPDATE marketdata_budget SET next_at=0')

    def credential(self):
        with patch.object(af, 'authorize', return_value='authorized'):
            data_sources.save(self.db, self.cfg, {'access_token':'fixture-token'}, 'marketdata')

    def response(self, status=200, value=None, headers=None):
        response=Mock(status_code=status, headers=headers or {})
        response.json.return_value=value if value is not None else {}
        return response

    def test_token_and_password_are_encrypted_and_never_returned(self):
        with patch.object(af, 'get', return_value={'api_requests':{'remaining':10}}):
            result=data_sources.save(self.db,self.cfg,dict(email='fig@example.com',password='private-password',access_token='private-token'),'marketdata')
        self.assertEqual(result['marketdata']['status'],'authorized')
        row=self.db.execute("SELECT secret FROM source_accounts WHERE provider='marketdata'").fetchone()
        for secret in ('private-password','private-token','fig@example.com'):
            self.assertNotIn(secret,row['secret']);self.assertNotIn(secret,json.dumps(result))
        saved=json.loads(data_sources.cipher(self.cfg).decrypt(row['secret'].encode()))
        self.assertEqual(saved['access_token'],'private-token')
        with af.client_for(self.db,self.cfg) as client:
            self.assertEqual(client.headers['Authorization'],'Bearer private-token')
            self.assertTrue(client.headers['User-Agent'].isascii())
            self.assertIsNone(client.auth)

    def test_password_only_never_attempts_unsupported_basic_login(self):
        with patch.object(af,'get') as request:
            result=data_sources.save(self.db,self.cfg,dict(email='a@example.com',password='secret'),'marketdata')
        self.assertEqual(result['marketdata']['status'],'api_token_required')
        request.assert_not_called()

    def test_quota_headers_cooldown_and_origin_guard(self):
        client=Mock();client.source_profile=self.cfg['source_profiles']['marketdata']
        client.get.return_value=self.response(headers={'X-Request-Limit':'10','X-Request-Usage':'10'})
        af.get(self.db,client,'/usage')
        self.allow()
        with self.assertRaises(af.SourceError) as caught:af.get(self.db,client,'/usage')
        self.assertEqual(caught.exception.code,'rate_limited')
        self.assertEqual(client.get.call_count,1)
        self.assertFalse(client.get.call_args.kwargs['allow_redirects'])
        with self.assertRaises(ValueError):af.get(self.db,client,'https://evil.test/usage')

    def test_420_and_429_backoff_without_recording_error_body(self):
        client=Mock();client.source_profile=self.cfg['source_profiles']['marketdata']
        for status in (420,429):
            self.allow()
            client.get.return_value=self.response(status,headers={'Retry-After':'120'})
            with self.assertRaises(af.SourceError) as caught:af.get(self.db,client,'/usage')
            self.assertEqual(caught.exception.code,'rate_limited')
            self.assertGreater(caught.exception.delay,0)
            self.assertGreater(self.db.execute('SELECT next_at FROM marketdata_budget').fetchone()['next_at'],time.time())

    def test_full_resume_pagination_discovery_and_raw_provenance(self):
        self.credential()
        run=self.plan(kinds=['metadata','reviews'],start_date='2025-01-01',end_date='2025-01-02')
        calls=[]
        def get(db,client,path,params,limit):
            calls.append((path,params))
            if path=='/data/countries':return {'US':{'iso':'US'}}
            if path=='/data/categories':return {'71':{'id':71,'store':'google_play'}}
            if path=='/data/stores':return {'google_play':{'id':'google_play'}}
            if path=='/products/mine':return {}
            if path.startswith('/products/'):return {'id':123,'store':'google_play','package_name':'com.example.test','name':'Test'}
            if path.startswith('/ranks/snapshots'):
                return {'timestamp':'2026-11-05T03:00:00','page_start':params['start'],'total_count':2,'entries':[{'id':123,'store':'google_play','package_name':'com.example.test'}]}
            if path=='/reviews':return {'this_page':params['page'],'pages':2,'reviews':[{'id':str(params['page']),'review':'actual text'}]}
            self.fail(path)
        with patch.object(af,'get',side_effect=get):
            for _ in range(4):af.work(self.cfg,self.db,3)
            af.work(self.cfg,self.db,10)
        report=af.report(self.db)
        self.assertEqual(report['runs'][0]['status'],'complete')
        self.assertEqual(len([x for x in calls if x[0]=='/reviews']),2)
        self.assertEqual(len([x for x in calls if x[0]=='/products/123']),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM apps').fetchone()['n'],1)
        for artifact in report['artifacts']:
            self.assertIsInstance(json.loads(af.artifact_path(self.db,self.cfg,artifact['id'])),(dict,list))
            self.assertNotIn('fixture-token',artifact['provenance'])
        before=len(calls)
        af.work(self.cfg,self.db)
        self.assertEqual(len(calls),before)

    def test_permission_circuit_applies_to_future_jobs_and_manual_retry(self):
        self.credential();run=self.plan(charts=False)
        self.db.execute("UPDATE marketdata_jobs SET status='done'")
        af.enqueue(self.db,run['id'],'estimates','/reports/estimates',{'products':'1'})
        with patch.object(af,'schedule',return_value=run['settings']),patch.object(af,'get',side_effect=af.SourceError('access_denied','No access')) as request:
            af.work(self.cfg,self.db,1)
            af.enqueue(self.db,run['id'],'estimates','/reports/estimates',{'products':'2'})
            af.work(self.cfg,self.db,1)
            self.assertEqual(request.call_count,1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM marketdata_jobs WHERE status='blocked'").fetchone()['n'],2)
        af.control(self.db,run['id'],'retry')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM marketdata_blocks').fetchone()['n'],0)

    def test_pause_expiry_and_global_pause_prevent_requests(self):
        self.credential();run=self.plan()
        af.control(self.db,run['id'],'pause')
        with patch.object(af,'get') as request:
            af.work(self.cfg,self.db)
            af.control(self.db,run['id'],'resume')
            save_limits(self.db,self.cfg,{'paused':True})
            af.work(self.cfg,self.db)
            save_limits(self.db,self.cfg,{'paused':False})
            self.db.execute('UPDATE marketdata_runs SET stop_at=%s',(time.time()-1,))
            af.work(self.cfg,self.db)
            request.assert_not_called()
        self.assertEqual(af.report(self.db)['runs'][0]['status'],'expired')

    def test_archive_failure_does_not_advance_checkpoint(self):
        self.credential();run=self.plan()
        with patch.object(af,'get',return_value={'US':{'iso':'US'}}),patch.object(af,'archive',side_effect=OSError('full')):
            af.work(self.cfg,self.db,1)
        job=self.db.execute('SELECT * FROM marketdata_jobs ORDER BY id LIMIT 1').fetchone()
        self.assertEqual(job['status'],'error');self.assertIsNone(job['artifact_id'])
        af.control(self.db,run['id'],'retry')
        with patch.object(af,'get',return_value={'US':{'iso':'US'}}):af.work(self.cfg,self.db,1)
        self.assertEqual(self.db.execute('SELECT status FROM marketdata_jobs ORDER BY id LIMIT 1').fetchone()['status'],'done')

    def test_csv_roundtrip_exact_row_count_and_admin_routes(self):
        content='Date,Downloads,Revenue\n2025-01-01,12,3.40\n2025-01-02,10,2.30\n'
        api=TestClient(create_app(self.cfg))
        self.assertEqual(api.get('/api/data-sources/marketdata/collection').status_code,401)
        self.login_admin(api)
        result=api.post('/api/data-sources/marketdata/datasets',json={'content':content})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['records'],2)
        response=api.get('/api/data-sources/marketdata/artifacts/'+str(result.json()['id']))
        self.assertEqual(gzip.decompress(response.content).decode(),content)
        self.assertEqual(api.post('/api/data-sources/marketdata/datasets',json={'content':'a,b\n1\n'}).status_code,400)
        member=TestClient(create_app(self.cfg))
        member.post('/api/auth/register',json={'username':'ordinary-user','password':'password-123'})
        login=member.post('/api/auth/login',json={'username':'ordinary-user','password':'password-123'})
        member.headers['X-CSRF-Token']=login.json()['csrf_token']
        for path in ('collection','artifacts/1'):
            self.assertEqual(member.get('/api/data-sources/marketdata/'+path).status_code,403)
        self.assertEqual(member.put('/api/data-sources/marketdata',json={'access_token':'leak'}).status_code,403)

    def test_country_grouping_date_windows_and_no_repeat_expansion(self):
        run=self.plan(charts=False,countries=[])
        settings=run['settings'] | {'resolved_countries':['us','cn','th']}
        af.product_jobs(self.db,run['id'],settings,'987')
        af.product_jobs(self.db,run['id'],settings,'987')
        jobs=self.db.execute("SELECT params FROM marketdata_jobs WHERE kind='ranks'").fetchall()
        self.assertEqual(len(jobs),2)
        self.assertEqual(json.loads(jobs[0]['params'])['countries'],'us;cn;th')
        periods=list(af.windows('2025-01-01','2025-02-01'))
        self.assertEqual(periods,[('2025-01-02','2025-02-01'),('2025-01-01','2025-01-01')])
        self.assertEqual(af.record_count('estimates',{'2025-01-01':{'US':{'downloads':12},'CN':{'revenue':'4.0'}}}),2)

    def test_invalid_plan_rejected(self):
        for value in ({'stop_at':'bad'},{'countries':['us/secret']},{'daily_limit':True},{'kinds':['anything']}):
            with self.assertRaises(ValueError):self.plan(**value)

    def test_review_page_limit_splits_dates_without_gaps(self):
        run=self.plan(charts=False)
        params=dict(products='1',start='2025-01-01',end='2025-01-31',page=1,count=500)
        job=dict(kind='reviews',path='/reviews',params=json.dumps(params))
        af.expand(self.db,run,run['settings'],job,{'this_page':1,'pages':600,'reviews':[{'id':'1'}]})
        children=[json.loads(r['params']) for r in self.db.execute("SELECT params FROM marketdata_jobs WHERE kind='reviews' ORDER BY id").fetchall()]
        self.assertEqual([(c['start'],c['end']) for c in children],[('2025-01-01','2025-01-16'),('2025-01-17','2025-01-31')])

    def test_daily_refresh_retains_unvisited_chart_targets(self):
        run=self.plan(products=[],kinds=['metadata'],daily_charts=True)
        run=self.db.execute('SELECT * FROM marketdata_runs WHERE id=%s',(run['id'],)).fetchone()
        with patch.object(af,'catalog',side_effect=lambda db,cfg,r,k: {'US':{'iso':'US'}} if k=='countries' else {'71':{'id':71,'store':'google_play','subcategories':['free']}} if k=='categories' else {}):
            af.schedule(self.db,self.cfg,run)
            pending=self.db.execute("SELECT id FROM marketdata_jobs WHERE kind='charts'").fetchone()['id']
            fresh=self.db.execute('SELECT * FROM marketdata_runs WHERE id=%s',(run['id'],)).fetchone()
            with patch.object(af,'utc_day',return_value='2099-01-01'):af.schedule(self.db,self.cfg,fresh)
        charts=self.db.execute("SELECT id,status FROM marketdata_jobs WHERE kind='charts'").fetchall()
        self.assertEqual(charts,[{'id':pending,'status':'pending'}])

    def test_previous_day_retry_after_is_still_enforced(self):
        self.db.execute('INSERT INTO marketdata_budget VALUES(%s,1,1000,%s)',('2000-01-01',time.time()+300))
        client=Mock();client.source_profile=self.cfg['source_profiles']['marketdata']
        with self.assertRaises(af.SourceError):af.get(self.db,client,'/usage')
        client.get.assert_not_called()

    def test_api_archive_retains_exact_response_bytes(self):
        raw=b'{ "downloads": 1, "revenue": "0.20" }\n'
        identifier=af.archive(self.db,self.cfg,'estimates',json.loads(raw),{'run_id':1},1,raw_bytes=raw)
        self.assertEqual(af.artifact_path(self.db,self.cfg,identifier),raw)
