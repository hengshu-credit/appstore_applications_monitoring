"""Durable one-shot ASO work consumed by the shared collection dispatcher."""
import hashlib
import json
import time
import uuid
from .db import dump
from .intelligence import market, text
from .task_logs import emit, safe_message
from .collection_batch import touch, write_batch

KINDS = {'aso_popularity':'ASO 关键词热度', 'aso_import':'ASO 热度导入', 'aso_report':'ASO 报告入库'}


def normalize(kind, value):
    from . import apple_popularity, keyword_reports
    if kind not in KINDS:
        raise ValueError('未知来源任务类型')
    if kind == 'aso_report':
        m = market(value)
        keyword_reports.preview(value)  # Validate files before acknowledging durable intake.
        payload = m | {k:value.get(k) for k in ('filename','content_base64')}
    else:
        m, genre, period, start, _, _ = apple_popularity.specification(value)
        payload = {k:m[k] for k in ('store','country','language')}
        payload.update(genre=genre,period=period,start=start.isoformat(),limit=value.get('limit',200))
        if kind == 'aso_import':
            response = value.get('response')
            result = response.get('result') if isinstance(response,dict) else None
            if not isinstance(result,dict) or not isinstance(result.get('rows'),list):
                raise ValueError('请提供包含 result.rows 的响应')
            if len(dump(response)) > 5_000_000:
                raise ValueError('响应过大')
            payload['response'] = response
    return payload


def enqueue(db, kind, value, source='aso', request_id=None):
    payload = normalize(kind,value)
    source = text(source,64); request_id = text(uuid.uuid4().hex if request_id is None else request_id,120)
    grouping = payload.copy()
    if kind == 'aso_popularity':
        # Apple returns a country/genre/time slice; language is a local destination.
        grouping.pop('language'); grouping.pop('limit')
    fingerprint = hashlib.sha256(dump([kind,grouping]).encode()).hexdigest()
    with db.named_lock('source-intake:'+dump([source,request_id]),timeout=10):
        old = db.execute('SELECT id,kind,payload FROM collection_source_tasks WHERE source=%s AND request_id=%s',(source,request_id)).fetchone()
        if old:
            if old['kind']!=kind or json.loads(old['payload'])!=payload:
                raise ValueError('同一来源请求编号不能对应不同任务')
            return receipt(db,old['id']) | {'reused':True}
        label = str(payload.get('filename') or payload.get('genre') or KINDS[kind])[:255]
        with db:
            db.execute('''INSERT INTO collection_source_tasks(store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (payload['store'],payload.get('app_id',''),payload['country'],payload['language'],kind,label,dump(payload),fingerprint,source,request_id,time.time()))
            identifier=db.cursor.lastrowid
            emit(db,'sources',identifier,'queued','一次性任务已入队：'+KINDS[kind])
            touch(db)
    return receipt(db,identifier) | {'reused':False}


def receipt(db, identifier):
    row = db.execute('''SELECT id,kind,source,request_id,enabled,due,lease_until,failures,last_error,result,created_at,success_at
        FROM collection_source_tasks WHERE id=%s''',(identifier,)).fetchone()
    if not row: raise ValueError('任务不存在')
    status = ('done' if row['due'] is None else 'paused' if not row['enabled'] else
              'running' if row['lease_until']>time.time() else 'error' if row['failures'] else 'pending')
    return row | {'resource':'sources','status':status,'result':json.loads(row['result']) if row['result'] else None}


def work(db,cfg):
    from . import apple_popularity, keyword_reports, collection_limits
    from .collection_lineage import record_dispatch
    now=time.time(); token=uuid.uuid4().hex
    try:
        # Serialize only claiming, so simultaneous consumers do not split one compatible batch.
        # File batches share a payload; read the uploaded bytes once, not once per receipt.
        with db.named_lock('source-claim'):
            with db:
                first=db.execute('''SELECT * FROM collection_source_tasks WHERE enabled=1 AND due<=%s AND lease_until<=%s
                    AND failures<3 ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED''',(now,now)).fetchone()
                if not first:return {'processed':0}
                rows=db.execute('''SELECT id,language,failures,JSON_EXTRACT(payload,'$.limit') requested_limit
                    FROM collection_source_tasks WHERE batch_key=%s AND enabled=1 AND due<=%s
                    AND lease_until<=%s AND failures<3 ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED''',(first['batch_key'],now,now,cfg.get('collection_batch_size',2000))).fetchall()
                for row in rows:
                    db.execute('UPDATE collection_source_tasks SET token=%s,lease_until=%s WHERE id=%s',(token,now+max(600,cfg.get('lease_seconds',600)),row['id']))
                touch(db)
    except RuntimeError as exc:
        if str(exc)=='Another process is already importing this dataset':return {'processed':0}
        raise
    kind=first['kind']; payload=json.loads(first['payload'])
    record_dispatch(db,kind,first['store'],len(rows))
    with db:
        for row in rows:emit(db,'sources',row['id'],'started',f'消费者已领取；合并 {len(rows)} 个兼容任务')
    error=None; data=None
    try:
        if kind=='aso_popularity':
            delay=collection_limits.admission(db,cfg,'apple')
            if delay is None or delay>0:
                for row in rows:
                    db.execute('UPDATE collection_source_tasks SET token=NULL,lease_until=0,due=%s WHERE id=%s AND token=%s',
                               (time.time()+(delay if delay is not None else 30),row['id'],token))
                touch(db)
                return {'processed':0}
            payload['limit']=max(int(row['requested_limit']) for row in rows)
            data=apple_popularity.request_data(cfg,db,payload)
    except Exception as exc:
        error=exc
    completed=failed=0
    try:
        if error:raise error
        cached={}
        with write_batch(db) as writer:
            for row in rows:
                current=db.execute('SELECT token,enabled,lease_until FROM collection_source_tasks WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
                if current['token']!=token or not current['enabled'] or current['lease_until']<=time.time():continue
                spec=payload | {'language':row['language']}
                cache_key=row['language']
                if cache_key not in cached:
                    if kind=='aso_report':cached[cache_key]=keyword_reports.import_report(cfg,writer,spec)
                    else:cached[cache_key]=apple_popularity.ingest(cfg,writer,spec,data if kind=='aso_popularity' else spec['response'],
                        origin='Apple Ads官方接口' if kind=='aso_popularity' else '用户导入Apple格式响应')
                result=cached[cache_key]
                saved=writer.execute('''UPDATE collection_source_tasks SET due=NULL,token=NULL,lease_until=0,failures=0,last_error=NULL,
                    result=%s,success_at=%s WHERE id=%s AND token=%s AND enabled=1''',(dump(result),time.time(),row['id'],token)).rowcount
                if saved:
                    completed+=1
                    emit(writer,'sources',row['id'],'ok',f'一次性任务已完成；保存 {result.get("returned",result.get("rows",0))} 条结果')
    except Exception as exc:
        completed=0
        with db:
            for row in rows:
                failed+=db.execute('''UPDATE collection_source_tasks SET failures=failures+1,last_error=%s,due=%s,token=NULL,lease_until=0
                    WHERE id=%s AND token=%s''',(safe_message(exc),time.time()+min(3600,60*2**row['failures']),row['id'],token)).rowcount
                emit(db,'sources',row['id'],'error','执行失败：'+safe_message(exc),'error')
            touch(db)
    return {'processed':len(rows),'ok':completed,'error':failed}
