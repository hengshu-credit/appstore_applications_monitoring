import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from appmon.stores import FetchError
from appmon.observation_stores import ObservationClient, chart_result
from appmon.keyword_stores import KeywordClient
from appmon.google_rpc import parse_frames
from appmon.pagination import PageWindow
from test_google_batches import response
from test_monitor import OceanBaseTestCase
from appmon.intelligence import add_observation_target, work_observations


def review_rows(start,end):
    return [[str(i),None,4,None,'review '+str(i),[1720000000],0,None,None,None,'1.0'] for i in range(start,end)]


def search_first(start,end,token=None):
    section=[None]*23
    section[22]=[[[[f'com.app.a{i}'],None,None,f'App {i}']] for i in range(start,end)]
    section[22]=[section[22],[None,None,None,[None,token]]]
    return [[None,[section]]]


def search_next(start,end,token=None):
    rows=[]
    for i in range(start,end):
        row=[None]*13;row[2]=f'App {i}';row[12]=[f'com.app.a{i}'];rows.append(row)
    root=[None]*8;root[0]=rows;root[7]=[None,token]
    return [[root]]


class CollectionDepthTest(unittest.TestCase):
    def target(self,depth=500,**values):
        return dict(kind='reviews',store='google',country='us',language='en',app_id='com.example.app',depth=depth)|values

    def test_single_review_request_can_take_500(self):
        client=ObservationClient({},None)
        def rpc(t,name,payload):
            self.assertEqual(payload[2][2],[500,None,None])
            return response([[name,'',None,'0']],[[review_rows(0,500),[None,'more']]])
        with patch.object(client,'rpc',side_effect=rpc) as send:
            result=client.reviews(self.target())
        self.assertEqual((send.call_count,len(result.items)),(1,500))
        self.assertEqual(result.pagination,dict(requested=500,collected=500,pages=1,end='depth_reached'))

    def test_batch_requests_each_entire_depth_in_one_http(self):
        client=ObservationClient({},None)
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];sizes=[json.loads(call[1])[2][2][0] for call in calls]
            self.assertEqual(sizes,[300,500])
            return response(calls,[[review_rows(0,n),[None,'more']] for n in sizes])
        with patch.object(client,'request',side_effect=send) as http:
            results=client.fetch_observations([self.target(300),self.target(500,app_id='com.example.other')])
        self.assertEqual(http.call_count,1)
        self.assertEqual([len(r.items) for r in results],[300,500])

    def test_short_and_overlapping_pages_continue_past_old_page_estimate(self):
        client=ObservationClient({},None)
        pages=[(0,30,'a'),(20,60,'b'),(60,90,'c'),(90,120,'d')]
        sent=[]
        def rpc(t,name,payload):
            sent.append(payload[2][2]);start,end,token=pages[len(sent)-1]
            return response([[name,'',None,'0']],[[review_rows(start,end),[None,token]]])
        with patch.object(client,'rpc',side_effect=rpc) as http:
            result=client.reviews(self.target(120))
        self.assertEqual(http.call_count,4)
        self.assertEqual([r['id'] for r in result.items],[str(i) for i in range(120)])
        self.assertEqual([p[2] for p in sent],[None,'a','b','c'])
        self.assertEqual([p[0] for p in sent],[120,90,60,30])

    def test_cycle_empty_more_pages_and_request_budget_fail_without_partial_success(self):
        window=PageWindow(100)
        window.add([{'id':'1'}],'repeat')
        with self.assertRaisesRegex(FetchError,'循环'):window.add([{'id':'2'}],'repeat')
        window=PageWindow(100)
        window.add([],'a');window.add([],'b')
        with self.assertRaisesRegex(FetchError,'未正常结束'):window.add([],'c')
        window=PageWindow(100,max_pages=2)
        window.add([{'id':'1'}],'a')
        with self.assertRaises(FetchError):window.add([{'id':'2'}],'b')

    def test_a_finished_target_survives_later_batch_http_failure(self):
        client=ObservationClient({},None)
        first=True
        def send(*args,**kwargs):
            nonlocal first
            if not first:raise FetchError('HTTP 503',900)
            first=False;calls=json.loads(kwargs['data']['f.req'])[0]
            return response(calls,[[review_rows(0,50),[None,None]],[review_rows(0,30),[None,'next']]])
        with patch.object(client,'request',side_effect=send):
            results=client.fetch_observations([self.target(50),self.target(100,app_id='com.example.other')])
        self.assertEqual(len(results[0].items),50)
        self.assertIsInstance(results[1],FetchError)

    def test_apple_short_pages_follow_navigation_and_source_limit_is_explicit(self):
        client=ObservationClient({},None);pages=[]
        def get(store,url):
            page=len(pages)+1;pages.append(page)
            data={'feed':{'entry':[{'id':{'label':str(page)},'im:rating':{'label':'4'},'content':{'label':'review'},'title':{'label':'title'}}],
                          'link':[{'attributes':{'rel':'next','href':'unused'}}]}}
            return SimpleNamespace(status_code=200,text=json.dumps(data),json=lambda:data)
        with patch.object(client,'get',side_effect=get):
            result=client.reviews(self.target(100,store='apple',app_id='123'))
        self.assertEqual(pages,list(range(1,11)))
        self.assertEqual(len(result.items),10)
        self.assertEqual(result.coverage,'reviews_source_limit')
        self.assertEqual(result.pagination['end'],'source_limit')

    def test_search_cursor_continuation_keeps_unique_order_until_depth(self):
        client=KeywordClient({},None)
        target=dict(store='google',country='us',language='en',keyword='game',depth=120)
        rounds=[search_first(0,20,'a'),search_next(15,65,'b'),search_next(65,120,'c')];seen=[]
        def send(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];seen.append(calls)
            return response(calls,[rounds[len(seen)-1]])
        with patch.object(client,'request',side_effect=send) as http:
            result=client.search_many([target])[0]
        self.assertEqual(http.call_count,3)
        self.assertEqual([r['app_id'] for r in result.apps],[f'com.app.a{i}' for i in range(120)])
        self.assertEqual([calls[0][0] for calls in seen],['lGYRle','qnKhOb','qnKhOb'])
        self.assertEqual([json.loads(calls[0][1])[0][3] for calls in seen[1:]],['a','b'])
        self.assertEqual((result.coverage,result.pagination['end']),('paginated_search','depth_reached'))

    def test_search_failures_do_not_publish_the_first_page_as_complete(self):
        client=KeywordClient({},None);target=dict(store='google',country='us',language='en',keyword='game',depth=100)
        for next_payload in [FetchError('HTTP 503'),search_next(20,40,'a')]:
            with patch('appmon.google_rpc.rpc_many',side_effect=[([search_first(0,20,'a')],b'first'),([next_payload],b'next')]):
                self.assertIsInstance(client.search_many([target])[0],FetchError)
        value=parse_frames(json.dumps([['wrb.fr','qnKhOb',None,None,None,[5],'0']]),['qnKhOb'])[0]
        self.assertIsInstance(value,FetchError)  # A stale cursor may also produce status 5.
        value=parse_frames(json.dumps([['wrb.fr','qnKhOb',None,None,None,[3],'0']]),['qnKhOb'])[0]
        self.assertIsInstance(value,FetchError)

    def test_source_boundaries_are_not_claimed_as_requested_depth(self):
        result=chart_result(self.target(500),[{'id':str(i)} for i in range(200)],b'raw','google-play-chart-rpc')
        self.assertEqual(result.coverage,'chart_source_limit')
        self.assertEqual(result.pagination,dict(requested=500,collected=200,pages=1,end='source_limit'))
        window=PageWindow(100)
        self.assertTrue(window.add([{'id':'1'}],None))
        self.assertEqual(window.end,'source_exhausted')


class CollectionDepthPersistenceTest(OceanBaseTestCase):
    def test_page_failure_preserves_the_previous_complete_review_snapshot(self):
        target=dict(kind='reviews',store='google',country='us',language='en',app_id='com.example.app',depth=100)
        identifier=add_observation_target(self.db,target)['id'];client=ObservationClient(self.cfg,self.db)
        # Single eligible Google targets now use the same batch transport as
        # multiple targets. Mock HTTP at their shared boundary, never live Play.
        with patch.object(client,'request',return_value=response([['UsvDTd','',None,'0']],[[review_rows(0,100),[None,'more']]])) as http:
            self.assertEqual(work_observations(self.cfg,self.db,1,client)['ok'],1)
            self.assertEqual(http.call_count,1)
        success=self.db.execute('SELECT latest_success_id FROM observation_targets').fetchone()['latest_success_id']
        self.db.execute('UPDATE observation_targets SET due=0')
        with patch.object(client,'request',side_effect=[response([['UsvDTd','',None,'0']],[[review_rows(100,130),[None,'next']]]),FetchError('HTTP 503',900)]) as http:
            self.assertEqual(work_observations(self.cfg,self.db,1,client)['error'],1)
            self.assertEqual(http.call_count,2)
            calls=json.loads(http.call_args_list[1].kwargs['data']['f.req'])[0]
            self.assertEqual(json.loads(calls[0][1])[2][2],[70,None,'next'])
        self.assertEqual(self.db.execute('SELECT latest_success_id FROM observation_targets').fetchone()['latest_success_id'],success)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM reviews').fetchone()['n'],100)
        self.assertEqual(self.db.execute("SELECT count(*) n FROM observation_runs WHERE outcome='error'").fetchone()['n'],1)
