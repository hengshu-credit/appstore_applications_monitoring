"""Two-phase icon collection: batched metadata, then one download per URL."""
import hashlib
import os
import time
import uuid
from .stores import Client, FetchError, CollectionDeferred
from .collection_batch import can_batch, retry_condition, NETWORK_ERRORS, write_batch
from .collection_limits import blocked
from .task_logs import Execution, BatchProgress
from .ingest import disk_check


def claim(db,cfg,limit,client):
    from .app_icons import source_url
    now=time.time();token=uuid.uuid4().hex
    from .storage_policy import media_scope_apps
    source='app_icons i'
    if cfg.get('media_scope','all')!='all':
        source+=' JOIN ('+media_scope_apps()+') scope ON scope.store=i.store AND scope.app_id=i.app_id'
    ready='i.requested=1 AND i.enabled=1 AND i.due<=%s AND i.lease_until<=%s'
    # Resolve scope outside the locking transaction. A slow membership scan
    # must never hold icon locks needed by detail writes or Top50 expansion.
    first=db.execute('SELECT i.* FROM '+source+' WHERE '+ready+' ORDER BY i.due,i.id LIMIT 1',(now,now)).fetchone()
    if not first:return []
    url=source_url(first['source_url'])
    if url:
        condition='i.source_url=%s AND i.store=%s';args=[first['source_url'],first['store']];capacity=limit
    else:
        condition="(i.source_url IS NULL OR i.source_url='' OR (i.id=%s AND i.source_url<=>%s)) AND i.store=%s AND i.country=%s AND i.language=%s AND (i.id=%s OR "+retry_condition('i.')+")"
        args=[first['id'],first['source_url'],first['store'],first['country'],first['language'],first['id'],NETWORK_ERRORS]
        capacity=min(limit,cfg.get('apple_lookup_batch_size',50) if first['store']=='apple' else cfg.get('google_rpc_batch_size',20))
        if not can_batch(first) or not callable(getattr(client,'fetch_many',None)):capacity=1
    ids=[first['id']]
    if capacity>1:
        ids += [r['id'] for r in db.execute('SELECT i.id FROM '+source+' WHERE '+ready+' AND i.id<>%s AND '+condition+
            ' ORDER BY i.due,i.id LIMIT %s',(now,now,first['id'],*args,capacity-1)).fetchall()]
    with db:
        rows=list(db.execute('SELECT i.* FROM app_icons i WHERE i.id IN ('+','.join('%s' for _ in ids)+') AND '+ready+
            ' AND '+condition+' ORDER BY i.id FOR UPDATE SKIP LOCKED',(*ids,time.time(),time.time(),*args)).fetchall())
        if not rows:return []
        selected=[r['id'] for r in rows]
        db.execute("UPDATE app_icons SET token=%s,lease_until=%s,status='running' WHERE id IN ("+','.join('%s' for _ in selected)+')',
            (token,time.time()+max(600,cfg['lease_seconds']),*selected))
        for row in rows:row['token']=token
        from .collection_batch import touch
        touch(db)
    from .collection_lineage import record_dispatch
    record_dispatch(db,'icons',first['store'],len(rows))
    return rows


def download(cfg,db,client,row,started,max_bytes=None,max_age_seconds=None,force=False):
    from .app_icons import source_url, cached_path, media_type, MAX_BYTES
    MAX_BYTES=max_bytes or MAX_BYTES
    url=source_url(row['source_url']);key=hashlib.sha256(url.encode()).hexdigest()
    with db.named_lock('icon-download:'+key,timeout=120):
        cached=db.execute('SELECT * FROM icon_downloads WHERE url_hash=%s',(key,)).fetchone()
        age=max(0,max_age_seconds if max_age_seconds is not None else row.get('refresh_hours',0)*3600)
        force=force or (row.get('checked_at') is not None and row.get('due',0)<=0)
        required=max(started if force else started-age,time.time()-age)
        path=cached_path(cfg,cached) if cached and cached['source_url']==url and cached['fetched_at']>=required else None
        if path and path.stat().st_size<=MAX_BYTES:
            if getattr(client,'progress',None):client.progress('cache_hit','复用有效期内的共享图片；保留原获取时间')
            return cached
        with client.request(row['store'],'GET',url,stream=True,allow_redirects=False) as response:
            if response.status_code!=200:raise FetchError('图标下载失败')
            if int(response.headers.get('Content-Length','0'))>MAX_BYTES:raise FetchError(f'图片超过{MAX_BYTES//1024**2}MiB限制；保留任务待处理')
            raw=bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw)>MAX_BYTES:raise FetchError(f'图片超过{MAX_BYTES//1024**2}MiB限制；保留任务待处理')
        mime,extension=media_type(raw);digest=hashlib.sha256(raw).hexdigest()
        path=cfg['data_dir']/'icons'/digest[:2]/(digest+'.'+extension);path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            temp=path.with_suffix('.'+row['token']+'.part')
            try:temp.write_bytes(raw);os.replace(temp,path)
            finally:temp.unlink(missing_ok=True)
        result=dict(source_url=url,file_path=path.relative_to(cfg['data_dir']).as_posix(),mime=mime,fetched_at=time.time())
        db.execute('''INSERT INTO icon_downloads(url_hash,source_url,file_path,mime,fetched_at) VALUES(%s,%s,%s,%s,%s) AS incoming
            ON DUPLICATE KEY UPDATE source_url=incoming.source_url,file_path=incoming.file_path,mime=incoming.mime,fetched_at=incoming.fetched_at''',
            (key,url,result['file_path'],mime,result['fetched_at']))
        return result


def work(cfg,db,limit=5,client=None,max_requests=None):
    from .app_icons import source_url
    own=client is None;client=client or Client(cfg,db)
    counts=dict(processed=0,ok=0,error=0,stale=0,prepared=0)
    completed=set();prepared=set();requests=0
    try:
        while len(completed)<limit and (max_requests is None or requests<max_requests):
            if blocked(db,cfg):break
            disk_check(cfg['data_dir'],cfg['min_free_gb'])
            started=time.time()
            rows=claim(db,cfg,limit-len(completed),client)
            if not rows:break
            logs=[Execution(db,'icons',row['id']) for row in rows]
            old_guard,old_progress=getattr(client,'request_guard',None),getattr(client,'progress',None)
            metadata=not source_url(rows[0]['source_url'])
            def guard():
                if old_guard:old_guard()
                active=db.execute('SELECT COUNT(*) n FROM app_icons WHERE token=%s AND enabled=1 AND lease_until>%s',
                    (rows[0]['token'],time.time())).fetchone()['n']
                if active==0 or metadata and active!=len(rows):raise CollectionDeferred('图标批次已暂停或租约失效，等待重新组批',1)
            progress=BatchProgress(logs)
            client.request_guard,client.progress=guard,progress
            client.lineage_batch_key=rows[0]['token'];client.lineage_tasks=len(rows)
            try:
                with db:
                    for log in logs:log.write('started',f'批量{"补齐图标地址" if metadata else "下载相同图标"}；{len(rows)} 个任务')
                try:
                    guard();requests+=1
                    if metadata:
                        batch=len(rows)>1 or rows[0]['store']=='google' and can_batch(rows[0]) and callable(getattr(client,'fetch_many',None))
                        results=client.fetch_many(rows) if batch else [client.fetch(rows[0])]
                        if len(results)!=len(rows):raise FetchError('图标详情批次结果数量异常')
                    else:
                        result=download(cfg,db,client,rows[0],started,
                            max_age_seconds=min(r['refresh_hours']*3600 for r in rows),
                            force=any(r.get('checked_at') is not None and r['due']<=0 for r in rows))
                        results=[result]*len(rows)
                except (FetchError,ValueError,OSError,RuntimeError) as exc:results=[exc]*len(rows)
                with write_batch(db) as writer:
                    for row,result,log in zip(rows,results,logs):
                        try:
                            if isinstance(result,Exception):raise result
                            if metadata:
                                if result.outcome!='ok':raise FetchError('当前地区未找到可用应用图标')
                                url=source_url(result.payload.get('icon'))
                                if not url:raise FetchError('商店未提供受支持的图标地址')
                                saved=writer.execute('''UPDATE app_icons SET source_url=%s,source_updated=%s,name=COALESCE(%s,name),
                                    status='queued',due=0,token=NULL,lease_until=0 WHERE id=%s AND token=%s AND enabled=1 AND lease_until>%s''',
                                    (url,time.time(),result.payload.get('name'),row['id'],row['token'],time.time())).rowcount
                                if saved:
                                    prepared.add(row['id']);log.write('metadata','图标地址已保存，等待共享下载')
                                    if result.payload.get('name'):
                                        writer.execute("UPDATE apps SET name=%s WHERE store=%s AND app_id=%s AND (name IS NULL OR name='' OR name=app_id)",
                                            (result.payload['name'],row['store'],row['app_id']))
                                else:counts['stale']+=1;completed.add(row['id'])
                            else:
                                saved=writer.execute('''UPDATE app_icons SET file_path=%s,mime=%s,checked_at=%s,status='ready',due=%s,
                                    failures=0,last_error=NULL,token=NULL,lease_until=0 WHERE id=%s AND token=%s AND enabled=1 AND lease_until>%s''',
                                    (result['file_path'],result['mime'],result['fetched_at'],result['fetched_at']+row['refresh_hours']*3600,row['id'],row['token'],time.time())).rowcount
                                log.finish('ok',bool(saved),'同一响应按任务独立回填')
                                counts['ok' if saved else 'stale']+=1;completed.add(row['id'])
                        except (FetchError,ValueError,OSError,RuntimeError) as exc:
                            if isinstance(exc,CollectionDeferred):
                                from .collection_batch import defer
                                saved=defer(writer,'app_icons',row,exc)
                                log.write('deferred' if saved else 'stale',str(exc));completed.add(row['id'])
                                counts['deferred']=counts.get('deferred',0)+int(bool(saved))
                                counts['stale']+=int(not saved)
                                continue
                            failures=row['failures']+1
                            saved=writer.execute('''UPDATE app_icons SET status='error',failures=%s,last_error=%s,due=%s,token=NULL,lease_until=0
                                WHERE id=%s AND token=%s AND enabled=1''',
                                (failures,str(exc) if isinstance(exc,FetchError) else type(exc).__name__,
                                 time.time()+max(getattr(exc,'retry_after',0),min(86400,300*2**min(failures,8))),row['id'],row['token'])).rowcount
                            log.finish('error',bool(saved),error=exc);counts['error' if saved else 'stale']+=1;completed.add(row['id'])
            finally:client.request_guard,client.progress=old_guard,old_progress
        counts['prepared']=len(prepared-completed);counts['processed']=len(prepared|completed)
        return counts
    finally:
        if own:client.session.close()
