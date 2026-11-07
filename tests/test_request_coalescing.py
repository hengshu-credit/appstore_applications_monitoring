import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace
from appmon.rpc_coalescer import RpcCoalescer, LookupCoalescer
from appmon.google_rpc import rpc_many
from appmon.stores import Client, FetchError, Result
from appmon import app_icons, keyword_discovery
from test_monitor import MySQLTestCase
from test_experience import PNG
from appmon.db import connect


class RpcCoalescingTest(unittest.TestCase):
    def test_apple_detail_and_icon_demands_share_lookup_but_ambiguous_missing_retries_alone(self):
        broker=LookupCoalescer(wait_seconds=0.1)
        clients=[Client({'_lookup_coalescer':broker},None) for _ in range(2)]
        m=dict(store='apple',country='us',language='en')
        batches=[[m|{'app_id':str(i)} for i in range(30)],[m|{'app_id':str(i)} for i in range(20,50)]]
        calls=[]
        def lookup(client,jobs):
            calls.append(jobs);return [Result('ok',{'app_id':j['app_id']},b'raw') for j in jobs]
        try:
            with patch.object(Client,'fetch_apple_many',lookup),ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda i:clients[i].fetch_many(batches[i]),range(2)))
                self.assertEqual(len(calls),1);self.assertEqual(len(calls[0]),50)
                self.assertEqual([[r.payload['app_id'] for r in group] for group in results],[[j['app_id'] for j in group] for group in batches])
                client=clients[0]
                client.fetch_many([m|{'app_id':'missing','failures':1,'last_error':'Apple batch omitted this ID; verify with a single lookup'}])
                self.assertEqual(len(calls),2);self.assertEqual(len(calls[-1]),1)
        finally:
            broker.close()
            for client in clients:client.session.close()

    def run_calls(self, specs):
        broker=RpcCoalescer(wait_seconds=0.1);sent=[];clients=[]
        def send(client,store,method,url,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];sent.append((kwargs['params'],calls))
            frames=[['wrb.fr',rpc,json.dumps([json.loads(payload)]),None,None,None,index] for rpc,payload,_,index in reversed(calls)]
            raw=json.dumps(frames).encode()
            return SimpleNamespace(status_code=200,text=raw.decode(),content=raw)
        try:
            for _ in specs:clients.append(Client({'timeout_seconds':30,'_rpc_coalescer':broker},None))
            barrier=threading.Barrier(len(specs))
            def run(i):
                market,calls=specs[i];barrier.wait();return rpc_many(clients[i],market,calls)[0]
            with patch.object(Client,'request',send),ThreadPoolExecutor(max_workers=len(specs)) as pool:
                results=list(pool.map(run,range(len(specs))))
            return sent,results
        finally:
            broker.close()
            for client in clients:client.session.close()

    def test_mixed_rpcs_share_one_http_and_keep_out_of_order_identity(self):
        m=dict(store='google',country='us',language='en')
        specs=[(m,[(rpc,[i]) for i in range(n)]) for rpc,n in [('Ws7gDc',5),('xdSrCf',7),('lGYRle',8)]]
        sent,results=self.run_calls(specs)
        self.assertEqual(len(sent),1);self.assertEqual(len(sent[0][1]),20)
        self.assertEqual(results,[[[p] for _,p in calls] for _,calls in specs])

    def test_duplicate_calls_fan_out_and_market_boundaries_never_mix(self):
        m=dict(store='google',country='us',language='en')
        sent,results=self.run_calls([(m,[('rpc',[1])]*15),(m,[('rpc',[1])]*10)])
        self.assertEqual(len(sent),1);self.assertEqual(len(sent[0][1]),1)
        self.assertEqual([len(v) for v in results],[15,10])
        sent,_=self.run_calls([(m,[('rpc',[1])]),(m|{'language':'es'},[('rpc',[1])]),(m|{'country':'gb'},[('rpc',[1])])])
        self.assertEqual(len(sent),3)

    def test_protocol_capacity_is_not_exceeded(self):
        m=dict(store='google',country='us',language='en')
        sent,results=self.run_calls([(m,[('rpc',[i]) for i in range(a,b)]) for a,b in [(0,14),(14,20),(20,26)]])
        self.assertEqual(len(sent),2)
        self.assertTrue(all(len(calls)<=20 for _,calls in sent))
        self.assertEqual(sum(map(len,results)),26)


class IconBatchTest(MySQLTestCase):
    def test_two_thousand_receipts_share_one_download(self):
        self.db.executemany('''INSERT INTO app_icons(store,app_id,country,language,source_url,requested)
            VALUES('apple',%s,'us','en','https://is1.mzstatic.com/shared.png',1)''',[(str(10000+i),) for i in range(2000)])
        client=self.client()
        result=app_icons.work_icons(self.cfg,self.db,self.cfg['collection_batch_size'],client,max_requests=1)
        self.assertEqual((result['processed'],result['ok'],client.downloads),(2000,2000,1))
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM app_icons WHERE status='ready' AND file_path IS NOT NULL").fetchone()['n'],2000)

    def test_mixed_workers_keep_results_leases_and_one_physical_lineage_count(self):
        from appmon.intelligence import watch
        from appmon.app_permissions import enqueue
        from appmon.dispatcher import run_batch
        from appmon.collection_lineage import report
        from test_google_batches import FIXTURES, response
        m=dict(store='google',app_id='com.google.android.apps.translate',country='us',language='en')
        watch(self.db,m);enqueue(self.db,[m])
        broker=RpcCoalescer(wait_seconds=1)
        cfg=self.cfg | {'_rpc_coalescer':broker}
        def send(session,method,url,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            payloads=[FIXTURES['detail'] if c[0]=='Ws7gDc' else [[['Camera',None,[[None,'take pictures']],None]]] for c in calls]
            return response(calls,payloads)
        def collect(lane):
            db=connect(cfg,initialize=False)
            try:return run_batch(db,cfg,lane)
            finally:db.close()
        try:
            with patch('requests.Session.request',autospec=True,side_effect=send) as http,ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(collect,['details','permissions']))
            self.assertEqual([r['ok'] for r in results],[1,1]);self.assertEqual(http.call_count,1)
            graph=report(self.db,self.cfg,'google')
            edge=next(e for e in graph['edges'] if e['source']=='d:mixed_google' and e['target']=='s:google')
            self.assertEqual(edge['count'],1)
            self.assertEqual(next(n['value'] for n in graph['nodes'] if n['id']=='d:mixed_google'),2)
        finally:broker.close()

    def queue(self,count,known=False):
        ids=[]
        for i in range(count):
            m=dict(store='apple',app_id=str(1000+i),country='us',language='en')
            if known:app_icons.remember(self.db,**m,url='https://is1.mzstatic.com/shared.png')
            ids.append(app_icons.request_icon(self.db,self.cfg,m)['id'])
        return ids

    def client(self,metadata=None):
        class Response:
            status_code=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,n):yield PNG
        class Images:
            def __init__(self):self.lookups=[];self.downloads=0
            def fetch_many(self,rows):
                self.lookups.append(len(rows))
                return [Result('ok',{'icon':'https://is1.mzstatic.com/shared.png','name':r['app_id']},b'raw') for r in rows] if metadata is None else metadata(rows)
            def fetch(self,row):return self.fetch_many([row])[0]
            def request(self,*args,**kwargs):self.downloads+=1;return Response()
        return Images()

    def test_fifty_metadata_targets_then_one_shared_download(self):
        self.queue(50);client=self.client()
        first=app_icons.work_icons(self.cfg,self.db,500,client,max_requests=1)
        self.assertEqual(first['prepared'],50);self.assertEqual(client.lookups,[50]);self.assertEqual(client.downloads,0)
        second=app_icons.work_icons(self.cfg,self.db,500,client,max_requests=1)
        self.assertEqual(second['ok'],50);self.assertEqual(client.downloads,1)
        rows=self.db.execute('SELECT file_path,checked_at,due,refresh_hours FROM app_icons WHERE requested=1').fetchall()
        self.assertEqual(len({r['file_path'] for r in rows}),1)
        self.assertTrue(all(abs(r['due']-r['checked_at']-r['refresh_hours']*3600)<0.01 for r in rows))

    def test_failed_metadata_isolated_and_successful_addresses_do_not_refetch(self):
        self.queue(3)
        def metadata(rows):
            return [FetchError('source omitted one item',60) if i==1 else Result('ok',{'icon':'https://is1.mzstatic.com/shared.png'},b'raw') for i,_ in enumerate(rows)]
        client=self.client(metadata)
        first=app_icons.work_icons(self.cfg,self.db,500,client,max_requests=1)
        self.assertEqual((first['prepared'],first['error']),(2,1))
        second=app_icons.work_icons(self.cfg,self.db,500,client,max_requests=1)
        self.assertEqual(second['ok'],2);self.assertEqual(client.lookups,[3]);self.assertEqual(client.downloads,1)

    def test_same_url_uses_large_logical_batch_and_new_cycle_fetches_fresh_bytes(self):
        self.queue(70,known=True);client=self.client()
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,500,client,max_requests=1)['ok'],70)
        self.assertEqual(client.downloads,1)
        self.db.execute('UPDATE app_icons SET due=0')
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,500,client,max_requests=1)['ok'],70)
        self.assertEqual(client.downloads,2)  # Old cache must not silently extend freshness.

    def test_fixed_version_word_file_downloaded_once_for_multiple_imports(self):
        spec={'language':'en','limit':1,'markets':[dict(store='google',country='us',language='en')]}
        response=SimpleNamespace(text='loan 50\nbudget 40\n',raise_for_status=lambda:None)
        with patch('appmon.keyword_discovery.requests.get',return_value=response) as get:
            keyword_discovery.import_frequency(self.db,spec)
            keyword_discovery.import_frequency(self.db,spec|{'limit':2,'markets':[dict(store='apple',country='gb',language='en')]})
        get.assert_called_once()
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_candidates').fetchone()['n'],3)
