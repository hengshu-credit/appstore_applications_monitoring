"""One paginated task registry and atomic controls for all observation workers."""
import time
from .intelligence import hours, text

TABLES = {'watchlist': 'watchlist', 'keywords': 'keyword_targets', 'targets': 'observation_targets'}
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
    FROM observation_targets t LEFT JOIN observation_runs r ON r.id=t.latest_success_id'''


def list_tasks(db, *, kind='', store='', country='', language='', state='', q='', page=1, limit=50):
    if kind and kind not in ('details','keywords','chart','reviews','featured'):
        raise ValueError('未知采集类型')
    if store and store not in ('google','apple'):
        raise ValueError('未知商店')
    if not 1 <= page <= 100000 or not 1 <= limit <= 200 or len(q) > 255:
        raise ValueError('分页或搜索参数无效')
    now = time.time()
    query = f'''SELECT task.*,CASE WHEN enabled=0 THEN 'paused' WHEN lease_until>{now} THEN 'running'
        WHEN failures>0 THEN 'error' WHEN success_at IS NULL OR due<={now} THEN 'pending'
        ELSE 'scheduled' END task_state FROM ({UNION}) task'''
    where, args = [], []
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
    counts = db.execute(f'SELECT task_state,count(*) n FROM ({filtered}) f GROUP BY task_state', args).fetchall()
    summary = {name:0 for name in ('paused','running','error','pending','scheduled')}
    summary.update({r['task_state']:r['n'] for r in counts})
    total = sum(summary.values())
    rows = db.execute(filtered+' ORDER BY resource,id DESC LIMIT %s OFFSET %s', (*args,limit,(page-1)*limit)).fetchall()
    return {'items':rows,'total':total,'summary':summary,'page':page,'limit':limit,'has_more':page*limit<total}


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
        else:
            depth = value.get('depth',row['depth'])
            maximum = 200 if resource=='keywords' else 500
            if type(depth) is not int or not 1<=depth<=maximum: raise ValueError(f'采集深度须为1–{maximum}的整数')
            db.execute(f'''UPDATE {table} SET refresh_hours=%s,depth=%s,
                due=LEAST(due,%s),token=NULL,lease_until=0 WHERE id=%s''',
                       (interval,depth,time.time()+interval*3600,identifier))
    return {'id':identifier,'resource':resource,'saved':True}


def control_tasks(db, value):
    refs, action = value.get('tasks'), value.get('action')
    if not isinstance(refs,list) or not 1<=len(refs)<=100 or action not in ('pause','resume','refresh'):
        raise ValueError('每次请选择1–100个任务及有效操作')
    with db:
        for ref in refs:
            if not isinstance(ref,dict): raise ValueError('任务标识无效')
            resource, identifier = ref.get('resource'), ref.get('id')
            table, row = _target(db, resource, identifier)
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
    return {'updated':len(refs),'action':action}
