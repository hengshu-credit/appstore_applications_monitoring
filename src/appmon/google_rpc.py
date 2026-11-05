"""Multiplex the same public Play RPCs; keep each call's identity and payload."""
import json
from importlib.resources import files
from .stores import FetchError


def template(name):
    return json.loads(files('appmon').joinpath('google_'+name+'_request.json').read_text(encoding='utf-8'))


def parse_frames(text, rpc_ids):
    found = {}
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
            if len(frame)<7 or not isinstance(frame[6],str) or frame[6] not in {str(i) for i in range(len(rpc_ids))}:
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
    if not 1<=len(calls)<=20:
        raise ValueError('Google RPC batch must contain 1–20 calls')
    response=client.request('google','POST','https://play.google.com/_/PlayStoreUi/data/batchexecute',
        params={'rpcids':','.join(dict.fromkeys(rpc for rpc,_ in calls)),
                'hl':market['language'],'gl':market['country'],'rt':'c'},
        data={'f.req':json.dumps([[[rpc,json.dumps(payload),None,str(i)] for i,(rpc,payload) in enumerate(calls)]])})
    if response.status_code!=200:
        raise FetchError('Google batch endpoint unavailable')
    return parse_frames(response.text,[rpc for rpc,_ in calls]),response.content


def same_market(targets):
    if not targets or len(targets)>20 or len({(t['store'],t['country'],t['language']) for t in targets})!=1 or targets[0]['store']!='google':
        raise ValueError('Google batch requires 1–20 targets in the same country and language')
