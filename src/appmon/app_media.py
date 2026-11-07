"""Image-only archives for group applications and latest chart members."""
import hashlib
import json
import time
import uuid
from .app_icons import source_url
from .storage_policy import media_allowed,media_predicate
from .collection_batch import touch

FIELDS={'screenshots':'应用截图','ipad_screenshots':'iPad 截图','appletv_screenshots':'Apple TV 截图',
        'header_image':'宣传图','video_image':'视频封面'}


def remember(db,cfg,job,payload):
    if not media_allowed(db,cfg,job['store'],job['app_id']):return 0
    from .app_icons import remember as icon_remember
    icon_remember(db,job['store'],job['app_id'],job['country'],job['language'],payload.get('icon'),payload.get('name'))
    db.execute('''UPDATE app_icons SET requested=1 WHERE store=%s AND app_id=%s AND country=%s AND language=%s''',
               tuple(job[k] for k in ('store','app_id','country','language')))
    count=0
    for field,label in FIELDS.items():
        if field not in payload:continue
        values=payload[field] or []
        if isinstance(values,str):values=[values]
        if not isinstance(values,list):continue
        db.execute('DELETE FROM app_media_refs WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND kind=%s',
                   (*[job[k] for k in ('store','app_id','country','language')],field))
        for position,value in enumerate(values):
            url=source_url(value)
            if not url:continue
            digest=hashlib.sha256(url.encode()).hexdigest()
            db.execute('''INSERT INTO app_media_assets(store,url_hash,source_url,label) VALUES(%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(app_media_assets.id),enabled=IF(app_media_assets.manual_paused=0,1,0)''',(job['store'],digest,url,label))
            identifier=db.cursor.lastrowid
            db.execute('INSERT INTO app_media_refs VALUES(%s,%s,%s,%s,%s,%s,%s)',
                       (*[job[k] for k in ('store','app_id','country','language')],field,position,identifier))
            count+=1
    return count


def active_predicate(alias='a'):
    return f'EXISTS(SELECT 1 FROM app_media_refs r WHERE r.asset_id={alias}.id AND '+media_predicate('r')+')'


def sync_existing(db,cfg,wait=0):
    """Revisit only small focus/chart sets; never scan millions of listings in memory."""
    with db.named_lock('media-scope-sync',timeout=wait):
        from .chart_enrichment import settings as enrichment_settings
        incremental=enrichment_settings(db).get('enabled',False)
        desired='''SELECT store,app_id,country,language FROM app_group_members UNION
            SELECT t.store,o.item_id,t.country,t.language FROM observation_targets t
            JOIN observation_items o ON o.run_id=t.latest_success_id
            WHERE t.kind='chart' AND t.latest_success_id IS NOT NULL AND o.position<=50 '''
        # DISTINCT/UNION materializes the small eligible set once, rather than
        # rechecking all current charts for every icon in the global app pool.
        eligible='SELECT DISTINCT store,app_id FROM ('+desired+') media_markets'
        if incremental:desired='SELECT store,app_id,country,language FROM app_group_members'
        # Materialize only focus/latest-chart detail dependencies, not a global market product.
        with db:
            db.execute('''INSERT INTO watchlist(store,app_id,country,language,group_name,refresh_hours,enabled,created_at)
                SELECT store,app_id,country,language,'图片素材补全',24,1,%s FROM ('''+desired+''') desired
                ON DUPLICATE KEY UPDATE id=watchlist.id''',(time.time(),))
            db.execute('''INSERT INTO jobs(store,app_id,country,language)
                SELECT store,app_id,country,language FROM ('''+desired+''') desired
                ON DUPLICATE KEY UPDATE app_id=jobs.app_id''')
            db.execute('UPDATE app_icons i JOIN ('+eligible+''') scope ON scope.store=i.store AND scope.app_id=i.app_id
                SET i.requested=1 WHERE i.requested=0''')
        count=processed=0;last=None
        while True:
            scope='SELECT DISTINCT store,app_id FROM app_group_members' if incremental else eligible
            rows=db.execute('SELECT l.* FROM ('+scope+''') scope JOIN listings l
                ON scope.store=l.store AND scope.app_id=l.app_id WHERE l.payload IS NOT NULL'''+
                (' AND (l.store,l.app_id,l.country,l.language)>(%s,%s,%s,%s)' if last else '')+
                ' ORDER BY l.store,l.app_id,l.country,l.language LIMIT 200',last).fetchall()
            if not rows:break
            with db:
                for row in rows:count+=remember(db,cfg,row,json.loads(row['payload']))
            processed+=len(rows)
            last=tuple(rows[-1][k] for k in ('store','app_id','country','language'))
        db.execute('''UPDATE app_media_assets a LEFT JOIN (SELECT DISTINCT r.asset_id FROM app_media_refs r
            JOIN ('''+eligible+''') scope ON scope.store=r.store AND scope.app_id=r.app_id) active ON active.asset_id=a.id
            SET a.enabled=0,a.token=NULL,a.lease_until=0 WHERE active.asset_id IS NULL AND (a.enabled=1 OR a.token IS NOT NULL)''')
        if cfg.get('media_scope','all')!='all':
            db.execute('UPDATE app_icons i LEFT JOIN ('+eligible+''') scope ON scope.store=i.store AND scope.app_id=i.app_id
                SET i.requested=0,i.token=NULL,i.lease_until=0 WHERE scope.app_id IS NULL AND (i.requested=1 OR i.token IS NOT NULL)''')
        touch(db)
        return {'listings':processed,'image_references':count}


def work(cfg,db,client):
    from .collection_limits import blocked
    from .icon_batches import download
    from .task_logs import Execution
    if blocked(db,cfg):return {'processed':0}
    now=time.time();token=uuid.uuid4().hex
    # Scope joins are nonlocking; only the chosen asset is locked and rechecked.
    candidate=db.execute('SELECT a.id FROM app_media_assets a WHERE a.enabled=1 AND a.due<=%s AND a.lease_until<=%s AND '+active_predicate()+
        ' ORDER BY a.due,a.id LIMIT 1',(now,now)).fetchone()
    if not candidate:return {'processed':0}
    with db:
        row=db.execute('SELECT * FROM app_media_assets WHERE id=%s AND enabled=1 AND due<=%s AND lease_until<=%s FOR UPDATE SKIP LOCKED',
            (candidate['id'],now,now)).fetchone()
        if not row:return {'processed':0}
        db.execute('UPDATE app_media_assets SET token=%s,lease_until=%s WHERE id=%s',(token,now+600,row['id']))
    row['token']=token;log=Execution(db,'media',row['id']);log.write('started','开始下载共享图片：'+row['label'])
    from .collection_lineage import record_dispatch
    record_dispatch(db,'screenshots',row['store'],1)
    old=client.request_guard
    def guard():
        from .stores import CollectionDeferred
        if old:old()
        if not db.execute('SELECT 1 FROM app_media_assets a WHERE a.id=%s AND a.token=%s AND a.enabled=1 AND '+active_predicate(),(row['id'],token)).fetchone():
            raise CollectionDeferred('图片任务已暂停或移出关注范围',60)
    client.request_guard=guard
    try:
        guard()
        result=download(cfg,db,client,row,now,max_bytes=16*1024*1024)
        guard()
        with db:
            saved=db.execute('''UPDATE app_media_assets SET file_path=%s,mime=%s,checked_at=%s,due=%s,
                token=NULL,lease_until=0,failures=0,last_error=NULL WHERE id=%s AND token=%s''',
                (result['file_path'],result['mime'],result['fetched_at'],result['fetched_at']+row['refresh_hours']*3600,row['id'],token)).rowcount
            log.finish('ok',bool(saved));touch(db)
        return {'processed':1,'ok':saved}
    except Exception as exc:
        from .stores import CollectionDeferred
        if isinstance(exc,CollectionDeferred):
            from .collection_batch import defer
            with db:
                saved=defer(db,'app_media_assets',row,exc)
                log.write('deferred' if saved else 'stale',str(exc));touch(db)
            return {'processed':1,'deferred':saved}
        from .task_logs import safe_message
        with db:
            saved=db.execute('''UPDATE app_media_assets SET failures=failures+1,last_error=%s,due=%s,token=NULL,lease_until=0
                WHERE id=%s AND token=%s''',(safe_message(exc),time.time()+max(60,min(86400,300*2**min(row['failures'],8))),row['id'],token)).rowcount
            log.finish('error',bool(saved),error=exc);touch(db)
        return {'processed':1,'error':saved}
    finally:client.request_guard=old
