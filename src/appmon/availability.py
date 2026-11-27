"""Observed listing status, separately from unknown seed IDs and network failures."""
import json
import time
from .db import dump
from .app_search import identity_filter
from .intelligence import market
from . import listing_status


def watched(db, store='all', country='', group_name='', q='', offset=0, limit=100, language='', enabled='', state=''):
    from .locales import stores
    import re
    selected=stores(store)
    if country and not re.fullmatch('[a-zA-Z]{2}',country):raise ValueError('国家代码无效')
    if len(group_name)>120 or len(q)>255:raise ValueError('筛选条件过长')
    if language and not re.fullmatch('[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*',language):raise ValueError('语言代码无效')
    if enabled not in ('','0','1'):raise ValueError('监控状态无效')
    state=listing_status.state_key(state)
    if state not in ('','available','region_unavailable','unverified'):raise ValueError('观察状态无效')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=200:raise ValueError('分页参数无效')
    clauses=['w.store IN ('+','.join('%s' for _ in selected)+')'];args=list(selected)
    for column,value in [('country',country.lower()),('group_name',group_name),('language',language),('enabled',enabled)]:
        if value:clauses.append('w.'+column+'=%s');args.append(value)
    if state:clauses.append(listing_status.CURRENT_STATE+'=%s');args.append(state)
    if q:
        match, terms = identity_filter('w.app_id', q)
        clauses.append(match)
        args.extend(terms)
    where=' AND '.join(clauses)
    base=''' FROM watchlist w LEFT JOIN apps a USING(store,app_id) LEFT JOIN listings l USING(store,app_id,country,language)
        LEFT JOIN jobs j USING(store,app_id,country,language) '''
    total=db.execute('SELECT COUNT(*) n'+base+'WHERE '+where,args).fetchone()['n']
    rows=db.execute(f'''SELECT w.*,a.name,{listing_status.CURRENT_STATE} state,l.checked_at,l.last_success,j.due,j.failures,j.last_error'''+base+' WHERE '+where+'''
        ORDER BY w.group_name,w.store,w.country,w.app_id,w.language LIMIT %s OFFSET %s''',(*args,limit,offset)).fetchall()
    groups=[r['group_name'] for r in db.execute('SELECT DISTINCT group_name FROM watchlist ORDER BY group_name').fetchall()]
    countries=[r['country'] for r in db.execute('SELECT DISTINCT country FROM watchlist ORDER BY country').fetchall()]
    return dict(items=rows,total=total,offset=offset,limit=limit,has_more=offset+len(rows)<total,groups=groups,countries=countries)


def record_daily(db, now=None):
    now = time.time() if now is None else now
    with db:
        db.execute("INSERT INTO schedules VALUES('availability-rollup',0) ON DUPLICATE KEY UPDATE name=schedules.name")
        due = db.execute("SELECT next_at FROM schedules WHERE name='availability-rollup' FOR UPDATE").fetchone()['next_at']
        if due > now:
            return 0
        rows = listing_status.counts(db,now,include_unchecked_watches=True)
        markets = {}
        for row in rows:
            key = (row['store'], row['country'], row['language'])
            markets.setdefault(key, {})[row['state']] = {'count': row['count'], 'fresh': int(row['fresh'] or 0)}
        for key, counts in markets.items():
            # An explicit zero distinguishes new snapshots from old ones that did not count failed lookups.
            counts.setdefault('unverified',{'count':0,'fresh':0})
            db.execute('''INSERT INTO availability_daily(store,country,language,day,observed_at,counts)
                VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                observed_at=VALUES(observed_at),counts=VALUES(counts)''', (*key, int(now//86400)*86400, now, dump(counts)))
        db.execute("UPDATE schedules SET next_at=%s WHERE name='availability-rollup'", (now+3600,))
    return len(markets)


def report(db, value):
    if value.get('store')=='all':
        reports=[report(db,value|{'store':s}) for s in ('google','apple')]
        counts={};attempts={}
        for r in reports:
            for c in r['counts']:
                row=counts.setdefault(c['state'],dict(state=c['state'],count=0,fresh=0))
                row['count']+=c['count'];row['fresh']+=int(c['fresh'] or 0)
            for a in r['attempts']:attempts[a['outcome']]=attempts.get(a['outcome'],0)+a['count']
        return {'market':value,'reports':reports,'observed_at':time.time(),'counts':list(counts.values()),'history':[],
            'attempts':[{'outcome':k,'count':v} for k,v in attempts.items()],
            'pending':[p for r in reports for p in r['pending']],
            'events':sorted([p for r in reports for p in r['events']],key=lambda p:p['observed_at'],reverse=True),
            'unobserved_watched':sum(r['unobserved_watched'] for r in reports),'limit':200,
            'scope':'两家商店分别按当前国家与请求语言查询；数量汇总，历史曲线按商店区分。首次验证不等于新上架。'}
    m = market(value, require_app=False)
    days = value.get('days', 30)
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('观察区间必须为1–365天')
    now=time.time();key=tuple(m[k] for k in ('store','country','language'));since=now-days*86400
    counts=listing_status.counts(db,now,*key,include_unchecked_watches=True)
    history=db.execute('SELECT * FROM availability_daily WHERE store=%s AND country=%s AND language=%s AND day>=%s ORDER BY day',(*key,int(since//86400)*86400)).fetchall()
    for row in history:
        row['counts']=json.loads(row['counts'])
        missing=row['counts'].pop('suspected_missing',None)
        if missing:
            unavailable=row['counts'].setdefault('region_unavailable',{'count':0,'fresh':0})
            for field in ('count','fresh'):unavailable[field]+=missing[field]
    attempts=db.execute('''SELECT outcome,count(*) count FROM requests WHERE store=%s AND country=%s AND language=%s
        AND observed_at>=%s GROUP BY outcome''',(*key,since)).fetchall()
    events=db.execute('''SELECT e.*,a.name FROM events e LEFT JOIN apps a USING(store,app_id)
        WHERE e.store=%s AND e.country=%s AND e.language=%s AND e.observed_at>=%s
        AND e.kind IN ('first_verified','reappeared','region_unavailable') ORDER BY e.id DESC LIMIT 100''',(*key,since)).fetchall()
    pending=db.execute('''SELECT j.store,j.app_id,j.country,j.language,'unverified' state,a.name,j.last_error,j.failures,
        l.last_success,l.checked_at,l.misses FROM jobs j LEFT JOIN apps a USING(store,app_id)
        LEFT JOIN listings l USING(store,app_id,country,language)
        WHERE j.store=%s AND j.country=%s AND j.language=%s AND j.failures>0
        ORDER BY j.failures DESC,j.app_id LIMIT 100''',key).fetchall()
    unobserved=sum(int(c['unchecked']) for c in counts)
    return {'market':m,'observed_at':now,'counts':list(counts),'history':list(history),'attempts':list(attempts),'events':list(events),'pending':list(pending),
            'unobserved_watched':unobserved,'limit':100,
            'scope':'统计当前商店、国家与语言下已查询的目标及已启用、待首次查询的重点目标。日趋势保存每日最后一次汇总快照。'}
