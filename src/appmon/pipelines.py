"""Database-owned collection settings shared by API and long-running workers."""
import json
import re
import time
from .db import dump
from .keywords import validate_target
from .intelligence import hours
from .task_logs import emit

NAMES = {'pool':'全池应用详情', 'seed':'AppGoblin 公开种子同步', 'discovery':'榜单 / 搜索 / 开发者发现'}
INTERVALS = {'pool':'refresh_hours','seed':'seed_sync_hours','discovery':'discovery_hours'}


def settings(db, cfg, name):
    if name not in NAMES: raise ValueError('未知采集流程')
    defaults = {'enabled':True,'refresh_hours':cfg[INTERVALS[name]]}
    if name!='seed': defaults.update(countries=cfg['countries'],language=cfg['language'])
    if name=='discovery': defaults.update(search_terms=cfg['search_terms'],google_categories=cfg['google_categories'])
    row = db.execute('SELECT settings FROM collection_controls WHERE name=%s',(name,)).fetchone()
    return defaults | (json.loads(row['settings']) if row else {})


def configured(db, cfg, name):
    value = settings(db,cfg,name)
    return value['enabled'], cfg | {INTERVALS[name]:value['refresh_hours']} | {
        k:value[k] for k in ('countries','language','search_terms','google_categories') if k in value}


def save(db, cfg, name, value):
    old = settings(db,cfg,name)
    enabled = value.get('enabled',old['enabled'])
    if type(enabled) is not bool: raise ValueError('启用状态须为布尔值')
    result = {'enabled':enabled,'refresh_hours':hours(value.get('refresh_hours',old['refresh_hours']))}
    if name!='seed':
        countries = value.get('countries',old['countries'])
        if not isinstance(countries,list) or not 1<=len(countries)<=30 or any(
                not isinstance(c,str) or not re.fullmatch('[a-zA-Z]{2}',c) for c in countries):
            raise ValueError('请选择1–30个两字母国家代码')
        language = value.get('language',old['language'])
        if language=='auto':raise ValueError('基础流程跨多个国家，请指定固定语言；单市场任务支持自动推荐')
        validate_target({'store':'google','country':'us','language':language,'keyword':'check'})
        result.update(countries=list(dict.fromkeys(c.lower() for c in countries)),language=language.lower().replace('_','-'))
    if name=='discovery':
        from .catalog import BY_ID
        terms=value.get('search_terms',old['search_terms'])
        categories=value.get('google_categories',old['google_categories'])
        if not isinstance(terms,list) or len(terms)>100 or any(not isinstance(t,str) or not 1<=len(t.strip())<=255 for t in terms):
            raise ValueError('发现词最多100个，每词1–255字符')
        if not isinstance(categories,list) or len(categories)>100 or any(not isinstance(c,str) or c not in BY_ID['google'] or BY_ID['google'][c]['legacy'] for c in categories):
            raise ValueError('请选择有效的Google官方分类ID')
        result.update(search_terms=list(dict.fromkeys(t.strip() for t in terms)),google_categories=list(dict.fromkeys(categories)))
    with db:
        db.execute('''INSERT INTO collection_controls VALUES(%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE
            settings=incoming.settings,updated_at=incoming.updated_at''',(name,dump(result),time.time()))
        # Re-evaluate periodic source schedules after a configuration change.
        if name=='seed': db.execute("UPDATE schedules SET next_at=0 WHERE name='seed-sync'")
        if name=='discovery': db.execute("UPDATE schedules SET next_at=0 WHERE name LIKE 'discover:%'")
        emit(db,'pipelines',name,'settings_updated',f'流程设置已更新：{"启用" if enabled else "暂停"}，周期 {result["refresh_hours"]} 小时')
    return result


def report(db,cfg, *, counts=True):
    rows = [{'id':name,'name':label,**settings(db,cfg,name)} for name,label in NAMES.items()]
    if counts:
        rows[0].update(pool_summary(db,rows[0]))
    return rows


def pool_summary(db,pool):
    now = time.time()
    countries = ','.join('%s' for _ in pool['countries'])
    counts = db.execute(f'''SELECT CASE WHEN j.country NOT IN ({countries}) OR j.language<>%s THEN 'out_of_scope'
        WHEN %s=0 THEN 'paused' WHEN j.lease_until>%s THEN 'running'
        WHEN j.failures>0 THEN 'error' WHEN j.due<=%s THEN 'pending' ELSE 'scheduled' END state,count(*) n
        FROM jobs j WHERE NOT EXISTS (SELECT 1 FROM watchlist w WHERE w.store=j.store AND w.app_id=j.app_id
        AND w.country=j.country AND w.language=j.language) GROUP BY state''',
        (*pool['countries'],pool['language'],pool['enabled'],now,now)).fetchall()
    summary = dict.fromkeys(('paused','running','error','pending','scheduled','out_of_scope'),0)
    summary.update({r['state']:r['n'] for r in counts})
    return dict(summary=summary,total=sum(summary.values()),observed_at=time.time())
