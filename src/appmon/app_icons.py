"""Bounded, leased local caching of public store icons; no arbitrary URL proxy."""
import hashlib
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from .intelligence import market
from .stores import Client,FetchError
from .ingest import disk_check
from .task_logs import execution

MAX_BYTES=2*1024*1024


def source_url(value):
    if not isinstance(value,str):return None
    try:
        p=urlsplit(value);host=(p.hostname or '').lower()
        if p.scheme not in ('https','http') or p.username or p.password or p.port not in (None,443):return None
        if not any(host.endswith('.'+domain) or host==domain for domain in ('mzstatic.com','googleusercontent.com','ggpht.com')):return None
        return urlunsplit(('https',host,p.path,p.query,''))
    except ValueError:return None


def media_type(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw)>=32 and b'IEND' in raw[-16:]:return 'image/png','png'
    if raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'):return 'image/jpeg','jpg'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' and len(raw)>=20:return 'image/webp','webp'
    if raw[:6] in (b'GIF87a',b'GIF89a') and len(raw)>=14:return 'image/gif','gif'
    raise FetchError('图标不是支持的位图格式')


def remember(db,store,app_id,country,language,url,name=None):
    url=source_url(url)
    if not url:return False
    db.execute('''INSERT INTO app_icons(store,app_id,country,language,name,source_url,source_updated)
        VALUES(%s,%s,%s,%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE
        status=IF(app_icons.requested=1 AND NOT(app_icons.source_url<=>incoming.source_url),'queued',app_icons.status),
        due=IF(app_icons.requested=1 AND NOT(app_icons.source_url<=>incoming.source_url),0,app_icons.due),
        token=IF(NOT(app_icons.source_url<=>incoming.source_url),NULL,app_icons.token),
        lease_until=IF(NOT(app_icons.source_url<=>incoming.source_url),0,app_icons.lease_until),
        source_url=incoming.source_url,source_updated=incoming.source_updated,name=COALESCE(incoming.name,app_icons.name)''',
        (store,app_id,country,language,name,url,time.time()))
    return True


def cached_path(cfg,row):
    if not row.get('file_path'):return None
    folder=(cfg['data_dir']/'icons').resolve();path=(cfg['data_dir']/row['file_path']).resolve()
    return path if path.is_relative_to(folder) and path.is_file() else None


def request_icon(db,cfg,value,force=False):
    m=market(value);key=tuple(m[k] for k in ('store','app_id','country','language'))
    with db:
        row=db.execute('SELECT * FROM app_icons WHERE store=%s AND app_id=%s AND country=%s AND language=%s FOR UPDATE',key).fetchone()
        if not row:
            known=db.execute('''SELECT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.icon')) icon
                FROM listings WHERE store=%s AND app_id=%s AND payload IS NOT NULL
                ORDER BY country=%s AND language=%s DESC,last_success DESC LIMIT 1''',key).fetchone()
            url=source_url((known or {}).get('icon')) or source_url(value.get('source_url'))
            db.execute('''INSERT INTO app_icons(store,app_id,country,language,name,source_url,requested,status)
                VALUES(%s,%s,%s,%s,%s,%s,1,'queued') ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(app_icons.id)''',(*key,str(value.get('name') or m['app_id'])[:255],url))
            row=db.execute('SELECT * FROM app_icons WHERE id=%s',(db.cursor.lastrowid,)).fetchone()
        if force:
            db.execute("UPDATE app_icons SET requested=1,enabled=1,status='queued',due=0 WHERE id=%s AND lease_until<=%s",(row['id'],time.time()))
        elif not cached_path(cfg,row) and (not row['requested'] or row['status']=='ready'):
            db.execute("UPDATE app_icons SET requested=1,status='queued',due=0 WHERE id=%s",(row['id'],))
        row=db.execute('SELECT * FROM app_icons WHERE id=%s',(row['id'],)).fetchone()
    return public(cfg,row)


def public(cfg,row):
    exists=bool(cached_path(cfg,row))
    return {k:row[k] for k in ('id','store','app_id','country','language','status','checked_at')}|{
        'status':'paused' if not row['enabled'] else row['status'],
        'url':f"/api/icons/{row['id']}?v={int((row['checked_at'] or 0)*1000)}" if exists else None,
        'pending':bool(row['enabled'] and row['status'] in ('queued','running'))}


def work_icons(cfg,db,limit=5,client=None):
    client=client or Client(cfg,db);counts={'processed':0,'ok':0,'error':0,'stale':0}
    for _ in range(limit):
        disk_check(cfg['data_dir'],cfg['min_free_gb'])
        with db:
            row=db.execute('''SELECT * FROM app_icons WHERE requested=1 AND enabled=1 AND due<=%s AND lease_until<=%s
                ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED''',(time.time(),time.time())).fetchone()
            if not row:break
            token=uuid.uuid4().hex
            db.execute("UPDATE app_icons SET token=%s,lease_until=%s,status='running' WHERE id=%s",(token,time.time()+cfg['lease_seconds'],row['id']))
        with execution(db,client,'icons',row['id'],f"{row['store']} / {row['app_id']}") as log:
            try:
                url=source_url(row['source_url'])
                if not url:
                    detail=client.fetch(row)
                    if detail.outcome!='ok':raise FetchError('当前地区未找到可用应用图标')
                    url=source_url(detail.payload.get('icon'))
                if not url:raise FetchError('商店未提供受支持的图标地址')
                # Redirects are deliberately not followed outside the CDN allowlist.
                with client.request(row['store'],'GET',url,stream=True,allow_redirects=False) as response:
                    if response.status_code!=200:raise FetchError('图标下载失败')
                    if int(response.headers.get('Content-Length','0'))>MAX_BYTES:raise FetchError('图标超过2MB限制')
                    raw=bytearray()
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw)>MAX_BYTES:raise FetchError('图标超过2MB限制')
                mime,extension=media_type(raw);digest=hashlib.sha256(raw).hexdigest()
                path=cfg['data_dir']/'icons'/digest[:2]/(digest+'.'+extension);path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():
                    temp=path.with_suffix('.'+token+'.part');temp.write_bytes(raw);os.replace(temp,path)
                saved=db.execute('''UPDATE app_icons SET source_url=%s,file_path=%s,mime=%s,checked_at=%s,status='ready',
                    due=%s,failures=0,last_error=NULL,token=NULL,lease_until=0 WHERE id=%s AND token=%s''',
                    (url,path.relative_to(cfg['data_dir']).as_posix(),mime,time.time(),time.time()+row['refresh_hours']*3600,row['id'],token)).rowcount
                log.finish('ok',bool(saved),f'图标已缓存，{len(raw)} 字节');counts['ok' if saved else 'stale']+=1
            except (FetchError,ValueError,OSError) as exc:
                failures=row['failures']+1
                saved=db.execute('''UPDATE app_icons SET status='error',failures=%s,last_error=%s,due=%s,token=NULL,lease_until=0
                    WHERE id=%s AND token=%s''',(failures,str(exc) if isinstance(exc,FetchError) else type(exc).__name__,
                    time.time()+min(86400,300*2**min(failures,8)),row['id'],token)).rowcount
                log.finish('error',bool(saved),detail=str(exc) if isinstance(exc,FetchError) else type(exc).__name__,error=exc);counts['error' if saved else 'stale']+=1
        counts['processed']+=1
    return counts
