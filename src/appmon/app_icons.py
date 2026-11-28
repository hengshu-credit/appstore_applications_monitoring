"""Bounded, leased S3 archiving of public store icons; no arbitrary URL proxy."""
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


def media_type(raw, allow_video=False):
    if allow_video and len(raw)>16 and raw[4:8]==b'ftyp':return 'video/mp4','mp4'
    if allow_video and raw.startswith(bytes.fromhex('1a45dfa3')):return 'video/webm','webm'
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw)>=32 and b'IEND' in raw[-16:]:return 'image/png','png'
    if jpeg_complete(raw):return 'image/jpeg','jpg'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' and len(raw)>=20:return 'image/webp','webp'
    if raw[:6] in (b'GIF87a',b'GIF89a') and len(raw)>=14:return 'image/gif','gif'
    raise FetchError('图标不是支持的位图格式')


def jpeg_complete(raw):
    """Check JPEG segment/scan framing; CDN metadata may follow the EOI marker."""
    if not raw.startswith(b'\xff\xd8'):return False
    pos=2;scan=False;frame=False;seen_scan=False
    while pos<len(raw):
        if scan:
            pos=raw.find(b'\xff',pos)
            if pos<0:return False
        elif raw[pos]!=255:return False
        while pos<len(raw) and raw[pos]==255:pos+=1
        if pos>=len(raw):return False
        marker=raw[pos];pos+=1
        if marker==0:
            if not scan:return False
            continue
        if 0xd0<=marker<=0xd7:
            if not scan:return False
            continue
        if marker==0xd9:return frame and seen_scan
        if marker==0xd8:return False
        if marker==1:continue
        if pos+2>len(raw):return False
        length=int.from_bytes(raw[pos:pos+2],'big')
        if length<2 or pos+length>len(raw):return False
        if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
            if length<11 or raw[pos+7]==0 or length<8+3*raw[pos+7]:return False
            frame=True
        if marker==0xda:
            if not frame or length<6:return False
            scan=True;seen_scan=True
        else:scan=False
        pos+=length
    return False


def eligible(alias):
    return f"({alias}.request_mode<>'auto' OR (NULLIF({alias}.source_url,'') IS NULL AND NULLIF({alias}.file_path,'') IS NULL))"


def remember_many(db, rows):
    now=time.time()
    records=[(*key,name,url,now) for key,name,value in rows if (url:=source_url(value))]
    if not records:return False
    records.sort(key=lambda record:record[:4])
    db.executemany("""INSERT INTO app_icons(store,app_id,country,language,name,source_url,source_updated)
        VALUES(%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
        status=IF(app_icons.request_mode='auto',IF(app_icons.file_path IS NULL,'available','ready'),
            IF(app_icons.requested=1 AND NOT(app_icons.source_url<=>VALUES(source_url)),'queued',app_icons.status)),
        requested=IF(app_icons.request_mode='auto',0,app_icons.requested),
        due=IF(app_icons.requested=1 AND NOT(app_icons.source_url<=>VALUES(source_url)),LEAST(app_icons.due,0),app_icons.due),
        token=IF(app_icons.request_mode='auto' OR NOT(app_icons.source_url<=>VALUES(source_url)),NULL,app_icons.token),
        lease_until=IF(app_icons.request_mode='auto' OR NOT(app_icons.source_url<=>VALUES(source_url)),0,app_icons.lease_until),
        source_url=VALUES(source_url),source_updated=VALUES(source_updated),name=COALESCE(VALUES(name),app_icons.name)""",records)
    return True


def remember(db,store,app_id,country,language,url,name=None):
    return remember_many(db,[((store,app_id,country,language),name,url)])


def upgrade_requests(db,legacy_backup=False):
    """Recover explicit schedules before retiring old automatic downloads."""
    marker='icon-request-policy-upgrade'
    def pending():
        return db.execute('SELECT 1 FROM collection_controls WHERE name=%s',(marker,)).fetchone()
    def has_mode():
        return db.execute("SHOW COLUMNS FROM app_icons LIKE 'request_mode'").fetchone()
    if not legacy_backup and has_mode() and not pending():return
    import json
    with db.named_lock(marker,timeout=30):
        missing=not has_mode()
        if missing or legacy_backup:
            db.execute("INSERT INTO collection_controls VALUES(%s,'{}',%s) ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)",
                       (marker,time.time()))
        elif not pending():return
        if missing:
            db.execute("ALTER TABLE app_icons ADD COLUMN request_mode VARCHAR(12) NOT NULL DEFAULT 'auto'")
        # A completed plan action (or an edited icon interval) is durable
        # evidence of a periodic schedule. A visible page alone was not.
        last=0
        while True:
            plans=db.execute("""SELECT id,actions,completed FROM collection_plans WHERE id>%s AND completed>0
                AND JSON_SEARCH(actions,'one','icons',NULL,'$[*].kind') IS NOT NULL ORDER BY id LIMIT 25""",(last,)).fetchall()
            if not plans:break
            keys=[tuple(a['value'][k] for k in ('store','app_id','country','language'))
                  for plan in plans for a in json.loads(plan['actions'])[:plan['completed']] if a['kind']=='icons']
            if keys:
                db.executemany("""UPDATE app_icons SET request_mode='periodic',requested=1
                    WHERE store=%s AND app_id=%s AND country=%s AND language=%s""",keys)
            last=plans[-1]['id']
        with db:
            db.execute("""UPDATE app_icons i JOIN (SELECT DISTINCT target_key FROM collection_logs
                WHERE resource='icons' AND event IN ('interval','settings_updated')) edited
                ON edited.target_key=CAST(i.id AS CHAR CHARACTER SET ascii) SET i.request_mode='periodic',i.requested=1""")
            db.execute("""UPDATE app_icons SET request_mode='once' WHERE requested=1 AND due<0 AND request_mode='auto'""")
        last=0
        while True:
            rows=db.execute("""SELECT id,source_url,file_path FROM app_icons WHERE id>%s AND requested=1
                AND request_mode='auto' ORDER BY id LIMIT 1000""",(last,)).fetchall()
            if not rows:break
            known=[r['id'] for r in rows if r['file_path'] or source_url(r['source_url'])]
            invalid=[r['id'] for r in rows if r['source_url'] and not r['file_path'] and not source_url(r['source_url'])]
            if invalid:
                db.execute("UPDATE app_icons SET source_url=NULL WHERE request_mode='auto' AND id IN %s",(tuple(invalid),))
            if known:
                db.execute("""UPDATE app_icons SET requested=0,token=NULL,lease_until=0,
                    status=IF(file_path IS NULL,'available','ready') WHERE request_mode='auto' AND id IN %s""",(tuple(known),))
            last=rows[-1]['id']
        db.execute('DELETE FROM collection_controls WHERE name=%s',(marker,))


def cached_path(cfg,row):
    if not row.get('file_path'):return None
    from .objects import for_config
    return row['file_path'] if for_config(cfg).exists(row['file_path']) else None


def peer_urls(db,cfg,store,country,language,app_ids):
    """Reuse another locale's display image for the same store application."""
    peers=db.execute("""SELECT * FROM app_icons WHERE store=%s AND app_id IN %s AND NOT(country=%s AND language=%s)
        AND (source_url IS NOT NULL OR file_path IS NOT NULL) ORDER BY country=%s DESC,checked_at DESC,id""",
        (store,tuple(app_ids),country,language,country)).fetchall()
    found={}
    for peer in peers:
        if peer['app_id'] not in found:
            url=source_url(peer['source_url']) or public(cfg,peer)['url']
            if url:found[peer['app_id']]=url
    return found


def request_icon(db,cfg,value,force=False):
    from .collection_batch import PAGE_DUE
    m=market(value);key=tuple(m[k] for k in ('store','app_id','country','language'))
    from .storage_policy import media_allowed
    def find():
        return db.execute('SELECT * FROM app_icons WHERE store=%s AND app_id=%s AND country=%s AND language=%s',key).fetchone()

    row=find()
    current=public(cfg,row) if row else m|{'id':None,'url':None,'pending':False,'checked_at':None}
    if not force and current['url']:
        if row['request_mode']=='auto' and row['enabled'] and current['url'].startswith('https://'):
            current['status']='links_only'
        return current
    url=source_url((row or {}).get('source_url')) or source_url(value.get('source_url'))
    if not url and not current['url']:
        if not force:
            # The app may already have an icon from a different listing or chart.
            known=peer_urls(db,cfg,m['store'],m['country'],m['language'],[m['app_id']]).get(m['app_id'])
            if known:
                if row and row['requested'] and row['request_mode']=='auto':
                    db.execute("""UPDATE app_icons SET requested=0,status='available',token=NULL,lease_until=0
                        WHERE id=%s AND request_mode='auto'""",(row['id'],))
                return current|{'url':known,'pending':False,'status':'available'}
        listing=db.execute("""SELECT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.icon')) icon FROM listings
            WHERE store=%s AND app_id=%s AND JSON_EXTRACT(payload,'$.icon') IS NOT NULL
            ORDER BY (country=%s AND language=%s) DESC,last_success DESC LIMIT 1""",key).fetchone()
        url=source_url((listing or {}).get('icon'))
    if not force:
        if url and not source_url((row or {}).get('source_url')):
            remember(db,*key,url,value.get('name'))
            row=find()
            current=public(cfg,row)
        if url and not current['pending']:
            return current|{'url':current['url'] or url,'status':'links_only' if current.get('status')=='available' else current['status']}
        if row and not row['enabled']:return current
        if current['pending'] and not url and (row['request_mode']!='auto' or not row['source_url'] and not row['file_path']):
            return current
        if not row or row['request_mode']=='auto':
            if not media_allowed(db,cfg,m['store'],m['app_id']):
                return current|{'pending':False,'status':'links_only','url':url}
    with db:
        if url and (not row or not source_url(row['source_url'])):
            remember(db,*key,url,value.get('name'))
        db.execute("""INSERT INTO app_icons(store,app_id,country,language,name)
            VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=app_icons.id""",(*key,str(value.get('name') or m['app_id'])[:255]))
        row=find()
        if force:
            db.execute("""UPDATE app_icons SET request_mode=IF(request_mode='periodic','periodic','once'),
                requested=1,enabled=1,status=IF(lease_until<=%s,'queued',status),due=LEAST(due,%s)
                WHERE id=%s""",(time.time(),PAGE_DUE,row['id']))
            from .task_windows import page_submitted
            page_submitted(db,'icons',[row['id']])
        elif row['enabled'] and not source_url(row['source_url']) and not cached_path(cfg,row) and (not row['requested'] or row['file_path'] or row['source_url']):
            db.execute("UPDATE app_icons SET requested=1,status='queued',source_url=NULL,file_path=NULL,due=LEAST(due,0) WHERE id=%s",(row['id'],))
        return public(cfg,find())


def public(cfg,row):
    exists=bool(cached_path(cfg,row))
    url=f"/api/icons/{row['id']}?v={int((row['checked_at'] or 0)*1000)}" if exists else source_url(row['source_url'])
    return {k:row[k] for k in ('id','store','app_id','country','language','status','checked_at')}|{
        'status':'paused' if not row['enabled'] else row['status'],
        'url':url, 'pending':bool(row['requested'] and row['enabled'] and row['status'] in ('queued','running'))}


def work_icons(cfg,db,limit=5,client=None,max_requests=None,*,due_by=None):
    from .icon_batches import work
    return work(cfg,db,limit,client,max_requests,due_by=due_by)
