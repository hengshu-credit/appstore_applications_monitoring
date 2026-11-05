"""Observed listing status, separately from unknown seed IDs and network failures."""
import json
import time
from .db import dump
from .intelligence import market


def watched(db, store='all', country='', group_name='', q='', offset=0, limit=100):
    from .locales import stores
    import re
    selected=stores(store)
    if country and not re.fullmatch('[a-zA-Z]{2}',country):raise ValueError('国家代码无效')
    if len(group_name)>120 or len(q)>255:raise ValueError('筛选条件过长')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=200:raise ValueError('分页参数无效')
    clauses=['w.store IN ('+','.join('%s' for _ in selected)+')'];args=list(selected)
    for column,value in [('country',country.lower()),('group_name',group_name)]:
        if value:clauses.append('w.'+column+'=%s');args.append(value)
    if q:
        clauses.append('(LOCATE(%s,w.app_id)>0 OR LOCATE(%s,a.name)>0)');args.extend([q,q])
    where=' AND '.join(clauses)
    base=' FROM watchlist w LEFT JOIN apps a USING(store,app_id) '
    total=db.execute('SELECT COUNT(*) n'+base+'WHERE '+where,args).fetchone()['n']
    rows=db.execute('''SELECT w.*,a.name,l.state,l.checked_at,l.last_success,j.due,j.failures,j.last_error'''+base+'''
        LEFT JOIN listings l USING(store,app_id,country,language)
        LEFT JOIN jobs j USING(store,app_id,country,language) WHERE '''+where+'''
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
        rows = db.execute('''SELECT store,country,language,state,count(*) count,
            SUM(checked_at>=%s) fresh FROM listings GROUP BY store,country,language,state''', (now-48*3600,)).fetchall()
        markets = {}
        for row in rows:
            key = (row['store'], row['country'], row['language'])
            markets.setdefault(key, {})[row['state']] = {'count': row['count'], 'fresh': int(row['fresh'] or 0)}
        for key, counts in markets.items():
            db.execute('''INSERT INTO availability_daily(store,country,language,day,observed_at,counts)
                VALUES(%s,%s,%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE
                observed_at=incoming.observed_at,counts=incoming.counts''', (*key, int(now//86400)*86400, now, dump(counts)))
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
    counts=db.execute('''SELECT state,count(*) count,SUM(checked_at>=%s) fresh FROM listings
        WHERE store=%s AND country=%s AND language=%s GROUP BY state''',(now-48*3600,*key)).fetchall()
    history=db.execute('SELECT * FROM availability_daily WHERE store=%s AND country=%s AND language=%s AND day>=%s ORDER BY day',(*key,int(since//86400)*86400)).fetchall()
    for row in history:row['counts']=json.loads(row['counts'])
    attempts=db.execute('''SELECT outcome,count(*) count FROM requests WHERE store=%s AND country=%s AND language=%s
        AND observed_at>=%s GROUP BY outcome''',(*key,since)).fetchall()
    events=db.execute('''SELECT e.*,a.name FROM events e LEFT JOIN apps a USING(store,app_id)
        WHERE e.store=%s AND e.country=%s AND e.language=%s AND e.observed_at>=%s
        AND e.kind IN ('first_verified','reappeared','region_unavailable') ORDER BY e.id DESC LIMIT 100''',(*key,since)).fetchall()
    pending=db.execute('''SELECT l.*,a.name,j.last_error,j.failures FROM listings l LEFT JOIN apps a USING(store,app_id)
        LEFT JOIN jobs j USING(store,app_id,country,language)
        WHERE l.store=%s AND l.country=%s AND l.language=%s AND (l.state='suspected_missing' OR j.failures>0)
        ORDER BY l.checked_at DESC LIMIT 100''',key).fetchall()
    for row in pending:row.pop('payload',None)
    unobserved=db.execute('''SELECT count(*) count FROM watchlist w LEFT JOIN listings l USING(store,app_id,country,language)
        WHERE w.store=%s AND w.country=%s AND w.language=%s AND w.enabled=1 AND l.app_id IS NULL''',key).fetchone()['count']
    return {'market':m,'observed_at':now,'counts':list(counts),'history':list(history),'attempts':list(attempts),'events':list(events),'pending':list(pending),
            'unobserved_watched':unobserved,'limit':100,
            'scope':'当前商店、国家与语言中已实际观察的应用；不代表全商店。日趋势保存 worker 每小时汇总后的当日最后快照，不回填历史。首次验证不等于新上架；地区不可用不等于全球下架。'}
