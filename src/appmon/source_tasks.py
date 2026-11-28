"""Durable one-shot source work consumed by the shared collection dispatcher."""
import hashlib
import json
import time
import uuid
from .db import dump
from .intelligence import market, text
from .task_logs import emit, safe_message, INSERT_LOG
from .collection_batch import touch, write_batch
from . import ad_sources, ad_channels, intake, task_windows
from .stores import CollectionDeferred

KINDS = {'aso_popularity':'ASO 关键词热度', 'aso_import':'ASO 热度导入', 'aso_report':'ASO 报告入库', **ad_sources.KINDS}


def admitted(alias='collection_source_tasks', schedule_alias=None):
    # app-ads is the existing explicit per-app submit endpoint. Old catch-all
    # collection-plan receipts have no user submission for advertising.
    schedule = (f'{schedule_alias}.enabled=1' if schedule_alias else
                f'EXISTS (SELECT 1 FROM ad_tracking_schedules ads WHERE ads.receipt_source={alias}.source AND ads.enabled=1)')
    shared = f"""EXISTS (SELECT 1 FROM collection_request_aliases r WHERE r.resource='sources' AND r.task_id={alias}.id
        AND (r.source IN ('app-ads','user-ad-plan') OR EXISTS
             (SELECT 1 FROM ad_tracking_schedules a WHERE a.receipt_source=r.source AND a.enabled=1)))"""
    return f"(LEFT({alias}.kind,3)<>'ad_' OR {alias}.source IN ('app-ads','user-ad-plan') OR {schedule} OR {shared})"


def normalize(kind, value):
    from . import apple_popularity, keyword_reports
    if kind not in KINDS:
        raise ValueError('未知来源任务类型')
    if kind in ad_sources.KINDS:
        m = market(value)
        if kind not in ad_sources.available(m):
            raise ValueError('该商店或地区不在广告来源覆盖范围内')
        return m
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


def batch_key(kind, payload):
    grouping = payload.copy()
    if kind in ad_sources.KINDS:
        if kind != 'ad_google':
            grouping.pop('language')
        if kind in ('ad_creatives', 'ad_rankings', 'ad_reddit'):
            grouping.pop('country')
    if kind == 'aso_popularity':
        # Apple returns a country/genre/time slice; language is a local destination.
        grouping.pop('language'); grouping.pop('limit')
    return hashlib.sha256(dump([kind,grouping]).encode()).hexdigest()


def enqueue(db, kind, value, source='aso', request_id=None, *, submitted_by=None, due=0, submitted_at=None):
    at=time.time() if submitted_at is None else submitted_at
    payload = normalize(kind,value)
    source = text(source,64); request_id = text(uuid.uuid4().hex if request_id is None else request_id,120)
    if kind in ad_sources.KINDS:
        if type(submitted_by) is not int or submitted_by <= 0:
            raise ValueError('广告任务需要用户明确提交')
        if source not in ('app-ads','user-ad-plan'):
            schedule = db.execute('''SELECT kinds FROM ad_tracking_schedules WHERE receipt_source=%s AND enabled=1
                AND submitted_by=%s AND store=%s AND app_id=%s AND country=%s AND language=%s''',
                (source,submitted_by,*[payload[k] for k in ('store','app_id','country','language')])).fetchone()
            if not schedule or kind not in json.loads(schedule['kinds']):
                raise ValueError('没有生效的用户广告周期任务')
    fingerprint = batch_key(kind, payload)
    request_hash = intake.digest([kind,payload])
    with db:
        intake.lock_source_intake(db)
        alias = intake.find_alias(db,'sources',source,request_id)
        if alias:
            if alias['fingerprint'] != request_hash:
                raise ValueError('同一来源请求编号不能对应不同任务')
            return receipt(db,alias['task_id'],source,request_id) | {'reused':True,'source':source,'request_id':request_id}
        old = db.execute('SELECT id,kind,payload FROM collection_source_tasks WHERE source=%s AND request_id=%s',(source,request_id)).fetchone()
        if old:
            if old['kind']!=kind or json.loads(old['payload'])!=payload:
                raise ValueError('同一来源请求编号不能对应不同任务')
            return receipt(db,old['id'],source,request_id) | {'reused':True}
        current = active_task(db,kind,payload,fingerprint)
        if not current and submitted_at is not None:
            # Another plan may have finished the work while this one was expanding.
            current = matching_task(db,kind,payload,fingerprint,'due IS NULL AND success_at>=%s',(submitted_at,))
        if current:
            identifier = current['id']
            intake.record_alias(db,'sources',source,request_id,identifier,request_hash)
            if current['due'] is not None and not current['token'] and not current['failures'] and not has_progress(current):
                accepted=task_windows.latest(db,'sources',identifier,at,source,request_id,
                    fallback={'request_source':current['source'],'request_id':current['request_id'],
                              'submitted_at':current['created_at'],'pending':True})
                if accepted:
                    # Origin keys remain durable; retain the original payload digest
                    # for retries if a globally shared ad source changes market.
                    if current['payload']!=dump(payload) and not intake.find_alias(db,'sources',current['source'],current['request_id']):
                        intake.record_alias(db,'sources',current['source'],current['request_id'],identifier,
                                            intake.digest([kind,json.loads(current['payload'])]))
                    old_due=current['due']
                    slot=old_due if (old_due< -1 and due< -1) or (due>=0 and old_due<=at) else min(old_due,due)
                    if source.startswith('ad-schedule-') and due==0:
                        slot=task_windows.promoted_slot(slot)
                    db.execute("""UPDATE collection_source_tasks SET payload=%s,store=%s,country=%s,language=%s,app_id=%s,due=%s
                        WHERE id=%s""",(dump(payload),payload['store'],payload['country'],payload['language'],payload.get('app_id',''),slot,identifier))
                    touch(db)
            result = receipt(db,identifier,source,request_id) | {'reused':True}
            return result | {'source':source,'request_id':request_id}
        label = str(payload.get('filename') or payload.get('genre') or KINDS[kind])[:255]
        db.execute("""INSERT INTO collection_source_tasks(store,app_id,country,language,kind,label,payload,batch_key,source,request_id,created_at,due)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload['store'],payload.get('app_id',''),payload['country'],payload['language'],kind,label,dump(payload),fingerprint,source,request_id,time.time(),due))
        identifier=db.cursor.lastrowid
        task_windows.latest(db,'sources',identifier,at,source,request_id)
        emit(db,'sources',identifier,'queued','采集任务已入队：'+KINDS[kind],
             details={'submitted_by':submitted_by} if submitted_by is not None else None)
        touch(db)
    return receipt(db,identifier) | {'reused':False}


def has_progress(row):
    return bool(row['result'] and 'progress' in json.loads(row['result']))


def active_task(db, kind, payload, fingerprint):
    return matching_task(db,kind,payload,fingerprint,'due IS NOT NULL AND failures<3')


def matching_task(db, kind, payload, fingerprint, state, params=()):
    # Compatible Apple popularity requests may differ in output language or depth.
    exact = '' if kind in ad_sources.KINDS else ' AND payload=%s'
    args = (fingerprint,*params) if not exact else (fingerprint,*params,dump(payload))
    return db.execute(f"""SELECT id,source,request_id,created_at,enabled,failures,lease_until,token,result,payload,due FROM collection_source_tasks
        WHERE batch_key=%s AND {state} AND {admitted()}{exact}
        ORDER BY id LIMIT 1 FOR UPDATE""",args).fetchone()


def consolidate_pending(db):
    """Merge legacy pending receipts, keeping old request IDs resolvable."""
    batches = db.execute(f"""SELECT batch_key FROM collection_source_tasks
        WHERE due IS NOT NULL AND failures<3 AND {admitted()} GROUP BY batch_key HAVING COUNT(*)>1""").fetchall()
    for batch in batches:
        with db:
            intake.lock_source_intake(db)
            rows = db.execute(f"""SELECT * FROM collection_source_tasks WHERE batch_key=%s AND due IS NOT NULL
                AND failures<3 AND {admitted()} ORDER BY (lease_until>%s) DESC,enabled DESC,
                (result IS NOT NULL) DESC,id FOR UPDATE""",(batch['batch_key'],time.time())).fetchall()
            seen = {}
            for row in rows:
                payload = json.loads(row['payload'])
                key = row['batch_key'] if row['kind'] in ad_sources.KINDS else dump(payload)
                keeper = seen.setdefault(key,row)
                if keeper is row or row['token'] or row['lease_until']>time.time():continue
                db.execute("""UPDATE collection_request_aliases SET task_id=%s
                    WHERE resource='sources' AND task_id=%s""",(keeper['id'],row['id']))
                intake.record_alias(db,'sources',row['source'],row['request_id'],keeper['id'],
                                    intake.digest([row['kind'],payload]),row['id'])
                if not keeper['token'] and not keeper['failures'] and not has_progress(keeper) and not row['failures'] and row['enabled']:
                    origin=dict(request_source=keeper['source'],request_id=keeper['request_id'],
                                submitted_at=keeper['created_at'],pending=True)
                    if task_windows.latest(db,'sources',keeper['id'],row['created_at'],row['source'],row['request_id'],fallback=origin):
                        if keeper['payload']!=row['payload'] and not intake.find_alias(db,'sources',keeper['source'],keeper['request_id']):
                            intake.record_alias(db,'sources',keeper['source'],keeper['request_id'],keeper['id'],
                                                intake.digest([row['kind'],json.loads(keeper['payload'])]))
                        db.execute('''UPDATE collection_source_tasks SET payload=%s,country=%s,language=%s WHERE id=%s''',
                                   (row['payload'],row['country'],row['language'],keeper['id']))
                    if keeper['due']>=-1 and row['due']<keeper['due']:
                        keeper['due']=row['due']
                        db.execute('UPDATE collection_source_tasks SET due=%s WHERE id=%s',(row['due'],keeper['id']))
                if row['success_at'] is None:
                    db.execute('DELETE FROM collection_source_tasks WHERE id=%s',(row['id'],))
                else:
                    db.execute('UPDATE collection_source_tasks SET due=NULL,token=NULL,lease_until=0 WHERE id=%s',(row['id'],))
                touch(db)


def request_task(db,source,request_id):
    alias = intake.find_alias(db,'sources',source,request_id)
    if alias:
        return {'id':alias['task_id']}
    return db.execute('SELECT id FROM collection_source_tasks WHERE source=%s AND request_id=%s',
                      (source,request_id)).fetchone()


def receipt(db, identifier, source=None, request_id=None):
    row = db.execute(f'''SELECT id,kind,source,request_id,enabled,due,lease_until,failures,last_error,result,created_at,success_at,
        {admitted()} AS admitted
        FROM collection_source_tasks WHERE id=%s''',(identifier,)).fetchone()
    if not row:
        current = intake.resolve_id(db,'sources',identifier)
        if current != identifier:return receipt(db,current,source,request_id)
        raise ValueError('任务不存在')
    status = ('done' if row['due'] is None else 'stopped' if not row['admitted'] else 'paused' if not row['enabled'] else
              'running' if row['lease_until']>time.time() else 'error' if row['failures'] else 'pending')
    if source is not None and task_windows.is_expired(db,'sources',identifier,source,request_id):
        status='expired'
        row.update(result=None,success_at=None,last_error=None)
    return row | {'resource':'sources','status':status,'result':json.loads(row['result']) if row['result'] else None}


def work(db,cfg,*,due_by=None):
    from . import apple_popularity, keyword_reports, collection_limits
    from .collection_lineage import record_dispatch
    from .storage_policy import storage_guard
    if storage_guard(db,cfg):return {'processed':0}
    local_only=collection_limits.blocked(db,cfg)
    local_scope=" AND kind IN ('aso_import','aso_report')" if local_only else ''
    now=time.time(); token=uuid.uuid4().hex
    due_by = now if due_by is None else due_by
    try:
        # Serialize only claiming, so simultaneous consumers do not split one compatible batch.
        # File batches share a payload; read the uploaded bytes once, not once per receipt.
        with db.named_lock('source-claim'):
            with db:
                first=db.claim(f'''SELECT * FROM collection_source_tasks WHERE {admitted()} AND enabled=1 AND due<=%s AND lease_until<=%s
                    AND failures<3'''+local_scope+' ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED',(due_by,now)).fetchone()
                if not first:return {'processed':0}
                if first['kind'] == 'ad_apple' or first['kind'] in ad_channels.SOCIAL:
                    # Each receipt advances its own durable page cursor.
                    rows = [first]
                else:
                    # The selected receipt determines which request runs. Other
                    # receipts for that same source response can share the result.
                    rows=db.claim(f'''SELECT id,language,failures,JSON_EXTRACT(payload,'$.limit') requested_limit
                        FROM collection_source_tasks WHERE {admitted()} AND batch_key=%s AND enabled=1 AND due<=%s
                        AND lease_until<=%s AND failures<3 ORDER BY due,id LIMIT %s FOR UPDATE SKIP LOCKED''',(first['batch_key'],now,now,cfg.get('collection_batch_size',2000))).fetchall()
                if not rows:return {'processed':0}
                task_windows.end_pending(db,'sources',[r['id'] for r in rows])
                db.execute('UPDATE collection_source_tasks SET token=%s,lease_until=%s WHERE id IN %s',
                           (token,now+max(600,cfg.get('lease_seconds',600)),tuple(r['id'] for r in rows)))
                touch(db)
    except RuntimeError as exc:
        if str(exc)=='Another process is already importing this dataset':return {'processed':0}
        raise
    kind=first['kind']; payload=json.loads(first['payload'])
    record_dispatch(db,kind,first['store'],len(rows))
    with db:
        events=[]
        for row in rows:emit(db,'sources',row['id'],'started',f'消费者已领取；合并 {len(rows)} 个兼容任务',events=events)
        db.executemany(INSERT_LOG,events)
    error=None; data=None
    try:
        if kind in ad_sources.KINDS:
            def guard():
                if not db.execute(f'''SELECT 1 FROM collection_source_tasks WHERE id IN %s AND token=%s AND enabled=1
                    AND lease_until>%s AND {admitted()} LIMIT 1''',(tuple(r['id'] for r in rows),token,time.time())).fetchone():
                    raise CollectionDeferred('广告任务已停止或租约已结束',1)
            guard()
            previous = json.loads(first['result'] or '{}')
            progress = previous.pop('progress', {})
            data = ad_sources.fetch(db, cfg, kind, payload, progress.get('page', 0), request_guard=guard)
        elif kind=='aso_popularity':
            delay=collection_limits.admission(db,cfg,'apple')
            if delay is None or delay>0:
                db.execute('UPDATE collection_source_tasks SET token=NULL,lease_until=0,due=%s WHERE id IN %s AND token=%s',
                           (time.time()+(delay if delay is not None else 30),tuple(r['id'] for r in rows),token))
                touch(db)
                return {'processed':0}
            payload['limit']=max(int(row['requested_limit']) for row in rows)
            data=apple_popularity.request_data(cfg,db,payload)
    except CollectionDeferred as exc:
        with db:
            db.execute('UPDATE collection_source_tasks SET token=NULL,lease_until=0,due=%s WHERE id IN %s AND token=%s',
                       (time.time()+max(1,exc.retry_after),tuple(r['id'] for r in rows),token))
            touch(db)
        return {'processed':0}
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
                cache_key=kind if kind in ad_sources.KINDS else row['language']
                if cache_key not in cached:
                    if kind in ad_sources.KINDS:
                        from .ad_tracking import save_page
                        cached[cache_key] = save_page(writer, kind, spec, data, previous, progress)
                    elif kind=='aso_report':cached[cache_key]=keyword_reports.import_report(cfg,writer,spec)
                    else:cached[cache_key]=apple_popularity.ingest(cfg,writer,spec,data if kind=='aso_popularity' else spec['response'],
                        origin='Apple Ads官方接口' if kind=='aso_popularity' else '用户导入Apple格式响应')
                result=cached[cache_key]
                if 'progress' in result:
                    writer.execute('''UPDATE collection_source_tasks SET due=IF(due<0,due,%s),token=NULL,lease_until=0,
                        failures=0,last_error=NULL,result=%s WHERE id=%s AND token=%s''',
                        (time.time(),dump(result),row['id'],token))
                    emit(writer,'sources',row['id'],'progress',f"广告来源已保存 {result['progress']['returned']} 条，等待下一页")
                    continue
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
