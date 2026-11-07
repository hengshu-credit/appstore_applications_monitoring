"""Bounded, leased local caching of public store icons; no arbitrary URL proxy."""
import time
from urllib.parse import urlsplit,urlunsplit
from .intelligence import market
from .stores import FetchError

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


def work_icons(cfg,db,limit=5,client=None,max_requests=None):
    from .icon_batches import work
    return work(cfg,db,limit,client,max_requests)
