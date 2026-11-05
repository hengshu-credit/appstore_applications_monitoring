"""Google Play declared permissions, collected separately from listing availability."""
import json
import time
import uuid
from .db import dump
from .stores import Client, FetchError
from .monitor import raw_save
from .collection_limits import settings


def parse(raw):
    from appgoblin_play_scraper.constants.element import ElementSpecs
    from .observation_stores import batch_payload
    try:
        container=batch_payload(raw.decode('utf-8'),'xdSrCf')
        if not isinstance(container,list):raise ValueError()
        if container and all(v is None for v in container):raise ValueError()
        result={}
        for items in container:
            if items is None:continue
            if not isinstance(items,list):raise ValueError()
            if not items:continue
            if not isinstance(items[0],list):raise ValueError()
            if len(items[0])==2:items=[['Other',None,items,None]]
            for permission in items:
                if permission is None:continue
                name=ElementSpecs.PermissionType.extract_content(permission)
                values=ElementSpecs.PermissionList.extract_content(permission)
                if not isinstance(name,str) or not isinstance(values,list) or any(not isinstance(v,str) for v in values):raise ValueError()
                result.setdefault(name,[]).extend(values)
        return {k:sorted(set(v)) for k,v in result.items()}
    except (ValueError,TypeError,IndexError,KeyError,StopIteration,AttributeError):
        raise FetchError('权限响应格式异常，保留上次有效清单') from None


def enqueue(db, members):
    rows=sorted({(r['store'],r['app_id'],r['country']) for r in members if r['store']=='google'})
    if rows:db.executemany('''INSERT INTO app_permission_targets(store,app_id,country) VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE id=app_permission_targets.id''',rows)
    return len(rows)


def state(row, now=None):
    now=time.time() if now is None else now
    if row.get('store')=='apple':return 'unsupported'
    if ('permission_id' in row and not row['permission_id']) or ('permission_id' not in row and not row.get('id')):return 'not_scheduled'
    if row.get('permission_enabled',row.get('enabled',True))==0:return 'paused'
    if (row.get('permission_lease',row.get('lease_until',0)) or 0)>now:return 'running'
    if row.get('permission_failures',row.get('failures',0)):return 'error'
    success=row.get('permission_success',row.get('success_at'))
    if success:return 'ok' if success+row.get('permission_refresh_hours',row.get('refresh_hours',24))*3600>now else 'stale'
    return 'pending'


def work(cfg, db, limit=5, client=None):
    from appgoblin_play_scraper.constants.request import Formats
    own=client is None;client=client or Client(cfg,db);count=dict(processed=0,ok=0,error=0)
    try:
        for _ in range(limit):
            from .ingest import disk_check
            disk_check(cfg['data_dir'],cfg.get('min_free_gb',2))
            if own:
                if settings(db,cfg)['paused']:break
                cooldown=db.execute("SELECT MAX(next_at) at FROM schedules WHERE name IN ('rate:global','rate:google')").fetchone()['at']
                if cooldown and cooldown>time.time()+5:break
            with db:
                row=db.execute('''SELECT * FROM app_permission_targets WHERE enabled=1 AND due<=%s AND lease_until<=%s
                    ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED''',(time.time(),time.time())).fetchone()
                if not row:break
                token=uuid.uuid4().hex
                db.execute('UPDATE app_permission_targets SET token=%s,lease_until=%s WHERE id=%s',(token,time.time()+cfg['lease_seconds'],row['id']))
            payload=raw_path=None;error=None;delay=0
            try:
                def guard():
                    current=db.execute('SELECT enabled,token FROM app_permission_targets WHERE id=%s',(row['id'],)).fetchone()
                    if not current or not current['enabled'] or current['token']!=token:raise FetchError('权限任务已暂停或被其他进程接管',60)
                client.request_guard=guard
                response=client.request('google','POST',Formats.Permissions.build(lang='en',country=row['country']),
                    data=Formats.Permissions.build_body(row['app_id']),headers={'Content-Type':'application/x-www-form-urlencoded'})
                if response.status_code!=200:raise FetchError('权限接口暂不可用')
                payload=parse(response.content)
                raw_path=raw_save(cfg['data_dir'],response.content)
            except FetchError as exc:error=str(exc);delay=exc.retry_after
            except Exception as exc:error='权限采集失败：'+type(exc).__name__
            with db:
                current=db.execute('SELECT token,enabled FROM app_permission_targets WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
                if not current or current['token']!=token or not current['enabled']:continue
                now=time.time();outcome='error' if error else 'ok'
                db.execute('INSERT INTO app_permission_runs(target_id,observed_at,outcome,payload,raw_path,error) VALUES(%s,%s,%s,%s,%s,%s)',
                    (row['id'],now,outcome,dump(payload) if payload is not None else None,raw_path,error))
                if error:
                    db.execute('''UPDATE app_permission_targets SET failures=failures+1,last_error=%s,checked_at=%s,
                        due=%s,lease_until=0,token=NULL WHERE id=%s''',(error,now,now+max(delay,min(86400,300*2**min(row['failures'],8))),row['id']))
                else:
                    db.execute('''UPDATE app_permission_targets SET failures=0,last_error=NULL,checked_at=%s,success_at=%s,
                        due=%s,lease_until=0,token=NULL,payload=%s,permission_count=%s,raw_path=%s WHERE id=%s''',
                        (now,now,now+row['refresh_hours']*3600,dump(payload),sum(len(v) for v in payload.values()),raw_path,row['id']))
            count['processed']+=1;count[outcome]+=1
    finally:
        if own:client.session.close()
    return count
