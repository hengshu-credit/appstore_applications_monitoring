"""Reviewable collection plans expanded through existing idempotent collectors."""
import json
import time
from .db import dump
from .intelligence import market, hours, text, watch, add_observation_target
from .keywords import validate_target, add_targets
from .catalog import BY_ID

SOURCES = [
    {'id':'details','name':'应用详情与版本','source':'Apple lookup / Google Play详情','method':'public','cadence':'重点池默认24小时，可配置',
     'fields':'名称、开发者、官方分类、版本、售价、累计评分、截图URL、描述','coverage':'按应用×国家×语言观察；历史从首次采集积累'},
    {'id':'chart','name':'分类榜单','source':'Apple RSS / Google榜单RPC','method':'public','cadence':'默认24小时，可按目标调整',
     'fields':'有序应用、名次、榜类、实际返回深度','coverage':'Apple最多200、Google请求最多500；离开范围不等于下架'},
    {'id':'keywords','name':'关键词排名','source':'Apple native search / Google公开搜索','method':'public','cadence':'默认24小时，独立词任务',
     'fields':'关键词、市场、观察位置、实际深度、历史','coverage':'仅维护词库和首个响应，不产生搜索量/难度估算'},
    {'id':'reviews','name':'用户评论','source':'Apple RSS / Google评论RPC','method':'public','cadence':'默认24小时，最近评论窗口',
     'fields':'原文、星级、版本、时间、公开回复字段（来源提供时）','coverage':'每轮最多500条，最多10页；无法获取的回复状态为未知'},
    {'id':'featured','name':'编辑推荐','source':'Apple Today公开结构化页面','method':'public','cadence':'默认24小时',
     'fields':'卡片、关联应用、故事链接、观察时间','coverage':'iPhone Today可见卡片，卡片顺序不等于商店排名'},
    {'id':'owned','name':'自有后台与广告报表','source':'用户导入 / 开发者授权接口','method':'authorization',
     'cadence':'按报表覆盖期间与授权配额配置','fields':'真实广告指标、后台关键词、转化与回复权限',
     'coverage':'现支持广告报表导入及本地草稿；尚未连接开发者账户，不读取竞品私有后台'},
]


def preview_plan(value):
    m = market(value, require_app=False)
    interval = hours(value.get('refresh_hours',24))
    apps = value.get('app_ids',[]); words = value.get('keywords',[])
    if not isinstance(apps,list) or len(apps)>50 or not isinstance(words,list) or len(words)>100:
        raise ValueError('每个计划最多50个应用、100个关键词')
    if any(not isinstance(v,str) for v in apps+words):
        raise ValueError('应用和关键词必须是文本')
    apps=list(dict.fromkeys(a.strip() for a in apps if a.strip()))
    base={k:m[k] for k in ('store','country','language')}
    for app_id in apps:market(base|{'app_id':app_id})
    flags={name:value.get(name,False) for name in ('details','reviews','chart','featured')}
    if any(type(v) is not bool for v in flags.values()):raise ValueError('采集开关必须是布尔值')
    group=text(value.get('group_name','重点应用'))
    actions=[]
    for app_id in apps:
        if flags['details']:actions.append({'kind':'details','value':base|{'app_id':app_id,'group_name':group,'refresh_hours':interval}})
        if flags['reviews']:actions.append({'kind':'reviews','value':base|{'app_id':app_id,'kind':'reviews','depth':100,'refresh_hours':interval}})
    for keyword in list(dict.fromkeys(words)):
        if keyword.strip():actions.append({'kind':'keywords','value':validate_target(base|{'keyword':keyword,'refresh_hours':interval})})
    if flags['chart']:
        category=value.get('category',''); collection=value.get('collection','top-free')
        if not isinstance(category,str) or len(category)>64:raise ValueError('分类无效')
        if collection not in ('top-free','top-paid','top-grossing'):
            raise ValueError('未知榜单类型')
        if category and category!='APPLICATION' and (category not in BY_ID[m['store']] or BY_ID[m['store']][category]['legacy']):
            raise ValueError('请选择当前商店的官方分类')
        if category=='APPLICATION' and m['store']!='google':raise ValueError('Apple不支持Google分类ID')
        actions.append({'kind':'chart','value':base|{'kind':'chart','category':category,'collection':collection,'depth':100,'refresh_hours':interval}})
    if flags['featured']:
        if m['store']!='apple':raise ValueError('Today推荐只支持Apple')
        actions.append({'kind':'featured','value':base|{'kind':'featured','depth':100,'refresh_hours':interval}})
    if not actions:raise ValueError('请至少选择一个有目标的采集任务')
    return {'actions':actions,'total':len(actions),'market':base,
            'scope':'这是任务配置数量，不是请求量或完成时间承诺。已有目标会更新周期，暂停状态保留。'}


def create_plan(db, value):
    result=preview_plan(value)
    name=text(value.get('name','采集计划'))
    now=time.time()
    db.execute('INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s)',
               (name,dump(value),dump(result['actions']),result['total'],now,now))
    return {'id':db.cursor.lastrowid,'total':result['total'],'status':'queued'}


def work_plans(db, limit=20):
    # A MySQL named lock survives the helper transactions and is released if the
    # process disconnects. A crash between action and checkpoint safely replays it.
    try:
        with db.named_lock('collection-plans'):
            row=db.execute("SELECT * FROM collection_plans WHERE status IN ('queued','running') ORDER BY id LIMIT 1").fetchone()
            if not row:return {'processed':0,'error':0}
            actions=json.loads(row['actions']);completed=row['completed']
            db.execute("UPDATE collection_plans SET status='running',updated_at=%s WHERE id=%s",(time.time(),row['id']))
            processed=0
            try:
                for action in actions[completed:completed+limit]:
                    if action['kind']=='details':watch(db,action['value'])
                    elif action['kind']=='keywords':add_targets(db,[action['value']])
                    else:add_observation_target(db,action['value'])
                    completed+=1;processed+=1
                    db.execute("UPDATE collection_plans SET completed=%s,updated_at=%s WHERE id=%s",(completed,time.time(),row['id']))
                status='complete' if completed==len(actions) else 'running'
                db.execute('UPDATE collection_plans SET status=%s,error=NULL WHERE id=%s',(status,row['id']))
                return {'processed':processed,'error':0,'id':row['id'],'status':status}
            except Exception as exc:
                db.execute("UPDATE collection_plans SET status='error',error=%s,updated_at=%s WHERE id=%s",(type(exc).__name__,time.time(),row['id']))
                return {'processed':processed,'error':1,'id':row['id']}
    except RuntimeError as exc:
        if str(exc)=='Another process is already importing this dataset':return {'processed':0,'error':0}
        raise


def quality(db):
    now=time.time()
    targets=[]
    for table,kind,runs in [('keyword_targets','keywords','keyword_runs'),('observation_targets',None,'observation_runs')]:
        rows=db.execute(f'''SELECT t.*,r.observed_at success_at,r.returned_count,r.coverage,r.source,
            a.observed_at attempt_at,a.outcome attempt_outcome FROM {table} t
            LEFT JOIN {runs} r ON r.id=t.latest_success_id LEFT JOIN {runs} a ON a.id=t.latest_attempt_id
            ORDER BY t.id DESC LIMIT 1000''').fetchall()
        for row in rows:
            row.pop('token',None)
            row['kind']=kind or row['kind'];row['resource']='keywords' if kind else 'targets'
            row['stale']=row['success_at'] is None or bool(row['failures']) or now>=row['success_at']+row['refresh_hours']*3600
        targets.extend(rows)
    rows=db.execute('''SELECT w.*,j.due,j.failures,j.last_error,l.last_success success_at,r.observed_at attempt_at,
        r.outcome attempt_outcome,l.state FROM watchlist w LEFT JOIN jobs j USING(store,app_id,country,language)
        LEFT JOIN listings l USING(store,app_id,country,language)
        LEFT JOIN requests r ON r.id=(SELECT qr.id FROM requests qr WHERE qr.store=w.store AND qr.app_id=w.app_id
            AND qr.country=w.country AND qr.language=w.language ORDER BY qr.id DESC LIMIT 1)
        ORDER BY w.id DESC LIMIT 1000''').fetchall()
    for row in rows:
        row.update(kind='details',resource='watchlist',coverage='one_regional_listing',source=row['store']+'-public-listing',
                   returned_count=1 if row['success_at'] is not None else None,
                   stale=row['success_at'] is None or bool(row['failures']) or now>=row['success_at']+row['refresh_hours']*3600)
    targets.extend(rows)
    return {'targets':targets,'sources':SOURCES,'at':now,'limit_per_type':1000,
            'summary':{'listed':len(targets),'stale':sum(t['stale'] for t in targets),
                       'failed':sum(bool(t['failures']) for t in targets),'paused':sum(not t['enabled'] for t in targets)},
            'plans':db.execute('SELECT id,name,status,completed,total,error,created_at,updated_at FROM collection_plans ORDER BY id DESC LIMIT 100').fetchall(),
            'heartbeat':db.execute("SELECT next_at FROM schedules WHERE name='console-heartbeat'").fetchone()}
