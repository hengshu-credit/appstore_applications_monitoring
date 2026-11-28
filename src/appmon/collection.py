"""Reviewable collection plans expanded through existing idempotent collectors."""
import json
import time
import uuid
from .db import dump
from .intelligence import market, hours, text, watch, add_observation_target
from .keywords import validate_target, add_targets
from .catalog import BY_ID
from .charts import CHART_DEPTH
from .collection_batch import PAGE_DUE
from .task_logs import emit
from .log_details import diagnostics
from .source_tasks import KINDS as SOURCE_KINDS
from . import ad_sources, intake, task_windows
from .listing_status import CURRENT_STATE

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
    {'id':'ad_tracking','name':'应用广告追踪','source':'AppGoblin、Google、Apple 及已授权的广告 API','method':'public / authorization','cadence':'用户提交一次性任务或指定周期间隔',
     'fields':'投放渠道与广告位、推广素材及版本、文案、证据链接、广告主样本排名','coverage':'Google、Meta、Instagram、TikTok、Reddit、Yandex、App Store，覆盖依来源市场和授权范围而定'},
    {'id':'owned','name':'自有后台与广告报表','source':'用户导入 / 开发者授权接口','method':'authorization',
     'cadence':'按报表覆盖期间与授权配额配置','fields':'真实广告指标、后台关键词、转化与回复权限',
     'coverage':'现支持广告报表导入及本地草稿；尚未连接开发者账户，不读取竞品私有后台'},
]


def target_key(action):
    value=action['value']
    if action['kind'] not in SOURCE_KINDS:
        value={k:v for k,v in value.items() if k not in ('refresh_hours','depth','group_name')}
    return dump([action['kind'],value])


def unique_actions(actions):
    """Keep the latest settings at the first occurrence of each queue target."""
    unique = {}
    for action in actions:
        value = action['value'].copy()
        settings = {}
        if action['kind'] not in SOURCE_KINDS:
            for key in ('refresh_hours','depth','group_name'):
                if key in value:
                    number = value.pop(key)
                    settings[key] = int(number) if isinstance(number,float) and number.is_integer() else number
        identity = target_key(action)
        # Replacing a dictionary value retains the first occurrence's queue position.
        unique[identity] = action | {'value':value | settings}
    return list(unique.values())


def plan_key(mode,actions,spec=None):
    if mode=='app':
        return intake.digest(['app',spec['app_request']])
    return intake.digest([mode,sorted(dump(a) for a in actions)])


def active_plan(db,fingerprint):
    from .app_collection import report
    # Serialize by normalized work, independently of a producer's request ID.
    db.execute("""INSERT INTO collection_plan_keys VALUES(%s,0)
        ON DUPLICATE KEY UPDATE fingerprint=collection_plan_keys.fingerprint""",(fingerprint,))
    current = db.execute('SELECT plan_id FROM collection_plan_keys WHERE fingerprint=%s FOR UPDATE',(fingerprint,)).fetchone()
    old = db.execute('SELECT id,total,status FROM collection_plans WHERE id=%s',(current['plan_id'],)).fetchone()
    if old and old['status']=='queued':return old
    if old and old['status'] in ('running','complete'):
        progress=report(db,old['id'],True)
        if progress['state']=='running' and not progress['expired']:return old


def save_plan(db,name,spec,actions,mode,source=None,request_id=None):
    fingerprint = plan_key(mode,actions,spec)
    slots_key=intake.digest(['pending-targets',mode,sorted(target_key(a) for a in actions)])
    with db:
        # A plan may still be waiting to create its first queue target. Keep
        # its position among other plans even when the new settings differ.
        db.execute("""INSERT INTO collection_plan_keys VALUES(%s,0)
            ON DUPLICATE KEY UPDATE fingerprint=collection_plan_keys.fingerprint""",(slots_key,))
        slots=db.execute('SELECT plan_id FROM collection_plan_keys WHERE fingerprint=%s FOR UPDATE',(slots_key,)).fetchone()
        waiting=db.execute("SELECT * FROM collection_plans WHERE id=%s AND status='queued' AND completed=0 FOR UPDATE",
                           (slots['plan_id'],)).fetchone()
        old = active_plan(db,fingerprint)
        now=time.time()
        if waiting and not old:
            previous=json.loads(waiting['actions'])
            previous_hash=plan_key(mode,previous,json.loads(waiting['specification']))
            by_target={target_key(a):a for a in actions}
            ordered=[by_target[target_key(a)] for a in previous]
            if waiting['request_source'] and not intake.find_alias(db,'plans',waiting['request_source'],waiting['request_id']):
                intake.record_alias(db,'plans',waiting['request_source'],waiting['request_id'],waiting['id'],previous_hash)
            db.execute('UPDATE collection_plan_keys SET plan_id=0 WHERE fingerprint=%s AND plan_id=%s',(previous_hash,waiting['id']))
            db.execute('UPDATE collection_plan_keys SET plan_id=%s WHERE fingerprint=%s',(waiting['id'],fingerprint))
            db.execute('UPDATE collection_plans SET specification=%s,actions=%s WHERE id=%s',(dump(spec),dump(ordered),waiting['id']))
            old={k:waiting[k] for k in ('id','total','status')}
        if old:
            origin=db.execute('SELECT request_source,request_id,created_at FROM collection_plans WHERE id=%s',(old['id'],)).fetchone()
            accepted=task_windows.latest(db,'plans',old['id'],now,source,request_id,
                fallback={'request_source':origin['request_source'],'request_id':origin['request_id'],'submitted_at':origin['created_at'],'pending':True})
            if accepted:db.execute('UPDATE collection_plans SET name=%s,updated_at=%s WHERE id=%s',(name,now,old['id']))
            if source:
                intake.record_alias(db,'plans',source,request_id,old['id'],fingerprint)
            return old | {'reused':True} if accepted else old | {'reused':True,'status':'expired'}
        identifier = db.execute("""INSERT INTO collection_plans(name,specification,actions,total,created_at,updated_at,request_source,request_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(name,dump(spec),dump(actions),len(actions),now,now,source,request_id)).lastrowid
        db.execute('UPDATE collection_plan_keys SET plan_id=%s WHERE fingerprint IN %s',(identifier,(fingerprint,slots_key)))
        emit(db,'plans',identifier,'queued',f'采集计划已创建，待配置 {len(actions)} 个目标')
    return {'id':identifier,'total':len(actions),'status':'queued','reused':False}


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
    flags={name:value.get(name,False) for name in ('details','reviews','chart','featured','icons')}
    if any(type(v) is not bool for v in flags.values()):raise ValueError('采集开关必须是布尔值')
    group=text(value.get('group_name','重点应用'))
    actions=[]
    for app_id in apps:
        if flags['icons']:actions.append({'kind':'icons','value':base|{'app_id':app_id,'refresh_hours':interval}})
        if flags['details']:actions.append({'kind':'details','value':base|{'app_id':app_id,'group_name':group,'refresh_hours':interval}})
        if flags['reviews']:actions.append({'kind':'reviews','value':base|{'app_id':app_id,'kind':'reviews','depth':depth,'refresh_hours':interval}})
    for keyword in list(dict.fromkeys(words)):
        if keyword.strip():actions.append({'kind':'keywords','value':validate_target(base|{'keyword':keyword,'depth':keyword_depth,'refresh_hours':interval})})
    if flags['chart']:
        chart_depth=value.get('chart_depth',value.get('depth',CHART_DEPTH[m['store']]))
        if type(chart_depth) is not int or not 1<=chart_depth<=500:raise ValueError('榜单采集深度须为1–500的整数')
        category=value.get('category',''); collection=value.get('collection','top-free')
        if not isinstance(category,str) or len(category)>64:raise ValueError('分类无效')
        from .charts import definition
        collection=definition(m['store'],collection)['base_collection']
        if category and not definition(m['store'],collection)['category_supported']:
            raise ValueError('该商店公开新应用榜仅支持总榜，请选择全部分类')
        if category and category!='APPLICATION' and (category not in BY_ID[m['store']] or BY_ID[m['store']][category]['legacy']):
            raise ValueError('请选择当前商店的官方分类')
        if category=='APPLICATION' and m['store']!='google':raise ValueError('Apple不支持Google分类ID')
        actions.append({'kind':'chart','value':base|{'kind':'chart','category':category,'collection':collection,'depth':min(chart_depth,CHART_DEPTH[m['store']]),'refresh_hours':interval}})
    if flags['featured']:
        if m['store']!='apple':raise ValueError('Today推荐只支持Apple')
        actions.append({'kind':'featured','value':base|{'kind':'featured','depth':depth,'refresh_hours':interval}})
    if 'validity_minutes' in value:
        minutes=task_windows.validate(value['validity_minutes'])
        for action in actions:action['validity_minutes']=minutes
    actions=unique_actions(actions)
    if not actions:raise ValueError('请至少选择一个有目标的采集任务')
    return {'actions':actions,'total':len(actions),'market':base,
            'scope':'这是任务配置数量，不是请求量或完成时间承诺。已有目标会更新周期，暂停状态保留。'}


def create_plan(db, value):
    result=preview_plan(value)
    name=text(value.get('name','采集计划'))
    return save_plan(db,name,value,result['actions'],'configure')


def expired_plan_receipt(db,row,source,request_id):
    if task_windows.is_expired(db,'plans',row['id'],source,request_id):return True
    if row['status']=='complete':
        from .app_collection import report
        return report(db,row['id'],True)['state']=='expired'
    return False


def enqueue(db, value, *, ad_requested_by=None):
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
        if not isinstance(kind,str):
            raise ValueError('未知采集类型')
        if kind in ad_sources.KINDS:
            if type(ad_requested_by) is not int or ad_requested_by <= 0:
                raise ValueError('广告任务需要用户明确提交')
            if task.get('mode','once') != 'once' or task.get('refresh_hours') is not None:
                raise ValueError('请在应用广告追踪中设置周期任务')
        if kind in SOURCE_KINDS:
            from .source_tasks import normalize
            actions.append({'kind':kind,'value':normalize(kind,task)})
        elif kind in ('icons','permissions'):
            target = market(task)
            if kind=='permissions' and target['store']!='google':raise ValueError('权限采集仅支持 Google Play')
            if kind=='permissions':target['language']='en'
            action={'kind':kind,'value':target|{'refresh_hours':hours(task.get('refresh_hours',24))}}
            if 'validity_minutes' in task:action['validity_minutes']=task_windows.validate(task['validity_minutes'])
            actions.append(action)
        elif kind in ('details','keywords','chart','reviews','featured'):
            spec = task | {k:k==kind for k in ('details','chart','reviews','featured','icons')}
            spec['app_ids'] = [task.get('app_id','')] if kind in ('details','reviews') else []
            spec['keywords'] = [task.get('keyword','')] if kind=='keywords' else []
            spec['keyword_depth'] = task.get('depth',50) if kind=='keywords' else 50
            result = preview_plan(spec)
            if len(result['actions'])!=1:raise ValueError('采集目标无效')
            actions.extend(result['actions'])
        else:
            raise ValueError('未知采集类型')
    actions = unique_actions(actions)
    ad_actions = [(index,action) for index,action in enumerate(actions) if action['kind'] in ad_sources.KINDS]
    with db.named_lock('enqueue:'+dump([source,request_id]),timeout=10):
        alias = intake.find_alias(db,'plans',source,request_id)
        if alias:
            if alias['fingerprint'] != plan_key('collect',actions):
                raise ValueError('同一来源请求编号不能对应不同任务')
            row = db.execute('SELECT id,total,status FROM collection_plans WHERE id=%s',(alias['task_id'],)).fetchone()
            if expired_plan_receipt(db,row,source,request_id):row['status']='expired'
            return row | {'reused':True,'request_id':request_id}
        old = db.execute('''SELECT id,total,status,actions,specification FROM collection_plans WHERE
            request_source=%s AND request_id=%s LIMIT 1''',(source,request_id)).fetchone()
        if old:
            stored_actions = json.loads(old['actions'])
            if plan_key('collect',unique_actions(stored_actions))!=plan_key('collect',actions):raise ValueError('同一来源请求编号不能对应不同任务')
            if expired_plan_receipt(db,old,source,request_id):
                return dict(id=old['id'],total=old['total'],status='expired',reused=True,request_id=request_id)
            spec = json.loads(old['specification'])
            if ad_actions and not spec.get('ad_requested_by'):
                # An authenticated user has now explicitly re-submitted an old
                # intake receipt. Keep successful observations and schedule
                # only the advertising work, without replaying unrelated tasks.
                from .collection_batch import write_batch
                from .source_tasks import enqueue as enqueue_source
                with write_batch(db) as writer:
                    for index, action in enumerate(stored_actions):
                        if action['kind'] not in ad_sources.KINDS:continue
                        key = f'{old["id"]}:{index}'
                        saved = writer.execute("""UPDATE collection_source_tasks SET source='user-ad-plan'
                            WHERE source='collection-plan' AND request_id=%s AND due IS NULL AND success_at IS NOT NULL""",(key,)).rowcount
                        if not saved:
                            enqueue_source(writer,action['kind'],action['value'],source='user-ad-plan',request_id=key,
                                           submitted_by=ad_requested_by)
                    spec['ad_requested_by'] = ad_requested_by
                    writer.execute('UPDATE collection_plans SET specification=%s,updated_at=%s WHERE id=%s',
                                   (dump(spec),time.time(),old['id']))
            return {k:old[k] for k in ('id','total','status')} | {'reused':True,'request_id':request_id}
        specification = {'input_source':source,'request_id':request_id}
        if ad_actions:
            specification['ad_requested_by'] = ad_requested_by
        name = text(value.get('name',source+' 采集请求'))
        return save_plan(db,name,specification,actions,'collect',source,request_id) | {'request_id':request_id}


def retry_plan(db,identifier):
    identifier = intake.resolve_id(db,'plans',identifier)
    row = db.execute('SELECT status,actions,request_source,specification FROM collection_plans WHERE id=%s',(identifier,)).fetchone()
    if not row or row['status']!='error':raise ValueError('只有失败计划可以重试')
    spec = json.loads(row['specification'])
    actions = unique_actions(json.loads(row['actions']))
    mode = 'collect' if row['request_source'] is not None else 'configure'
    if mode=='configure' and 'app_request' in spec:mode='app'
    key = plan_key(mode,actions,spec)
    with db:
        current = active_plan(db,key)
        if current:
            return current | {'reused':True}
        if not db.execute("""UPDATE collection_plans SET status='queued',error=NULL,updated_at=%s
                WHERE id=%s AND status='error'""",(time.time(),identifier)).rowcount:
            raise ValueError('只有失败计划可以重试')
        # Legacy implicit advertising receipts cannot stand in for an explicit request.
        if not any(a['kind'] in ad_sources.KINDS for a in actions) or spec.get('ad_requested_by'):
            db.execute('UPDATE collection_plan_keys SET plan_id=%s WHERE fingerprint=%s',(identifier,key))
        emit(db,'plans',identifier,'retry','已安排从未完成的配置步骤继续重试')
    return {'id':identifier,'status':'queued'}


def configure_asset(db, action, initial_due=0, replace=False):
    target=action['value']
    interval='VALUES(refresh_hours)' if replace else 'LEAST(app_icons.refresh_hours,VALUES(refresh_hours))'
    with db:
        if action['kind']=='permissions':
            from .app_permissions import enqueue as enqueue_permissions
            enqueue_permissions(db,[target])
            setting='%s' if replace else 'LEAST(refresh_hours,%s)'
            db.execute(f'''UPDATE app_permission_targets SET refresh_hours={setting}
                WHERE store=%s AND app_id=%s AND country=%s''',
                (target['refresh_hours'],target['store'],target['app_id'],target['country']))
        else:
            db.execute(f"""INSERT INTO app_icons(store,app_id,country,language,requested,status,refresh_hours,request_mode,due)
                VALUES(%s,%s,%s,%s,1,'queued',%s,'periodic',%s) ON DUPLICATE KEY UPDATE
                status=IF(app_icons.requested=0,'queued',app_icons.status),
                due=IF(app_icons.failures=0 AND app_icons.lease_until<=UNIX_TIMESTAMP() AND
                    (app_icons.requested=0 OR VALUES(due)<0 AND app_icons.due<=UNIX_TIMESTAMP()),LEAST(app_icons.due,VALUES(due)),app_icons.due),requested=1,
                refresh_hours=IF(app_icons.request_mode='periodic',{interval},VALUES(refresh_hours)),
                request_mode='periodic'""",
                (target['store'],target['app_id'],target['country'],target['language'],target['refresh_hours'],initial_due))


def schedule_submission(db, action, submitted_at):
    from .app_collection import action_target
    ref=action_target(db,action)
    if ref['resource']=='watchlist':
        db.execute('''UPDATE jobs j JOIN watchlist w USING(store,app_id,country,language)
            LEFT JOIN listings l USING(store,app_id,country,language) SET j.due=IF(j.due<=UNIX_TIMESTAMP(),j.due,0)
            WHERE w.id=%s AND w.enabled=1 AND j.failures=0 AND (l.checked_at IS NULL OR l.checked_at<%s)''',
            (ref['id'],submitted_at))
    elif ref['resource'] in ('keywords','targets'):
        table,runs = ('keyword_targets','keyword_runs') if ref['resource']=='keywords' else ('observation_targets','observation_runs')
        db.execute(f'''UPDATE {table} t LEFT JOIN {runs} r ON r.id=t.latest_success_id SET t.due=IF(t.due<=UNIX_TIMESTAMP(),t.due,0)
            WHERE t.id=%s AND t.enabled=1 AND t.failures=0 AND (r.observed_at IS NULL OR r.observed_at<%s)''',
            (ref['id'],submitted_at))
    else:
        table='app_icons' if ref['resource']=='icons' else 'app_permission_targets'
        db.execute(f'''UPDATE {table} SET due=IF(due<=UNIX_TIMESTAMP(),due,0) WHERE id=%s AND enabled=1 AND failures=0
            AND (checked_at IS NULL OR checked_at<%s)''',(ref['id'],submitted_at))


def apply_target(db,action,plan,index,spec,initial_due,at):
    """Serialize replacement, settings and receipt on the same queue slot."""
    from .app_collection import action_target,schedule_action
    from .collection_batch import BatchWriter
    with db:
        writer=BatchWriter(db)
        ref=action_target(writer,action)
        native=task_windows.rows_for(writer,ref['resource'],[ref['id']]) if ref else []
        row=native[0] if native else None
        receipt=('plan-action',f'{plan}:{index}')
        if row and not row['token'] and not task_windows.admit_plan(writer,ref['resource'],row,at,receipt):return
        replace=bool(row and row['due']<=time.time() and not row['token'] and not row['failures'])
        if row is None:writer.execute('SAVEPOINT create_target')
        if action.get('immediate'):
            schedule_action(writer,action,at)
        elif action['kind']=='details':
            watch(writer,action['value'],merge_existing=not replace,initial_due=initial_due,record_page=False)
        elif action['kind']=='keywords':
            add_targets(writer,[action['value']],merge_existing=not replace,initial_due=initial_due,record_page=False)
        elif action['kind'] in ('icons','permissions'):
            configure_asset(writer,action,initial_due,replace)
        else:
            preserve=action.get('preserve_existing',False)
            add_observation_target(writer,action['value'],preserve_existing=preserve,merge_existing=not (replace or preserve),initial_due=initial_due,record_page=False)
        if 'input_source' in spec and (not row or not row['token']):schedule_submission(writer,action,at)
        ref=ref or action_target(writer,action)
        current=task_windows.rows_for(writer,ref['resource'],[ref['id']])[0]
        if row is None and not task_windows.latest(writer,ref['resource'],ref['id'],at,*receipt):
            # A page can create this same target after the initial lookup.
            # Undo our upsert before acknowledging the superseded action.
            writer.execute('ROLLBACK TO SAVEPOINT create_target')
            task_windows.expire_receipt(writer,ref['resource'],ref['id'],*receipt,time.time())
            return
        if 'validity_minutes' in action or row is None:
            task_windows.configure(writer,ref['resource'],current,action.get('validity_minutes',0),at)
        if current['due']>time.time():
            task_windows.end_pending(writer,ref['resource'],[ref['id']])
        elif row and not row['token']:
            changed=row['refresh_hours']!=current['refresh_hours']
            row['refresh_hours']=current['refresh_hours']
            task_windows.submitted(writer,ref['resource'],row,at,new_due=current['due'],interval_changed=changed)


def work_plans(db, limit=20):
    # A separate lock transaction survives the helper transactions and is released if the
    # process disconnects. A crash between action and checkpoint safely replays it.
    try:
        with db.named_lock('collection-plans'):
            with db:
                row=db.execute("SELECT * FROM collection_plans WHERE status IN ('queued','running') ORDER BY request_source IS NOT NULL,id LIMIT 1 FOR UPDATE").fetchone()
                if row:db.execute("UPDATE collection_plans SET status='running',updated_at=%s WHERE id=%s",(time.time(),row['id']))
            if not row:return {'processed':0,'error':0}
            actions=json.loads(row['actions']);completed=row['completed']
            specification=json.loads(row['specification'])
            initial_due = PAGE_DUE if row['request_source'] is None else 0
            stamp=db.execute("SELECT submitted_at FROM collection_latest_submissions WHERE resource='plans' AND target_id=%s",(row['id'],)).fetchone()
            submitted_at=stamp['submitted_at'] if stamp else row['created_at']
            processed=0
            emit(db,'plans',row['id'],'started',f'开始配置目标，从 {completed}/{len(actions)} 继续')
            try:
                for action in actions[completed:completed+limit]:
                    if action['kind'] in ad_sources.KINDS:
                        if specification.get('input_source') and specification.get('ad_requested_by'):
                            from .source_tasks import enqueue as enqueue_source
                            enqueue_source(db,action['kind'],action['value'],source='user-ad-plan',request_id=f'{row["id"]}:{completed}',
                                           submitted_by=specification['ad_requested_by'],submitted_at=submitted_at)
                        else:
                            emit(db,'plans',row['id'],'skipped','已跳过旧计划附带的广告任务')
                    elif action['kind'] in SOURCE_KINDS:
                        from .source_tasks import enqueue as enqueue_source
                        enqueue_source(db,action['kind'],action['value'],source='collection-plan',request_id=f'{row["id"]}:{completed}',submitted_at=submitted_at)
                    else:
                        apply_target(db,action,row['id'],completed,specification,initial_due,submitted_at)
                    completed+=1;processed+=1
                    db.execute("UPDATE collection_plans SET completed=%s,updated_at=%s WHERE id=%s",(completed,time.time(),row['id']))
                    emit(db,'plans',row['id'],'progress',f'已处理 {completed}/{len(actions)} 个目标；类型 {action["kind"]}')
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
    rows=db.execute(f'''SELECT w.*,j.due,j.failures,j.last_error,l.last_success success_at,r.observed_at attempt_at,
        r.outcome attempt_outcome,{CURRENT_STATE} state FROM watchlist w LEFT JOIN jobs j USING(store,app_id,country,language)
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
