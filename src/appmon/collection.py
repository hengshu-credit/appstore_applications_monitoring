"""Reviewable collection plans expanded through existing idempotent collectors."""
import json
import time
import uuid
from .db import dump
from .intelligence import market, hours, text, watch, add_observation_target
from .keywords import validate_target, add_targets
from .catalog import BY_ID
from .task_logs import emit
from .log_details import diagnostics

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
    keyword_depth = value.get('keyword_depth', 50)
    depth = value.get('depth', 100)
    if type(depth) is not int or not 1 <= depth <= 500:
        raise ValueError('榜单、推荐与评论深度须为1–500的整数')
    if type(keyword_depth) is not int or not 1 <= keyword_depth <= 200:
        raise ValueError('关键词深度须为1–200的整数')
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
        if flags['reviews']:actions.append({'kind':'reviews','value':base|{'app_id':app_id,'kind':'reviews','depth':depth,'refresh_hours':interval}})
    for keyword in list(dict.fromkeys(words)):
        if keyword.strip():actions.append({'kind':'keywords','value':validate_target(base|{'keyword':keyword,'depth':keyword_depth,'refresh_hours':interval})})
    if flags['chart']:
        category=value.get('category',''); collection=value.get('collection','top-free')
        if not isinstance(category,str) or len(category)>64:raise ValueError('分类无效')
        from .charts import definition
        collection=definition(m['store'],collection)['base_collection']
        if category and not definition(m['store'],collection)['category_supported']:
            raise ValueError('该商店公开新应用榜仅支持总榜，请选择全部分类')
        if category and category!='APPLICATION' and (category not in BY_ID[m['store']] or BY_ID[m['store']][category]['legacy']):
            raise ValueError('请选择当前商店的官方分类')
        if category=='APPLICATION' and m['store']!='google':raise ValueError('Apple不支持Google分类ID')
        actions.append({'kind':'chart','value':base|{'kind':'chart','category':category,'collection':collection,'depth':depth,'refresh_hours':interval}})
    if flags['featured']:
        if m['store']!='apple':raise ValueError('Today推荐只支持Apple')
        actions.append({'kind':'featured','value':base|{'kind':'featured','depth':depth,'refresh_hours':interval}})
    if not actions:raise ValueError('请至少选择一个有目标的采集任务')
    return {'actions':actions,'total':len(actions),'market':base,
            'scope':'这是任务配置数量，不是请求量或完成时间承诺。已有目标会更新周期，暂停状态保留。'}


def create_plan(db, value):
    result=preview_plan(value)
    name=text(value.get('name','采集计划'))
    now=time.time()
    with db:
        db.execute('INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at,request_source,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                   (name,dump(value),dump(result['actions']),result['total'],now,now,None,None))
        identifier=db.cursor.lastrowid
        emit(db,'plans',identifier,'queued',f'采集计划已创建，待配置 {result["total"]} 个目标')
    return {'id':identifier,'total':result['total'],'status':'queued'}


def enqueue(db, value):
    """Durable intake for APIs, other systems and schedulers; retries reuse the receipt."""
    source = text(value.get('source','api'),64)
    request_id = text(value.get('request_id',uuid.uuid4().hex),120)
    tasks = value.get('tasks')
    if not isinstance(tasks,list) or not 1<=len(tasks)<=500:
        raise ValueError('一次提交1–500个任务')
    actions = []
    for task in tasks:
        if not isinstance(task,dict):raise ValueError('采集任务须为对象')
        kind = task.get('kind')
        if kind in ('aso_popularity','aso_import','aso_report'):
            from .source_tasks import normalize
            actions.append({'kind':kind,'value':normalize(kind,task)})
        elif kind in ('icons','permissions'):
            target = market(task)
            if kind=='permissions' and target['store']!='google':raise ValueError('权限采集仅支持 Google Play')
            if kind=='permissions':target['language']='en'
            actions.append({'kind':kind,'value':target|{'refresh_hours':hours(task.get('refresh_hours',24))}})
        elif kind in ('details','keywords','chart','reviews','featured'):
            spec = task | {k:k==kind for k in ('details','chart','reviews','featured')}
            spec['app_ids'] = [task.get('app_id','')] if kind in ('details','reviews') else []
            spec['keywords'] = [task.get('keyword','')] if kind=='keywords' else []
            spec['keyword_depth'] = task.get('depth',50) if kind=='keywords' else 50
            result = preview_plan(spec)
            if len(result['actions'])!=1:raise ValueError('采集目标无效')
            actions.extend(result['actions'])
        else:
            raise ValueError('未知采集类型')
    with db.named_lock('enqueue:'+dump([source,request_id]),timeout=10):
        old = db.execute('''SELECT id,total,status,actions FROM collection_plans WHERE
            request_source=%s AND request_id=%s LIMIT 1''',(source,request_id)).fetchone()
        if old:
            if json.loads(old['actions'])!=actions:raise ValueError('同一来源请求编号不能对应不同任务')
            return {k:old[k] for k in ('id','total','status')} | {'reused':True,'request_id':request_id}
        now=time.time()
        with db:
            db.execute('INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at,request_source,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (text(value.get('name',source+' 采集请求')),dump({'input_source':source,'request_id':request_id}),dump(actions),len(actions),now,now,source,request_id))
            identifier=db.cursor.lastrowid
            emit(db,'plans',identifier,'queued',f'统一入队：{source}，{len(actions)} 个请求目标')
        return {'id':identifier,'total':len(actions),'status':'queued','reused':False,'request_id':request_id}


def configure_asset(db, action):
    target=action['value']
    with db:
        if action['kind']=='permissions':
            from .app_permissions import enqueue as enqueue_permissions
            enqueue_permissions(db,[target])
            db.execute('''UPDATE app_permission_targets SET refresh_hours=LEAST(refresh_hours,%s)
                WHERE store=%s AND app_id=%s AND country=%s''',
                (target['refresh_hours'],target['store'],target['app_id'],target['country']))
        else:
            db.execute('''INSERT INTO app_icons(store,app_id,country,language,requested,status,refresh_hours)
                VALUES(%s,%s,%s,%s,1,'queued',%s) ON DUPLICATE KEY UPDATE
                status=IF(app_icons.requested=0,'queued',app_icons.status),
                due=IF(app_icons.requested=0,0,app_icons.due),requested=1,refresh_hours=LEAST(app_icons.refresh_hours,VALUES(refresh_hours))''',
                (target['store'],target['app_id'],target['country'],target['language'],target['refresh_hours']))


def schedule_submission(db, action, submitted_at):
    from .app_collection import action_target
    ref=action_target(db,action)
    if ref['resource']=='watchlist':
        db.execute('''UPDATE jobs j JOIN watchlist w USING(store,app_id,country,language)
            LEFT JOIN listings l USING(store,app_id,country,language) SET j.due=0
            WHERE w.id=%s AND w.enabled=1 AND j.failures=0 AND (l.checked_at IS NULL OR l.checked_at<%s)''',
            (ref['id'],submitted_at))
    elif ref['resource'] in ('keywords','targets'):
        table,runs = ('keyword_targets','keyword_runs') if ref['resource']=='keywords' else ('observation_targets','observation_runs')
        db.execute(f'''UPDATE {table} t LEFT JOIN {runs} r ON r.id=t.latest_success_id SET t.due=0
            WHERE t.id=%s AND t.enabled=1 AND t.failures=0 AND (r.observed_at IS NULL OR r.observed_at<%s)''',
            (ref['id'],submitted_at))
    else:
        table='app_icons' if ref['resource']=='icons' else 'app_permission_targets'
        db.execute(f'''UPDATE {table} SET due=0 WHERE id=%s AND enabled=1 AND failures=0
            AND (checked_at IS NULL OR checked_at<%s)''',(ref['id'],submitted_at))


def work_plans(db, limit=20):
    # A separate lock transaction survives the helper transactions and is released if the
    # process disconnects. A crash between action and checkpoint safely replays it.
    try:
        with db.named_lock('collection-plans'):
            row=db.execute("SELECT * FROM collection_plans WHERE status IN ('queued','running') ORDER BY id LIMIT 1").fetchone()
            if not row:return {'processed':0,'error':0}
            actions=json.loads(row['actions']);completed=row['completed']
            db.execute("UPDATE collection_plans SET status='running',updated_at=%s WHERE id=%s",(time.time(),row['id']))
            processed=0
            emit(db,'plans',row['id'],'started',f'开始配置目标，从 {completed}/{len(actions)} 继续')
            try:
                for action in actions[completed:completed+limit]:
                    if action['kind'] in ('aso_popularity','aso_import','aso_report'):
                        from .source_tasks import enqueue as enqueue_source
                        enqueue_source(db,action['kind'],action['value'],source='collection-plan',request_id=f'{row["id"]}:{completed}')
                    elif action.get('immediate'):
                        from .app_collection import schedule_action
                        schedule_action(db,action)
                    elif action['kind']=='details':watch(db,action['value'],merge_existing=True)
                    elif action['kind']=='keywords':add_targets(db,[action['value']],merge_existing=True)
                    elif action['kind'] in ('icons','permissions'):configure_asset(db,action)
                    else:add_observation_target(db,action['value'],preserve_existing=action.get('preserve_existing',False),merge_existing=not action.get('preserve_existing',False))
                    if 'input_source' in json.loads(row['specification']) and action['kind'] not in ('aso_popularity','aso_import','aso_report'):
                        schedule_submission(db,action,row['created_at'])
                    completed+=1;processed+=1
                    db.execute("UPDATE collection_plans SET completed=%s,updated_at=%s WHERE id=%s",(completed,time.time(),row['id']))
                    emit(db,'plans',row['id'],'progress',f'已配置 {completed}/{len(actions)} 个目标；类型 {action["kind"]}')
                status='complete' if completed==len(actions) else 'running'
                db.execute('UPDATE collection_plans SET status=%s,error=NULL WHERE id=%s',(status,row['id']))
                emit(db,'plans',row['id'],status,'计划目标已全部配置' if status=='complete' else '本批次已完成，等待下一批')
                from .collection_batch import touch
                touch(db)
                return {'processed':processed,'error':0,'id':row['id'],'status':status}
            except Exception as exc:
                db.execute("UPDATE collection_plans SET status='error',error=%s,updated_at=%s WHERE id=%s",(type(exc).__name__,time.time(),row['id']))
                emit(db,'plans',row['id'],'error','配置失败：'+type(exc).__name__+'；可从断点重试','error',details=diagnostics(exc,stage='plan_configuration'))
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
