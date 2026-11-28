"""Combine compatible public Play RPC calls from concurrent collection lanes."""
import threading
import time
import weakref
from dataclasses import dataclass
from concurrent.futures import Future
from .db import dump
from .batch_limits import window_key
from .stores import FetchError


@dataclass(eq=False)
class Submission:
    scope: tuple
    capacity: int
    client: object
    market: dict
    calls: list
    keys: frozenset
    future: Future


class RpcCoalescer:
    lineage_lane='mixed_google'
    store='google'
    capacity_key='google_rpc_batch_size'
    def __init__(self, capacity=20, wait_seconds=0.2):
        self.capacity, self.wait_seconds = capacity, wait_seconds
        self.condition = threading.Condition()
        self.pending = []
        self.closed = False
        self.seen = weakref.WeakKeyDictionary()
        self.thread = threading.Thread(target=self._run, name='google-rpc-transport', daemon=True)
        self.thread.start()

    def submit(self, client, market, calls):
        # Only this exact public RPC protocol is coalesced. Country and language
        # stay isolated, as do session headers/cookies and transport settings.
        session = client.session
        scope = (market['country'], market['language'],
                 dump(dict(session.headers)), tuple(sorted((c.domain,c.path,c.name,c.value,c.secure,c.expires)
                                                         for c in session.cookies)),
                 dump(session.proxies),dump(session.params),session.verify,session.cert,session.trust_env,id(session.auth),
                 client.cfg.get('timeout_seconds'),client.cfg.get('tls_maximum_version'))
        capacity = min(self.capacity,client.cfg.get(self.capacity_key,self.capacity))
        methods = ('apple-lookup',) if self.store=='apple' else (rpc for rpc,_ in calls)
        capacity = client.batch_window.limit(window_key(market,methods),capacity)
        keys = frozenset((rpc,dump(payload)) for rpc,payload in calls)
        future = Future()
        with self.condition:
            if self.closed: raise FetchError('RPC consumer stopped',60)
            self.pending.append(Submission(scope,capacity,client,dict(market),calls,keys,future))
            self.condition.notify()
        return future.result()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join()

    def _take(self):
        with self.condition:
            while not self.pending and not self.closed:
                self.condition.wait()
            if not self.pending: return []
            deadline = time.monotonic() + self.wait_seconds
            while True:
                first = self.pending[0]
                group,unique,ceiling = [first], set(first.keys), first.capacity
                for entry in self.pending[1:]:
                    if entry.scope != first.scope:
                        continue
                    common = min(ceiling,entry.capacity)
                    if len(unique | entry.keys) <= common:
                        group.append(entry)
                        unique.update(entry.keys)
                        ceiling = common
                remaining = deadline-time.monotonic()
                if self.closed or len(unique)>=ceiling or remaining<=0:
                    for entry in group:self.pending.remove(entry)
                    return group
                self.condition.wait(remaining)

    def _run(self):
        while True:
            group = self._take()
            if not group: return
            leader = group[0].client
            old_lane = getattr(leader,'lineage_lane',None)
            old_guard = leader.request_guard
            guards = [entry.client.request_guard for entry in group if entry.client.request_guard]
            try:
                def guard():
                    for check in guards: check()
                leader.request_guard = guard
                leader.lineage_lane = self.lineage_lane
                # Record logical tasks once per leased batch, not once per page.
                from .task_logs import emit
                for entry in group:
                    client=entry.client
                    token = getattr(client,'lineage_batch_key',None)
                    if token and self.seen.get(client) != token:
                        emit(client.db,'dispatch_mixed',old_lane if client is leader else getattr(client,'lineage_lane','direct'),
                             'claimed','加入同市场联合批次',details={'store':self.store,'tasks':client.lineage_tasks})
                        self.seen[client] = token
                calls = [call for entry in group for call in entry.calls]
                values, raw = self._send(leader,group[0].market,calls)
                offset=0;answers=[]
                for entry in group:
                    size=len(entry.calls);answers.append((entry.future,(values[offset:offset+size],raw)));offset+=size
            except Exception as exc:
                error = exc if isinstance(exc,FetchError) else FetchError('合并请求失败：'+type(exc).__name__,60)
                answers=[(entry.future,error) for entry in group]
            finally:
                leader.request_guard=old_guard
                leader.lineage_lane=old_lane
            # No caller connection is touched after its future is released.
            for future, answer in answers:
                if isinstance(answer,Exception):future.set_exception(answer)
                else:future.set_result(answer)

    def _send(self,leader,market,calls):
        from .google_rpc import send_rpc
        return send_rpc(leader,market,calls,min(self.capacity,leader.cfg.get(self.capacity_key,self.capacity)))


class LookupCoalescer(RpcCoalescer):
    lineage_lane='mixed_apple'
    store='apple'
    capacity_key='apple_lookup_batch_size'

    def __init__(self,capacity=50,wait_seconds=0.2):
        super().__init__(capacity,wait_seconds)

    def _send(self,leader,market,calls):
        ids=list(dict.fromkeys(identifier for _,identifier in calls))
        results=leader.fetch_apple_many([market|{'store':'apple','app_id':identifier} for identifier in ids])
        lookup=dict(zip(ids,results))
        return [lookup[identifier] for _,identifier in calls],None
