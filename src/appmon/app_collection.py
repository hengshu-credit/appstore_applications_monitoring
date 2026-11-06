"""One-click public app collection using the existing durable plan/worker queues."""
import json
import time
from .db import dump
from .intelligence import market, watch, add_observation_target
from .keywords import add_targets, validate_target
from .catalog import category_id, BY_ID
from .charts import capabilities, definition
from .task_logs import emit, target_state


def action_target(db,action):
    v=action['value'];kind=action['kind']
    if kind=='details':
        row=db.execute('SELECT id FROM watchlist WHERE store=%s AND app_id=%s AND country=%s AND language=%s',tuple(v[k] for k in ('store','app_id','country','language'))).fetchone();resource='watchlist'
    elif kind=='keywords':
        row=db.execute('SELECT id FROM keyword_targets WHERE store=%s AND country=%s AND language=%s AND keyword=%s',tuple(v[k] for k in ('store','country','language','keyword'))).fetchone();resource='keywords'
    elif kind in ('icons','permissions'):
        resource=kind
        table='app_icons' if kind=='icons' else 'app_permission_targets'
        keys=('store','app_id','country','language') if kind=='icons' else ('store','app_id','country')
        row=db.execute(f'SELECT id FROM {table} WHERE '+' AND '.join(k+'=%s' for k in keys),tuple(v[k] for k in keys)).fetchone()
    else:
        row=db.execute('''SELECT id FROM observation_targets WHERE kind=%s AND store=%s AND app_id=%s
            AND country=%s AND language=%s AND collection=%s AND category=%s''',
            (kind,v['store'],v.get('app_id','') if kind=='reviews' else '',v['country'],v['language'],v.get('collection','') if kind=='chart' else '',v.get('category','') if kind=='chart' else '')).fetchone();resource='targets'
    return {'resource':resource,'id':row['id'],'kind':kind} if row else None


def schedule_action(db,action):
    # Explicit app refresh activates these targets. Existing periods/depth are kept.
    existing=action_target(db,action)
    if not existing:
        if action['kind']=='details':watch(db,action['value'])
        elif action['kind']=='keywords':add_targets(db,[action['value']])
        else:add_observation_target(db,action['value'])
    ref=action_target(db,action)
    table={'watchlist':'watchlist','keywords':'keyword_targets','targets':'observation_targets'}[ref['resource']]
    with db:
        db.execute(f'UPDATE {table} SET enabled=1 WHERE id=%s',(ref['id'],))
        if ref['resource']=='watchlist':
            v=action['value'];db.execute('''UPDATE jobs SET due=0 WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND lease_until<=%s''',
                (*[v[k] for k in ('store','app_id','country','language')],time.time()))
        else:db.execute(f'UPDATE {table} SET due=0 WHERE id=%s AND lease_until<=%s',(ref['id'],time.time()))
        emit(db,ref['resource'],ref['id'],'refresh','应用详情请求采集：已创建或启用目标，并安排立即执行')
    return ref


def report(db,identifier,allow_plan=False):
    plan=db.execute('SELECT * FROM collection_plans WHERE id=%s',(identifier,)).fetchone()
    if not plan or not allow_plan and 'app_request' not in json.loads(plan['specification']):raise ValueError('应用采集请求不存在')
    actions=json.loads(plan['actions']);tasks=[];finished=0;errors=0
    for action in actions:
        ref=action_target(db,action);state='configuring';observed=None;outcome=None
        if ref:
            state=target_state(db,ref['resource'],str(ref['id']))['status']
            v=action['value']
            if ref['resource']=='watchlist':
                attempted=db.execute('''SELECT observed_at,outcome FROM requests WHERE store=%s AND app_id=%s AND country=%s AND language=%s
                    ORDER BY id DESC LIMIT 1''',tuple(v[k] for k in ('store','app_id','country','language'))).fetchone()
            elif ref['resource']=='icons':
                attempted=db.execute("SELECT checked_at observed_at,IF(failures>0,'error','ok') outcome FROM app_icons WHERE id=%s AND checked_at IS NOT NULL",(ref['id'],)).fetchone()
            elif ref['resource']=='permissions':
                attempted=db.execute('SELECT observed_at,outcome FROM app_permission_runs WHERE target_id=%s ORDER BY id DESC LIMIT 1',(ref['id'],)).fetchone()
            else:
                table='keyword_runs' if ref['resource']=='keywords' else 'observation_runs'
                attempted=db.execute(f'SELECT observed_at,outcome FROM {table} WHERE target_id=%s ORDER BY id DESC LIMIT 1',(ref['id'],)).fetchone()
            if attempted and attempted['observed_at']>=plan['created_at']:
                observed=attempted['observed_at'];outcome=attempted['outcome'];finished+=1;errors+=int(outcome=='error');state='failed' if outcome=='error' else 'complete'
        label=action['value'].get('keyword') or action['value'].get('app_id') or 'Apple Today'
        if action['kind']=='chart':
            v=action['value'];label=definition(v['store'],v['collection'])['name']+' / '+BY_ID[v['store']].get(v.get('category',''),{}).get('name','总榜')
        tasks.append({'kind':action['kind'],'label':label,
                      'ref':ref,'state':state,'observed_at':observed,'outcome':outcome})
    state='failed' if plan['status']=='error' else ('partial_failure' if errors else 'complete') if finished==len(actions) else 'running'
    deferred=max(0,json.loads(plan['specification']).get('keyword_total',0)-100)
    return {'id':identifier,'state':state,'requested_at':plan['created_at'],'total':len(actions),'finished':finished,'errors':errors,'tasks':tasks,'deferred_keywords':deferred,
            'scope':('共享目标执行进度，多个计划复用同一目标的实际采集结果。' if allow_plan else '当前市场的公开详情/素材、最多100条近期评论、可用总榜和分类榜、本市场最多100个已跟踪关键词及应用名（缺失时用ID）。会创建或启用对应任务；不承诺全历史评论或全网关键词。'+(f'另有{deferred}个词需在采集中心分批安排。' if deferred else ''))}


def latest(db,m):
    rows=db.execute('''SELECT id FROM collection_plans WHERE JSON_UNQUOTE(JSON_EXTRACT(specification,'$.app_request.store'))=%s
        AND JSON_UNQUOTE(JSON_EXTRACT(specification,'$.app_request.app_id'))=%s
        AND JSON_UNQUOTE(JSON_EXTRACT(specification,'$.app_request.country'))=%s
        AND JSON_UNQUOTE(JSON_EXTRACT(specification,'$.app_request.language'))=%s ORDER BY id DESC LIMIT 1''',
        tuple(m[k] for k in ('store','app_id','country','language'))).fetchone()
    return report(db,rows['id']) if rows else None


def collect(db,value):
    m=market(value);base={k:m[k] for k in ('store','country','language')}
    app=db.execute('SELECT name,category FROM apps WHERE store=%s AND app_id=%s',(m['store'],m['app_id'])).fetchone()
    if not app:raise ValueError('应用尚未进入应用池')
    with db.named_lock('collect-app:'+dump(m)):
        previous=latest(db,m)
        if previous and previous['state']=='running':return previous
        actions=[{'kind':'details','value':m|{'group_name':'应用详情采集','refresh_hours':24}},
                 {'kind':'reviews','value':m|{'kind':'reviews','depth':100,'refresh_hours':24}}]
        category=category_id(m['store'],app['category'])
        categories=['']+([category] if category and not BY_ID[m['store']][category]['legacy'] else [])
        for cat in dict.fromkeys(categories):
            for spec in capabilities(m['store']):
                if spec['mode']=='direct' and (not cat or spec['category_supported']):actions.append({'kind':'chart','value':base|{'kind':'chart','collection':spec['id'],'category':cat,'depth':200,'refresh_hours':24}})
        words=db.execute('SELECT keyword FROM keyword_targets WHERE store=%s AND country=%s AND language=%s ORDER BY id LIMIT 100',tuple(base.values())).fetchall()
        keyword_total=db.execute('SELECT count(*) n FROM keyword_targets WHERE store=%s AND country=%s AND language=%s',tuple(base.values())).fetchone()['n']
        names=[w['keyword'] for w in words]
        seed=(app['name'] or m['app_id'])[:255]
        try:validate_target(base|{'keyword':seed})
        except ValueError:seed=m['app_id']
        names.append(seed)
        for word in dict.fromkeys(names):
            actions.append({'kind':'keywords','value':validate_target(base|{'keyword':word,'depth':50,'refresh_hours':24})})
        if m['store']=='apple':actions.append({'kind':'featured','value':base|{'kind':'featured','depth':100,'refresh_hours':24}})
        for action in actions:action['immediate']=True
        now=time.time()
        with db:
            db.execute('INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s)',
                       ('应用信息采集',dump({'app_request':m,'keyword_total':keyword_total}),dump(actions),len(actions),now,now))
            identifier=db.cursor.lastrowid;emit(db,'plans',identifier,'queued',f'应用采集已排队：{len(actions)} 个目标')
        return report(db,identifier)
