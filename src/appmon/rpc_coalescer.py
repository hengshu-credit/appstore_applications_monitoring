"""Combine compatible public Play RPC calls from concurrent collection lanes."""
import threading
import time
import weakref
from concurrent.futures import Future
from .db import dump
from .stores import FetchError


class RpcCoalescer:
    lineage_lane='mixed_google'
    store='google'
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
                 dump(dict(session.headers)), dump(session.cookies.get_dict()),
                 dump(session.proxies), client.cfg.get('timeout_seconds'))
        future = Future()
        with self.condition:
            if self.closed: raise FetchError('RPC consumer stopped',60)
            self.pending.append((scope, client, dict(market), calls, future))
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
            while not self.closed and time.monotonic() < deadline:
                scope = self.pending[0][0]
                if len({(rpc,dump(payload)) for e in self.pending if e[0]==scope for rpc,payload in e[3]}) >= self.capacity: break
                self.condition.wait(deadline-time.monotonic())
            first = self.pending.pop(0)
            group, unique = [first], {(rpc,dump(payload)) for rpc,payload in first[3]}
            for entry in list(self.pending):
                if entry[0] != first[0]: continue
                keys = {(rpc,dump(payload)) for rpc,payload in entry[3]}
                if len(unique | keys) <= self.capacity:
                    group.append(entry);unique.update(keys);self.pending.remove(entry)
            return group

    def _run(self):
        while True:
            group = self._take()
            if not group: return
            leader = group[0][1]
            old_lane = getattr(leader,'lineage_lane',None)
            old_guard = leader.request_guard
            guards = [entry[1].request_guard for entry in group if entry[1].request_guard]
            try:
                def guard():
                    for check in guards: check()
                leader.request_guard = guard
                leader.lineage_lane = self.lineage_lane
                # Record logical tasks once per leased batch, not once per page.
                from .task_logs import emit
                for _,client,market,_,_ in group:
                    token = getattr(client,'lineage_batch_key',None)
                    if token and self.seen.get(client) != token:
                        emit(client.db,'dispatch_mixed',old_lane if client is leader else getattr(client,'lineage_lane','direct'),
                             'claimed','加入同市场联合批次',details={'store':self.store,'tasks':client.lineage_tasks})
                        self.seen[client] = token
                calls = [call for entry in group for call in entry[3]]
                values, raw = self._send(leader,group[0][2],calls)
                offset=0;answers=[]
                for entry in group:
                    size=len(entry[3]);answers.append((entry[4],(values[offset:offset+size],raw)));offset+=size
            except Exception as exc:
                error = exc if isinstance(exc,FetchError) else FetchError('合并请求失败：'+type(exc).__name__,60)
                answers=[(entry[4],error) for entry in group]
            finally:
                leader.request_guard=old_guard
                leader.lineage_lane=old_lane
            # No caller connection is touched after its future is released.
            for future, answer in answers:
                if isinstance(answer,Exception):future.set_exception(answer)
                else:future.set_result(answer)

    def _send(self,leader,market,calls):
        from .google_rpc import send_rpc
        return send_rpc(leader,market,calls,self.capacity)


class LookupCoalescer(RpcCoalescer):
    lineage_lane='mixed_apple'
    store='apple'

    def __init__(self,capacity=50,wait_seconds=0.2):
        super().__init__(capacity,wait_seconds)

    def _send(self,leader,market,calls):
        ids=list(dict.fromkeys(identifier for _,identifier in calls))
        results=leader.fetch_apple_many([market|{'store':'apple','app_id':identifier} for identifier in ids])
        lookup=dict(zip(ids,results))
        return [lookup[identifier] for _,identifier in calls],None
