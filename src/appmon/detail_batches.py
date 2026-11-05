"""Bounded detail batches shared by priority and full-pool workers."""
import time
from .db import claim_details
from .ingest import disk_check
from .stores import FetchError
from .task_logs import Execution


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
        old_progress, old_guard = getattr(client,'progress',None),getattr(client,'request_guard',None)
        def progress(event,message):
            for task_log in logs:
                task_log.write(event,message)
        def guard():
            if old_guard:
                old_guard()
            if managed:
                from .pipelines import settings
                if not settings(db,cfg,'pool')['enabled']:
                    raise FetchError('全池采集已暂停',60)
            # A batch shares a token, but retains independent identities and results.
            rows = db.execute('''SELECT j.app_id FROM jobs j LEFT JOIN watchlist w
                USING(store,app_id,country,language) WHERE j.store=%s AND j.country=%s AND j.language=%s
                AND j.app_id IN ('''+','.join('%s' for _ in jobs)+''') AND j.token=%s AND j.lease_until>%s
                AND '''+('w.enabled=1' if watchlist else 'w.id IS NULL'),
                (jobs[0]['store'],jobs[0]['country'],jobs[0]['language'],*[j['app_id'] for j in jobs],jobs[0]['token'],time.time())).fetchall()
            if len(rows)!=len(jobs):
                raise FetchError('批次内任务已暂停或租约已替换',60)
        client.progress,client.request_guard = progress,guard
        try:
            for job, task_log in zip(jobs,logs):
                task_log.write('started',f"开始采集：{job['store']} / {job['app_id']} / {job['country']}；本批 {len(jobs)} 个应用")
            try:
                guard()
                results = client.fetch_many(jobs) if len(jobs)>1 else [client.fetch(jobs[0])]
                if len(results)!=len(jobs):
                    raise FetchError('批次结果数量异常，保留原有数据')
            except FetchError as exc:
                results = [exc]*len(jobs)
            for job,result,task_log in zip(jobs,results,logs):
                local_cfg = cfg | {'refresh_hours':job['refresh_hours']} if watchlist else cfg
                failure = result if isinstance(result,FetchError) else None
                accepted = save_result(db,local_cfg,job,error=failure) if failure else save_result(db,local_cfg,job,result=result)
                outcome = 'error' if failure else result.outcome
                task_log.finish(outcome,accepted,'批次结果按应用独立保存',error=failure)
                counts['processed'] += 1
                counts[('ok' if watchlist and outcome=='missing' else outcome) if accepted else 'stale'] += 1
            if any(isinstance(result,FetchError) and result.retry_after>=300 for result in results):
                break
        finally:
            client.progress,client.request_guard = old_progress,old_guard
    return counts
