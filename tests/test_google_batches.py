import json
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from appmon.google_rpc import parse_frames
from appmon.stores import Client, FetchError, GOOGLE_MAPPING
from appmon.keyword_stores import KeywordClient, parse_google
from appmon.observation_stores import ObservationClient, batch_payload, google_chart
from appgoblin_play_scraper.features.app import parse_dom
from appmon.db import add_apps, enqueue, connect
from appmon.monitor import work
from appmon.keywords import add_targets, work_keywords
from appmon.intelligence import add_observation_target, work_observations
from appmon.collection_tasks import control_tasks
from appmon.target_batches import claim_targets
from test_monitor import MySQLTestCase

FIXTURES=json.loads((Path(__file__).parent/'fixtures/google_batch.json').read_text(encoding='utf-8'))


def fixture_html(kind,key):
    return "<script>AF_initDataCallback({key: '"+key+"', data:"+json.dumps(FIXTURES[kind])+", sideChannel: {}});</script>"


def dataset(text,key):
    for block in re.findall(r'AF_initDataCallback[\s\S]*?</script',text):
        if re.search(r"key:\s*'"+key+"'",block):
            return json.loads(re.search(r'data:([\s\S]*?),\s*sideChannel:',block)[1])
    raise AssertionError('fixture dataset missing')


def response(calls, payloads):
    frames=[['wrb.fr',call[0],json.dumps(payload),None,None,None,call[3]] for call,payload in zip(calls,payloads)]
    text=")]}'\n\n"+'\n'.join(json.dumps([frame]) for frame in reversed(frames))
    return SimpleNamespace(status_code=200,text=text,content=text.encode())


class GoogleBatchParserTest(unittest.TestCase):
    def test_envelopes_correlate_out_of_order_and_isolate_missing_or_bad_items(self):
        text=")]}'\n42\n"+json.dumps([['wrb.fr','rpc','[2]',None,None,None,'1']])
        values=parse_frames(text,['rpc','rpc'])
        self.assertIsInstance(values[0],FetchError);self.assertEqual(values[1],[2])
        for frames in ([['wrb.fr','wrong','[]',None,None,None,'0']],
                       [['wrb.fr','rpc','[]',None,None,None,'2']],
                       [['wrb.fr','rpc','[]',None,None,None,'0']]*2):
            with self.assertRaises(FetchError):parse_frames(json.dumps(frames),['rpc'])
        self.assertIsInstance(parse_frames(json.dumps([['wrb.fr','rpc',None,None,None,[3],'0']]),['rpc'])[0],FetchError)

    def test_detail_batch_has_every_original_field_and_rejects_wrong_identity(self):
        html=fixture_html('detail','ds:5')
        payload=dataset(html,'ds:5')
        jobs=[dict(store='google',app_id='com.google.android.apps.translate',country='us',language='en')]*2
        client=Client({'timeout_seconds':30},None)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            return response(calls,[payload,payload])
        with patch.object(client,'request',side_effect=send) as http:
            results=client.fetch_many(jobs)
        self.assertEqual(http.call_count,1)
        old=parse_dom(html,jobs[0]['app_id'],results[0].payload['url'])
        self.assertEqual({k:results[0].payload[k] for k in GOOGLE_MAPPING},{k:old.get(v) for k,v in GOOGLE_MAPPING.items()})
        self.assertEqual(results[0].payload['app_id'],jobs[0]['app_id'])
        self.assertEqual(results[0].payload['store_fields']['developerId'],old['developerId'])
        self.assertEqual(results[0].payload,results[1].payload)
        with patch.object(client,'request',side_effect=send):
            bad=client.fetch_many([jobs[0]|{'app_id':'com.wrong.app'}])[0]
        self.assertIsInstance(bad,FetchError)

    def test_search_batch_preserves_exact_order_names_depth_and_source(self):
        html=fixture_html('search','ds:4');payload=dataset(html,'ds:4')
        client=KeywordClient({},None)
        targets=[dict(store='google',country='us',language='en',keyword=k,depth=depth) for k,depth in [('panda',200),('loan',5)]]
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            self.assertEqual([json.loads(c[1])[0][2][0] for c in calls],['panda','loan'])
            self.assertEqual(kwargs['params']['gl'],'us')
            return response(calls,[payload,payload])
        with patch.object(client,'request',side_effect=send) as http:
            results=client.search_many(targets)
        self.assertEqual(http.call_count,1)
        self.assertEqual(results[0].apps,parse_google(html))
        self.assertEqual(results[1].apps,parse_google(html)[:5])
        self.assertEqual((results[0].source,results[0].coverage),('google-play-public-search','first_response'))
        with self.assertRaises(ValueError):client.search_many([targets[0],targets[1]|{'country':'th'}])

    def test_chart_batch_preserves_depth_and_every_item(self):
        payload=FIXTURES['chart']
        targets=[dict(store='google',country='us',language='en',kind='chart',collection='top-free',category='GAME',depth=n) for n in (100,5)]
        client=ObservationClient({},None)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            self.assertEqual([json.loads(c[1])[0][1][0][1][1] for c in calls],[100,5])
            return response(calls,[payload,payload])
        with patch.object(client,'request',side_effect=send) as http:
            results=client.fetch_observations(targets)
        self.assertEqual(http.call_count,1)
        self.assertEqual(results[0].items,google_chart(payload)[:100])
        self.assertEqual(results[1].items,google_chart(payload)[:5])

    def test_reviews_batch_keeps_independent_cursors_and_full_requested_depth(self):
        targets=[dict(store='google',country='us',language='en',kind='reviews',app_id=f'com.example.app{i}',depth=n) for i,n in enumerate((75,100))]
        client=ObservationClient({},None);requested=[]
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];payloads=[]
            for call in calls:
                p=json.loads(call[1]);count,_,token=p[2][2];app=p[3][0]
                requested.append((app,count,token))
                start=50 if token else 0
                self.assertTrue(token is None or token==app+':page2')
                # A source may cap the response at 50 despite a larger request.
                rows=[[app+':'+str(i),None,4,None,'review '+str(i),[1720000000],0,None,None,None,'1.0'] for i in range(start,start+min(50,count))]
                payloads.append([rows,[None,None if token else app+':page2']])
            return response(calls,payloads)
        with patch.object(client,'request',side_effect=send) as http:
            results=client.fetch_observations(targets)
        self.assertEqual(http.call_count,2)  # Formerly two pages per app: four requests.
        self.assertEqual([len(r.items) for r in results],[75,100])
        for target,result in zip(targets,results):
            self.assertEqual([r['id'] for r in result.items],[target['app_id']+':'+str(i) for i in range(target['depth'])])
        self.assertEqual(len(requested),4)


class GoogleBatchWorkerTest(MySQLTestCase):
    def test_chart_worker_keeps_every_requested_item_and_target_interval(self):
        for collection,depth,interval in [('top-free',100,6),('top-paid',5,48)]:
            add_observation_target(self.db,dict(kind='chart',store='google',country='us',language='en',category='GAME',
                                               collection=collection,depth=depth,refresh_hours=interval))
        client=ObservationClient(self.cfg,self.db)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];return response(calls,[FIXTURES['chart']]*len(calls))
        with patch.object(client,'request',side_effect=send) as http:
            result=work_observations(self.cfg,self.db,2,client)
        self.assertEqual((result['ok'],http.call_count),(2,1))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM observation_items').fetchone()['n'],105)
        rows=self.db.execute('SELECT depth,refresh_hours,due FROM observation_targets ORDER BY id').fetchall()
        self.assertEqual([(r['depth'],r['refresh_hours']) for r in rows],[(100,6),(5,48)])
        self.assertTrue(all(abs(r['due']-time.time()-r['refresh_hours']*3600)<10 for r in rows))

    def test_partial_batch_failure_retries_only_failed_target_using_original_protocol(self):
        ids=add_targets(self.db,[dict(store='google',country='us',language='en',keyword=k) for k in ['a','b']])['ids']
        html=fixture_html('search','ds:4');client=KeywordClient(self.cfg,self.db)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];return response(calls[:1],[FIXTURES['search']])
        with patch.object(client,'request',side_effect=send):
            result=work_keywords(self.cfg,self.db,2,client)
        self.assertEqual((result['ok'],result['error']),(1,1))
        self.db.execute('UPDATE keyword_targets SET due=0 WHERE failures>0')
        with patch.object(client,'request') as batch,patch.object(client,'get',return_value=SimpleNamespace(status_code=200,text=html,url='https://play.google.com',content=html.encode())) as get:
            result=work_keywords(self.cfg,self.db,2,client)
        batch.assert_not_called();self.assertEqual((get.call_count,result['ok']),(1,1))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets WHERE latest_success_id IS NOT NULL AND failures=0').fetchone()['n'],2)

    def test_21_google_details_take_two_requests_and_do_not_change_schedule(self):
        add_apps(self.db,[('google',f'com.example.app{i}',None,None) for i in range(21)],'test');enqueue(self.db,['us'],'en')
        reference=dataset(fixture_html('detail','ds:5'),'ds:5')
        client=Client(self.cfg,self.db)
        def batch(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];payloads=[]
            for call in calls:
                value=json.loads(json.dumps(reference));value[1][2][77][0]=json.loads(call[1])[-1][0][0];payloads.append(value)
            return response(calls,payloads)
        # The final singleton follows the unchanged HTML parser.
        html=fixture_html('detail','ds:5')
        with patch.object(client,'request',side_effect=batch) as http,patch.object(client,'get',return_value=SimpleNamespace(status_code=200,text=html,url='https://play.google.com',content=html.encode())) as get:
            result=work(self.cfg,self.db,21,client)
        self.assertEqual(result['ok'],21);self.assertEqual(http.call_count+get.call_count,2)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM listings').fetchone()['n'],21)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM jobs WHERE due>%s',(time.time()+23*3600,)).fetchone()['n'],21)

    def test_keyword_worker_batches_all_targets_preserving_ranks_and_intervals(self):
        ids=add_targets(self.db,[dict(store='google',country='us',language='en',keyword=f'word {i}',depth=10,refresh_hours=6) for i in range(21)])['ids']
        html=fixture_html('search','ds:4');payload=dataset(html,'ds:4');client=KeywordClient(self.cfg,self.db)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];return response(calls,[payload]*len(calls))
        with patch.object(client,'request',side_effect=send) as http,patch.object(client,'get',return_value=SimpleNamespace(status_code=200,text=html,url='https://play.google.com',content=html.encode())) as get:
            result=work_keywords(self.cfg,self.db,21,client)
        self.assertEqual((result['ok'],http.call_count+get.call_count),(21,2))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_ranks').fetchone()['n'],21*10)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets WHERE refresh_hours=6 AND depth=10 AND due>%s',(time.time()+5*3600,)).fetchone()['n'],21)

    def test_claims_are_disjoint_market_scoped_and_skip_paused_and_leased(self):
        add_targets(self.db,[dict(store='google',country=c,language='en',keyword=f'word {i}') for c in ['us','gb'] for i in range(25)])
        self.db.execute('UPDATE keyword_targets SET enabled=0 WHERE id=1')
        self.db.execute('UPDATE keyword_targets SET lease_until=%s WHERE id=2',(time.time()+300,))
        def claim_one(_):
            db=connect(self.cfg,initialize=False)
            try:return claim_targets(db,'keyword_targets',600,20)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:batches=list(pool.map(claim_one,range(2)))
        identifiers=[r['id'] for batch in batches for r in batch]
        self.assertEqual(len(identifiers),len(set(identifiers)))
        self.assertNotIn(1,identifiers);self.assertNotIn(2,identifiers)
        self.assertTrue(all(1<=len(batch)<=20 and len({r['country'] for r in batch})==1 for batch in batches))

    def test_pause_during_response_rejects_only_stale_target(self):
        ids=add_targets(self.db,[dict(store='google',country='us',language='en',keyword=k) for k in ['a','b']])['ids']
        payload=dataset(fixture_html('search','ds:4'),'ds:4');client=KeywordClient(self.cfg,self.db)
        def send(*args,**kwargs):
            control_tasks(self.db,{'action':'pause','tasks':[{'resource':'keywords','id':ids[0]}]})
            calls=json.loads(kwargs['data']['f.req'])[0];return response(calls,[payload,payload])
        with patch.object(client,'request',side_effect=send):result=work_keywords(self.cfg,self.db,2,client)
        self.assertEqual((result['ok'],result['stale']),(1,1))
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_runs').fetchone()['n'],1)
