"""Observed events, version markets and developer portfolios; no modeled metrics."""
import json
import math
import time
from urllib.parse import urlsplit
from .intelligence import market, text, compare_apps
from .db import dump


def event_feed(db, filters, markets=None):
    from datetime import date, datetime, timezone
    selected=markets or [market(filters,require_app=False)]
    days=filters.get('days',30);offset=filters.get('offset',0);limit=filters.get('limit',50)
    if type(days) is not int or not 1<=days<=365:raise ValueError('观察区间必须为1–365天')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=200:raise ValueError('分页参数无效')
    day=filters.get('day','');at=None
    if day:
        try:at=datetime.combine(date.fromisoformat(day),datetime.min.time(),timezone.utc).timestamp()
        except (ValueError,TypeError):raise ValueError('请选择有效日期') from None
    scope='('+' OR '.join('(e.store=%s AND e.country=%s AND e.language=%s)' for _ in selected)+')'
    market_args=[v for m in selected for v in (m['store'],m['country'],m['language'])]
    clauses=[scope,'e.observed_at>=%s'];args=[*market_args,time.time()-days*86400]
    for field in ('app_id','kind'):
        if filters.get(field):clauses.append(f'e.{field}=%s');args.append(filters[field])
    if filters.get('field'):
        allowed={'version','name','description','summary','subtitle','keywords','screenshots','icon','price','developer','release_notes','rating','ratings'}
        if filters['field'] not in allowed:raise ValueError('未知变更字段')
        clauses.append("JSON_CONTAINS_PATH(e.changes,'one',%s)");args.append('$.'+filters['field'])
    if filters.get('group_name'):
        clauses.append('EXISTS (SELECT 1 FROM watchlist w WHERE w.store=e.store AND w.app_id=e.app_id AND w.country=e.country AND w.language=e.language AND w.group_name=%s)');args.append(filters['group_name'])
    where=' WHERE '+' AND '.join(clauses)
    daily=db.execute('SELECT FLOOR(e.observed_at/86400)*86400 day,e.kind,count(*) count FROM events e'+where+' GROUP BY day,e.kind ORDER BY day',args).fetchall()
    summary={}
    for row in daily:summary[row['kind']]=summary.get(row['kind'],0)+row['count']
    total=sum(r['count'] for r in daily if at is None or r['day']==at)
    row_where=where;row_args=list(args)
    if at is not None:row_where+=' AND e.observed_at>=%s AND e.observed_at<%s';row_args.extend([at,at+86400])
    rows=list(db.execute('SELECT e.*,a.name FROM events e LEFT JOIN apps a USING(store,app_id)'+row_where+' ORDER BY e.id DESC LIMIT %s OFFSET %s',(*row_args,limit,offset)).fetchall())
    for row in rows:row['changes']=json.loads(row['changes'])
    annotations_where=' WHERE '+scope+' AND e.event_at>=%s';annotation_args=[*market_args,args[len(market_args)]]
    if filters.get('app_id'):annotations_where+=' AND e.app_id=%s';annotation_args.append(filters['app_id'])
    if at is not None:annotations_where+=' AND e.event_at>=%s AND e.event_at<%s';annotation_args.extend([at,at+86400])
    if filters.get('group_name'):
        annotations_where+=' AND EXISTS (SELECT 1 FROM watchlist w WHERE w.store=e.store AND w.app_id=e.app_id AND w.country=e.country AND w.language=e.language AND w.group_name=%s)';annotation_args.append(filters['group_name'])
    annotations=[] if filters.get('kind') or filters.get('field') else db.execute('SELECT e.* FROM event_annotations e'+annotations_where+' ORDER BY e.event_at DESC LIMIT 100',annotation_args).fetchall()
    groups=[r['group_name'] for r in db.execute('SELECT DISTINCT e.group_name FROM watchlist e WHERE '+scope+' ORDER BY e.group_name',market_args).fetchall()]
    return {'items':rows,'daily':daily,'summary':[{'kind':k,'count':v} for k,v in summary.items()],'annotations':annotations,'groups':groups,
            'scope':'事件时间为本系统观察时间；首次验证不是新上架。','limit':limit,'offset':offset,'total':total,'has_more':offset+len(rows)<total,'day':day}


def add_annotation(db, value):
    m=market(value)
    at=value.get('event_at')
    if isinstance(at,bool) or not isinstance(at,(int,float)) or not math.isfinite(at) or not 0<at<=time.time()+366*86400:
        raise ValueError('事件时间无效')
    title=text(value.get('title'),255)
    note=value.get('note','');url=value.get('evidence_url','')
    if not isinstance(note,str) or len(note)>4000 or not isinstance(url,str) or len(url)>2000:
        raise ValueError('备注或证据链接过长')
    if url and (urlsplit(url).scheme not in ('http','https') or not urlsplit(url).hostname or urlsplit(url).username):
        raise ValueError('证据链接必须是无凭据的HTTP(S)链接')
    db.execute('''INSERT INTO event_annotations(store,app_id,country,language,event_at,title,note,evidence_url,created_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(m['store'],m['app_id'],m['country'],m['language'],at,title,note,url,time.time()))
    return {'id':db.cursor.lastrowid}


def app_markets(db, store, app_id):
    market(dict(store=store,app_id=app_id,country='us',language='en'))
    rows=db.execute('''SELECT l.*,j.failures,j.last_error FROM listings l LEFT JOIN jobs j
        USING(store,app_id,country,language) WHERE l.store=%s AND l.app_id=%s ORDER BY l.country,l.language''',(store,app_id)).fetchall()
    for row in rows:
        row['payload']=json.loads(row['payload']) if row['payload'] else None
    return {'items':rows,'scope':'仅展示实际观察过的市场；未列出地区为未知。'}


def developers(db, store, query=''):
    if store not in ('apple','google'):
        raise ValueError('未知商店')
    # IDs are scoped to each store. Identically named developers are never merged across stores.
    where=" WHERE store=%s AND payload IS NOT NULL AND JSON_TYPE(JSON_EXTRACT(payload,'$.developer'))='STRING' AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer'))<>''"
    args=[store]
    if query:
        where+=" AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer')) LIKE %s";args.append('%'+query[:255]+'%')
    return db.execute('''SELECT store,developer_id,MAX(name) name,COUNT(DISTINCT app_id) apps,
        COUNT(DISTINCT country) markets,MAX(last_success) last_success FROM
        (SELECT store,app_id,country,last_success,NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer_id')),'null') developer_id,
        JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer')) name FROM listings'''+where+
        ") d GROUP BY store,developer_id,CASE WHEN developer_id IS NULL THEN name ELSE '' END ORDER BY apps DESC,name LIMIT 100",args).fetchall()


def developer_apps(db, store, name, developer_id=''):
    if store not in ('apple','google'):
        raise ValueError('未知商店')
    where="store=%s"
    args=[store]
    if developer_id and developer_id!='null':
        where+=" AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer_id'))=%s";args.append(developer_id)
    else:
        where+=" AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer'))=%s";args.append(name)
    rows=db.execute('SELECT * FROM listings WHERE '+where+' ORDER BY app_id,country,language LIMIT 500',args).fetchall()
    for row in rows:row['payload']=json.loads(row['payload'])
    return {'items':rows,'scope':'旗下已观察应用，最多500个地区记录；不代表发行商全部产品。'}


def save_comparison(db, value):
    name=text(value.get('name'))
    if value.get('id') is not None and (type(value['id']) is not int or type(value.get('revision')) is not int):
        raise ValueError('更新集合必须提供整数ID和版本')
    members=value.get('members')
    if not isinstance(members,list) or not 2<=len(members)<=6:
        raise ValueError('集合需要2–6个不同应用')
    prepared=[]
    for member in members:
        if not isinstance(member,dict):raise ValueError('集合成员无效')
        m=market(member)
        role=member.get('role','competitor')
        if role not in ('own','competitor'):raise ValueError('应用角色无效')
        prepared.append(m|{'role':role})
    identities={(m['store'],m['country'],m['language']) for m in prepared}
    if len(identities)!=1 or len({m['app_id'] for m in prepared})!=len(prepared):
        raise ValueError('请选择同一商店、国家和语言的不同应用')
    store,country,language=next(iter(identities));now=time.time()
    with db:
        if value.get('id') is not None:
            row=db.execute('SELECT revision FROM comparison_sets WHERE id=%s FOR UPDATE',(value['id'],)).fetchone()
            if not row or row['revision']!=value.get('revision'):raise ValueError('集合已更新，请刷新重试')
            revision=row['revision']+1;identifier=value['id']
            db.execute('UPDATE comparison_sets SET name=%s,store=%s,country=%s,language=%s,members=%s,updated_at=%s,revision=%s WHERE id=%s',
                       (name,store,country,language,dump(prepared),now,revision,identifier))
        else:
            db.execute('INSERT INTO comparison_sets(name,store,country,language,members,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',(name,store,country,language,dump(prepared),now,now))
            identifier,revision=db.cursor.lastrowid,1
    return {'id':identifier,'revision':revision}


def comparison_detail(db, identifier):
    row=db.execute('SELECT * FROM comparison_sets WHERE id=%s',(identifier,)).fetchone()
    if not row:raise ValueError('集合不存在')
    row['members']=json.loads(row['members'])
    report=compare_apps(db,row['members'])
    for member in report['apps']:
        member['recent_events']=db.execute('SELECT id,kind,observed_at,changes FROM events WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY id DESC LIMIT 10',
            (member['store'],member['app_id'],member['country'],member['language'])).fetchall()
    return {'set':row,'report':report}
