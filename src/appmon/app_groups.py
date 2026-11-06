"""Cross-store application portfolios with market-scoped, evidence-backed dashboards."""
import csv
import io
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import pymysql
from .db import add_apps, dump
from .intelligence import market, text
from . import app_permissions, metric_facts


def group(db, identifier):
    row=db.execute('SELECT * FROM app_groups WHERE id=%s',(identifier,)).fetchone()
    if not row:raise ValueError('应用分组不存在')
    return row


def create(db, value):
    name=text(value.get('name'))
    description=value.get('description','')
    if not isinstance(description,str) or len(description)>2000:raise ValueError('分组说明最多2000字符')
    try:
        identifier=db.execute('INSERT INTO app_groups(name,description,created_at,updated_at) VALUES(%s,%s,%s,%s)',(name,description,time.time(),time.time())).lastrowid
    except pymysql.IntegrityError:raise ValueError('分组名称已存在') from None
    return group(db,identifier)


def update(db, identifier, value):
    name=text(value.get('name'));description=value.get('description','');revision=value.get('revision')
    if not isinstance(description,str) or len(description)>2000 or type(revision) is not int:raise ValueError('分组说明或版本无效')
    try:
        changed=db.execute('UPDATE app_groups SET name=%s,description=%s,revision=revision+1,updated_at=%s WHERE id=%s AND revision=%s',
            (name,description,time.time(),identifier,revision)).rowcount
    except pymysql.IntegrityError:raise ValueError('分组名称已存在') from None
    if not changed:raise ValueError('分组已更新，请刷新后重试')
    return group(db,identifier)


def list_groups(db):
    return db.execute('''SELECT g.*,COUNT(m.id) targets,COUNT(DISTINCT m.store,m.app_id) apps,
        COUNT(DISTINCT m.country) countries FROM app_groups g LEFT JOIN app_group_members m ON m.group_id=g.id
        GROUP BY g.id ORDER BY targets DESC,g.id''').fetchall()


def bootstrap(db):
    with db:
        db.execute("INSERT INTO schedules VALUES('group-bootstrap',0) ON DUPLICATE KEY UPDATE name=schedules.name")
        if db.execute("SELECT next_at FROM schedules WHERE name='group-bootstrap' FOR UPDATE").fetchone()['next_at']>0:return
        now=time.time()
        db.execute('''INSERT INTO app_groups(name,description,created_at,updated_at)
            SELECT DISTINCT group_name,'从已有关注分类建立',%s,%s FROM watchlist
            ON DUPLICATE KEY UPDATE name=app_groups.name''',(now,now))
        db.execute('''INSERT INTO app_group_members(group_id,store,app_id,country,language,added_at)
            SELECT g.id,w.store,w.app_id,w.country,w.language,w.created_at FROM watchlist w JOIN app_groups g ON g.name=w.group_name
            ON DUPLICATE KEY UPDATE id=app_group_members.id''')
        app_permissions.enqueue(db,db.execute('''SELECT m.store,m.app_id,m.country FROM app_group_members m
            JOIN watchlist w USING(store,app_id,country,language) WHERE w.enabled=1''').fetchall())
        db.execute("UPDATE schedules SET next_at=%s WHERE name='group-bootstrap'",(now,))


def normalize_members(value):
    rows=value.get('members')
    if 'text' in value:
        content=value['text']
        if not isinstance(content,str) or len(content)>2_000_000:raise ValueError('应用名单过长')
        rows=[]
        try:
            for line in csv.reader(io.StringIO(content),strict=True):
                if not line or not any(x.strip() for x in line):continue
                line=[x.strip() for x in line]
                if line[0].lower()=='store':continue
                if len(line)==1:
                    for country in str(value.get('countries','')).replace('，',',').split(','):
                        rows.append(dict(store=value.get('store'),app_id=line[0],country=country.strip(),language=value.get('language','auto')))
                elif 3<=len(line)<=4:
                    rows.append(dict(store=line[0],app_id=line[1],country=line[2],language=line[3] if len(line)>3 else 'auto'))
                else:raise ValueError('每行使用 APP ID，或 store,app_id,country,language')
        except csv.Error:raise ValueError('应用名单格式无效') from None
    if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise ValueError('每批加入1–10000个国家观察目标')
    prepared={}
    from .app_discovery import identity
    for row in rows:
        if not isinstance(row,dict):raise ValueError('应用记录须为对象')
        app_id=row.get('app_id')
        if isinstance(app_id,str) and app_id.startswith('https://'):app_id=identity(row.get('store'),app_id)
        if not isinstance(app_id,str) or len(app_id)>512:raise ValueError('应用 ID 无效或过长')
        m=market(row|{'app_id':app_id})
        prepared[tuple(m[k] for k in ('store','app_id','country','language'))]=m
    return list(prepared.values())


def add_members(db, identifier, value):
    owner=group(db,identifier);rows=normalize_members(value);now=time.time()
    with db.named_lock('group-members:'+str(identifier)), db:
        before=db.execute('SELECT COUNT(*) n FROM app_group_members WHERE group_id=%s',(identifier,)).fetchone()['n']
        for start in range(0,len(rows),500):
            batch=rows[start:start+500]
            add_apps(db,[(r['store'],r['app_id'],None,None) for r in batch],'group-monitoring')
            db.executemany('''INSERT INTO app_group_members(group_id,store,app_id,country,language,added_at)
                VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=app_group_members.id''',
                [(identifier,*(r[k] for k in ('store','app_id','country','language')),now) for r in batch])
            db.executemany('''INSERT INTO watchlist(store,app_id,country,language,group_name,created_at)
                VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=watchlist.id''',
                [(*(r[k] for k in ('store','app_id','country','language')),owner['name'],now) for r in batch])
            db.executemany('''INSERT INTO jobs(store,app_id,country,language) VALUES(%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE app_id=jobs.app_id''',[tuple(r[k] for k in ('store','app_id','country','language')) for r in batch])
        app_permissions.enqueue(db,db.execute('''SELECT m.store,m.app_id,m.country FROM app_group_members m
            JOIN watchlist w USING(store,app_id,country,language) WHERE m.group_id=%s AND w.enabled=1''',(identifier,)).fetchall())
        after=db.execute('SELECT COUNT(*) n FROM app_group_members WHERE group_id=%s',(identifier,)).fetchone()['n']
        db.execute('UPDATE app_groups SET updated_at=%s WHERE id=%s',(now,identifier))
    return dict(added=after-before,existing=len(rows)-(after-before),total=after)


def remove_members(db, identifier, value):
    group(db,identifier);ids=value.get('ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=10000 or any(type(v) is not int or v<=0 for v in ids):raise ValueError('请选择要移出的分组成员')
    placeholders=','.join('%s' for _ in ids)
    with db:
        owned=db.execute(f'SELECT id FROM app_group_members WHERE group_id=%s AND id IN ({placeholders}) FOR UPDATE',(identifier,*ids)).fetchall()
        if owned:
            db.execute('DELETE FROM app_group_adjustments WHERE member_id IN ('+','.join('%s' for _ in owned)+')',tuple(r['id'] for r in owned))
        count=db.execute(f'DELETE FROM app_group_members WHERE group_id=%s AND id IN ({placeholders})',(identifier,*ids)).rowcount
    return dict(removed=count,note='仅移出本组，其他分组、采集任务及历史记录保留')


def import_references(db, rows):
    prepared=[]
    for row in rows:
        m=market(row);state=row.get('source_status')
        if state not in ('on','off','error'):continue
        prepared.append((*(m[k] for k in ('store','app_id','country','language')),state,str(row.get('source_generated_at') or '')[:120],time.time()))
    if prepared:db.executemany('''INSERT INTO app_reference_states VALUES(%s,%s,%s,%s,%s,%s,%s) AS incoming
        ON DUPLICATE KEY UPDATE state=incoming.state,observed_text=incoming.observed_text,imported_at=incoming.imported_at''',prepared)
    return len(prepared)


def sync_watch_category(db, name):
    name=text(name);now=time.time()
    db.execute('''INSERT INTO app_groups(name,description,created_at,updated_at) VALUES(%s,'由分类名单导入',%s,%s)
        ON DUPLICATE KEY UPDATE name=app_groups.name''',(name,now,now))
    identifier=db.execute('SELECT id FROM app_groups WHERE name=%s',(name,)).fetchone()['id']
    db.execute('''INSERT INTO app_group_members(group_id,store,app_id,country,language,added_at)
        SELECT %s,store,app_id,country,language,%s FROM watchlist WHERE group_name=%s
        ON DUPLICATE KEY UPDATE id=app_group_members.id''',(identifier,now,name))
    app_permissions.enqueue(db,db.execute('SELECT store,app_id,country FROM watchlist WHERE group_name=%s AND enabled=1',(name,)).fetchall())
    return identifier


def _scope(identifier, store='all', country='', alias='m'):
    from .locales import stores
    import re
    selected=stores(store)
    if country and not re.fullmatch('[a-z]{2}',country):raise ValueError('国家代码无效')
    where=f'{alias}.group_id=%s AND {alias}.store IN ('+','.join('%s' for _ in selected)+')'
    args=[identifier,*selected]
    if country:where+=f' AND {alias}.country=%s';args.append(country)
    return where,args


def _number(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):return None


def resolve_state(observed, reference, override=None):
    """Derive a group display decision while keeping its evidence and origin separate."""
    if override:
        return override, 'adjustment'
    if observed in ('available', 'region_unavailable'):
        return observed, 'store'
    if observed == 'suspected_missing':
        if reference == 'off':
            return 'region_unavailable', 'corroborated'
        return 'suspected_missing', 'store'
    if reference in ('on', 'off'):
        return ('available' if reference == 'on' else 'region_unavailable'), 'reference'
    return 'unverified', 'unknown'


def ensure_verification_jobs(db):
    """Repair missing jobs for active group watches without resetting due dates or leases."""
    now = time.time()
    with db:
        db.execute("INSERT INTO schedules VALUES('group-job-repair',0) ON DUPLICATE KEY UPDATE name=schedules.name")
        scheduled = db.execute("SELECT next_at FROM schedules WHERE name='group-job-repair' FOR UPDATE").fetchone()
        if scheduled['next_at'] > now:
            return 0
        added = db.execute('''INSERT INTO jobs(store,app_id,country,language)
            SELECT w.store,w.app_id,w.country,w.language FROM watchlist w LEFT JOIN jobs j USING(store,app_id,country,language)
            WHERE w.enabled=1 AND j.app_id IS NULL AND EXISTS (SELECT 1 FROM app_group_members m WHERE
                m.store=w.store AND m.app_id=w.app_id AND m.country=w.country AND m.language=w.language)
            ON DUPLICATE KEY UPDATE app_id=jobs.app_id''').rowcount
        db.execute("UPDATE schedules SET next_at=%s WHERE name='group-job-repair'", (now + 3600,))
    return added


def adjust_member(db, identifier, member_id, value, actor):
    status = value.get('status_override')
    permissions = value.get('permission_overrides')
    revision = value.get('revision')
    if 'status_override' not in value or status not in (None, 'available', 'region_unavailable'):
        raise ValueError('分组状态无效')
    if type(revision) is not int or revision < 0:
        raise ValueError('版本无效')
    if not isinstance(permissions, dict) or len(permissions) > 64:
        raise ValueError('权限修正无效')
    for category, declared in permissions.items():
        if not isinstance(category, str) or not category.strip() or len(category) > 128 or not category.isprintable():
            raise ValueError('权限类别无效')
        if type(declared) is not bool:
            raise ValueError('权限修正须为布尔值')
    with db:
        member = db.execute('SELECT store FROM app_group_members WHERE group_id=%s AND id=%s FOR UPDATE',
                            (identifier, member_id)).fetchone()
        if not member:
            raise ValueError('该应用不在当前分组')
        if member['store'] != 'google' and permissions:
            raise ValueError('只支持调整 Google Play 声明权限')
        old = db.execute('SELECT revision FROM app_group_adjustments WHERE member_id=%s', (member_id,)).fetchone()
        if revision != (old['revision'] if old else 0):
            raise ValueError('此行已更新，请刷新后再调整')
        db.execute('''INSERT INTO app_group_adjustments(member_id,status_override,permission_overrides,revision,updated_by,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE status_override=incoming.status_override,
            permission_overrides=incoming.permission_overrides,revision=incoming.revision,updated_by=incoming.updated_by,updated_at=incoming.updated_at''',
            (member_id, status, dump(permissions), revision + 1, actor, time.time()))
    return {'revision': revision + 1}


def member_rows(db, identifier, store='all'):
    where,args=_scope(identifier,store)
    rows=db.execute('''SELECT m.*,a.name,w.enabled,w.refresh_hours,l.state,l.checked_at,l.last_success,
        j.failures,j.last_error,j.due,j.lease_until,
        JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.rating')) rating,
        JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.ratings')) ratings,
        JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.installs')) installs,
        JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.version')) version,
        p.id permission_id,p.enabled permission_enabled,p.refresh_hours permission_refresh_hours,
        p.success_at permission_success,p.checked_at permission_checked,p.permission_count,p.failures permission_failures,p.lease_until permission_lease,
        p.last_error permission_error,JSON_KEYS(p.payload) permission_groups,ref.state reference_state,ref.observed_text reference_time,
        adj.status_override,adj.permission_overrides,COALESCE(adj.revision,0) adjustment_revision,adj.updated_by,adj.updated_at adjustment_time
        FROM app_group_members m LEFT JOIN apps a USING(store,app_id)
        LEFT JOIN watchlist w USING(store,app_id,country,language)
        LEFT JOIN listings l USING(store,app_id,country,language)
        LEFT JOIN jobs j USING(store,app_id,country,language)
        LEFT JOIN app_permission_targets p ON p.store=m.store AND p.app_id=m.app_id AND p.country=m.country
        LEFT JOIN app_reference_states ref ON ref.store=m.store AND ref.app_id=m.app_id AND ref.country=m.country AND ref.language=m.language
        LEFT JOIN app_group_adjustments adj ON adj.member_id=m.id
        WHERE '''+where+' ORDER BY m.country,m.store,m.app_id,m.language',args).fetchall()
    for r in rows:
        for key in ('rating','ratings','installs'):r[key]=_number(r[key])
        r['permission_groups']=json.loads(r['permission_groups']) if r['permission_groups'] else []
        r['state']=r['state'] or 'unverified'
        r['classification'],r['classification_source']=resolve_state(r['state'],r['reference_state'],r['status_override'])
        r['permission_overrides']=json.loads(r['permission_overrides'] or '{}')
        r['permission_state']=app_permissions.state(r)
        r['details_state']=('not_scheduled' if r['enabled'] is None or r['due'] is None else 'paused' if not r['enabled']
            else 'running' if r['lease_until']>time.time() else 'retry' if r['failures'] else 'scheduled' if r['due']>time.time() else 'pending')
    return rows


def rankings(db, identifier, store='all', country='', now=None):
    where,args=_scope(identifier,store,country);now=time.time() if now is None else now
    keyword=db.execute('''SELECT m.id,COUNT(DISTINCT t.id) keyword_count,MIN(k.position) keyword_best,
        COUNT(DISTINCT CASE WHEN k.position<=3 THEN t.id END) keyword_top3_count
        FROM app_group_members m JOIN keyword_ranks k ON k.app_id=m.app_id
        JOIN keyword_targets t ON t.latest_success_id=k.run_id AND t.store=m.store AND t.country=m.country AND t.language=m.language
        JOIN keyword_runs r ON r.id=k.run_id WHERE '''+where+'''
        AND t.enabled=1 AND t.failures=0 AND r.outcome='ok' AND r.requested_depth=t.depth
        AND r.observed_at+t.refresh_hours*3600>%s GROUP BY m.id''',(*args,now)).fetchall()
    chart=db.execute('''SELECT m.id,COUNT(DISTINCT t.id) chart_count,MIN(k.position) chart_best
        FROM app_group_members m JOIN observation_items k ON k.item_id=m.app_id
        JOIN observation_targets t ON t.latest_success_id=k.run_id AND t.kind='chart'
            AND t.store=m.store AND t.country=m.country AND t.language=m.language
        JOIN observation_runs r ON r.id=k.run_id WHERE '''+where+'''
        AND t.enabled=1 AND t.failures=0 AND r.outcome='ok' AND r.observed_at+t.refresh_hours*3600>%s GROUP BY m.id''',(*args,now)).fetchall()
    sampled=[]
    for target,runs,extra in [('keyword_targets','keyword_runs'," AND r.requested_depth=t.depth"),
                               ('observation_targets','observation_runs'," AND t.kind='chart'")]:
        values=db.execute(f'''SELECT t.store,t.country,t.language,COUNT(*) n FROM {target} t JOIN {runs} r ON r.id=t.latest_success_id
            WHERE t.enabled=1 AND t.failures=0 AND r.outcome='ok' AND r.observed_at+t.refresh_hours*3600>%s'''+extra+'''
            AND EXISTS (SELECT 1 FROM app_group_members m WHERE '''+where+''' AND m.store=t.store AND m.country=t.country AND m.language=t.language)
            GROUP BY t.store,t.country,t.language''',(now,*args)).fetchall()
        sampled.append({(r['store'],r['country'],r['language']):r['n'] for r in values})
    return {r['id']:r for r in keyword},{r['id']:r for r in chart},*sampled


def member_rank_details(db, identifier, page, now):
    if not page:
        return
    members = {row['id']: row for row in page}
    for row in page:
        row['top_keywords'] = []
        row['chart_positions'] = []
    where = 'm.group_id=%s AND m.id IN (' + ','.join('%s' for _ in page) + ')'
    args = (identifier, *members)
    identity = db.execute(f'''SELECT m.id,JSON_OBJECT('developer',l.payload->'$.developer','icon',l.payload->'$.icon',
        'price',l.payload->'$.price','currency',l.payload->'$.currency','has_iap',l.payload->'$.has_iap',
        'languages',l.payload->'$.languages','updated',l.payload->'$.updated','version',l.payload->'$.version') metadata,
        r.observed_at review_sample_at FROM app_group_members m LEFT JOIN listings l USING(store,app_id,country,language)
        LEFT JOIN observation_targets t ON t.kind='reviews' AND t.store=m.store AND t.app_id=m.app_id AND t.country=m.country
        AND t.language=m.language AND t.category='' AND t.collection=''
        LEFT JOIN observation_runs r ON r.id=t.latest_success_id AND r.outcome='ok' WHERE {where}''',args).fetchall()
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    yesterday = midnight - timedelta(days=1)
    for item in identity:
        member = members[item['id']]
        member.update(json.loads(item['metadata']))
        member['yesterday_reviews'] = 0 if (item['review_sample_at'] or 0)>=midnight.timestamp() else None
    # Published dates may carry a storefront offset. Narrow the SQL range, then compare in UTC.
    dates = db.execute(f'''SELECT m.id,v.published_at FROM app_group_members m JOIN reviews v USING(store,app_id,country,language)
        WHERE {where} AND v.published_at >= %s AND v.published_at < %s''',
        (*args,(yesterday-timedelta(days=1)).date().isoformat(),(midnight+timedelta(days=1)).date().isoformat())).fetchall()
    for item in dates:
        try:
            published = datetime.fromisoformat(item['published_at'].replace('Z','+00:00'))
        except ValueError:
            continue
        if published.tzinfo and yesterday <= published < midnight:
            row = members[item['id']]
            row['yesterday_reviews'] = (row['yesterday_reviews'] or 0) + 1
    keywords = db.execute(f'''SELECT member_id,keyword,position FROM (
        SELECT m.id member_id,t.keyword,k.position,ROW_NUMBER() OVER (
            PARTITION BY m.id ORDER BY k.position,t.keyword,t.id) seq
        FROM app_group_members m JOIN keyword_targets t USING(store,country,language)
        JOIN keyword_runs r ON r.id=t.latest_success_id
        JOIN keyword_ranks k ON k.run_id=r.id AND k.app_id=m.app_id
        WHERE {where} AND t.enabled=1 AND t.failures=0 AND r.outcome='ok'
        AND r.requested_depth=t.depth AND r.observed_at+t.refresh_hours*3600>%s
        ) ranked WHERE seq<=3 ORDER BY member_id,seq''', (*args, now)).fetchall()
    for item in keywords:
        members[item['member_id']]['top_keywords'].append({'keyword':item['keyword'], 'position':item['position']})
    charts = db.execute(f'''SELECT m.id member_id,t.collection,t.category,k.position,
        (t.enabled=1 AND t.failures=0 AND r.outcome='ok' AND r.observed_at+t.refresh_hours*3600>%s) fresh
        FROM app_group_members m JOIN observation_targets t USING(store,country,language)
        LEFT JOIN observation_runs r ON r.id=t.latest_success_id
        LEFT JOIN observation_items k ON k.run_id=r.id AND k.item_id=m.app_id
        WHERE {where} AND t.kind='chart' ORDER BY t.collection,t.category''', (now, *args)).fetchall()
    for item in charts:
        members[item['member_id']]['chart_positions'].append({
            'collection':item['collection'], 'category':item['category'], 'sampled':bool(item['fresh']),
            'position':item['position'] if item['fresh'] else None,
        })


def facts(db, identifier, days, store='all', country='', app_id=''):
    where,args=_scope(identifier,store,country)
    since=(datetime.now(timezone.utc).date()-timedelta(days=days-1)).isoformat()
    clauses=['f.day>=%s','EXISTS (SELECT 1 FROM app_group_members m WHERE '+where+' AND m.store=f.store AND m.app_id=f.app_id AND m.country=f.country)']
    args=[since,*args]
    if app_id:clauses.append('f.app_id=%s');args.append(app_id)
    rows=db.execute('SELECT f.* FROM app_metric_facts f WHERE '+' AND '.join(clauses)+' ORDER BY day',args).fetchall()
    for row in rows:row['value']=str(row['value'])
    return rows


def page_metric_facts(db, identifier, page, days, now):
    if not page:
        return
    end = datetime.fromtimestamp(now, timezone.utc).date()
    start = end - timedelta(days=days-1)
    previous = start - timedelta(days=days)
    marks = ','.join('%s' for _ in page)
    rows = db.execute(f'''SELECT f.store,f.app_id,f.country,f.metric,f.currency,f.is_estimate,
        SUM(IF(f.day>=%s,f.value,0)) value,COUNT(IF(f.day>=%s,1,NULL)) days,
        SUM(IF(f.day<%s,f.value,0)) prior,COUNT(IF(f.day<%s,1,NULL)) prior_days
        FROM app_metric_facts f JOIN (SELECT DISTINCT store,app_id FROM app_group_members WHERE group_id=%s AND id IN ({marks})) apps
        USING(store,app_id) WHERE f.day BETWEEN %s AND %s AND EXISTS (SELECT 1 FROM app_group_members g
            WHERE g.group_id=%s AND g.store=f.store AND g.app_id=f.app_id AND g.country=f.country)
        GROUP BY f.store,f.app_id,f.country,f.metric,f.currency,f.is_estimate''',
        (start,start,start,start,identifier,*(r['id'] for r in page),previous,end,identifier)).fetchall()
    by_market = defaultdict(list)
    distributions = defaultdict(dict)
    for fact in rows:
        if not fact['days']:
            continue
        series = (fact['store'],fact['app_id'],fact['metric'],fact['currency'],fact['is_estimate'])
        distributions[series][fact['country']] = fact['value']
        by_market[(fact['store'],fact['app_id'],fact['country'])].append((series,fact))
    for row in page:
        row['metrics'] = []
        for series,fact in by_market[row['store'],row['app_id'],row['country']]:
            shares = distributions[series]
            total = sum(shares.values())
            comparable = fact['days']==days and fact['prior_days']==days and fact['prior']>0
            row['metrics'].append(dict(metric=fact['metric'],currency=fact['currency'],is_estimate=bool(fact['is_estimate']),
                value=str(fact['value']),days=fact['days'],change_pct=float(100*(fact['value']/fact['prior']-1)) if comparable else None,
                market_mix=[dict(country=c,pct=float(100*v/total)) for c,v in sorted(shares.items())] if total>0 and min(shares.values())>=0 else []))


def fact_summaries(db, identifier, days, store='all', country='', member_ids=None):
    where,args=_scope(identifier,store,country)
    if member_ids is not None:
        if not member_ids:return []
        where+=' AND m.id IN ('+','.join('%s' for _ in member_ids)+')';args.extend(member_ids)
    since=(datetime.now(timezone.utc).date()-timedelta(days=days-1)).isoformat()
    today=datetime.now(timezone.utc).date().isoformat()
    grouping='f.metric,f.is_estimate,f.currency'
    if member_ids is not None:grouping='f.store,f.app_id,f.country,'+grouping
    rows=db.execute('SELECT '+grouping+''',SUM(f.value) value,COUNT(DISTINCT f.day) days,
        COUNT(DISTINCT f.store,f.app_id,f.country) markets FROM app_metric_facts f
        JOIN (SELECT DISTINCT m.store,m.app_id,m.country FROM app_group_members m WHERE '''+where+''') owned
        ON owned.store=f.store AND owned.app_id=f.app_id AND owned.country=f.country
        WHERE f.day>=%s AND f.day<=%s GROUP BY '''+grouping,(*args,since,today)).fetchall()
    for row in rows:row['value']=str(row['value']);row['is_estimate']=bool(row['is_estimate'])
    return rows


def dashboard(db, cfg, identifier, store='all', country='', state='', q='', days=30, offset=0, limit=50, classification=''):
    owner=group(db,identifier)
    if type(days) is not int or not 1<=days<=365 or type(offset) is not int or not 0<=offset<=1000000 or not 1<=limit<=200:raise ValueError('观察期间或分页参数无效')
    if len(q)>255 or state not in ('','available','suspected_missing','region_unavailable','unverified','error'):raise ValueError('筛选参数无效')
    if classification not in ('','available','suspected_missing','region_unavailable','unverified'):raise ValueError('分组状态无效')
    country=country.lower();where,args=_scope(identifier,store,country)
    all_rows=member_rows(db,identifier,store)
    countries={}
    for row in all_rows:
        c=countries.setdefault(row['country'],dict(country=row['country'],apps=set(),targets=0,states=Counter(),classifications=Counter(),reference=Counter()))
        c['classifications'][row['classification']]+=1
        c['apps'].add((row['store'],row['app_id']));c['targets']+=1;c['states'][row['state']]+=1
        if row['reference_state']:c['reference'][row['reference_state']]+=1
    countries=[c|{'apps':len(c['apps']),'states':dict(c['states']),'reference':dict(c['reference'])} for c in countries.values()]
    rows=[r for r in all_rows if (not country or r['country']==country) and
          (not classification or r['classification']==classification) and
          (not state or (bool(r['failures']) if state=='error' else r['state']==state)) and
          (not q or q.casefold() in (r['app_id']+' '+(r['name'] or '')).casefold())]
    now = time.time()
    keywords,charts,keyword_samples,chart_samples=rankings(db,identifier,store,country,now)
    for r in rows:
        key=(r['store'],r['country'],r['language'])
        r.update(keywords.get(r['id'],{'keyword_count':0 if key in keyword_samples else None,'keyword_best':None,'keyword_top3_count':0 if key in keyword_samples else None}),
                 **charts.get(r['id'],{'chart_count':0 if key in chart_samples else None,'chart_best':None}))
        r['keyword_samples']=keyword_samples.get(key,0);r['chart_samples']=chart_samples.get(key,0)
    since=time.time()-days*86400
    events=db.execute('''SELECT e.kind,FLOOR(e.observed_at/86400)*86400 day,COUNT(DISTINCT e.store,e.app_id,e.country) count
        FROM events e WHERE e.observed_at>=%s AND e.kind IN ('first_verified','reappeared','region_unavailable')
        AND EXISTS (SELECT 1 FROM app_group_members m WHERE '''+where+''' AND m.store=e.store AND m.app_id=e.app_id
            AND m.country=e.country AND m.language=e.language) GROUP BY e.kind,day ORDER BY day''',(since,*args)).fetchall()
    page=rows[offset:offset+limit]
    member_rank_details(db,identifier,page,now)
    page_metric_facts(db,identifier,page,days,now)
    from .collection_limits import settings
    rate=db.execute("SELECT MAX(next_at) at FROM schedules WHERE name IN ('rate:global','rate:google','rate:apple')").fetchone()['at']
    statistics=dict(apps=len({(r['store'],r['app_id']) for r in rows}),targets=len(rows),countries=len({r['country'] for r in rows}),
        states=dict(Counter(r['state'] for r in rows)),classifications=dict(Counter(r['classification'] for r in rows)),new_members=sum(r['added_at']>=since for r in rows),
        fresh=sum(bool(r['last_success'] and r['last_success']>=time.time()-48*3600) for r in rows),errors=sum(bool(r['failures']) for r in rows),
        permission_states=dict(Counter(r['permission_state'] for r in rows)),permissions_known=sum(r['permission_success'] is not None for r in rows),ratings_known=sum(r['rating'] is not None for r in rows),
        keyword_apps=sum(bool(r['keyword_count']) for r in rows),chart_apps=sum(bool(r['chart_count']) for r in rows))
    # Period events and financial coverage use country/store filters, independent of table search/status filters.
    event_counts=db.execute('''SELECT e.kind,COUNT(DISTINCT e.store,e.app_id,e.country) count FROM events e
        WHERE e.observed_at>=%s AND e.kind IN ('first_verified','reappeared','region_unavailable')
        AND EXISTS (SELECT 1 FROM app_group_members m WHERE '''+where+''' AND m.store=e.store AND m.app_id=e.app_id AND m.country=e.country AND m.language=e.language)
        GROUP BY e.kind''',(since,*args)).fetchall()
    permission_categories = sorted({name for row in all_rows for name in (*row['permission_groups'], *row['permission_overrides'])})
    return dict(group=owner,permission_categories=permission_categories,stats=statistics,countries=countries,events=events,event_counts={r['kind']:r['count'] for r in event_counts},
        scope_targets=sum(not country or r['country']==country for r in all_rows),
        money=fact_summaries(db,identifier,days,store,country),items=page,total=len(rows),offset=offset,limit=limit,has_more=offset+limit<len(rows),
        collection=dict(paused=settings(db,cfg)['paused'],cooldown_seconds=max(0,int((rate or 0)-time.time()))),
        observed_at=time.time(),days=days,scope='按当前分组成员回看观察。应用按商店去重；状态按国家/语言观察目标统计。首次验证不等于新发布；地区不可用不等于全球下架。关键词和榜单只显示有效采样命中；空值表示尚无有效数据。')


def member_detail(db, identifier, member_id, days=30):
    if type(days) is not int or not 1<=days<=365:raise ValueError('观察期间无效')
    member=db.execute('SELECT m.*,a.name FROM app_group_members m LEFT JOIN apps a USING(store,app_id) WHERE m.group_id=%s AND m.id=%s',(identifier,member_id)).fetchone()
    if not member:raise ValueError('该应用不在当前分组')
    key=tuple(member[k] for k in ('store','app_id','country','language'));since=time.time()-days*86400
    listing=db.execute('SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s',key).fetchone()
    if listing:listing['payload']=json.loads(listing['payload']) if listing['payload'] else {}
    job=db.execute('SELECT due,failures,last_error,lease_until FROM jobs WHERE store=%s AND app_id=%s AND country=%s AND language=%s',key).fetchone()
    watch=db.execute('SELECT enabled,refresh_hours,group_name FROM watchlist WHERE store=%s AND app_id=%s AND country=%s AND language=%s',key).fetchone()
    permission=db.execute('SELECT * FROM app_permission_targets WHERE store=%s AND app_id=%s AND country=%s',key[:3]).fetchone()
    if permission:permission['payload']=json.loads(permission['payload']) if permission['payload'] is not None else None;permission.pop('token',None)
    keyword=db.execute('''SELECT t.id target_id,t.keyword,k.position,r.observed_at,t.failures,t.refresh_hours,r.requested_depth,t.depth,r.outcome,r.returned_count
        FROM keyword_targets t JOIN (SELECT DISTINCT s.target_id FROM keyword_runs s JOIN keyword_ranks h ON h.run_id=s.id WHERE h.app_id=%s) seen ON seen.target_id=t.id
        LEFT JOIN keyword_runs r ON r.id=t.latest_success_id LEFT JOIN keyword_ranks k ON k.run_id=r.id AND k.app_id=%s
        WHERE t.store=%s AND t.country=%s AND t.language=%s AND t.enabled=1 ORDER BY k.position IS NULL,k.position,t.keyword LIMIT 1000''',
        (member['app_id'],member['app_id'],member['store'],member['country'],member['language'])).fetchall()
    chart=db.execute('''SELECT t.id target_id,t.collection,t.category,k.position,r.observed_at,t.failures,t.refresh_hours,r.outcome,r.returned_count
        FROM observation_targets t LEFT JOIN observation_runs r ON r.id=t.latest_success_id
        LEFT JOIN observation_items k ON k.run_id=r.id AND k.item_id=%s
        WHERE t.store=%s AND t.country=%s AND t.language=%s AND t.kind='chart' AND t.enabled=1 ORDER BY k.position IS NULL,k.position LIMIT 1000''',
        (member['app_id'],member['store'],member['country'],member['language'])).fetchall()
    for row in [*keyword,*chart]:row['fresh']=bool(not row['failures'] and row['outcome']=='ok' and row['observed_at'] and row['observed_at']+row['refresh_hours']*3600>time.time() and row.get('requested_depth',0)==row.get('depth',0))
    snapshots=db.execute('SELECT observed_at,payload FROM snapshots WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND observed_at>=%s ORDER BY id DESC LIMIT 300',(*key,since)).fetchall()
    for row in snapshots:row['payload']=json.loads(row['payload'])
    events=db.execute('SELECT id,kind,observed_at,changes FROM events WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND observed_at>=%s ORDER BY id DESC LIMIT 100',(*key,since)).fetchall()
    references=db.execute('SELECT state,observed_text FROM app_reference_states WHERE store=%s AND app_id=%s AND country=%s AND language=%s',key).fetchone()
    adjustment=db.execute('SELECT status_override,permission_overrides,updated_by,updated_at FROM app_group_adjustments WHERE member_id=%s',(member_id,)).fetchone() or {}
    adjustment['permission_overrides'] = json.loads(adjustment.get('permission_overrides') or '{}')
    classification,source=resolve_state((listing or {}).get('state'),(references or {}).get('state'),adjustment.get('status_override'))
    permissions_history=[] if not permission else db.execute('SELECT observed_at,outcome,error FROM app_permission_runs WHERE target_id=%s ORDER BY id DESC LIMIT 30',(permission['id'],)).fetchall()
    reports=db.execute('SELECT id,observed_at,imported_at,summary FROM keyword_reports WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY observed_at DESC LIMIT 100',key).fetchall()
    for report in reports:
        report['summary']=[{k:s.get(k) for k in ('sheet','rows','imported_keyword_rows')} for s in json.loads(report['summary'])]
    markets=db.execute('''SELECT m.id,m.country,m.language,l.state,l.last_success FROM app_group_members m
        LEFT JOIN listings l USING(store,app_id,country,language)
        WHERE m.group_id=%s AND m.store=%s AND m.app_id=%s ORDER BY m.country,m.language''',(identifier,member['store'],member['app_id'])).fetchall()
    return dict(member=member,classification=classification,classification_source=source,adjustment=adjustment,listing=listing,job=job,watch=watch,permission=permission,permission_state=app_permissions.state(permission or {'store':member['store']}),permission_history=permissions_history,
                keywords=keyword,charts=chart,snapshots=snapshots,events=events,reference=references,reports=reports,markets=markets,
                metrics=facts(db,identifier,days,member['store'],member['country'],member['app_id']),
                reviews=db.execute('SELECT rating,title,content,published_at FROM reviews WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY last_seen DESC LIMIT 50',key).fetchall())


def reference_report(db, identifier, member_id, report_id, sheet='ASO关键词', offset=0):
    from .keyword_reports import report_rows
    member=db.execute('SELECT store,app_id,country,language FROM app_group_members WHERE group_id=%s AND id=%s',(identifier,member_id)).fetchone()
    if not member:raise ValueError('该应用不在当前分组')
    report=db.execute('SELECT store,app_id,country,language FROM keyword_reports WHERE id=%s',(report_id,)).fetchone()
    if report!=member:raise ValueError('报告不属于该应用市场')
    result=report_rows(db,report_id,sheet,offset=offset)
    result['items']=[{k:r[k] for k in ('source_row','keyword','position','popularity','search_index')} for r in result['items']]
    return result


def rank_history(db, identifier, member_id, kind, target_id, days=30):
    if kind not in ('keyword','chart') or not 1<=days<=365:raise ValueError('排名历史参数无效')
    member=db.execute('SELECT * FROM app_group_members WHERE group_id=%s AND id=%s',(identifier,member_id)).fetchone()
    if not member:raise ValueError('该应用不在当前分组')
    targets,runs,items,item_id=('keyword_targets','keyword_runs','keyword_ranks','app_id') if kind=='keyword' else ('observation_targets','observation_runs','observation_items','item_id')
    target=db.execute(f'SELECT * FROM {targets} WHERE id=%s',(target_id,)).fetchone()
    if not target or any(target[k]!=member[k] for k in ('store','country','language')) or (kind=='chart' and target['kind']!='chart'):
        raise ValueError('排名任务不属于该应用市场')
    rows=db.execute(f'''SELECT r.observed_at,r.outcome,r.returned_count,k.position FROM {runs} r
        LEFT JOIN {items} k ON k.run_id=r.id AND k.{item_id}=%s
        WHERE r.target_id=%s AND r.observed_at>=%s ORDER BY r.id DESC LIMIT 500''',
        (member['app_id'],target_id,time.time()-days*86400)).fetchall()
    return dict(label=target.get('keyword') or target.get('collection'),points=[dict(at=r['observed_at'],
        value=r['position'] if r['outcome']=='ok' else None,
        note=f"{r['outcome']} · 返回 {r['returned_count']} 条；未命中不等于下架") for r in reversed(rows)],limit=500)


def import_metrics(db, identifier, value):
    group(db,identifier);rows=metric_facts.prepare(value)
    allowed={(r['store'],r['app_id'],r['country']) for r in db.execute('SELECT store,app_id,country FROM app_group_members WHERE group_id=%s',(identifier,)).fetchall()}
    if any(r[:3] not in allowed for r in rows):raise ValueError('指标包含不在本组的应用或国家，未导入')
    with db:return metric_facts.save(db,rows)


def collect(db, identifier, value):
    from .keywords import validate_target
    group(db,identifier)
    kinds=value.get('kinds',['details','permissions'])
    if not isinstance(kinds,list) or not kinds or any(k not in ('details','permissions','keywords','charts','reviews') for k in kinds):raise ValueError('请选择有效采集内容')
    where,args=_scope(identifier,value.get('store','all'),str(value.get('country','')).lower())
    if 'member_id' in value:
        if type(value['member_id']) is not int or value['member_id']<=0:raise ValueError('应用目标无效')
        where+=' AND m.id=%s';args.append(value['member_id'])
    members=db.execute('SELECT * FROM app_group_members m WHERE '+where,args).fetchall()
    if not members:raise ValueError('当前范围没有应用')
    words=value.get('keywords',[])
    if not isinstance(words,list) or len(words)>100 or any(not isinstance(w,str) for w in words):raise ValueError('最多填写100个关键词')
    if 'keywords' in kinds and not words:raise ValueError('请填写要追踪的关键词')
    markets=sorted({(r['store'],r['country'],r['language']) for r in members});now=time.time()
    prepared=[validate_target(dict(store=s,country=c,language=l,keyword=w)) for s,c,l in markets for w in words] if 'keywords' in kinds else []
    prepared=list({tuple(r[k] for k in ('store','country','language','keyword')):r for r in prepared}.values())
    counts={}
    with db:
        if 'details' in kinds:
            db.execute('''UPDATE watchlist w JOIN app_group_members m USING(store,app_id,country,language) SET w.enabled=1 WHERE '''+where,args)
            db.execute('''UPDATE jobs j JOIN app_group_members m USING(store,app_id,country,language) SET j.due=0 WHERE '''+where+' AND j.lease_until<=%s',(*args,now))
            counts['details']=len(members)
        if 'permissions' in kinds:
            counts['permissions']=app_permissions.enqueue(db,members)
            db.execute('''UPDATE app_permission_targets p JOIN app_group_members m ON m.store=p.store AND m.app_id=p.app_id AND m.country=p.country
                SET p.enabled=1,p.due=IF(p.lease_until<=%s,0,p.due) WHERE '''+where,(now,*args))
        targets=[]
        if 'reviews' in kinds:targets.extend(('reviews',r['store'],r['app_id'],r['country'],r['language'],'','',100,24) for r in members);counts['reviews']=len(members)
        if 'charts' in kinds:
            targets.extend(('chart',s,'',c,l,chart,'',200,24) for s,c,l in markets for chart in ('top-free','top-paid','top-grossing'));counts['charts']=len(markets)*3
        if targets:db.executemany('''INSERT INTO observation_targets(kind,store,app_id,country,language,collection,category,depth,refresh_hours)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE enabled=1,
            due=IF(observation_targets.lease_until<=UNIX_TIMESTAMP(),0,observation_targets.due)''',targets)
        if prepared:
            db.executemany('''INSERT INTO keyword_targets(store,country,language,keyword,depth,refresh_hours) VALUES(%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE enabled=1,due=IF(keyword_targets.lease_until<=UNIX_TIMESTAMP(),0,keyword_targets.due)''',[tuple(r.values()) for r in prepared])
            counts['keywords']=len(prepared)
    return dict(queued=counts,note='已安排采集，遵守共享限速和暂停设置；已有目标的周期及深度保持不变。')


def control_monitoring(db, identifier, value):
    group(db,identifier);action=value.get('action')
    if action not in ('pause','resume'):raise ValueError('请选择暂停或恢复')
    where,args=_scope(identifier,value.get('store','all'),str(value.get('country','')).lower())
    now=time.time()
    with db:
        targets=db.execute('SELECT id,store,app_id,country FROM app_group_members m WHERE '+where,args).fetchall()
        if not targets:raise ValueError('当前范围没有应用目标')
        db.execute('UPDATE watchlist w JOIN app_group_members m USING(store,app_id,country,language) SET w.enabled=%s WHERE '+where,(action=='resume',*args))
        if action=='pause':
            db.execute('UPDATE jobs j JOIN app_group_members m USING(store,app_id,country,language) SET j.token=NULL,j.lease_until=0 WHERE '+where,args)
            db.execute('''UPDATE app_permission_targets p JOIN app_group_members m ON p.store=m.store AND p.app_id=m.app_id AND p.country=m.country
                SET p.enabled=0,p.token=NULL,p.lease_until=0 WHERE '''+where,args)
        else:
            db.execute('UPDATE jobs j JOIN app_group_members m USING(store,app_id,country,language) SET j.due=0 WHERE '+where+' AND j.lease_until<=%s',(*args,now))
            app_permissions.enqueue(db,targets)
            db.execute('''UPDATE app_permission_targets p JOIN app_group_members m ON p.store=m.store AND p.app_id=m.app_id AND p.country=m.country
                SET p.enabled=1,p.due=IF(p.lease_until<=%s,0,p.due) WHERE '''+where,(now,*args))
    return dict(targets=len(targets),action=action,note='已'+('暂停' if action=='pause' else '恢复')+'当前范围的详情与权限采集；历史保留，共享这些目标的其他分组同步受影响。')
