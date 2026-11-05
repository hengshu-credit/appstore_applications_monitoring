import json
import os
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon.db import add_apps, dump, connect
from appmon.aso import check_metadata, save_draft, draft_detail, workspace
from appmon.review_desk import desk, review_history
from appmon.insights import event_feed, add_annotation, save_comparison, comparison_detail, developers, developer_apps
from appmon.collection import preview_plan, create_plan, work_plans, quality
from appmon.web import create_app
from test_monitor import MySQLTestCase


class MetadataRulesTest(unittest.TestCase):
    def test_apple_keywords_use_bytes_and_duplicate_checks_not_fake_volume(self):
        r=check_metadata('apple',{'name':'Test','keywords':'贷款'*17,'subtitle':'','description':'Example'},['贷款','missing'])
        count=next(v for v in r['counters'] if v['field']=='keywords')
        self.assertEqual((count['count'],count['limit']),(102,100))
        self.assertFalse(r['valid'])
        self.assertEqual(r['coverage'][0]['fields'],['keywords'])
        self.assertEqual(r['coverage'][1]['fields'],[])
        duplicate=check_metadata('apple',{'name':'Test','keywords':'bank,BANK','description':'Example'})
        self.assertTrue(any(x['message']=='关键词重复' for x in duplicate['findings']))
        self.assertNotIn('volume',r)

    def test_google_character_limit_and_store_specific_fields(self):
        r=check_metadata('google',{'name':'界'*31,'summary':'😀'*80,'description':'x'})
        self.assertFalse(r['valid'])
        self.assertEqual(next(c['count'] for c in r['counters'] if c['field']=='summary'),80)
        with self.assertRaises(ValueError):check_metadata('google',{'keywords':'private'})


class WorkbenchTest(MySQLTestCase):
    def seed_listing(self,app_id='123',**updates):
        now=time.time();store=updates.pop('store','apple');country=updates.pop('country','us');language=updates.pop('language','en')
        p={'name':'Observed app','description':'Observed description','category':'Finance','version':'1',
           'developer':'Developer','developer_id':'d1','rating':4.5,'ratings':100,'price':0,'currency':'USD','updated':'2026-10-20T00:00:00Z'}|updates
        add_apps(self.db,[(store,app_id,p['name'],p['category'])],'fixture')
        self.db.execute('INSERT INTO listings VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(store,app_id,country,language,'available',now,now,0,dump(p),'digest'))
        self.db.execute('INSERT INTO snapshots(store,app_id,country,language,observed_at,digest,raw_path,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(store,app_id,country,language,now,'digest','responses/example.gz',dump(p)))
        return dict(store=store,app_id=app_id,country=country,language=language)

    def seed_review(self,review_id='r1',rating=2,**extra):
        m=dict(store='google',app_id='com.example.app',country='us',language='en')
        now=time.time();p={'id':review_id,'rating':rating,'content':'App crashes','version':'1',**extra}
        self.db.execute('INSERT INTO reviews VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (m['store'],m['app_id'],m['country'],m['language'],review_id,rating,p['content'],'','1','2026-11-03',now,now,dump(p)))
        return m|{'review_id':review_id}

    def test_draft_versions_conflict_and_private_keywords_not_inferred(self):
        m=self.seed_listing()
        loaded=workspace(self.db,m)
        self.assertEqual(loaded['metadata']['keywords'],'')
        self.assertIn('keywords',loaded['unavailable_fields'])
        first=save_draft(self.db,m|{'name':'Draft','metadata':{'name':'Title','keywords':'bank','description':'Text'}})
        second=save_draft(self.db,m|{'id':first['id'],'revision':1,'name':'Draft','metadata':{'name':'Updated','keywords':'loan','description':'Text'}})
        self.assertEqual(second['revision'],2)
        self.assertFalse(second['published'])
        with self.assertRaisesRegex(ValueError,'已被更新'):
            save_draft(self.db,m|{'id':first['id'],'revision':1,'metadata':{'name':'stale'}})
        report=draft_detail(self.db,first['id'])
        self.assertEqual([v['metadata']['name'] for v in report['versions']],['Updated','Title'])
        self.assertIsNotNone(report['baseline_snapshot_id'])
        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM listings').fetchone()['payload'])['name'],'Observed app')

    def test_review_archive_ignores_legacy_workflow_and_disables_writes(self):
        key=self.seed_review(reply=None,thumbs_up=1)
        identity=tuple(key[k] for k in ('store','app_id','country','language','review_id'))
        self.db.execute('INSERT INTO review_workflow VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (*identity,'closed','Legacy owner',dump(['old']), 'Historical note','Historical draft','d'*64,1,time.time()))
        before=self.db.execute('SELECT * FROM review_workflow').fetchone()
        report=desk(self.db,key)
        self.assertEqual(report['summary']['apps'],1)
        row=report['items'][0]
        self.assertEqual(row['content'],'App crashes')
        self.assertEqual(row['public_reply_status'],'not_observed')
        self.assertTrue({'workflow_status','owner','labels','note','reply_draft','revision'}.isdisjoint(row))
        self.db.execute("UPDATE reviews SET content='Updated review',rating=1")
        self.assertEqual(desk(self.db,key)['items'][0]['content'],'Updated review')
        with patch.dict(os.environ,{'APPMON_API_TOKEN':''}):client=self.login_admin(TestClient(create_app(self.cfg)))
        self.assertEqual(client.post('/api/research/reviews/workflow',json=key|{'status':'open','revision':1}).status_code,404)
        self.assertEqual(client.get('/api/research/reviews',params=key).json()['items'][0]['content'],'Updated review')
        schema=client.get('/api/openapi.json').json()
        self.assertNotIn('/api/research/reviews/workflow',schema['paths'])
        params={p['name'] for p in schema['paths']['/api/research/reviews']['get']['parameters']}
        self.assertTrue({'status','label'}.isdisjoint(params))
        from appmon.exports import request_export
        with self.assertRaisesRegex(ValueError,'Invalid export kind'):
            request_export(self.db,'review_workflow',{})
        self.assertEqual(request_export(self.db,'reviews',{'app_id':key['app_id']})['status'],'queued')
        self.assertEqual(self.db.execute('SELECT * FROM review_workflow').fetchone(),before)

    def test_collected_reviews_keep_revisions_with_exact_market_and_app_scope(self):
        from appmon.intelligence import add_observation_target, claim_observation, save_observation
        from appmon.observation_stores import ObservationResult
        key=dict(store='google',app_id='com.example.app',country='th',language='th',review_id='r1')
        add_observation_target(self.db,dict(kind='reviews',refresh_hours=1,**{k:v for k,v in key.items() if k!='review_id'}))
        for now,content in [(100,'original'),(200,'edited')]:
            self.db.execute('UPDATE observation_targets SET due=0')
            target=claim_observation(self.db,60,now)
            result=ObservationResult([dict(id='r1',rating=2,content=content,version='1')],b'fixture','fixture','recent')
            self.assertTrue(save_observation(self.db,self.cfg,target,result,now=now))
        self.assertEqual(desk(self.db,key)['items'][0]['content'],'edited')
        self.assertEqual([r['payload']['content'] for r in review_history(self.db,key)['items']],['edited','original'])
        self.assertEqual(review_history(self.db,key|{'country':'us'})['items'],[])
        self.assertEqual(review_history(self.db,key|{'app_id':'com.another.app'})['items'],[])
        self.assertEqual(self.db.execute('SELECT count(*) n FROM review_workflow').fetchone()['n'],0)
        from appmon.auth import requirements
        self.assertEqual(requirements('/api/research/reviews/history','POST'),['reviews:read'])

    def test_review_reply_unknown_is_not_unreplied_and_statistics_share_filters(self):
        key=self.seed_review('r1',1)
        self.seed_review('r2',5,reply='Developer response')
        self.assertEqual(desk(self.db,key|{'rating':1})['items'][0]['public_reply_status'],'unknown')
        low=desk(self.db,key|{'rating':1,'q':'crashes'})
        self.assertEqual(low['summary']['total'],1)
        self.assertEqual(float(low['summary']['mean_review_rating']),1)
        self.assertEqual(low['ratings'],[{'rating':1,'count':1}])
        from appmon.scoped_views import reviews as review_records
        all_stores=review_records(self.db,dict(store='all',country='us',language='en'))
        self.assertEqual((all_stores['summary']['apps'],all_stores['summary']['total']),(1,2))
        self.assertEqual(float(all_stores['summary']['mean_review_rating']),3)


    def test_collection_preview_no_side_effects_and_replay_preserves_pause(self):
        value=dict(name='Research',store='google',country='th',language='th',app_ids=['com.a.app','com.a.app'],keywords=['เงินกู้'],details=True,reviews=True,chart=True,category='FINANCE',refresh_hours=6)
        preview=preview_plan(value)
        self.assertEqual(preview['total'],4)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM watchlist').fetchone()['n'],0)
        saved=create_plan(self.db,value)
        work_plans(self.db,limit=1)
        self.assertEqual(self.db.execute('SELECT completed FROM collection_plans').fetchone()['completed'],1)
        self.db.execute('UPDATE watchlist SET enabled=0')
        self.db.execute("UPDATE collection_plans SET completed=0,status='queued'")
        work_plans(self.db)
        self.assertEqual(self.db.execute('SELECT enabled FROM watchlist').fetchone()['enabled'],0)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM observation_targets').fetchone()['n'],2)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets').fetchone()['n'],1)
        self.assertEqual(self.db.execute('SELECT status FROM collection_plans WHERE id=%s',(saved['id'],)).fetchone()['status'],'complete')

    def test_plan_partial_failure_checkpoint_and_resume(self):
        value=dict(store='google',country='us',language='en',name='Plan',app_ids=['com.a.app'],keywords=[],details=True,reviews=True)
        create_plan(self.db,value)
        with patch('appmon.collection.add_observation_target',side_effect=ValueError('broken')):
            self.assertEqual(work_plans(self.db)['error'],1)
        row=self.db.execute('SELECT * FROM collection_plans').fetchone()
        self.assertEqual((row['status'],row['completed']),('error',1))
        self.db.execute("UPDATE collection_plans SET status='queued'")
        self.assertEqual(work_plans(self.db)['status'],'complete')
        self.assertEqual(self.db.execute('SELECT count(*) n FROM watchlist').fetchone()['n'],1)
        self.assertEqual(quality(self.db)['summary']['listed'],2)

    def test_quality_keeps_failed_attempt_time_separate_from_last_success(self):
        from appmon.intelligence import watch
        m=self.seed_listing('com.example.app',store='google')
        watch(self.db,m)
        attempted=time.time()+10
        self.db.execute('INSERT INTO requests(store,app_id,country,language,observed_at,outcome,error) VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (m['store'],m['app_id'],m['country'],m['language'],attempted,'error','HTTP 429'))
        self.db.execute("UPDATE jobs SET failures=1,last_error='HTTP 429'")
        row=quality(self.db)['targets'][0]
        self.assertEqual(row['attempt_at'],attempted)
        self.assertLess(row['success_at'],row['attempt_at'])
        self.assertTrue(row['stale'])
        self.assertEqual(row['returned_count'],1)

    def test_plan_lock_prevents_parallel_expansion_and_bad_inputs_are_rejected(self):
        other=connect(self.cfg)
        try:
            with self.db.named_lock('collection-plans'):
                self.assertEqual(work_plans(other)['processed'],0)
        finally:other.close()
        for value in [dict(store='apple',country='us',language='en',chart=True,category='APPLICATION'),
                      dict(store='google',country='us',language='en',featured=True),
                      dict(store='google',country='us',language='en',details=True,app_ids=['bad'])]:
            with self.assertRaises(ValueError):preview_plan(value)

    def test_comparison_sets_are_market_scoped_and_revision_protected(self):
        first=self.seed_listing('123');second=self.seed_listing('456',name='Competitor')
        saved=save_comparison(self.db,{'name':'Set','members':[first|{'role':'own'},second]})
        report=comparison_detail(self.db,saved['id'])
        self.assertEqual(len(report['report']['apps']),2)
        self.assertEqual(report['set']['members'][0]['role'],'own')
        with self.assertRaises(ValueError):save_comparison(self.db,{'name':'Bad','members':[first,second|{'country':'th'}]})
        with self.assertRaises(ValueError):save_comparison(self.db,{'name':'Bad','members':[first,first]})
        with self.assertRaises(ValueError):save_comparison(self.db,{'id':saved['id'],'revision':0,'name':'Stale','members':[first,second]})

    def test_developer_id_groups_localized_names_and_keeps_distinct_ids(self):
        self.seed_listing('123',developer='Developer EN',developer_id='same')
        self.seed_listing('456',developer='开发者',developer_id='same',country='cn')
        self.seed_listing('789',developer='Developer EN',developer_id='different')
        rows=developers(self.db,'apple')
        self.assertEqual(len(rows),2)
        same=next(r for r in rows if r['developer_id']=='same')
        self.assertEqual(same['apps'],2)
        self.assertEqual(len(developer_apps(self.db,'apple',same['name'],'same')['items']),2)

    def test_events_separate_observations_and_annotations_and_filter_market(self):
        m=self.seed_listing();now=time.time()
        for country in ('us','th'):
            self.db.execute('INSERT INTO events(store,app_id,country,language,observed_at,kind,changes) VALUES(%s,%s,%s,%s,%s,%s,%s)',('apple','123',country,'en',now,'metadata_changed',dump({'version':{'old':'1','new':'2'}})))
        add_annotation(self.db,m|{'event_at':now,'title':'Release note','note':'Hypothesis'})
        self.assertEqual(len(event_feed(self.db,m)['items']),1)
        self.assertEqual(len(event_feed(self.db,m)['annotations']),1)
        self.assertEqual(event_feed(self.db,m|{'field':'price'})['items'],[])
        self.assertEqual(event_feed(self.db,m|{'field':'version'})['annotations'],[])

    def test_api_advanced_filters_exclude_unknown_and_workbench_endpoints(self):
        m=self.seed_listing('123');self.seed_listing('456',rating=2,price=5,updated='2020-01-01T00:00:00Z')
        add_apps(self.db,[('apple','789','Unknown',None)],'fixture')
        with patch.dict(os.environ,{'APPMON_API_TOKEN':''}):client=self.login_admin(TestClient(create_app(self.cfg)))
        rows=client.get('/api/apps?store=apple&min_rating=4&pricing=free&updated_since=2026-01-01').json()['items']
        self.assertEqual([r['app_id'] for r in rows],['123'])
        self.assertEqual(client.get('/research/aso').status_code,404)
        self.assertEqual(client.get('/api/research/aso',params=m).status_code,200)
        self.assertEqual(client.get('/api/research/aso/keywords',params=m).json()['total'],0)
        from appmon.auth import requirements
        self.assertEqual(requirements('/api/research/aso/keywords','GET'),['aso:read'])
        self.assertEqual(client.get('/api/research/events',params=m).status_code,200)
        self.assertEqual(client.get('/api/research/markets/apple/123').json()['items'][0]['country'],'us')
        plan={'store':'apple','country':'us','language':'en','name':'P','app_ids':['123'],'details':True}
        self.assertEqual(client.post('/api/research/plans/preview',json=plan).json()['total'],1)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM collection_plans').fetchone()['n'],0)
        self.assertEqual(client.post('/api/research/plans',json=plan).status_code,200)
        self.assertEqual(client.get('/api/research/collection').json()['plans'][0]['status'],'queued')


if __name__=='__main__':unittest.main()
