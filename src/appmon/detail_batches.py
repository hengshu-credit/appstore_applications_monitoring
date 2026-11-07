"""Bounded detail batches shared by priority and full-pool workers."""
import time
from .db import claim_details
from .ingest import disk_check
from .stores import FetchError,CollectionDeferred
from .task_logs import Execution, BatchProgress
from .collection_batch import persist_batch


def work_details(cfg, db, limit, client, *, watchlist=False, managed=False, max_requests=None):
    from .monitor import save_result
    counts = dict(processed=0,ok=0,error=0,stale=0)
    if not watchlist:
        counts['missing'] = 0
    batches = 0
    while counts['processed'] < limit and (max_requests is None or batches<max_requests):
        from .collection_limits import blocked
        if blocked(db,cfg):
            break
        if managed:
            from .pipelines import configured
            enabled, cfg = configured(db,cfg,'pool')
            if not enabled:
                break
        disk_check(cfg['data_dir'],cfg['min_free_gb'])
        size = min(max(cfg.get('apple_lookup_batch_size',50),cfg.get('google_rpc_batch_size',20)),limit-counts['processed']) if callable(getattr(client,'fetch_many',None)) else 1
        jobs = claim_details(db,cfg['countries'],cfg['language'],cfg['lease_seconds'],size,watchlist,
                             google_limit=cfg.get('google_rpc_batch_size',20),apple_limit=cfg.get('apple_lookup_batch_size',50))
        if not jobs:
            break
        batches += 1
        logs = [Execution(db,'watchlist' if watchlist else 'pipelines',j['watch_id'] if watchlist else 'pool') for j in jobs]
        client.lineage_batch_key=jobs[0]['token']
        client.lineage_tasks=len(jobs)
        old_progress, old_guard = getattr(client,'progress',None),getattr(client,'request_guard',None)
        progress=BatchProgress(logs)
        def guard():
            if old_guard:
                old_guard()
            if managed:
                from .pipelines import settings
                if not settings(db,cfg,'pool')['enabled']:
                    raise CollectionDeferred('全池采集已暂停',60)
            # A batch shares a token, but retains independent identities and results.
            rows = db.execute('''SELECT j.app_id FROM jobs j LEFT JOIN watchlist w
                USING(store,app_id,country,language) WHERE j.store=%s AND j.country=%s AND j.language=%s
                AND j.app_id IN ('''+','.join('%s' for _ in jobs)+''') AND j.token=%s AND j.lease_until>%s
                AND '''+('w.enabled=1' if watchlist else 'w.id IS NULL'),
                (jobs[0]['store'],jobs[0]['country'],jobs[0]['language'],*[j['app_id'] for j in jobs],jobs[0]['token'],time.time())).fetchall()
            if len(rows)!=len(jobs):
                raise CollectionDeferred('批次内任务已暂停或租约已替换，等待重新组批',1)
        client.progress,client.request_guard = progress,guard
        try:
            for job, task_log in zip(jobs,logs):
                task_log.write('started',f"开始采集：{job['store']} / {job['app_id']} / {job['country']}；本批 {len(jobs)} 个应用")
            try:
                guard()
                from .collection_batch import can_batch
                batchable=len(jobs)>1 or jobs[0]['store']=='google' and can_batch(jobs[0]) and callable(getattr(client,'fetch_many',None))
                results = client.fetch_many(jobs) if batchable else [client.fetch(jobs[0])]
                if len(results)!=len(jobs):
                    raise FetchError('批次结果数量异常，保留原有数据')
            except FetchError as exc:
                results = [exc]*len(jobs)
            entries=list(zip(jobs,results,logs))
            # Keep upstream batches wide while bounding lock duration per commit.
            for offset in range(0,len(entries),100):
                portion=entries[offset:offset+100]
                before_counts=counts.copy()
                app_keys=[(j['store'],j['app_id']) for j,r,_ in portion if not isinstance(r,Exception) and r.outcome=='ok']
                def save_results(writer):
                    counts.clear();counts.update(before_counts)
                    for job,result,task_log in portion:
                        if isinstance(result,CollectionDeferred):
                            from .collection_batch import defer
                            accepted=defer(writer,'jobs',job,result)
                            task_log.write('deferred' if accepted else 'stale',str(result))
                            counts['processed']+=1;counts['deferred']=counts.get('deferred',0)+int(bool(accepted))
                            counts['stale']+=int(not accepted)
                            continue
                        local_cfg = cfg | {'refresh_hours':job['refresh_hours']} if watchlist else cfg
                        failure = result if isinstance(result,FetchError) else None
                        accepted = save_result(writer,local_cfg,job,error=failure) if failure else save_result(writer,local_cfg,job,result=result)
                        outcome = 'error' if failure else result.outcome
                        task_log.finish(outcome,accepted,'批次结果按应用独立保存',error=failure)
                        counts['processed'] += 1
                        counts[('ok' if watchlist and outcome=='missing' else outcome) if accepted else 'stale'] += 1
                persist_batch(db,save_results,app_keys)
            if any(isinstance(result,FetchError) and result.retry_after>=300 for result in results):
                break
        finally:
            client.progress,client.request_guard = old_progress,old_guard
    return counts
