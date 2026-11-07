"""Durable per-task logs. A read cursor never exposes process output or secrets."""
import re
import time
import uuid
import json
from .log_details import clean, sanitize, diagnostics
from contextlib import contextmanager


def safe_message(value):
    message = re.sub(r'https?://\S+', '[URL已隐藏]', str(value))
    message = re.sub(r'(?i)\bBearer\s+[^\s,;]+', 'Bearer [已隐藏]', message)
    message = re.sub(r'(?i)\b(password|token|secret|cookie|authorization)\s*[:=]\s*[^\s,;]+', r'\1=[已隐藏]', message)
    return message[:4000]


def emit(db, resource, identifier, event, message, level='info', execution_id=None, details=None):
    # No transaction wrapper here: callers may emit atomically with a state change.
    db.execute('''INSERT INTO collection_logs(resource,target_key,execution_id,created_at,level,event,message,details)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
               (resource,str(identifier),execution_id,time.time(),level,event,clean(message,4000),json.dumps(sanitize(details),ensure_ascii=False) if details else None))


def start_batch(logs,messages):
    """Keep a start event for every task with bounded packets and one commit."""
    if len(logs)!=len(messages):raise ValueError('Batch start event count mismatch')
    if not logs:return
    values=[(log.resource,str(log.identifier),log.id,time.time(),'info','started',clean(message,4000),None)
            for log,message in zip(logs,messages)]
    with logs[0].db:
        logs[0].db.executemany('''INSERT INTO collection_logs(resource,target_key,execution_id,created_at,level,event,message,details)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',values)


class Execution:
    def __init__(self, db, resource, identifier):
        self.db,self.resource,self.identifier = db,resource,identifier
        self.id,self.started = uuid.uuid4().hex,time.monotonic()

    def write(self,event,message,level='info',details=None):
        emit(self.db,self.resource,self.identifier,event,message,level,self.id,details)

    def finish(self,outcome,accepted=True,detail='',error=None):
        elapsed = time.monotonic()-self.started
        state = 'stale' if not accepted else outcome
        labels = {'ok':'采集成功，数据已保存','missing':'本次地区查询未发现应用','error':'采集失败，保留上次有效数据','stale':'租约已失效，本次结果未写入'}
        if error is not None:
            detail=(detail+'；' if detail else '')+'原因：'+safe_message(error)
        self.write(state,f'{labels.get(state,state)}；耗时 {elapsed:.2f} 秒'+(f'；{detail}' if detail else ''),
                   'error' if state=='error' else 'warning' if state in ('stale','missing') else 'info',
                   diagnostics(error,duration_seconds=round(elapsed,2)) if error else None)


class BatchProgress:
    """One progress row per displayed task, with bounded wait sampling."""
    def __init__(self, logs, wait_interval=30):
        self.logs=list({(log.resource,str(log.identifier)):log for log in logs}.values())
        self.wait_interval=wait_interval
        self.last_wait=None
        self.wait_checks=0

    def __call__(self,event,message):
        if event=='rate_wait':
            self.wait_checks+=1
            now=time.monotonic()
            if self.last_wait is not None and now-self.last_wait<self.wait_interval:return
            self.last_wait=now
        if not self.logs:return
        with self.logs[0].db:
            for log in self.logs:
                log.write(event,message,details={'wait_checks':self.wait_checks} if event=='rate_wait' else None)


@contextmanager
def execution(db, client, resource, identifier, label):
    log = Execution(db,resource,identifier)
    old = getattr(client,'progress',None) if client is not None else None
    old_guard = getattr(client,'request_guard',None) if client is not None else None
    if client is not None:
        client.progress = log.write
        if resource in ('watchlist','keywords','targets','icons','permissions'):
            def state():
                if resource == 'watchlist':
                    return db.execute('''SELECT w.enabled,j.token FROM watchlist w LEFT JOIN jobs j
                        USING(store,app_id,country,language) WHERE w.id=%s''',(identifier,)).fetchone()
                table={'keywords':'keyword_targets','targets':'observation_targets','icons':'app_icons','permissions':'app_permission_targets'}[resource]
                return db.execute(f'SELECT enabled,token FROM {table} WHERE id=%s',(identifier,)).fetchone()
            initial=state()
            def guard():
                from .stores import FetchError
                current=state()
                if not current or not current['enabled'] or initial and current['token']!=initial['token']:
                    raise FetchError('任务已暂停或租约已替换',60)
            client.request_guard=guard
    log.write('started','开始采集：'+label)
    try:
        yield log
    except Exception as exc:
        log.write('aborted','执行异常中断：'+type(exc).__name__+'；已完成的数据保留，请查看任务状态','error',diagnostics(exc))
        raise
    finally:
        if client is not None:
            client.progress = old
            client.request_guard = old_guard


def target_state(db,resource,identifier):
    if resource=='pipelines':
        if identifier not in ('pool','seed','discovery'): raise ValueError('未知采集流程')
        return {'status':'pipeline','note':'基础流程中的每次操作按执行编号区分'}
    if not identifier.isdigit() or int(identifier)<1: raise ValueError('任务标识无效')
    if resource=='plans':
        row=db.execute('SELECT status,completed,total FROM collection_plans WHERE id=%s',(int(identifier),)).fetchone()
        if not row: raise ValueError('计划不存在')
        return row
    if resource=='watchlist':
        row=db.execute('''SELECT w.enabled,j.lease_until,j.due,j.failures,j.last_error FROM watchlist w
            LEFT JOIN jobs j USING(store,app_id,country,language) WHERE w.id=%s''',(int(identifier),)).fetchone()
    else:
        table={'keywords':'keyword_targets','targets':'observation_targets','icons':'app_icons','media':'app_media_assets','permissions':'app_permission_targets','sources':'collection_source_tasks'}.get(resource)
        if table is None: raise ValueError('未知日志类型')
        row=db.execute(f'SELECT enabled,lease_until,due,failures,last_error FROM {table} WHERE id=%s',(int(identifier),)).fetchone()
    if not row: raise ValueError('任务不存在')
    now=time.time()
    status=('done' if resource=='sources' and row['due'] is None else 'paused' if not row['enabled'] else 'running' if (row['lease_until'] or 0)>now
            else 'error' if row['failures'] else 'pending' if (row['due'] or 0)<=now else 'scheduled')
    return {**row,'status':status}


def read_logs(db,resource,identifier,after_id=0,before_id=0,limit=200):
    if after_id<0 or before_id<0 or (after_id and before_id) or not 1<=limit<=500:
        raise ValueError('日志游标或分页数量无效')
    state=target_state(db,resource,str(identifier))
    where='resource=%s AND target_key=%s';args=[resource,str(identifier)]
    if after_id: where+=' AND id>%s';args.append(after_id)
    if before_id: where+=' AND id<%s';args.append(before_id)
    order='ASC' if after_id else 'DESC'
    rows=list(db.execute(f'SELECT * FROM collection_logs WHERE {where} ORDER BY id {order} LIMIT %s',(*args,limit+1)).fetchall())
    more=len(rows)>limit;rows=rows[:limit]
    for row in rows:row['details']=json.loads(row['details']) if row.get('details') else None
    if not after_id:rows.reverse()
    oldest=rows[0]['id'] if rows else before_id or 0
    older=bool(oldest and db.execute('SELECT 1 FROM collection_logs WHERE resource=%s AND target_key=%s AND id<%s LIMIT 1',
                                    (resource,str(identifier),oldest)).fetchone())
    return {'items':rows,'next_cursor':rows[-1]['id'] if rows else after_id,'oldest_cursor':oldest,
            'has_more':bool(after_id and more),'has_older':older,'task':state,'server_time':time.time()}
