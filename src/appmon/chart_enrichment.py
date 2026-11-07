"""Global Top-50 charts and idempotent, incremental app-data follow-ups."""
import json
import hashlib
import time
from .db import dump
from .catalog import CATALOG,APPLE_COUNTRIES
from .locales import language_for
from .collection_batch import touch

CONTROL='global-top50'
COLLECTIONS=('top-free','top-paid','top-grossing')
DEPTH=50


def settings(db):
    row=db.execute('SELECT settings FROM collection_controls WHERE name=%s',(CONTROL,)).fetchone()
    return json.loads(row['settings']) if row else {'enabled':False}


def matrix():
    # These are configured request regions, not a claim that every upstream
    # country/category combination will actually return a supported chart.
    for store in ('google','apple'):
        categories=sorted({''}|{r['id'] for r in CATALOG['categories'][store] if not r['legacy']})
        for country in CATALOG['countries']:
            code=country['code']
            if store=='apple' and code not in APPLE_COUNTRIES:continue
            for collection in COLLECTIONS:
                for category in categories:
                    yield (store,code,language_for(store,code),collection,category)


def start(db):
    rows=list(matrix());signature=hashlib.sha256(dump(rows).encode()).hexdigest()
    now=time.time();counts={};batch=[]
    def write(rows):
        with db:
            db.executemany('''INSERT INTO observation_targets(kind,store,app_id,country,language,collection,category,depth,refresh_hours)
                VALUES('chart',%s,'',%s,%s,%s,%s,50,24) AS incoming ON DUPLICATE KEY UPDATE
                enabled=1,depth=GREATEST(observation_targets.depth,incoming.depth)''',rows)
    with db.named_lock('global-top50-start',timeout=30):
        old=settings(db)
        if old.get('enabled') and old.get('signature')==signature:return old|{'reused':True}
        for row in rows:
            counts[row[0]]=counts.get(row[0],0)+1;batch.append(row)
            if len(batch)==1000:write(batch);batch=[]
        if batch:write(batch)
        value={'enabled':True,'signature':signature,'depth':DEPTH,'collections':list(COLLECTIONS),'category_scope':'all_non_legacy',
               'chart_targets':counts,'request_regions':{'google':len(CATALOG['countries']),'apple':len(APPLE_COUNTRIES)},
               'started_at':now,'review_depth':500,'keyword_depth':200,'video_policy':'source_links_only',
               'source_gaps':['历史评论受来源窗口限制','关键词先跟踪应用名，不代表完整索引词库','下载和收入等商业数据仍需相应来源权限与额度']}
        db.execute('''INSERT INTO collection_controls VALUES(%s,%s,%s) AS incoming
            ON DUPLICATE KEY UPDATE settings=incoming.settings,updated_at=incoming.updated_at''',(CONTROL,dump(value),now))
        from .task_logs import emit
        for store,count in counts.items():
            emit(db,'top50_intake','chart','queued','全球 Top50 榜单目标已入队',details={'store':store,'tasks':count})
        touch(db)
    return value


def activate_once(db,resource,table,ids,started_at):
    ids=set(ids)
    if not ids:return 0
    old={r['target_id'] for r in db.execute('SELECT target_id FROM chart_enrichment_tasks WHERE resource=%s AND target_id IN ('+
        ','.join('%s' for _ in ids)+')',(resource,*ids)).fetchall()}
    added=sorted(ids-old)
    if not added:return 0
    # A new campaign can start previously paused work, but never overrides a
    # pause issued after the campaign began or repeatedly revives a shared task.
    db.execute(f'UPDATE {table} t SET enabled=1 WHERE id IN ('+','.join('%s' for _ in added)+''')
        AND NOT EXISTS(SELECT 1 FROM collection_logs l WHERE l.resource=%s AND l.target_key=CAST(t.id AS CHAR CHARACTER SET ascii)
            AND l.event='pause' AND l.created_at>=%s)''',(*added,resource,started_at))
    db.executemany('INSERT INTO chart_enrichment_tasks VALUES(%s,%s,%s)',[(resource,i,time.time()) for i in added])
    return len(added)


def sync(db,cfg,limit=20):
    plan=settings(db)
    if not plan.get('enabled'):return {'runs':0,'apps':0}
    total=0;processed=0
    try:
        with db.named_lock('chart-top50-followups'):
            targets=db.execute('''SELECT t.* FROM observation_targets t
                LEFT JOIN chart_enrichment_runs e ON e.run_id=t.latest_success_id
                WHERE t.kind='chart' AND t.latest_success_id IS NOT NULL AND e.run_id IS NULL
                ORDER BY t.latest_success_id LIMIT %s''',(limit,)).fetchall()
            for target in targets:
                with db:
                    current=db.execute('SELECT latest_success_id FROM observation_targets WHERE id=%s FOR UPDATE',(target['id'],)).fetchone()
                    if current['latest_success_id']!=target['latest_success_id']:continue
                    rows=db.execute('SELECT item_id,payload FROM observation_items WHERE run_id=%s AND position<=%s ORDER BY position',
                        (target['latest_success_id'],DEPTH)).fetchall()
                    apps=[(target['store'],r['item_id'],target['country'],target['language']) for r in rows]
                    if apps:
                        now=time.time()
                        db.executemany('''INSERT INTO chart_enrichment_apps(store,app_id,country,language,last_run_id,first_seen,last_seen)
                            VALUES(%s,%s,%s,%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE
                            last_run_id=incoming.last_run_id,last_seen=incoming.last_seen''',
                            [(*app,target['latest_success_id'],now,now) for app in apps])
                        db.executemany('''INSERT INTO watchlist(store,app_id,country,language,group_name,refresh_hours,enabled,created_at)
                            VALUES(%s,%s,%s,%s,'全球榜单 Top50',24,1,%s) ON DUPLICATE KEY UPDATE id=watchlist.id''',[(*app,now) for app in apps])
                        db.executemany('''INSERT INTO jobs(store,app_id,country,language) VALUES(%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE app_id=jobs.app_id''',apps)
                        db.executemany('''INSERT INTO observation_targets(kind,store,app_id,country,language,depth,refresh_hours)
                            VALUES('reviews',%s,%s,%s,%s,500,24) AS incoming ON DUPLICATE KEY UPDATE
                            due=IF(incoming.depth>observation_targets.depth AND observation_targets.failures=0,LEAST(observation_targets.due,UNIX_TIMESTAMP()),observation_targets.due),
                            depth=GREATEST(observation_targets.depth,incoming.depth)''',apps)
                        if target['store']=='google':
                            db.executemany('''INSERT INTO app_permission_targets(store,app_id,country) VALUES(%s,%s,%s)
                                ON DUPLICATE KEY UPDATE id=app_permission_targets.id''',[(s,a,c) for s,a,c,_ in apps])
                    words=[]
                    from .keywords import validate_target
                    from .app_icons import remember
                    from .app_media import remember as remember_media
                    for app,row in zip(apps,rows):
                        item=json.loads(row['payload']);s,a,c,l=app
                        if item.get('icon'):
                            remember(db,s,a,c,l,item['icon'],item.get('name'))
                            db.execute('UPDATE app_icons SET requested=1 WHERE store=%s AND app_id=%s AND country=%s AND language=%s',app)
                        listing=db.execute('SELECT payload FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s',app).fetchone()
                        if listing and listing['payload']:
                            remember_media(db,cfg,dict(zip(('store','app_id','country','language'),app)),json.loads(listing['payload']))
                        try:word=validate_target(dict(store=s,country=c,language=l,keyword=item.get('name',''),depth=200,refresh_hours=24))
                        except ValueError:continue
                        words.append((s,c,l,word['keyword'],200,24))
                    if words:
                        db.executemany('''INSERT INTO keyword_targets(store,country,language,keyword,depth,refresh_hours) VALUES(%s,%s,%s,%s,%s,%s) AS incoming
                            ON DUPLICATE KEY UPDATE due=IF(incoming.depth>keyword_targets.depth AND keyword_targets.failures=0,LEAST(keyword_targets.due,UNIX_TIMESTAMP()),keyword_targets.due),
                            depth=GREATEST(keyword_targets.depth,incoming.depth)''',list(dict.fromkeys(words)))
                    if apps:
                        app_ids=[app[1] for app in apps];marks=','.join('%s' for _ in app_ids)
                        for resource,table,extra in [('watchlist','watchlist',''),('targets','observation_targets'," AND kind='reviews'"),
                                                     ('icons','app_icons',''),('permissions','app_permission_targets','')]:
                            if resource=='permissions' and target['store']!='google':continue
                            scope='store=%s AND country=%s'+('' if resource=='permissions' else ' AND language=%s')
                            args=[target['store'],target['country']]+([] if resource=='permissions' else [target['language']])+app_ids
                            ids=[r['id'] for r in db.execute(f'SELECT id FROM {table} WHERE '+scope+' AND app_id IN ('+marks+')'+extra,args).fetchall()]
                            count=activate_once(db,resource,table,ids,plan['started_at'])
                            if count:
                                from .task_logs import emit
                                kind={'watchlist':'details','targets':'reviews','icons':'icons','permissions':'permissions'}[resource]
                                emit(db,'top50_intake',kind,'queued','榜内应用资料任务已关联',details={'store':target['store'],'tasks':count})
                    if words:
                        terms=list({w[3] for w in words})
                        ids=[r['id'] for r in db.execute('SELECT id FROM keyword_targets WHERE store=%s AND country=%s AND language=%s AND keyword IN ('+
                            ','.join('%s' for _ in terms)+')',(target['store'],target['country'],target['language'],*terms)).fetchall()]
                        count=activate_once(db,'keywords','keyword_targets',ids,plan['started_at'])
                        if count:
                            from .task_logs import emit
                            emit(db,'top50_intake','keywords','queued','榜内应用名称关键词已关联',details={'store':target['store'],'tasks':count})
                    db.execute('INSERT INTO chart_enrichment_runs(run_id,target_id,app_count,keyword_count,created_at) VALUES(%s,%s,%s,%s,%s)',
                        (target['latest_success_id'],target['id'],len(apps),len(set(words)),time.time()))
                    touch(db);processed+=1;total+=len(apps)
    except RuntimeError as exc:
        if str(exc)!='Another process is already importing this dataset':raise
    return {'runs':processed,'apps':total}


def report(db):
    value=settings(db)
    value['followups']=db.execute('SELECT COUNT(*) runs,COALESCE(SUM(app_count),0) app_appearances FROM chart_enrichment_runs').fetchone()
    value['followups']={k:int(v or 0) for k,v in value['followups'].items()}
    value['app_markets']=db.execute('SELECT COUNT(*) n FROM chart_enrichment_apps').fetchone()['n']
    value['chart_states']=db.execute("SELECT store,enabled,COUNT(*) targets,SUM(latest_success_id IS NOT NULL) observed,SUM(failures>0) errors FROM observation_targets WHERE kind='chart' GROUP BY store,enabled").fetchall()
    for row in value['chart_states']:
        for key in ('targets','observed','errors'):row[key]=int(row[key] or 0)
    return value
