import base64
import io
import json
import time
import unittest
import zipfile
from unittest.mock import patch
from xml.sax.saxutils import escape
from fastapi.testclient import TestClient
from appmon import growth, keyword_discovery as kd, keyword_reports as kr, charts
from appmon.db import add_apps, enqueue, claim
from appmon.monitor import save_result
from appmon.stores import Result, FetchError
from appmon.keywords import add_targets, claim_target, save_search
from appmon.keyword_stores import SearchResult
from appmon.web import create_app
from test_monitor import MySQLTestCase


def workbook_bytes():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="ASO关键词" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        rows=[[0,'测试_关键词明细_中国_20261103对比20261102'],['关键词','排名','搜索指数','流行度'],['豆包',1,6924,97],['测试词','未覆盖',0,0]]
        xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        for n,row in enumerate(rows,1):
            xml+='<row>'
            for i,v in enumerate(row):
                ref=f'{chr(65+i)}{n}'
                xml+=f'<c r="{ref}"><v>{v}</v></c>' if isinstance(v,int) else f'<c r="{ref}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            xml+='</row>'
        z.writestr('xl/worksheets/sheet1.xml',xml+'</sheetData></worksheet>')
    return stream.getvalue()


class DiscoveryTest(MySQLTestCase):
    def setUp(self):
        super().setUp();self.m=dict(store='google',app_id='com.example.test',country='us',language='en')

    def test_unchanged_observations_record_each_day_without_inventing_install_counts(self):
        add_apps(self.db,[('google',self.m['app_id'],'Test',None)],'test');enqueue(self.db,['us'],'en')
        now=time.time()-10*86400
        for i in range(2):
            t=now+i*86400;job=claim(self.db,['us'],'en',60,t)
            save_result(self.db,self.cfg,job,Result('ok',{'name':'Test','ratings':100,'installs':100000},b'page'),now=t)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM snapshots').fetchone()['n'],1)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM app_metrics_daily').fetchone()['n'],2)
        self.assertIsNone(self.db.execute('SELECT installs_reported FROM app_metrics_daily LIMIT 1').fetchone()['installs_reported'])

    def test_growth_uses_real_counter_source_and_fixed_windows_not_country_downloads(self):
        now=time.time()-60
        for days,value in ((14,100),(7,120),(0,160)):
            growth.record(self.db,self.m,{'ratings':value,'installs_reported':value*10,'name':'Test','category':'FINANCE'},now-days*86400,'raw')
        r=growth.report(self.db,'google','us','en',7,'installs_reported',now)
        self.assertEqual(r['items'][0]['delta'],400);self.assertEqual(r['items'][0]['growth_pct'],100)
        self.assertEqual(r['scope'],'global_installs_observed_in_market')
        self.assertEqual(growth.report(self.db,'google','th','en',7,'ratings',now)['items'],[])
        self.assertEqual(growth.report(self.db,'apple','us','en',7,'installs_reported',now)['items'],[])
        growth.record(self.db,self.m,{'ratings':1,'installs_reported':1},now-120,'old')
        self.assertEqual(growth.report(self.db,'google','us','en',7,'ratings',now)['items'][0]['ratings'],160)
        growth.record(self.db,self.m,{'ratings':10},now+1,'correction')
        r=growth.report(self.db,'google','us','en',7,'ratings',now+1)['items'][0]
        self.assertEqual(r['status'],'counter_correction');self.assertIsNone(r['delta'])

    def test_coverage_excludes_stale_paused_failed_and_depth_changed_targets(self):
        now=time.time()
        for i in range(5):
            identifier=add_targets(self.db,[self.m|{'keyword':f'word{i}','depth':10}])['ids'][0]
            target=claim_target(self.db,60,now=now,target_id=identifier)
            save_search(self.db,self.cfg,target,SearchResult([{'app_id':self.m['app_id'],'name':'Test'}],b'result','fixture','v1','first_response'),now=now)
            if i==1:self.db.execute('UPDATE keyword_targets SET enabled=0 WHERE id=%s',(identifier,))
            if i==2:self.db.execute('UPDATE keyword_targets SET failures=1 WHERE id=%s',(identifier,))
            if i==3:self.db.execute('UPDATE keyword_targets SET depth=20 WHERE id=%s',(identifier,))
            if i==4:self.db.execute('UPDATE keyword_runs SET observed_at=%s WHERE target_id=%s',(now-3*86400,identifier))
        r=kd.coverage(self.db,self.m,self.m['app_id'])
        self.assertEqual(r['stats']['tracked'],5);self.assertEqual(r['stats']['fresh'],1)
        self.assertEqual(r['apps'][0]['covered'],1)
        self.assertEqual(kd.market_matrix(self.db,'google',self.m['app_id'])['items'][0]['covered'],1)

    def test_candidate_import_keeps_source_license_market_and_does_not_auto_collect(self):
        v={'markets':[self.m], 'words':['loan','loan','借款'],'source':'test','source_url':'https://example.org',
           'license':'CC BY-SA 4.0','attribution':'Example','source_period':'2018'}
        kd.import_candidates(self.db,v);kd.import_candidates(self.db,v)
        rows=kd.candidates(self.db,self.m)['items'];self.assertEqual(len(rows),2)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets').fetchone()['n'],0)
        result=kd.track(self.db,{'ids':[r['id'] for r in rows]});self.assertEqual(result['targets'],2)
        self.assertEqual(kd.track(self.db,{'ids':[rows[0]['id']]})['existing'],1)
        self.assertEqual(kd.candidates(self.db,self.m|{'country':'th'})['total'],0)

    def test_external_hot_snapshots_keep_source_scale_and_are_not_observed_coverage(self):
        value=self.m|{'kind':'hot_search','source':'test','source_url':'https://example.org','observed_at':time.time()-10,
            'score_unit':'provider index','items':[{'keyword':'loan','score':12}]}
        identifier=kd.import_signals(self.db,value)['id']
        self.assertTrue(kd.import_signals(self.db,value)['duplicate'])
        self.assertEqual(kd.signals(self.db,self.m)['selected']['id'],identifier)
        self.assertEqual(kd.coverage(self.db,self.m)['stats']['tracked'],0)
        with self.assertRaises(ValueError):kd.import_signals(self.db,value|{'items':[{'keyword':'loan','score':float('nan')}]})

    def test_frequency_download_uses_pinned_available_file_and_exact_market_language(self):
        response=unittest.mock.Mock(text='テスト 123\nアプリ 90\n')
        with patch('appmon.keyword_discovery.requests.get',return_value=response) as request:
            kd.import_frequency(self.db,{'language':'ja','limit':2,'markets':[self.m|{'country':'jp','language':'ja'}]})
            self.assertIn('/ja/ja_full.txt',request.call_args.args[0])
            self.assertIn(kd.FREQUENCY_SHA,request.call_args.args[0])
            with self.assertRaises(ValueError):kd.import_frequency(self.db,{'language':'ja','markets':[self.m]})

    def test_xlsx_report_preview_idempotence_mismatched_market_and_zero_preservation(self):
        value={'store':'apple','app_id':'6459478672','country':'cn','language':'zh-cn','filename':'test.xlsx',
               'content_base64':base64.b64encode(workbook_bytes()).decode()}
        p=kr.preview(value);self.assertEqual(p['keyword_rows'],2);self.assertEqual(p['inferred']['country'],'cn')
        with self.assertRaises(ValueError):kr.import_report(self.cfg,self.db,value|{'country':'us'})
        r=kr.import_report(self.cfg,self.db,value);self.assertTrue(kr.import_report(self.cfg,self.db,value)['duplicate'])
        rows=kr.report_rows(self.db,r['id'])['items'];self.assertIsNone(rows[1]['position']);self.assertEqual(rows[1]['search_index'],0)
        kr.promote(self.db,{'report_id':r['id'],'sheet':'ASO关键词','source_rows':[3]})
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_candidates').fetchone()['n'],1)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_ranks').fetchone()['n'],0)

    def test_http_validation_and_new_routes(self):
        with patch.dict('os.environ',{'APPMON_API_TOKEN':'test-discovery-token'}), TestClient(create_app(self.cfg),headers={'Authorization':'Bearer test-discovery-token'}) as client:
            for route in ('coverage','candidates','signals'):
                response=client.get('/api/discovery/keywords/'+route,params={'store':'google','country':'us','language':'en'})
                self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(client.get('/api/discovery/growth?store=google&days=6').status_code,400)
            self.assertEqual(client.post('/api/discovery/reports/preview',json={'content_base64':'bad'}).status_code,400)


class ChartHistoryTest(MySQLTestCase):
    def test_history_has_no_future_leak_and_does_not_infer_exit_from_shallower_results(self):
        from appmon.intelligence import add_observation_target
        t=add_observation_target(self.db,dict(kind='chart',store='google',country='us',language='en',collection='top-free'))['id']
        now=time.time()-100
        for at,ids in ((now-7*86400,['com.a.a','com.b.b','com.c.c']),(now,['com.b.b','com.d.d'])):
            self.db.execute("INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count) VALUES(%s,%s,'ok','test','v1','returned_chart_range',%s)",(t,at,len(ids)))
            run=self.db.cursor.lastrowid
            for n,app in enumerate(ids,1):self.db.execute('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',(run,app,n,json.dumps({'app_id':app})))
            self.db.execute('UPDATE observation_targets SET previous_success_id=latest_success_id,latest_success_id=%s WHERE id=%s',(run,t))
        r=charts.view(self.db,'google','us','en',as_of=now,compare_days=7)
        self.assertEqual(r['items'][0]['change'],1);self.assertEqual(r['items'][1]['entry_status'],'entered_range')
        self.assertEqual([x['app_id'] for x in r['exited_items']],['com.a.a'])
        old=charts.view(self.db,'google','us','en',as_of=now-86400)
        self.assertEqual(old['items'][0]['app_id'],'com.a.a')
