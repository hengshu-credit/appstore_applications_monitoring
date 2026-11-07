import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from appmon.stores import Client,FetchError
from appmon.google_rpc import rpc_many


class LargeBatchTest(unittest.TestCase):
    def test_apple_210_identities_and_fields_survive_one_response(self):
        client=Client({'apple_lookup_batch_size':210},None)
        jobs=[dict(store='apple',app_id=str(i),country='us',language='en') for i in range(1000,1210)]
        payload=[{'trackId':int(j['app_id']),'wrapperType':'software','trackName':'App '+j['app_id'],'description':'full description','artistId':12,'screenshotUrls':['https://is1.mzstatic.com/a.png']} for j in reversed(jobs)]
        r=SimpleNamespace(status_code=200,content=b'raw',json=lambda:{'resultCount':210,'results':payload})
        with patch.object(client,'get',return_value=r) as call:results=client.fetch_many(jobs)
        call.assert_called_once();self.assertEqual([r.payload['app_id'] for r in results],[j['app_id'] for j in jobs])
        self.assertTrue(all(r.payload['description']=='full description' and r.payload['store_fields']['artistId']==12 for r in results))
        client.session.close()

    def test_google_100_calls_split_only_on_size_rejection_and_preserve_every_identity(self):
        client=Client({'google_rpc_batch_size':100},None);sizes=[]
        def request(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];sizes.append(len(calls))
            if len(calls)>50:raise FetchError('HTTP 413')
            frames=[['wrb.fr',rpc,payload,None,None,None,index] for rpc,payload,_,index in reversed(calls)]
            text=json.dumps(frames);return SimpleNamespace(status_code=200,text=text,content=text.encode())
        with patch.object(client,'request',side_effect=request):
            results,_=rpc_many(client,{'country':'us','language':'en'},[('rpc',[i]) for i in range(100)])
        self.assertEqual(sizes,[100,50,50]);self.assertEqual(results,[[i] for i in range(100)])
        with patch.object(client,'request',side_effect=FetchError('HTTP 429')) as limited:
            with self.assertRaises(FetchError):rpc_many(client,{'country':'us','language':'en'},[('rpc',[i]) for i in range(100)])
        limited.assert_called_once();client.session.close()

    def test_three_thousand_out_of_order_results_fit_one_response_and_budget_split_retains_all(self):
        client=Client({'google_rpc_batch_size':3000},None);sizes=[]
        def request(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0];sizes.append(len(calls))
            raw=json.dumps([['wrb.fr',rpc,payload,None,None,None,index] for rpc,payload,_,index in reversed(calls)]).encode()
            return SimpleNamespace(status_code=200,iter_content=lambda n:iter([raw]),close=lambda:None,headers={'Content-Type':'application/json'})
        with patch.object(client,'request',side_effect=request):
            values,_=rpc_many(client,{'country':'us','language':'en'},[('rpc',[i]) for i in range(3000)])
        self.assertEqual(sizes,[3000]);self.assertEqual(values,[[i] for i in range(3000)])
        sizes.clear()
        with patch.object(client,'request',side_effect=request),patch('appmon.google_rpc.GOOGLE_RESPONSE_MAX_BYTES',2000):
            values,_=rpc_many(client,{'country':'us','language':'en'},[('rpc',[i]) for i in range(100)])
        self.assertGreater(len(sizes),1);self.assertEqual(values,[[i] for i in range(100)])
        client.session.close()

    def test_failed_split_does_not_discard_successful_portion(self):
        client=Client({'google_rpc_batch_size':100},None)
        def request(*args,**kwargs):
            calls=json.loads(kwargs['data']['f.req'])[0]
            if len(calls)>2:raise FetchError('HTTP 413')
            if json.loads(calls[0][1])[0]>=2:raise FetchError('HTTP 429',900)
            text=json.dumps([['wrb.fr',rpc,payload,None,None,None,index] for rpc,payload,_,index in calls])
            return SimpleNamespace(status_code=200,text=text,content=text.encode())
        with patch.object(client,'request',side_effect=request):values,_=rpc_many(client,{'country':'us','language':'en'},[('rpc',[i]) for i in range(4)])
        self.assertEqual(values[:2],[[0],[1]])
        self.assertTrue(all(isinstance(r,FetchError) and r.retry_after==900 for r in values[2:]));client.session.close()
