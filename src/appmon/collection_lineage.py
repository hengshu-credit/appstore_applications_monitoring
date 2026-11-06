"""Operational collection lineage from queue identities and persisted observations."""
import logging
import time
from urllib.parse import urlsplit

from . import collection_tasks, collection_limits, dispatcher, pipelines
from .task_logs import emit

log = logging.getLogger(__name__)
KINDS = dict(details='应用详情',keywords='关键词排名',chart='分类榜单',reviews='用户评论',
             featured='编辑推荐',permissions='声明权限',icons='应用图标',pool='全池详情')
SOURCES = {
    'google':('Google Play','play.google.com','详情、关键词、榜单、评论和权限；同市场合并 RPC'),
    'lookup':('Apple Lookup','itunes.apple.com/lookup','应用详情、开发者、版本、素材；同市场合并 ID'),
    'search':('Apple Search','search.itunes.apple.com / apps.apple.com','关键词搜索与排名；依地区采用原生或网页搜索'),
    'rss':('Apple RSS','itunes.apple.com/{country}/rss','分类榜单与近期评论'),
    'today':('Apple Today','apps.apple.com/{country}/iphone/today','编辑推荐卡片及应用'),
    'cdn':('商店图片 CDN','Google / Apple 图片域名','下载图标文件；缺少 URL 时会先读取商店详情'),
    'seed':('AppGoblin 种子','github.com/appgoblin-dev/appgoblin-data','公开应用目录及本地历史包导入'),
    'marketdata':('市场数据 API','数据管理中的授权接入','下载、收入、排名、评论和元数据；独立队列与配额'),
    'community':('公开元数据','数据管理中的接入地址','全球安装、活跃与收入参考；按需读取'),
    'directory':('安装指标','数据管理中的接入地址','Android 安装量参考；按需读取'),
    'web_feed':('网页趋势','数据管理中的 RSS 接入','网页搜索热点词；按需导入'),
    'ads':('Apple Ads','api.ads.apple.com/v1/insights','授权关键词热度'),
}
SINKS = dict(details=('应用资料','listings / snapshots / app_metrics_daily','应用观察'),
             keywords=('关键词排名','keyword_runs / keyword_ranks','排名记录'),
             chart=('榜单记录','observation_runs / observation_items','榜单条目'),
             reviews=('评论归档','observation_runs / reviews','评论条目'),
             featured=('编辑推荐','observation_runs / observation_items','推荐条目'),
             permissions=('权限清单','app_permission_runs / app_permission_targets','权限声明'),
             icons=('图标缓存','app_icons + 图标文件','图标文件'),
             seed=('应用池','apps / imports','导入行'),
             discovery=('发现应用 ID','discovery_runs / apps / jobs','发现记录'),
             marketdata=('来源归档与经营指标','marketdata_artifacts / app_metric_facts','来源记录'),
             signals=('关键词来源记录','keyword_source_fetches / keyword_signals','来源抓取'))


def source_for(kind, store):
    if kind=='icons':return 'cdn'
    if store=='google':return 'google'
    return {'details':'lookup','pool':'lookup','keywords':'search','chart':'rss','reviews':'rss','featured':'today'}.get(kind)


def http_source(url):
    parsed=urlsplit(url);host=parsed.hostname or '';path=parsed.path
    if host=='play.google.com':return 'google'
    if host=='search.itunes.apple.com' or '/search' in path and host=='apps.apple.com':return 'search'
    if host=='itunes.apple.com':return 'rss' if '/rss/' in path else 'lookup'
    if host=='rss.marketingtools.apple.com':return 'rss'
    if host=='apps.apple.com':return 'today'
    if any(host==suffix or host.endswith('.'+suffix) for suffix in ('googleusercontent.com','ggpht.com','mzstatic.com')):return 'cdn'
    return None


def record_http(db, store, url, status=None, elapsed=0, source=None, lane=None):
    source=source or http_source(url)
    if db is None or source not in SOURCES:return
    try:
        now=time.time()
        db.execute("INSERT IGNORE INTO schedules VALUES('lineage:http-since',%s)",(now,))
        emit(db,'http',source,'response','外部请求统计',details={
            'store':store,'status':status,'elapsed_ms':round(elapsed*1000),
            'failed':status is None or status>=400,'lane':lane or 'direct'})
    except Exception:
        log.warning('HTTP lineage counter unavailable')


def record_dispatch(db, lane, store, count):
    try:
        db.execute("INSERT IGNORE INTO schedules VALUES('lineage:dispatch-since',%s)",(time.time(),))
        emit(db,'dispatch',lane,'claimed','消费队列任务',details={'store':store,'tasks':count})
    except Exception:
        log.warning('Dispatch lineage counter unavailable')


def queue_counts(db,cfg,store,now):
    query,args=collection_tasks.task_query(store=store)
    rows=list(db.execute(f'''SELECT kind,store,task_state,count(*) n,
        SUM(enabled=1 AND due<=%s AND lease_until<=%s) eligible
        FROM ({query}) q GROUP BY kind,store,task_state''',(now,now,*args)).fetchall())
    pool=pipelines.settings(db,cfg,'pool');marks=','.join('%s' for _ in pool['countries'])
    clauses='' if store=='all' else ' AND j.store=%s'
    extra=() if store=='all' else (store,)
    scope=f"j.country NOT IN ({marks}) OR j.language<>%s"
    if pool['enabled']:
        state=f"CASE WHEN {scope} THEN 'out_of_scope' WHEN j.lease_until>%s THEN 'running' WHEN j.failures>0 THEN 'error' WHEN j.due<=%s THEN 'pending' ELSE 'scheduled' END"
        eligible=f"SUM(j.country IN ({marks}) AND j.language=%s AND j.due<=%s AND j.lease_until<=%s)"
        state_args=(*pool['countries'],pool['language'],now,now,*pool['countries'],pool['language'],now,now)
    else:
        state=f"CASE WHEN {scope} THEN 'out_of_scope' ELSE 'paused' END"
        eligible='0'
        state_args=(*pool['countries'],pool['language'])
    # Count the covering jobs index once, then subtract the small priority watch set.
    sql=f"SELECT j.store,{state} task_state,COUNT(*) n,{eligible} eligible FROM jobs j"
    total=db.execute(sql+' WHERE 1=1'+clauses+' GROUP BY j.store,task_state',(*state_args,*extra)).fetchall()
    watches=db.execute(sql+' JOIN watchlist w USING(store,app_id,country,language) WHERE 1=1'+clauses+' GROUP BY j.store,task_state',(*state_args,*extra)).fetchall()
    excluded={(r['store'],r['task_state']):r for r in watches}
    for row in total:
        watch=excluded.get((row['store'],row['task_state']),{})
        row['n']-=watch.get('n',0)
        row['eligible']-=watch.get('eligible',0)
        if row['n']:rows.append(row|{'kind':'pool'})
    return rows


def results(db,store,since,now):
    output=[]
    def collect(table,kind,unit,join='',store_field='store',count='1',time_field='observed_at'):
        scope='' if store=='all' else f' AND {store_field}=%s'
        args=(since,now) if store=='all' else (since,now,store)
        rows=db.execute(f'''SELECT {store_field} store,COUNT(*) attempts,SUM(outcome='error') errors,
            SUM(CASE WHEN outcome='ok' THEN {count} ELSE 0 END) records,MAX({time_field}) last_at
            FROM {table}{join} WHERE {time_field}>=%s AND {time_field}<=%s{scope} GROUP BY {store_field}''',args).fetchall()
        for row in rows:output.append(dict(source=source_for(kind,row['store']),sink=kind,kind=kind,unit=unit,**row))
    collect('requests','details','应用观察')
    collect('keyword_runs r','keywords','排名记录',' JOIN keyword_targets t ON t.id=r.target_id','t.store','COALESCE(r.returned_count,0)','r.observed_at')
    for kind in ('chart','reviews','featured'):
        collect('observation_runs r',kind,SINKS[kind][2],f" JOIN observation_targets t ON t.id=r.target_id AND t.kind='{kind}'",'t.store','COALESCE(r.returned_count,0)','r.observed_at')
    collect('app_permission_runs r','permissions','权限声明',' JOIN app_permission_targets t ON t.id=r.target_id','t.store',
        "COALESCE((SELECT SUM(JSON_LENGTH(g.items)) FROM JSON_TABLE(COALESCE(r.payload,'{}'),'$.*' COLUMNS(items JSON PATH '$')) g),0)",'r.observed_at')
    scope='' if store=='all' else ' AND i.store=%s';args=(since,now) if store=='all' else (since,now,store)
    icons=db.execute("""SELECT i.store,COUNT(*) attempts,SUM(l.event='error') errors,SUM(l.event='ok') records,
        MAX(l.created_at) last_at FROM collection_logs l JOIN app_icons i ON CAST(i.id AS CHAR)=l.target_key
        WHERE l.resource='icons' AND l.event IN ('ok','error') AND l.created_at BETWEEN %s AND %s"""+scope+' GROUP BY i.store',args).fetchall()
    for row in icons:output.append(dict(source='cdn',sink='icons',kind='icons',unit='图标采集结果',**row))
    discoveries=db.execute("""SELECT SUBSTRING_INDEX(source,':',1) store,
        SUBSTRING_INDEX(SUBSTRING_INDEX(source,':',3),':',-1) kind,COUNT(*) attempts,
        SUM(error IS NOT NULL) errors,COALESCE(SUM(count),0) records,MAX(observed_at) last_at
        FROM discovery_runs WHERE observed_at BETWEEN %s AND %s GROUP BY store,kind""",(since,now)).fetchall()
    for row in discoveries:
        if row['store'] not in ('google','apple') or store!='all' and row['store']!=store:continue
        source='google' if row['store']=='google' else 'rss' if row['kind']=='chart' else 'lookup'
        output.append(row|dict(source=source,sink='discovery',kind='discovery',unit='发现记录'))
    if store=='all':
        for source,sink,table,count,field in [('seed','seed','imports','processed','finished'),('marketdata','marketdata','marketdata_artifacts','records','created_at')]:
            row=db.execute(f'SELECT COALESCE(SUM({count}),0) records,COUNT(*) attempts,MAX({field}) last_at FROM {table} WHERE {field} BETWEEN %s AND %s',(since,now)).fetchone()
            output.append(dict(source=source,sink=sink,kind=sink,unit=SINKS[sink][2],errors=None,store='all',**row))
        rows=db.execute("""SELECT provider,COUNT(*) attempts,SUM(COALESCE(JSON_EXTRACT(metadata,'$.returned'),0)) records,
            MAX(observed_at) last_at FROM keyword_source_fetches WHERE observed_at BETWEEN %s AND %s GROUP BY provider""",(since,now)).fetchall()
        for row in rows:
            provider={'google_trends':'web_feed','apple_ads':'ads','apple_ads_platform':'ads'}.get(row['provider'],row['provider'])
            if provider in SOURCES:output.append(dict(source=provider,sink='signals',kind='signals',unit='热度记录',records=row['records'],last_at=row['last_at'],attempts=row['attempts'],errors=None,store='all'))
    return output


def report(db,cfg,store='all',hours=24):
    if store not in ('all','google','apple') or hours not in (1,24,168):raise ValueError('请选择有效商店和统计窗口')
    now=time.time();since=now-hours*3600
    queues=queue_counts(db,cfg,store,now);observations=results(db,store,since,now)
    nodes=[];edges=[]
    def node(identifier,label,stage,**data):
        item=dict(id=identifier,label=label,stage=stage,**data);nodes.append(item);return item
    def edge(a,b,kind=None,count=None,unit='',**data):
        edges.append(dict(id=f'{a}>{b}:{kind}',source=a,target=b,label=f'{count:,} {unit}' if count is not None else '',
                          kind=kind,count=count,unit=unit,**data))
    event_args=(since,now) if store=='all' else (since,now,store)
    event_scope='' if store=='all' else " AND JSON_UNQUOTE(JSON_EXTRACT(details,'$.store'))=%s"
    claims=db.execute("""SELECT target_key,SUM(JSON_EXTRACT(details,'$.tasks')) tasks FROM collection_logs
        WHERE resource='dispatch' AND created_at BETWEEN %s AND %s"""+event_scope+' GROUP BY target_key',event_args).fetchall()
    claims={r['target_key']:int(r['tasks']) for r in claims}
    http_rows=db.execute("""SELECT target_key,JSON_UNQUOTE(JSON_EXTRACT(details,'$.lane')) lane,COUNT(*) requests,
        SUM(JSON_EXTRACT(details,'$.failed')=true) errors FROM collection_logs
        WHERE resource='http' AND created_at BETWEEN %s AND %s"""+event_scope+' GROUP BY target_key,lane',event_args).fetchall()
    http={}
    interactions={}
    for row in http_rows:
        item=http.setdefault(row['target_key'],{'requests':0,'errors':0})
        item['requests']+=row['requests'];item['errors']+=int(row['errors'] or 0)
        interactions[row['lane'],row['target_key']]=row['requests']
    submissions=db.execute("""SELECT IF(JSON_EXTRACT(p.specification,'$.input_source') IS NULL,'platform','external') origin,
        a.kind,COUNT(*) n FROM collection_plans p JOIN JSON_TABLE(p.actions,'$[*]' COLUMNS(kind VARCHAR(24) PATH '$.kind',store VARCHAR(16) PATH '$.value.store')) a
        WHERE p.created_at BETWEEN %s AND %s"""+('' if store=='all' else ' AND a.store=%s')+' GROUP BY origin,a.kind',event_args).fetchall()
    submitted={(r['origin'],r['kind']):r['n'] for r in submissions}
    for identifier,label in [('platform','平台提交'),('external','系统 / API 提交')]:
        count=sum(n for (origin,_),n in submitted.items() if origin==identifier)
        node(identifier,label,0,value=count,unit='已提交任务',
            note='所选窗口和商店内已接收的任务需求，重复目标可共享队列',link='#collection?view=tasks')
    timer=node('timer','周期任务到期',0,value=sum(int(r['eligible'] or 0) for r in queues),unit='可领取目标',note='当前启用、到期且无有效租约的目标')
    control=collection_limits.settings(db,cfg);worker=dispatcher.status(db)
    for kind,label in KINDS.items():
        matching=[r for r in queues if r['kind']==kind];states={}
        for row in matching:states[row['task_state']]=states.get(row['task_state'],0)+int(row['n'])
        total=sum(states.values());eligible=sum(int(r['eligible'] or 0) for r in matching)
        node('q:'+kind,label,1,kind=kind,value=total,unit='共享目标',states=states,eligible=eligible,
             note='当前队列快照；同一目标可被多个计划复用',link='#collection?view=pipelines' if kind=='pool' else '#collection?kind='+kind)
        batch=cfg.get('apple_lookup_batch_size',50) if kind in ('details','pool') else cfg.get('google_rpc_batch_size',20)
        if kind in ('icons','featured'):batch=1
        node('d:'+kind,'动态分配 · '+label,2,kind=kind,value=claims.get(kind),unit='已消费任务',eligible=eligible,
             batch_limit=batch,workers=worker['workers'],paused=control['paused'],note='兼容商店、国家、语言和协议才组批；多个消费者共享额度',link='#collection?view=tasks')
        for origin in ('platform','external'):edge(origin,'q:'+kind,kind,submitted.get((origin,kind)),'次任务提交')
        edge('timer','q:'+kind,kind)
        edge('q:'+kind,'d:'+kind,kind,claims.get(kind),'次任务消费')
        by_source={}
        for row in matching:
            source=source_for(kind,row['store'])
            if source:by_source[source]=by_source.get(source,0)+int(row['n'])
        for target_store in (('google','apple') if store=='all' else (store,)):
            if kind=='permissions' and target_store!='google' or kind=='featured' and target_store!='apple':continue
            source=source_for(kind,target_store)
            if source:by_source.setdefault(source,0)
        for lane,source in interactions:
            if lane==kind:by_source.setdefault(source,0)
        for source,count in by_source.items():edge('d:'+kind,'s:'+source,kind,interactions.get((kind,source)),'次 HTTP',tasks=count)
    first=db.execute("SELECT next_at FROM schedules WHERE name='lineage:http-since'").fetchone()
    accounts={r['provider']:r['status'] for r in db.execute('SELECT provider,status FROM source_accounts').fetchall()}
    for source,(label,endpoint,note) in SOURCES.items():
        if store!='all' and source not in {source_for(kind,store) for kind in KINDS}:continue
        values=[r for r in observations if r['source']==source];events=http.get(source,{})
        node('s:'+source,label,3,value=sum(int(r['attempts'] or 0) for r in values) if values else None,unit='已处理任务',
             returned_records=sum(int(r['records'] or 0) for r in values) if values else None,
             allocated_tasks=sum(e.get('tasks',0) for e in edges if e['target']=='s:'+source),
             records=values,endpoint=endpoint,note=note,requests=int(events.get('requests',0)) if first and source in ('google','lookup','search','rss','today','cdn','marketdata','ads') else None,
             http_errors=int(events.get('errors') or 0),request_since=first['next_at'] if first else None,
             configured=accounts.get(source),link='#datasources' if source in ('marketdata','community','directory','web_feed') else '#collection?view=tasks')
    for sink,(label,tables,unit) in SINKS.items():
        values=[r for r in observations if r['sink']==sink]
        if store!='all' and sink in ('seed','marketdata','signals'):continue
        node('out:'+sink,label,4,value=sum(int(r['attempts'] or 0) for r in values),unit='目标结果',tables=tables,
             returned_records=sum(int(r['records'] or 0) for r in values),
             records=values,note='所选窗口内已提交的目标结果，包含多次采样',
             link={'details':'#apps','keywords':'#keywords','chart':'#charts','reviews':'#reviews','marketdata':'#datasources'}.get(sink,'#collection'))
        by_source={}
        for row in values:
            if row['attempts'] or row['records']:
                by_source[row['source']]=by_source.get(row['source'],0)+int(row['records'] or 0)
        if sink in KINDS:
            for target_store in (('google','apple') if store=='all' else (store,)):
                if sink=='permissions' and target_store!='google' or sink=='featured' and target_store!='apple':continue
                source=source_for(sink,target_store)
                if source:by_source.setdefault(source,None)
        elif sink in ('seed','marketdata'):by_source.setdefault(sink,None)
        for source,count in by_source.items():edge('s:'+source,'out:'+sink,sink,count,unit,records=count)
    if store=='all':
        discovery=pipelines.settings(db,cfg,'discovery')
        schedules=db.execute("SELECT count(*) total,SUM(next_at<=%s) due FROM schedules WHERE name LIKE %s",(now,'discover:%')).fetchone()
        node('discovery','榜单 / 搜索 / 开发者发现',1,value=schedules['total'],unit='来源检查任务',
             note='由基础 monitor 流程执行，配置状态为'+('启用' if discovery['enabled'] else '暂停'),link='#collection?view=pipelines')
        for source in ('google','rss','lookup'):edge('discovery','s:'+source,'discovery',interactions.get(('discovery',source)),'次 HTTP')
        edge('out:discovery','q:pool','pool')
        jobs=db.execute('SELECT status,COUNT(*) n FROM marketdata_jobs GROUP BY status').fetchall()
        node('marketdata-jobs','授权来源队列',1,value=sum(r['n'] for r in jobs),unit='来源任务',states={r['status']:r['n'] for r in jobs},link='#datasources',note='市场数据 API 的独立队列；与公开商店采集分开配额')
        edge('external','marketdata-jobs');edge('marketdata-jobs','s:marketdata',count=http.get('marketdata',{}).get('requests'),unit='次 HTTP')
        node('seed-files','种子同步 / 文件导入',1,value=db.execute('SELECT COUNT(*) n FROM imports').fetchone()['n'],unit='导入批次',note='种子同步由 monitor 流程配置；也支持手动文件导入',link='#collection?view=tasks')
        edge('seed-files','s:seed');edge('out:seed','q:pool','pool')
        node('manual','按需数据查询',1,value=None,unit='即时调用',note='公开元数据、安装指标、网页趋势与 Apple Ads 接口；未持久化的响应条数显示 —',link='#datasources')
        for source in ('community','directory','web_feed','ads'):edge('manual','s:'+source,count=http.get(source,{}).get('requests'),unit='次 HTTP')
    ids={n['id'] for n in nodes}
    return dict(nodes=nodes,edges=[e for e in edges if e['source'] in ids and e['target'] in ids],at=now,since=since,hours=hours,store=store,
                workers=worker['workers'],paused=control['paused'],http_since=first['next_at'] if first else None,
                notes=['节点数字为任务数量：队列采用当前快照，处理节点采用窗口内执行次数。连线标注实际提交、消费、HTTP 请求或入库交互量。',
                       '请求计数从启用埋点开始，一次批量 HTTP 只计一次；历史请求量和未持久化的返回条数显示 —。返回量采用每目标成功入库条数，含重复采样。',
                       '没有交互记录的连线不标数字。流动动画仅指示方向；未记录来源的旧目标不推断成某系统提交。'])
