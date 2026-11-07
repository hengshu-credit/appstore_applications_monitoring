"""Multiplex the same public Play RPCs; keep each call's identity and payload."""
import json
import time
import requests
from importlib.resources import files
from .stores import FetchError
from .batch_limits import GOOGLE_RPC_MAX,GOOGLE_RESPONSE_MAX_BYTES,GOOGLE_RESPONSE_SECONDS


def template(name):
    return json.loads(files('appmon').joinpath('google_'+name+'_request.json').read_text(encoding='utf-8'))


def parse_frames(text, rpc_ids):
    found = {}
    expected={str(i) for i in range(len(rpc_ids))}
    for line in text.splitlines():
        try:
            frames = json.loads(line)
        except ValueError:
            continue  # XSSI prefix and chunk byte lengths are not JSON frames.
        if not isinstance(frames,list):
            continue
        for frame in frames:
            if not isinstance(frame,list) or frame[:1]!=['wrb.fr']:
                continue
            if len(frame)<7 or not isinstance(frame[6],str) or frame[6] not in expected:
                raise FetchError('Google batch response identity missing')
            index=int(frame[6])
            if index in found or frame[1]!=rpc_ids[index]:
                raise FetchError('Google batch response identity duplicated or mismatched')
            try:
                payload=json.loads(frame[2])
                if not isinstance(payload,list):
                    raise ValueError('Invalid RPC payload')
                found[index]=payload
            except (TypeError,ValueError):
                found[index]=FetchError('Google RPC item failed; retry this target separately',60)
    return [found.get(i,FetchError('Google batch omitted this target; retry separately',60)) for i in range(len(rpc_ids))]


def rpc_many(client, market, calls):
    if not 1<=len(calls)<=GOOGLE_RPC_MAX:
        raise ValueError(f'Google RPC batch must contain 1–{GOOGLE_RPC_MAX} calls')
    if getattr(client,'rpc_coalescer',None):
        return client.rpc_coalescer.submit(client,market,calls)
    return send_rpc(client,market,calls,capacity=client.cfg.get('google_rpc_batch_size',20))


def response_body(response):
    try:
        content_type=(getattr(response,'headers',{}) or {}).get('Content-Type','')
        if isinstance(content_type,str) and 'text/html' in content_type.lower():
            raise FetchError('Google batch returned unexpected content type')
        if not callable(getattr(response,'iter_content',None)):
            raw=response.content or b''
            if len(raw)>GOOGLE_RESPONSE_MAX_BYTES:raise FetchError('Google batch response budget exceeded',details={'batch_budget':True})
            return response.text,raw
        started=time.monotonic();body=bytearray()
        for chunk in response.iter_content(65536):
            if len(body)+len(chunk)>GOOGLE_RESPONSE_MAX_BYTES or time.monotonic()-started>GOOGLE_RESPONSE_SECONDS:
                raise FetchError('Google batch response budget exceeded',details={'batch_budget':True})
            body.extend(chunk)
        raw=bytes(body)
        try:return raw.decode('utf8'),raw
        except UnicodeDecodeError:raise FetchError('Google response encoding invalid') from None
    except requests.RequestException as exc:raise FetchError(type(exc).__name__,60) from None
    finally:
        if callable(getattr(response,'close',None)):response.close()


def send_rpc(client, market, calls, capacity=20,split_depth=0):
    unique, indices, lookup = [], [], {}
    for rpc, payload in calls:
        key = (rpc,json.dumps(payload,separators=(',',':'),sort_keys=True))
        if key not in lookup:
            lookup[key] = len(unique)
            unique.append((rpc,payload))
        indices.append(lookup[key])
    if not unique or len(unique)>capacity:
        raise ValueError('Too many distinct RPC calls')
    try:
        response=client.request('google','POST','https://play.google.com/_/PlayStoreUi/data/batchexecute',
            params={'rpcids':','.join(dict.fromkeys(rpc for rpc,_ in unique)),
                    'hl':market['language'],'gl':market['country'],'rt':'c'},
            data={'f.req':json.dumps([[[rpc,json.dumps(payload),None,str(i)] for i,(rpc,payload) in enumerate(unique)]])},stream=True)
        if response.status_code!=200:
            if callable(getattr(response,'close',None)):response.close()
            raise FetchError('Google batch endpoint unavailable')
        text,raw=response_body(response)
    except FetchError as exc:
        if (str(exc) not in ('HTTP 413','HTTP 414') and not exc.details.get('batch_budget')) or len(unique)<2 or split_depth>=4:raise
        middle=len(unique)//2
        def portion(part):
            try:return send_rpc(client,market,part,capacity,split_depth+1)
            except FetchError as failure:return [failure]*len(part),b''
        left,raw_left=portion(unique[:middle])
        right,raw_right=portion(unique[middle:])
        results=left+right
        return [results[index] for index in indices],raw_left+b'\n'+raw_right
    results = parse_frames(text,[rpc for rpc,_ in unique])
    return [results[index] for index in indices],raw


def same_market(targets):
    if not targets or len(targets)>GOOGLE_RPC_MAX or len({(t['store'],t['country'],t['language']) for t in targets})!=1 or targets[0]['store']!='google':
        raise ValueError(f'Google batch requires 1–{GOOGLE_RPC_MAX} targets in the same country and language')
