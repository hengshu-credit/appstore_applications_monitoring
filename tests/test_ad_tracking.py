import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from appmon import ad_sources, ad_tracking, ad_channels, ad_libraries, analytics, app_collection, collection, source_tasks, source_registry
from appmon.auth import requirements
from appmon.db import add_apps, connect, dump
from appmon import dispatcher, collection_tasks
from appmon.web import create_app
from appmon import google_ad_transparency
from test_monitor import OceanBaseTestCase


def public_page(value):
    values = []
    def encode(obj):
        index = len(values)
        values.append(None)
        if isinstance(obj, dict):
            obj = {k: encode(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            obj = [encode(v) for v in obj]
        values[index] = obj
        return index
    encode(value)
    return {'type':'data', 'nodes':[None, {'type':'data','data':values}]}


class AdParsersTest(unittest.TestCase):
    def test_platform_aliases_and_exact_store_links(self):
        self.assertEqual([ad_channels.canonical(v) for v in ['google ads','Facebook','INS','TikTok','reddit','yandex.direct','appstore']],
                         ['Google','Meta','Instagram','TikTok','Reddit','Yandex','App Store'])
        self.assertEqual(ad_channels.canonical('Pangle'),'Pangle')
        m = dict(store='google',app_id='com.example.one')
        url = 'https://play.google.com/store/apps/details?id=com.example.one'
        self.assertEqual(ad_channels.store_link_in([url],m),url)
        for wrong in (url+'2',url.replace('play.google.com','play.google.com.other.test'),url.replace('/store/','/other/')):
            self.assertIsNone(ad_channels.store_link_in([wrong],m))
        self.assertIsNone(ad_channels.store_destination('https://apps.apple.com/de/app/id1234',dict(store='apple',app_id='123')))

    def test_meta_platforms_are_reported_individually_without_snapshot_tokens(self):
        m = dict(store='apple',app_id='123',country='fr')
        row = dict(id='99',publisher_platforms=['facebook','instagram'],ad_creative_bodies=['App campaign'],
                   ad_creative_link_captions=['apps.apple.com/fr/app/example/id123'],ad_snapshot_url='https://x/?access_token=secret')
        call = Mock(return_value={'data':[row,row|{'id':'100','ad_creative_link_captions':['apps.apple.com/fr/app/id1234']}],
                                  'paging':{'next':'https://graph.facebook.com/?access_token=secret','cursors':{'after':'after'}}})
        data = ad_libraries.meta_page(call,m,0)
        self.assertEqual([a['channel'] for a in data['items']],['Meta','Instagram'])
        self.assertTrue(all(a['ad_id']=='99' and a['country']=='fr' for a in data['items']))
        self.assertEqual(data['next_page'],'after')
        self.assertNotIn('secret',str(data))

    def test_tiktok_matches_download_after_query_and_preserves_videos(self):
        m = dict(store='google',app_id='com.example.app',country='de')
        url = 'https://play.google.com/store/apps/details?id=com.example.app'
        def request(method,endpoint,params,json):
            self.assertEqual(method,'POST')
            if endpoint.endswith('/query/'):
                self.assertEqual(json['filters']['country_code_list'],['DE'])
                data = {'ads':[{'ad':{'id':11}},{'ad':{'id':12}}],'has_more':True,'search_id':'next'}
            else:
                ad = {'id':json['ad_id'],'download_url':url if json['ad_id']==11 else url+'2',
                      'title':'Campaign','first_shown_date':20260108,'videos':[{'url':'https://example.org/v.mp4'}]}
                data = {'ad':ad,'advertiser':{'business_name':'Advertiser'}}
            return {'error':{'code':'ok'},'data':data}
        page = ad_libraries.tiktok_page(request,m,0,'Example')
        self.assertEqual(page['next_page']['after'],'next')
        self.assertEqual([(a['ad_id'],a['first_seen'],a['assets'][0]['type']) for a in page['items']],[('11','2026-01-08','video')])

    def test_reddit_imports_matching_account_creative_and_validates_pagination(self):
        m=dict(store='apple',app_id='123')
        listing={'id':'12','post_id':'t3_abcd','effective_status':'ACTIVE'}
        creative={'type':'VIDEO','headline':'Video post','destination':{'url':'https://apps.apple.com/de/app/id123'},
                  'video':{'media':{'url':'https://example.org/creative.mp4'}}}
        ads={'data':[listing,listing|{'id':'13','effective_status':'REJECTED'}],
             'pagination':{'next_url':'https://ads-api.reddit.com/api/v3/ad_accounts/t2_xyz/ads?page.token=second'}}
        post={'data':{'id':'t3_abcd','creative':creative}}
        result=ad_libraries.reddit_page(Mock(side_effect=[ads,post]),m,0,'t2_xyz')
        self.assertEqual(result['next_page'],{'query':'page.token=second','account':'t2_xyz'})
        self.assertEqual([(a['channel'],a['country'],a['assets'][0]['type']) for a in result['items']],[('Reddit','','video')])
        ads['pagination']['next_url']='https://other.example/steal?page.token=test'
        with self.assertRaisesRegex(ValueError,'分页地址'):
            ad_libraries.reddit_page(Mock(side_effect=[ads,post]),m,0,'t2_xyz')

    def test_google_public_sample_keeps_domain_only_ads_out_of_app_totals(self):
        m=dict(store='google',app_id='com.example.app',country='de',language='de')
        def response(**values):
            from unittest.mock import MagicMock
            ctx=MagicMock(); reply=ctx.__enter__.return_value;reply.status_code=200
            if 'data' in values:reply.json.return_value=values['data']
            else:reply.iter_content.return_value=[values['body']]
            return ctx
        row={'1':'AR123','2':'CR456','12':'Publisher','4':1,'3':{'1':{'4':'https://displayads-formats.googleusercontent.com/ads/preview/content.js'}}}
        client=Mock()
        client.request.side_effect=[response(data={'1':[row,row|{'2':'CR457','3':{}}]}),
            response(body=b"'headline': 'Install' https://play.google.com/store/apps/details?id=com.example.app")]
        with patch.object(google_ad_transparency,'developer_domain',return_value='example.org'):
            result=google_ad_transparency.fetch(client,None,m)
        self.assertEqual([a['ad_id'] for a in result['items']],['CR456'])
        self.assertEqual([a['ad_id'] for a in result['candidates']],['CR457'])
        self.assertEqual(result['items'][0]['title'],'Install')

    def test_public_creatives_match_advertiser_keep_channels_and_unattributed_assets(self):
        m = dict(store='google', app_id='com.example.app')
        app = {'store_id':m['app_id'], 'store':'Google Play', 'ad_creative_count':2, 'developer_name':'Publisher'}
        row = {'md5_hash':'a'*32,'file_extension':'webm','ad_domain_company_name':'Google','run_at':'2026-01-04',
               'pubs':[{'store_id':'com.other.game','name':'Another game'}]}
        page = public_page({'myapp':app, 'creatives':{'by_creative':[{'md5_hash':'b'*32,'file_extension':'jpeg'}],
                           'by_vhash':[row, row | {'run_at':'2026-01-05'}, row | {'ad_domain_company_name':'Unity'}]}})
        ads = ad_sources.goblin_creatives(page,m,'https://example.org/evidence')['items']
        self.assertEqual([a['channel'] for a in ads],['Google','Unity',None])
        self.assertEqual(ads[0]['last_seen'],'2026-01-05')
        self.assertEqual((ads[0]['country'],ads[0]['publishers']),('', ['Another game']))
        self.assertNotIn('url',ads[0]['assets'][0])
        self.assertEqual(ads[0]['assets'][0]['type'],'video')
        with self.assertRaises(ValueError):
            ad_sources.goblin_creatives(page,m|{'app_id':'com.other.game'},'https://example.org')
        with self.assertRaises(ValueError):
            ad_sources.goblin_creatives({'type':'data','nodes':[]},m,'https://example.org')

    def test_rankings_have_ties_explicit_sample_and_missing_target(self):
        page = public_page({'creatives':[
            {'store_id':'one.app','unique_creatives':8,'unique_publishers':2},
            {'store_id':'two.app','unique_creatives':8,'unique_publishers':4},
            {'store_id':'three.app','unique_creatives':1,'unique_publishers':None}]})
        ranks = ad_sources.buyer_rankings(page,{'app_id':'one.app'})['ranks']
        self.assertEqual(ranks['unique_creatives'],{'position':1,'sample':3,'value':8})
        self.assertEqual(ranks['unique_publishers'],{'position':2,'sample':2,'value':2})
        self.assertFalse(ad_sources.buyer_rankings(page,{'app_id':'other.app'})['listed'])

    def test_apple_market_identity_dates_media_and_page_number(self):
        m = dict(store='apple',app_id='123',country='de')
        row = {'appId':123,'countryOrRegion':'DE','adId':'ad/1','placement':'APPSTORE_SEARCH_RESULTS',
               'adBanner':{'subtitle':'Creative','promotionalText':'Copy','iconPictureUrl':'https://img.example/icon'},
               'adAssets':[{'videoUrl':'https://video.example/movie','pictureUrl':'null'}],
               'lastImpressionDate':'2026-01-23 00:00:00.0'}
        def get(url, params):
            self.assertEqual((url,params['offset']), (ad_sources.APPLE, 1))
            self.assertIn('countryOrRegion=in=(DE)', params['ql'])
            return {'data':[row],'pagination':{'totalResults':51,'startIndex':1,'itemsPerPage':50}}
        result = ad_sources.apple_page(get,m,1)
        self.assertIsNone(result['next_page'])
        ad = result['items'][0]
        self.assertEqual((ad['channel'],ad['body'],ad['last_seen']),('App Store','Copy','2026-01-23'))
        self.assertEqual([a['type'] for a in ad['assets']],['video','image'])
        self.assertIn('ad%2F1',ad['evidence_url'])
        with self.assertRaises(ValueError):
            ad_sources.apple_ad(row,m|{'country':'fr'})
        self.assertNotIn('ad_apple',ad_sources.available(m|{'country':'us'}))


class AppAdTrackingTest(OceanBaseTestCase):
    def setUp(self):
        super().setUp()
        self.m = dict(store='apple', app_id='123', country='de', language='de')
        add_apps(self.db,[('apple','123','Example',None)],'fixture')

    def test_source_queue_checkpoints_and_retries_retain_evidence_and_last_success(self):
        m = self.m
        task = source_tasks.enqueue(self.db,'ad_apple',m,source='app-ads',submitted_by=1)
        def page(db,cfg,kind,spec,index,*,request_guard):
            request_guard()
            ad = ad_sources.apple_ad({'appId':123,'countryOrRegion':'DE','adId':str(index),
                                      'placement':'APPSTORE_SEARCH_RESULTS','adBanner':{'subtitle':'Ad '+str(index)}},m)
            return {'items':[ad],'next_page':1 if index==0 else None,'total':2,'period_end':'2026-01-10'}
        with patch('appmon.ad_sources.fetch',side_effect=page):
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],1)
            first = ad_tracking.report(self.db,m)
            self.assertEqual(first['total'],1)
            apple = next(s for s in first['sources'] if s['kind']=='ad_apple')
            self.assertEqual(apple['progress'],{'returned':1,'total':2})
            self.assertIsNone(apple['success_at'])
            with patch('appmon.ad_sources.fetch',side_effect=ValueError('temporary network failure')):
                self.assertEqual(source_tasks.work(self.db,self.cfg)['error'],1)
            self.db.execute('UPDATE collection_source_tasks SET due=0 WHERE id=%s',(task['id'],))
            self.assertEqual(source_tasks.work(self.db,self.cfg)['ok'],1)
            self.db.execute('UPDATE collection_source_tasks SET due=0 WHERE id=%s',(task['id'],))
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],1)
            with patch('appmon.ad_sources.fetch',side_effect=ValueError('retry later')):
                source_tasks.work(self.db,self.cfg)
            state = ad_tracking.report(self.db,m)
            self.assertEqual(state['total'],2)
            apple = next(s for s in state['sources'] if s['kind']=='ad_apple')
            self.assertEqual((apple['status'],apple['result']['returned'],apple['progress']['returned']),('error',2,1))
            self.assertEqual(ad_tracking.report(self.db,m|{'country':'fr'})['total'],0)

    def test_ad_tab_api_filters_legacy_and_global_evidence_and_scopes_owned_reports(self):
        m = self.m
        legacy = m | dict(source='Field observation', keyword='app', observed_at=time.time(), evidence_url='https://example.org/ad')
        rich = legacy | dict(channel='Meta',title='New ad',body='Concrete copy',creative_type='image',
                             creative_url='https://example.org/img',landing_url='https://example.org/landing')
        analytics.import_ad_observations(self.db,[legacy,rich,rich|{'app_id':'124'},rich|{'country':'fr'}])
        with self.assertRaises(ValueError):
            analytics.import_ad_observations(self.db,[rich|{'landing_url':'javascript:example'}])
        ad = dict(country='',source='AppGoblin',channel='Google',ad_id='abcd',placement='应用内广告',evidence_url='https://example.org/2')
        ad_tracking.save_page(self.db,'ad_creatives',m,{'items':[ad]},{},{})
        report = dict(report_date='2026-01-01',app_id='123',country='de',campaign='Test',keyword='app',
                      impressions=10,taps=1,installs=1,spend='2.10',currency='EUR',source='owned')
        analytics.import_ad_reports(self.db,[report,report|{'campaign':'wrong market','country':'fr'},report|{'app_id':'124'}])
        client = self.login_admin(TestClient(create_app(self.cfg)))
        path = '/api/ads/apps/apple/123'
        query = dict(country='de',language='de')
        result = client.get(path,params=query).json()
        self.assertTrue(set(ad_channels.PRIMARY) <= {c['channel'] for c in result['channels']})
        self.assertEqual({s['kind'] for s in result['sources'] if s['status']=='needs_auth'},set(ad_channels.SOCIAL))
        self.assertEqual((result['total'],len(result['items']),len(result['reports'])),(3,3,1))
        item = client.get(path,params=query|{'channel':'Meta'}).json()['items'][0]
        self.assertEqual((item['body'],item['assets'][0]['url'],item['landing_url']),('Concrete copy',rich['creative_url'],rich['landing_url']))
        self.assertEqual(client.get(path,params=query|{'channel':'渠道未披露'}).json()['total'],1)
        self.assertEqual(client.get(path,params=query|{'offset':12}).json()['items'],[])
        self.assertEqual(client.get(path,params=query|{'country':'us'}).json()['total'],1)
        first = client.post(path+'/collect',json=query).json()['tasks']
        again = client.post(path+'/collect',json=query).json()['tasks']
        self.assertEqual([t['id'] for t in again],[t['id'] for t in first])
        elsewhere = client.post(path+'/collect',json=query|{'country':'us','language':'en'}).json()['tasks']
        self.assertEqual([t['id'] for t in elsewhere[:2]],[t['id'] for t in first[:2]])
        self.assertNotIn('ad_apple',[t['kind'] for t in elsewhere])
        self.assertEqual(requirements(path,'GET'),['ads:read'])
        self.assertEqual(set(requirements(path+'/collect','POST')),{'ads:write','collection:write'})

    def test_changed_creative_versions_preserve_previous_copy_and_ignore_signed_url_refresh(self):
        ad=dict(ad_id='91',source='TikTok 商业内容库',channel='TikTok',country='de',placement='TikTok',
                evidence_url='https://example.org/ad',title='First',assets=[{'type':'video','url':'https://example.org/v?x-signature=a'}])
        for item in (ad,ad|{'assets':[{'type':'video','url':'https://example.org/v?x-signature=b'}]}, ad|{'title':'Revised'}):
            ad_tracking.save_page(self.db,'ad_tiktok',self.m,{'items':[item]},{},{})
        report=ad_tracking.report(self.db,self.m)
        self.assertEqual(report['total'],1)
        item=report['items'][0]
        self.assertEqual(item['version_count'],2)
        history=ad_tracking.versions(self.db,self.m,item['id'],0)['items']
        self.assertEqual([r['title'] for r in history],['Revised','First'])
        with self.assertRaises(ValueError):
            ad_tracking.versions(self.db,self.m|{'country':'fr'},item['id'],0)

    def test_full_collection_excludes_ads_until_user_explicitly_submits(self):
        for name in ad_channels.SOCIAL:
            value = {'access_token':'test-'+name}
            if name=='ad_reddit':value['ad_account_id']='t2_abc'
            settings = source_registry.save(self.db,self.cfg,name,value)
            self.assertTrue(settings['account']['configured'])
            self.assertNotIn('test-'+name,str(settings))
            self.assertEqual(source_registry.ads_credentials(self.db,self.cfg,name),value)
        client = self.login_admin(TestClient(create_app(self.cfg)))
        plan = client.post('/api/apps/apple/123/collect',json=self.m|{'include_ads':True}).json()
        while collection.work_plans(self.db)['status']!='complete':
            pass
        tasks = [t for t in app_collection.report(self.db,plan['id'])['tasks'] if t['kind'] in ad_sources.KINDS]
        self.assertEqual(tasks,[])
        self.assertIn('chart', {t['kind'] for t in plan['tasks']})
        with patch('appmon.ad_sources.fetch') as fetch:
            self.assertIsNone(client.get('/api/ads/apps/apple/123',params=self.m).json()['schedule'])
            ad_tracking.schedule_due(self.db)
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],0)
            fetch.assert_not_called()
        response = client.post('/api/ads/apps/apple/123/collect',json=self.m|{'mode':'once'})
        self.assertEqual(response.status_code,200,response.text)
        explicit = response.json()
        self.assertEqual({t['kind'] for t in explicit['tasks']}, set(ad_sources.KINDS))
        self.assertIsNone(ad_tracking.schedule_for(self.db,self.m))
        with patch('appmon.ad_sources.fetch',side_effect=self.empty_source):
            for _ in explicit['tasks']:
                self.assertEqual(source_tasks.work(self.db,self.cfg)['ok'],1)
            ad_tracking.schedule_due(self.db)
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],0)
        self.assertTrue(all(source_tasks.receipt(self.db,t['id'])['due'] is None for t in explicit['tasks']))

    @staticmethod
    def empty_source(db,cfg,kind,m,page,*,request_guard):
        request_guard()
        return {'listed':False,'sample_size':20,'ranks':{}} if kind=='ad_rankings' else {'items':[]}

    def test_periodic_submission_update_nonoverlapping_rounds_and_stop(self):
        client = self.login_admin(TestClient(create_app(self.cfg)))
        path='/api/ads/apps/apple/123'
        before=time.time()
        spec=self.m|{'mode':'periodic','refresh_hours':2}
        for invalid in ({'mode':'auto'},{'mode':'periodic'},{'mode':'periodic','refresh_hours':0},{'mode':'once','refresh_hours':2}):
            self.assertEqual(client.post(path+'/collect',json=self.m|invalid).status_code,400)
        self.assertIsNone(ad_tracking.schedule_for(self.db,self.m))
        first=client.post(path+'/collect',json=spec|{'submitted_by':12345678}).json()
        saved=first['schedule'];self.assertTrue(saved['enabled'])
        self.assertEqual((saved['refresh_hours'],saved['run_number']),(2,1))
        self.assertGreaterEqual(saved['next_due'],before+7200)
        actor=self.db.execute('SELECT submitted_by FROM ad_tracking_schedules').fetchone()['submitted_by']
        self.assertEqual(actor,client.get('/api/auth/me').json()['user']['id'])
        same=client.post(path+'/collect',json=spec).json()
        self.assertEqual((same['schedule'],same['tasks']),(saved,[]))
        updated=client.post(path+'/collect',json=spec|{'refresh_hours':6}).json()
        self.assertEqual(updated['schedule']['id'],saved['id'])
        self.assertEqual(updated['tasks'],[])
        self.assertGreaterEqual(updated['schedule']['next_due'],before+21600)
        self.assertIsNone(ad_tracking.schedule_for(self.db,self.m|{'country':'fr'}))
        # A later credential change alone must not expand an existing schedule.
        source_registry.save(self.db,self.cfg,'ad_meta',{'access_token':'test-meta-token'})
        self.db.execute('UPDATE ad_tracking_schedules SET next_due=0')
        ad_tracking.schedule_due(self.db)
        self.assertEqual(ad_tracking.schedule_for(self.db,self.m)['run_number'],1)
        with patch('appmon.ad_sources.fetch',side_effect=self.empty_source):
            for _ in first['tasks']:
                self.assertEqual(source_tasks.work(self.db,self.cfg)['ok'],1)
        def tick(_):
            db=connect(self.cfg)
            try:ad_tracking.schedule_due(db)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(tick,range(3)))
        second=client.get(path,params=self.m).json()['schedule']
        self.assertEqual(second['run_number'],2)
        count=self.db.execute('SELECT COUNT(*) n FROM collection_source_tasks').fetchone()['n']
        self.assertEqual(count,2*len(first['tasks']))
        self.assertIsNone(self.db.execute("SELECT id FROM collection_source_tasks WHERE kind='ad_meta'").fetchone())
        pending=self.db.execute('SELECT id FROM collection_source_tasks WHERE due IS NOT NULL').fetchall()
        with patch('appmon.ad_sources.fetch',side_effect=self.empty_source):
            self.assertEqual(dispatcher.dispatch_once(self.db,self.cfg,'ad-test')['ok'],1)
        once=client.post(path+'/collect',json=self.m).json()['tasks']
        stopped=client.delete(path+'/schedule',params=self.m).json()['schedule']
        self.assertFalse(stopped['enabled']);self.assertIsNone(stopped['next_due'])
        self.assertTrue(all(source_tasks.receipt(self.db,r['id'])['status'] in ('done','stopped') for r in pending))
        self.assertTrue(all(source_tasks.receipt(self.db,t['id'])['status']=='pending' for t in once))
        restarted=client.post(path+'/collect',json=spec).json()['schedule']
        self.assertEqual(restarted['run_number'],1)
        self.assertTrue(all(source_tasks.receipt(self.db,r['id'])['status'] in ('done','stopped') for r in pending))
        self.assertEqual(set(requirements('/api/ads/apps/{store}/{app_id}/schedule','DELETE')),{'ads:write','collection:write'})

    def test_implicit_legacy_receipts_cannot_be_dispatched_or_batched_with_new_manual_tasks(self):
        m=self.m;kind='ad_creatives';actions=[{'kind':kind,'value':m},{'kind':'ad_apple','value':m}]
        plan=self.db.execute('''INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at)
            VALUES('legacy',%s,%s,2,1,1)''',(dump({'app_request':m}),dump(actions))).lastrowid
        old=self.db.execute('''INSERT INTO collection_source_tasks(store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at)
            VALUES(%s,%s,%s,%s,%s,'legacy',%s,%s,'collection-plan',%s,1)''',
            (*[m[k] for k in ad_tracking.MARKET_FIELDS],kind,dump(m),source_tasks.batch_key(kind,m),f'{plan}:0')).lastrowid
        with patch('appmon.ad_sources.fetch') as fetch:
            self.assertEqual(collection.work_plans(self.db)['status'],'complete')
            report=app_collection.report(self.db,plan)
            self.assertEqual((report['state'], report['skipped'],report['finished']),('complete',2,2))
            self.assertEqual(source_tasks.receipt(self.db,old)['status'],'stopped')
            self.assertFalse(dispatcher.ready(self.db,self.cfg,'sources',time.time()))
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],0)
            fetch.assert_not_called()
        with self.assertRaisesRegex(ValueError,'用户'):
            collection.enqueue(self.db,{'source':'app-ads','ad_requested_by':7,'tasks':[m|{'kind':kind}]})
        explicit=collection.enqueue(self.db,{'source':'external','tasks':[m|{'kind':kind}]},ad_requested_by=1)
        self.assertEqual(collection.work_plans(self.db)['status'],'complete')
        upgraded=connect(self.cfg,initialize=True)
        upgraded.close()
        self.assertFalse(self.db.execute('SELECT enabled FROM collection_source_tasks WHERE id=%s',(old,)).fetchone()['enabled'])
        collection_tasks.control_tasks(self.db,{'action':'resume','scope':'filtered','filters':{'kind':kind}})
        with patch('appmon.ad_sources.fetch',side_effect=self.empty_source):
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],1)
        self.assertIsNone(source_tasks.receipt(self.db,old)['result'])
        self.assertEqual(app_collection.report(self.db,explicit['id'],allow_plan=True)['state'],'complete')
        self.assertEqual(collection_tasks.control_tasks(self.db,{'action':'refresh','tasks':[{'resource':'sources','id':old}]})['skipped'],1)

    def test_failed_submission_rolls_back_and_stopping_fences_a_claimed_run(self):
        spec=self.m|{'mode':'periodic','refresh_hours':24}
        original=source_tasks.enqueue
        def fail_second(db,kind,*args,**kwargs):
            if kind=='ad_rankings':raise RuntimeError('simulated intake interruption')
            return original(db,kind,*args,**kwargs)
        with patch('appmon.source_tasks.enqueue',side_effect=fail_second), self.assertRaises(RuntimeError):
            ad_tracking.enqueue(self.db,spec,submitted_by=1)
        self.assertIsNone(ad_tracking.schedule_for(self.db,self.m))
        self.assertIsNone(self.db.execute('SELECT id FROM collection_source_tasks').fetchone())
        submitted=ad_tracking.enqueue(self.db,spec,submitted_by=1)
        def stop_during_fetch(db,cfg,kind,m,page,*,request_guard):
            request_guard()
            ad_tracking.stop_schedule(db,m)
            request_guard()
            self.fail('stopped run admitted an additional HTTP request')
        with patch('appmon.ad_sources.fetch',side_effect=stop_during_fetch):
            self.assertEqual(source_tasks.work(self.db,self.cfg)['processed'],0)
        self.assertTrue(all(source_tasks.receipt(self.db,t['id'])['status']=='stopped' for t in submitted['tasks']))
        self.assertIsNone(self.db.execute('SELECT id FROM ad_observations').fetchone())

    def test_collection_only_user_cannot_submit_ads_by_forging_origin(self):
        api=create_app(self.cfg)
        admin=self.login_admin(TestClient(api));client=TestClient(api)
        account={'username':'collector-user','password':'test-user-password'}
        self.assertEqual(client.post('/api/auth/register',json=account).status_code,200)
        self.assertEqual(admin.put('/api/auth/admin/roles/user',json={'permissions':['ads:read','collection:write']}).status_code,200)
        session=client.post('/api/auth/login',json=account).json()
        client.headers['X-CSRF-Token']=session['csrf_token']
        forged=self.m|{'source':'app-ads','submitted_by':1,'mode':'periodic','refresh_hours':24}
        self.assertEqual(client.post('/api/ads/apps/apple/123/collect',json=forged).status_code,403)
        self.assertEqual(client.delete('/api/ads/apps/apple/123/schedule',params=self.m).status_code,403)
        self.assertEqual(client.post('/api/research/collection/enqueue',json={
            'source':'app-ads','ad_requested_by':1,'tasks':[self.m|{'kind':'ad_apple'}]}).status_code,403)
        self.assertIsNone(ad_tracking.schedule_for(self.db,self.m))
        self.assertIsNone(self.db.execute('SELECT id FROM collection_source_tasks').fetchone())

    def test_explicit_replay_of_legacy_api_request_reuses_success_and_queues_only_missing_ads(self):
        m=self.m
        spec={'source':'console-api','request_id':'older-submission','tasks':[m|{'kind':k} for k in ('ad_creatives','ad_rankings')]}
        actions=[{'kind':t['kind'],'value':m} for t in spec['tasks']]
        plan=self.db.execute('''INSERT INTO collection_plans(name,specification,actions,total,completed,status,created_at,updated_at,
            request_source,request_id) VALUES('old API',%s,%s,2,2,'complete',1,1,%s,%s)''',
            (dump({'input_source':spec['source'],'request_id':spec['request_id']}),dump(actions),spec['source'],spec['request_id'])).lastrowid
        completed=self.db.execute('''INSERT INTO collection_source_tasks(store,app_id,country,language,kind,label,payload,batch_key,
            source,request_id,created_at,due,success_at,result) VALUES(%s,%s,%s,%s,'ad_creatives','old',%s,%s,'collection-plan',%s,1,NULL,42,'{}')''',
            (*[m[k] for k in ad_tracking.MARKET_FIELDS],dump(m),source_tasks.batch_key('ad_creatives',m),f'{plan}:0')).lastrowid
        for _ in range(2):
            receipt=collection.enqueue(self.db,spec,ad_requested_by=9)
            self.assertTrue(receipt['reused']);self.assertEqual(receipt['id'],plan)
        tasks=app_collection.report(self.db,plan,allow_plan=True)['tasks']
        self.assertEqual([(t['kind'],t['state']) for t in tasks],[('ad_creatives','complete'),('ad_rankings','pending')])
        self.assertEqual(tasks[0]['ref']['id'],completed)
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM collection_source_tasks WHERE source='user-ad-plan'").fetchone()['n'],2)
