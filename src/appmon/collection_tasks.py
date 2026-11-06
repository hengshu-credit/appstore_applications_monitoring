"""One paginated task registry and atomic controls for all observation workers."""
import time
from .intelligence import hours, text
from .task_logs import emit

TABLES = {'watchlist': 'watchlist', 'keywords': 'keyword_targets', 'targets': 'observation_targets', 'icons':'app_icons','permissions':'app_permission_targets'}
KINDS = ('details','keywords','chart','reviews','featured','icons','permissions')
STATES = ('paused','running','error','pending','scheduled')
UNION = '''SELECT 'watchlist' resource,w.id,'details' kind,w.store,w.country,w.language,w.app_id,
    COALESCE(a.name,w.app_id) label,'' category,'' collection,w.group_name,NULL depth,
    w.refresh_hours,w.enabled,COALESCE(j.failures,0) failures,j.last_error,j.due,j.lease_until,l.last_success success_at
    FROM watchlist w LEFT JOIN apps a USING(store,app_id)
    LEFT JOIN jobs j USING(store,app_id,country,language) LEFT JOIN listings l USING(store,app_id,country,language)
    UNION ALL
    SELECT 'keywords',t.id,'keywords',t.store,t.country,t.language,'',t.keyword,'','','',t.depth,
    t.refresh_hours,t.enabled,t.failures,t.last_error,t.due,t.lease_until,r.observed_at
    FROM keyword_targets t LEFT JOIN keyword_runs r ON r.id=t.latest_success_id
    UNION ALL
    SELECT 'targets',t.id,t.kind,t.store,t.country,t.language,t.app_id,
    CASE WHEN t.kind='reviews' THEN t.app_id WHEN t.kind='featured' THEN 'Apple Today' ELSE t.collection END,
    t.category,t.collection,'',t.depth,t.refresh_hours,t.enabled,t.failures,t.last_error,t.due,t.lease_until,r.observed_at
    FROM observation_targets t LEFT JOIN observation_runs r ON r.id=t.latest_success_id
    UNION ALL
    SELECT 'icons',i.id,'icons',i.store,i.country,i.language,i.app_id,COALESCE(i.name,i.app_id),'','','',NULL,
    i.refresh_hours,i.enabled,i.failures,i.last_error,i.due,i.lease_until,i.checked_at
    FROM app_icons i WHERE i.requested=1
    UNION ALL
    SELECT 'permissions',p.id,'permissions',p.store,p.country,'en',p.app_id,COALESCE(a.name,p.app_id),'','','',NULL,
    p.refresh_hours,p.enabled,p.failures,p.last_error,p.due,p.lease_until,p.success_at
    FROM app_permission_targets p LEFT JOIN apps a USING(store,app_id)'''


def _task_union(kind='', light=False, search=False, refs=None):
    parts=UNION.split('UNION ALL')
    if light:
        name='COALESCE(a.name,w.app_id)' if search else "''"
        keyword='t.keyword' if search else "''"
        ident=lambda alias:alias+'.app_id' if search else "''"
        label="CASE WHEN t.kind='reviews' THEN t.app_id WHEN t.kind='featured' THEN 'Apple Today' ELSE t.collection END" if search else "''"
        category='t.category' if search else "''"
        icon_label='COALESCE(i.name,i.app_id)' if search else "''"
        app_join=' LEFT JOIN apps a USING(store,app_id)' if search else ''
        permission_name='COALESCE(a.name,p.app_id)' if search else "''"
        parts=[f'''SELECT 'watchlist' resource,w.id,'details' kind,w.store,w.country,w.language,{ident('w')} app_id,{name} label,'' category,
            w.enabled,COALESCE(j.failures,0) failures,j.due,j.lease_until FROM watchlist w{app_join}
            LEFT JOIN jobs j USING(store,app_id,country,language)''',
            f"SELECT 'keywords',t.id,'keywords',t.store,t.country,t.language,'',{keyword},'',t.enabled,t.failures,t.due,t.lease_until FROM keyword_targets t",
            f"SELECT 'targets',t.id,t.kind,t.store,t.country,t.language,{ident('t')},{label},{category},t.enabled,t.failures,t.due,t.lease_until FROM observation_targets t",
            f"SELECT 'icons',i.id,'icons',i.store,i.country,i.language,{ident('i')},{icon_label},'',i.enabled,i.failures,i.due,i.lease_until FROM app_icons i WHERE i.requested=1",
            f"SELECT 'permissions',p.id,'permissions',p.store,p.country,'en',{ident('p')},{permission_name},'',p.enabled,p.failures,p.due,p.lease_until FROM app_permission_targets p{app_join}"]
    selected=[];args=[]
    for part,(resource,alias,kinds) in zip(parts,[('watchlist','w',('details',)),('keywords','t',('keywords',)),('targets','t',('chart','reviews','featured')),('icons','i',('icons',)),('permissions','p',('permissions',))]):
        if kind and kind not in kinds:continue
        if refs is not None:
            ids=[r['id'] for r in refs if r['resource']==resource]
            if not ids:continue
            part+=(' AND ' if resource=='icons' else ' WHERE ')+alias+'.id IN ('+','.join('%s' for _ in ids)+')'
            args.extend(ids)
        selected.append(part)
    return ' UNION ALL '.join(selected),args


def task_query(*, kind='', store='', country='', language='', state='', q='', light=False, refs=None):
    if any(not isinstance(v,str) for v in (kind,store,country,language,state,q)):
        raise ValueError('筛选参数须为文本')
    if kind and kind not in KINDS:
        raise ValueError('未知采集类型')
    if store=='all':store=''
    if store and store not in ('google','apple'):
        raise ValueError('未知商店')
    if len(q) > 255:
        raise ValueError('搜索参数无效')
    now = time.time()
    union,args=_task_union(kind,light,bool(q),refs)
    columns='resource,id,kind,store,country,language,app_id,label,category,'+('enabled,failures,due,lease_until' if light else 'collection,group_name,depth,refresh_hours,enabled,failures,last_error,due,lease_until,success_at')
    query = f'''SELECT task.*,CASE WHEN enabled=0 THEN 'paused' WHEN lease_until>{now} THEN 'running'
        WHEN failures>0 THEN 'error' WHEN COALESCE(due,0)<={now} THEN 'pending'
        ELSE 'scheduled' END task_state FROM ({union}) task ({columns})'''
    where = []
    for key, value in [('kind',kind),('store',store),('country',country.lower()),('language',language.lower())]:
        if value:
            where.append(key+'=%s'); args.append(value)
    if q:
        where.append('(LOCATE(%s,label)>0 OR LOCATE(%s,CONVERT(app_id USING utf8mb4))>0 OR LOCATE(%s,CONVERT(category USING utf8mb4))>0)')
        args.extend([q,q,q])
    if state:
        if state not in ('paused','running','error','pending','scheduled'): raise ValueError('未知任务状态')
        where.append('task_state=%s'); args.append(state)
    filtered = f'SELECT * FROM ({query}) classified' + (' WHERE '+' AND '.join(where) if where else '')
    return filtered, args


def list_tasks(db, *, page=1, limit=50, **filters):
    from .collection_batch import revision
    data_revision = revision(db)
    if not 1 <= page <= 100000 or not 1 <= limit <= 200:
        raise ValueError('分页参数无效')
    filtered, args = task_query(**filters,light=True)
    # Cards retain every category and state while the list is drilled down.
    # Aggregate each indexed resource before combining; avoid materializing every
    # task label/history join just to render the seven summary cards.
    count_sql=[];count_args=[]
    for kind in KINDS:
        overview,overview_args=task_query(**(filters|{'kind':kind,'state':''}),light=True)
        count_sql.append(f'SELECT kind,task_state,count(*) n FROM ({overview}) f GROUP BY kind,task_state')
        count_args.extend(overview_args)
    counts=db.execute(' UNION ALL '.join(count_sql),count_args).fetchall()
    categories = {kind:dict(kind=kind,total=0,summary=dict.fromkeys(STATES,0)) for kind in KINDS}
    summary = dict.fromkeys(STATES,0)
    for row in counts:
        category = categories[row['kind']]
        category['summary'][row['task_state']] = row['n']
        category['total'] += row['n']
        if (not filters.get('kind') or filters['kind']==row['kind']) and (not filters.get('state') or filters['state']==row['task_state']):
            summary[row['task_state']] += row['n']
    total = sum(summary.values())
    refs = db.execute('SELECT resource,id FROM ('+filtered+') matches ORDER BY resource,id DESC LIMIT %s OFFSET %s', (*args,limit,(page-1)*limit)).fetchall()
    rows=[]
    if refs:
        detail,detail_args=task_query(refs=refs)
        rows=db.execute(detail+' ORDER BY resource,id DESC',detail_args).fetchall()
    return {'items':rows,'total':total,'summary':summary,'categories':list(categories.values()),'page':page,'limit':limit,'has_more':page*limit<total,'data_revision':data_revision}


def _target(db, resource, identifier):
    table = TABLES.get(resource)
    if table is None or type(identifier) is not int or identifier < 1: raise ValueError('任务标识无效')
    row = db.execute(f'SELECT * FROM {table} WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
    if row is None: raise ValueError('任务不存在')
    return table, row


def edit_task(db, resource, identifier, value):
    interval = hours(value.get('refresh_hours'))
    with db:
        table, row = _target(db, resource, identifier)
        if resource == 'watchlist':
            group = text(value.get('group_name', row['group_name']))
            db.execute('UPDATE watchlist SET refresh_hours=%s,group_name=%s WHERE id=%s', (interval,group,identifier))
            db.execute('''UPDATE jobs SET due=LEAST(due,%s),token=NULL,lease_until=0
                WHERE store=%s AND app_id=%s AND country=%s AND language=%s''',
                       (time.time()+interval*3600, *[row[k] for k in ('store','app_id','country','language')]))
        elif resource in ('icons','permissions'):
            db.execute(f'UPDATE {table} SET refresh_hours=%s,due=LEAST(due,%s),token=NULL,lease_until=0 WHERE id=%s',(interval,time.time()+interval*3600,identifier))
        else:
            depth = value.get('depth',row['depth'])
            maximum = 200 if resource=='keywords' else 500
            if type(depth) is not int or not 1<=depth<=maximum: raise ValueError(f'采集深度须为1–{maximum}的整数')
            db.execute(f'''UPDATE {table} SET refresh_hours=%s,depth=%s,
                due=LEAST(due,%s),token=NULL,lease_until=0 WHERE id=%s''',
                       (interval,depth,time.time()+interval*3600,identifier))
        emit(db,resource,identifier,'settings_updated',f'采集设置已更新，刷新周期 {interval} 小时')
    return {'id':identifier,'resource':resource,'saved':True}


def control_tasks(db, value):
    refs, action = value.get('tasks'), value.get('action')
    scope = value.get('scope', 'selected')
    if scope not in ('selected','filtered','all'):
        raise ValueError('未知选择范围')
    if scope != 'selected':
        if action not in ('pause','resume','interval','retry') or refs is not None:
            raise ValueError('跨页支持暂停、恢复、修改周期和重试失败，不能同时指定任务列表')
        filters = value.get('filters', {})
        if not isinstance(filters,dict) or set(filters)-{'kind','store','country','language','state','q'}:
            raise ValueError('筛选参数无效')
        if scope == 'all' and filters:
            raise ValueError('全部任务操作不能带筛选条件')
        query, args = task_query(**filters)
        if action=='retry':
            query = f"SELECT * FROM ({query}) retryable WHERE enabled=1 AND failures>0 AND lease_until<=%s"
            args.append(time.time())
        interval = hours(value.get('refresh_hours')) if action=='interval' else None
        return _control_scope(db, query, args, action, interval)
    elif not isinstance(refs,list) or not 1<=len(refs)<=100 or action not in ('pause','resume','refresh','interval'):
        raise ValueError('每次请选择1–100个任务及有效操作')
    interval = hours(value.get('refresh_hours')) if action=='interval' else None
    with db:
        if any(not isinstance(ref,dict) or ref.get('resource') not in TABLES or type(ref.get('id')) is not int for ref in refs):
            raise ValueError('任务标识无效')
        refs = [dict(resource=r,id=i) for r,i in sorted({(ref['resource'],ref['id']) for ref in refs})]
        for ref in refs:
            if not isinstance(ref,dict): raise ValueError('任务标识无效')
            resource, identifier = ref.get('resource'), ref.get('id')
            table, row = _target(db, resource, identifier)
            if action=='interval':
                db.execute(f'UPDATE {table} SET refresh_hours=%s WHERE id=%s',(interval,identifier))
                if resource=='watchlist':
                    db.execute('UPDATE jobs SET due=%s,token=NULL,lease_until=0 WHERE store=%s AND app_id=%s AND country=%s AND language=%s',
                               (time.time()+interval*3600,*[row[k] for k in ('store','app_id','country','language')]))
                else:
                    db.execute(f'UPDATE {table} SET due=%s,token=NULL,lease_until=0 WHERE id=%s',(time.time()+interval*3600,identifier))
                emit(db,resource,identifier,action,f'刷新周期已改为 {interval} 小时，从现在重新排期')
                continue
            if action=='refresh' and not row['enabled']:
                raise ValueError('已暂停的任务须先恢复，再安排采集')
            if resource=='watchlist':
                keys = tuple(row[k] for k in ('store','app_id','country','language'))
                if action in ('pause','resume'):
                    db.execute('UPDATE watchlist SET enabled=%s WHERE id=%s',(action=='resume',identifier))
                    db.execute('''UPDATE jobs SET token=NULL,lease_until=0
                        WHERE store=%s AND app_id=%s AND country=%s AND language=%s''',keys)
                else:
                    db.execute('''UPDATE jobs SET due=0 WHERE store=%s AND app_id=%s AND country=%s
                        AND language=%s AND lease_until<=%s''',(*keys,time.time()))
            elif action in ('pause','resume'):
                db.execute(f'UPDATE {table} SET enabled=%s,token=NULL,lease_until=0 WHERE id=%s', (action=='resume',identifier))
            else:
                db.execute(f'UPDATE {table} SET due=0 WHERE id=%s AND lease_until<=%s', (identifier,time.time()))
            emit(db,resource,identifier,action,{'pause':'任务已暂停，当前租约已失效','resume':'任务已恢复，等待到期后领取','refresh':'已请求立即采集；由后台按租约和限速执行'}[action])
    return {'updated':len(refs),'action':action}


def _control_scope(db, query, args, action, interval):
    """Keep the identity snapshot on disk in MySQL; SQL count is independent of task count."""
    db.execute('CREATE TEMPORARY TABLE collection_selection (resource VARCHAR(16) NOT NULL,id BIGINT NOT NULL,PRIMARY KEY(resource,id)) ENGINE=InnoDB')
    try:
        with db:
            db.execute(f'INSERT INTO collection_selection SELECT resource,id FROM ({query}) matched',args)
            count = db.execute('SELECT count(*) n FROM collection_selection').fetchone()['n']
            now = time.time()
            for resource, table in TABLES.items():
                join = f"{table} t JOIN collection_selection s ON s.resource=%s AND s.id=t.id"
                if action in ('pause','resume'):
                    db.execute(f'UPDATE {join} SET t.enabled=%s'+('' if resource=='watchlist' else ',t.token=NULL,t.lease_until=0'),(resource,action=='resume'))
                elif action=='interval':
                    db.execute(f'UPDATE {join} SET t.refresh_hours=%s'+('' if resource=='watchlist' else ',t.due=%s,t.token=NULL,t.lease_until=0'),
                               (resource,interval) if resource=='watchlist' else (resource,interval,now+interval*3600))
                elif resource!='watchlist':
                    db.execute(f'UPDATE {join} SET t.due=0 WHERE t.enabled=1 AND t.failures>0 AND t.lease_until<=%s',(resource,now))
                if resource=='watchlist':
                    jobs_join = join+' JOIN jobs j ON j.store=t.store AND j.app_id=t.app_id AND j.country=t.country AND j.language=t.language'
                    if action=='retry':
                        db.execute(f'UPDATE {jobs_join} SET j.due=0 WHERE t.enabled=1 AND j.failures>0 AND j.lease_until<=%s',(resource,now))
                    else:
                        db.execute(f'UPDATE {jobs_join} SET j.token=NULL,j.lease_until=0'+(',j.due=%s' if action=='interval' else ''),
                                   (resource,now+interval*3600) if action=='interval' else (resource,))
            message = {'pause':'任务已暂停，当前租约已失效','resume':'任务已恢复，等待到期后领取',
                       'retry':'已安排失败任务重试，仍遵守共享控频和冷却','interval':f'刷新周期已改为 {interval} 小时，从现在重新排期'}[action]
            db.execute('''INSERT INTO collection_logs(resource,target_key,created_at,level,event,message)
                SELECT resource,CAST(id AS CHAR),%s,'info',%s,%s FROM collection_selection''',(now,action,message))
        return {'updated':count,'action':action}
    finally:
        db.execute('DROP TEMPORARY TABLE collection_selection')
