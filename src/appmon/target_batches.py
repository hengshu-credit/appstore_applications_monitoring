"""Market-scoped RPC batches with independent target leases and observations."""
import time
import uuid
from .collection_limits import blocked
from .ingest import disk_check
from .stores import FetchError
from .task_logs import Execution


def claim_targets(db, table, lease_seconds, size):
    if table not in ('keyword_targets','observation_targets'):
        raise ValueError('Unknown batch target table')
    now=time.time()
    with db:
        db.execute('INSERT INTO schedules VALUES(%s,0) ON DUPLICATE KEY UPDATE name=schedules.name',('claim:'+table,))
        first=db.execute(f'''SELECT * FROM {table} WHERE enabled=1 AND due<=%s AND lease_until<=%s
            ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED''',(now,now)).fetchone()
        if not first:
            return []
        rows=[first]
        if first['store']=='google' and first['failures']==0 and size>1:
            kind=' AND kind=%s' if table=='observation_targets' else ''
            rows+=list(db.execute(f'''SELECT * FROM {table} WHERE enabled=1 AND due<=%s AND lease_until<=%s
                AND store='google' AND country=%s AND language=%s AND failures=0 AND id<>%s{kind}
                ORDER BY due,id LIMIT %s FOR UPDATE SKIP LOCKED''',
                (now,now,first['country'],first['language'],first['id'],*([first['kind']] if kind else []),min(size,20)-1)).fetchall())
        token=uuid.uuid4().hex
        db.execute(f'UPDATE {table} SET token=%s,lease_until=%s WHERE id IN ('+','.join('%s' for _ in rows)+')',
                   (token,now+lease_seconds,*[r['id'] for r in rows]))
        for row in rows:
            row['token']=token
    return rows


def work_targets(cfg, db, limit, client, *, table, resource, single, multiple, save):
    counts=dict(processed=0,ok=0,error=0,stale=0)
    while counts['processed']<limit:
        if blocked(db,cfg):
            break
        disk_check(cfg['data_dir'],cfg['min_free_gb'])
        rows=claim_targets(db,table,max(cfg['lease_seconds'],900),min(cfg.get('google_rpc_batch_size',20),limit-counts['processed']))
        if not rows:
            break
        logs=[Execution(db,resource,row['id']) for row in rows]
        old_progress,old_guard=getattr(client,'progress',None),getattr(client,'request_guard',None)
        def guard():
            if old_guard:
                old_guard()
            active=db.execute(f'SELECT count(*) n FROM {table} WHERE enabled=1 AND token=%s AND lease_until>%s AND id IN ('+
                              ','.join('%s' for _ in rows)+')',(rows[0]['token'],time.time(),*[r['id'] for r in rows])).fetchone()['n']
            if active!=len(rows):
                raise FetchError('批次任务已暂停或租约已替换',60)
        def progress(event,message):
            for task_log in logs:
                task_log.write(event,message)
        client.request_guard,client.progress=guard,progress
        try:
            for row,task_log in zip(rows,logs):
                task_log.write('started',f"开始采集：{row['store']} / {row['country']} / {row['language']}；本批 {len(rows)} 个目标")
            try:
                guard()
                results=getattr(client,multiple)(rows) if len(rows)>1 else [getattr(client,single)(rows[0])]
                if len(results)!=len(rows):
                    raise FetchError('批次结果数量异常')
            except (FetchError,ValueError,TypeError,KeyError,IndexError) as exc:
                results=[exc if isinstance(exc,FetchError) else FetchError('批次解析失败：'+type(exc).__name__)]*len(rows)
            for row,result,task_log in zip(rows,results,logs):
                error=result if isinstance(result,FetchError) else None
                accepted=save(db,cfg,row,error=error) if error else save(db,cfg,row,result=result)
                outcome='error' if error else 'ok'
                task_log.finish(outcome,accepted,'按目标独立保存，保留原采集深度与周期',error=error)
                counts['processed']+=1
                counts[outcome if accepted else 'stale']+=1
            if any(isinstance(r,FetchError) and r.retry_after>=300 for r in results):
                break
        finally:
            client.request_guard,client.progress=old_guard,old_progress
    return counts
