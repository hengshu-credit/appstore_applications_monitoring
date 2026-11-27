"""Read models with an explicit selected-store scope; ranks never merge across stores."""
import json
import time
from datetime import date,datetime,timezone
from . import locales,catalog,insights,review_desk
from .ingest import valid_id
from .app_search import LISTING_NAME, identity_filter
from . import listing_status
from .content_store import SQL_FIELDS

LIST_SUMMARY = "IF(l.payload IS NULL,NULL,JSON_OBJECT("+','.join(
    f"'{key}',l.payload->'$.{key}'" for key in sorted(SQL_FIELDS))+"))"


def overview(db,store='all'):
    scope,args=locales.where(store)
    counts={table:db.execute(f'SELECT count(*) n FROM {table} WHERE {scope}',args).fetchone()['n']
            for table in ('watchlist','keyword_targets','observation_targets','reviews')}
    selected=locales.stores(store);marks=','.join('%s' for _ in selected)
    alert_scope=f"JSON_UNQUOTE(JSON_EXTRACT(payload,'$.store')) IN ({marks})"
    counts['unread_alerts']=db.execute('SELECT count(*) n FROM alerts WHERE acknowledged=0 AND '+alert_scope,selected).fetchone()['n']
    events_scope,events_args=locales.where(store,alias='e')
    return {'counts':counts,'at':time.time(),'stores':selected,
        'freshness':listing_status.counts(db,time.time(),store),
        'recent_events':db.execute('SELECT e.*,a.name FROM events e LEFT JOIN apps a USING(store,app_id) WHERE '+events_scope+' ORDER BY e.id DESC LIMIT 20',events_args).fetchall(),
        'worker':db.execute("SELECT next_at FROM schedules WHERE name='console-heartbeat'").fetchone(),
        'failures':{table:db.execute(f'SELECT id,store,last_error,failures FROM {table} WHERE failures>0 AND {scope} ORDER BY id DESC LIMIT 20',args).fetchall() for table in ('keyword_targets','observation_targets')}}


def filter_options(db, field, store, country, language, q, resolve=False):
    """Suggest observed application identities or developers within the requested market."""
    if field not in ('app', 'developer'):
        raise ValueError('未知筛选字段')
    if not q.strip() or (len(q.strip()) < 2 and not resolve):
        return []
    scope, args = locales.where(store, country, language)
    query = q.strip()
    if field == 'app':
        scope, args = locales.where(store, country, language, alias='l')
        match, terms = identity_filter('l.app_id', query)
        catalog_scope, stores = locales.where(store, alias='a')
        observed = f"""SELECT l.app_id value,MIN(COALESCE({LISTING_NAME},a.name,l.app_id)) label
            FROM listings l LEFT JOIN apps a USING(store,app_id) WHERE {scope}"""
        seed = f"""SELECT a.app_id value,MIN(COALESCE(a.name,a.app_id)) label FROM apps a
            WHERE {catalog_scope} AND NOT EXISTS (
                SELECT 1 FROM listings other WHERE other.store=a.store AND other.app_id=a.app_id)"""
        if resolve and any(valid_id(s,query) for s in stores):
            exact = db.execute(observed+' AND l.app_id=%s GROUP BY l.app_id',(*args,query)).fetchone()
            if not exact:
                exact = db.execute(seed+' AND a.app_id=%s GROUP BY a.app_id',(*stores,query)).fetchone()
            if exact:
                return [[exact['value'],exact['label']]]
        rows = db.execute(observed+' AND '+match+' GROUP BY l.app_id ORDER BY label,value LIMIT 12',
                          (*args,*terms)).fetchall()
        if len(rows)<12:
            # The local-market observations rank first. Read the seed catalog only to fill empty slots.
            extra = db.execute(seed+''' AND (LOCATE(LOWER(%s),LOWER(a.name))>0 OR
                LOCATE(LOWER(%s),LOWER(CONVERT(a.app_id USING utf8mb4)))>0)
                GROUP BY a.app_id ORDER BY label,value LIMIT 12''',(*stores,query,query)).fetchall()
            seen = {row['value'] for row in rows}
            rows = (list(rows)+[row for row in extra if row['value'] not in seen])[:12]
    else:
        label = "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer')),'null')"
        rows = db.execute(f"""SELECT DISTINCT {label} value,{label} label FROM listings WHERE {scope}
            AND LOCATE(LOWER(%s),LOWER({label}))>0 ORDER BY label LIMIT 12""", (*args,query)).fetchall()
    return [[r['value'],r['label']] for r in rows]


def _apps_page(db, selected, page, limit, options):
    """Interleave stores by ordinal, reading only the slices needed for this page."""
    if not 1 <= page <= 1000000:
        raise ValueError('页码超出范围')
    if len(selected)>1 and options['category']:
        raise ValueError('选择单个商店后可筛选分类')
    start=(page-1)*limit
    skips={store:(start+len(selected)-1-i)//len(selected) for i,store in enumerate(selected)}

    def read(store):
        return apps(db,store=store,limit=limit,_offset=skips[store],**options)

    parts={store:read(store) for store in selected}
    counts={}
    for store in selected:
        if skips[store] and not parts[store]['items']:
            counts[store]=apps(db,store=store,limit=limit,_count=True,**options)
    if len(counts)==len(selected):
        last=max(1,(sum(counts.values())+limit-1)//limit)
        if page>last:
            return _apps_page(db,selected,last,limit,options)
    if len(selected)==2:
        for store,count in counts.items():
            if count<skips[store]:
                other=next(s for s in selected if s!=store)
                skips[other]+=skips[store]-count
                parts[other]=read(other)
    rotation=start%len(selected)
    order=selected[rotation:]+selected[:rotation]
    items=[parts[s]['items'][i] for i in range(limit) for s in order if i<len(parts[s]['items'])]
    return dict(items=items[:limit],page=page,limit=limit,has_more=len(items)>limit or any(part['next'] for part in parts.values()),
                stores=selected,country=options['country'],language=options['language'])


def text_page(db, store, country, language, q, mode, after_store, after_id, offset, size, count):
    """Seek on the two independently ordered sources before fetching listing details."""
    if mode=='contains':
        catalog_match='(LOCATE(LOWER(%s),LOWER(CONVERT(a.app_id USING utf8mb4)))>0 OR LOCATE(LOWER(%s),LOWER(a.name))>0)'
        terms=[q.strip(),q.strip()]
        local_term=q.strip()
    else:
        catalog_match="(a.app_id=%s OR a.name COLLATE utf8mb4_0900_ai_ci LIKE %s ESCAPE '!')"
        terms=[q,'%'+q.replace('!','!!').replace('%','!%').replace('_','!_')+'%']
        local_term=q
    catalog='SELECT a.app_id FROM apps a WHERE a.store=%s AND '+catalog_match
    local=f'''SELECT l.app_id FROM listings l JOIN apps a USING(store,app_id)
        WHERE l.store=%s AND l.country=%s AND l.language=%s AND LOCATE(LOWER(%s),LOWER({LISTING_NAME}))>0'''
    left=[store,*terms]
    right=[store,country,language,local_term]
    if after_store or after_id:
        catalog+=' AND (a.store,a.app_id)>(%s,%s)'
        local+=' AND (l.store,l.app_id)>(%s,%s)'
        left.extend([after_store,after_id])
        right.extend([after_store,after_id])
    if not count:
        catalog+=' ORDER BY a.app_id LIMIT %s'
        local+=' ORDER BY l.app_id LIMIT %s'
        left.append(offset+size)
        right.append(offset+size)
    union=f' FROM (({catalog}) UNION ({local})) hits'
    if count:
        return db.execute('SELECT /*+ MAX_EXECUTION_TIME(15000) */ COUNT(*) n'+union,left+right).fetchone()['n']
    return [r['app_id'] for r in db.execute('SELECT /*+ MAX_EXECUTION_TIME(15000) */ app_id'+union+' ORDER BY app_id LIMIT %s OFFSET %s',
                                             [*left,*right,size,offset]).fetchall()]


def apps(db,*,store='',country='us',language='en',q='',category='',developer='',state='',limit=50,
         after_store='',after_id='',after_google='',after_apple='',google_done=False,apple_done=False,
         min_rating=None,min_ratings=None,pricing='',updated_since='',released_since='',q_match='auto',developer_match='exact',page=None,compact=False,_offset=0,_count=False):
    selected=locales.stores(store)
    if not 1<=limit<=200:raise ValueError('每页最多200个应用')
    if q_match not in ('auto','contains','id') or developer_match not in ('exact','contains'):
        raise ValueError('未知匹配方式')
    if page is not None:
        options=dict(country=country,language=language,q=q,category=category,developer=developer,state=state,min_rating=min_rating,
                     min_ratings=min_ratings,pricing=pricing,updated_since=updated_since,released_since=released_since,
                     q_match=q_match,developer_match=developer_match,compact=compact)
        return _apps_page(db,selected,page,limit,options)
    if len(selected)>1:
        if category:raise ValueError('两个商店的分类不同，请选择单个商店筛选分类')
        cursors={'google':after_google,'apple':after_apple};done={'google':google_done,'apple':apple_done}
        active=[s for s in selected if not done[s]];pages={};remaining=limit
        for i,name in enumerate(active):
            take=(remaining+len(active)-i-1)//(len(active)-i)
            if not take:continue
            part=apps(db,store=name,country=country,language=language,q=q,developer=developer,state=state,limit=take,
                after_store=name if cursors[name] else '',after_id=cursors[name],min_rating=min_rating,min_ratings=min_ratings,
                pricing=pricing,updated_since=updated_since,released_since=released_since,q_match=q_match,developer_match=developer_match,compact=compact)
            pages[name]=part['items'];remaining-=len(part['items'])
            if part['next']:cursors[name]=part['next']['after_id']
            else:done[name]=True
        rows=[]
        for index in range(max([len(v) for v in pages.values()] or [0])):
            for name in selected:
                if index<len(pages.get(name,[])):rows.append(pages[name][index])
        next_page=None if all(done.values()) else {'after_google':cursors['google'],'after_apple':cursors['apple'],'google_done':done['google'],'apple_done':done['apple']}
        return {'items':rows,'next':next_page,'country':country,'language':language,'stores':selected,
                'effective_languages':{s:locales.language_for(s,country,language) for s in selected}}
    store=selected[0];language=locales.language_for(store,country,language)
    clauses=['a.store=%s'];args=[country,language,store]
    if state:clauses.append(listing_status.CURRENT_STATE+'=%s');args.append(listing_status.state_key(state))
    if category:
        aliases=catalog.category_aliases(store,category);marks=','.join('%s' for _ in aliases)
        clauses.append(f'(a.category IS NULL OR a.category NOT IN ({marks}))' if category=='__unknown__' else f'a.category IN ({marks})');args.extend(aliases)
    exact_id=q_match=='id' or q_match=='auto' and valid_id(store,q)
    market_fields=bool(developer or pricing or updated_since or released_since or min_rating is not None or min_ratings is not None)
    if q and not exact_id and not (category or state or market_fields):
        hits=text_page(db,store,country,language,q,q_match,after_store,after_id,_offset,limit+1,_count)
        if _count:return hits
        if not hits:return {'items':[],'next':None,'country':country,'language':language}
        clauses.append('a.app_id IN ('+','.join('%s' for _ in hits)+')')
        args.extend(hits)
        _offset=0
    elif q and exact_id:
        clauses.append('a.app_id=%s');args.append(q)
    elif q and q_match == 'contains':
        match, terms = identity_filter('a.app_id', q)
        clauses.append(match)
        args.extend(terms)
    elif q:
        pattern = '%' + q.replace('!','!!').replace('%','!%').replace('_','!_') + '%'
        clauses.append(f"(a.app_id=%s OR a.name COLLATE utf8mb4_0900_ai_ci LIKE %s ESCAPE '!' OR LOCATE(LOWER(%s),LOWER({LISTING_NAME}))>0)")
        args.extend([q, pattern, q])
    if developer:
        expression = "JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.developer'))"
        clauses.append(expression+'=%s' if developer_match=='exact' else f'LOCATE(LOWER(%s),LOWER({expression}))>0')
        args.append(developer)
    for field,minimum in [('rating',min_rating),('ratings',min_ratings)]:
        if minimum is not None:
            clauses.append(f"JSON_TYPE(JSON_EXTRACT(l.payload,'$.{field}')) IN ('INTEGER','DOUBLE') AND CAST(JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.{field}')) AS DECIMAL(20,6))>=%s");args.append(minimum)
    if pricing:
        if pricing not in ('free','paid'):raise ValueError('售价筛选无效')
        clauses.append("JSON_TYPE(JSON_EXTRACT(l.payload,'$.price')) IN ('INTEGER','DOUBLE') AND CAST(JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.price')) AS DECIMAL(20,6))"+('=0' if pricing=='free' else '>0'))
    for field,minimum in [('updated',updated_since),('released',released_since)]:
        if minimum:
            day=date.fromisoformat(minimum);epoch=datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc).timestamp();path=f"JSON_EXTRACT(l.payload,'$.{field}')"
            clauses.append(f"((JSON_TYPE({path}) IN ('INTEGER','DOUBLE') AND CAST(JSON_UNQUOTE({path}) AS DECIMAL(20,6))>=%s) OR (JSON_UNQUOTE({path}) REGEXP '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' AND LEFT(JSON_UNQUOTE({path}),10)>=%s))");args.extend([epoch,minimum])
    if after_store or after_id:clauses.append('(a.store,a.app_id)>(%s,%s)');args.extend([after_store,after_id])
    listing_join='JOIN' if market_fields or listing_status.state_key(state) in ('available','region_unavailable') else 'LEFT JOIN'
    join=f" FROM apps a {listing_join} listings l ON a.store=l.store AND a.app_id=l.app_id AND l.country=%s AND l.language=%s"
    if state or not _count:
        join+=" LEFT JOIN jobs j ON j.store=l.store AND j.app_id=l.app_id AND j.country=l.country AND j.language=l.language"
    where=' WHERE '+' AND '.join(clauses)
    if _count:
        needs_listing=any('l.' in clause for clause in clauses)
        return db.execute('SELECT /*+ MAX_EXECUTION_TIME(15000) */ COUNT(*) n'+(join if needs_listing else ' FROM apps a')+where,
                          args if needs_listing else args[2:]).fetchone()['n']
    payload=LIST_SUMMARY if compact else 'l.payload'
    rows=db.execute(f'SELECT /*+ MAX_EXECUTION_TIME(15000) */ a.*,{listing_status.CURRENT_STATE} state,l.last_success,l.checked_at,{payload} payload'+join+where+
                    ' ORDER BY a.store,a.app_id LIMIT %s OFFSET %s',(*args,limit+1,_offset)).fetchall()
    more=len(rows)>limit;rows=rows[:limit]
    for row in rows:
        row['payload']=json.loads(row['payload']) if row['payload'] else None
        row['category_id']=catalog.category_id(store,row['category']);row['category_name']=catalog.BY_ID[store].get(row['category_id'],{}).get('name')
        row['country']=country;row['language']=language
    return {'items':rows,'next':{'after_store':store,'after_id':rows[-1]['app_id']} if more else None,'country':country,'language':language}


def events(db,filters):
    markets=[insights.market(filters|{'store':store},require_app=False) for store in matching_stores(filters)]
    return insights.event_feed(db,filters,markets)


def reviews(db,filters):
    reports=[review_desk.desk(db,filters|{'store':s}) for s in matching_stores(filters)]
    if len(reports)==1:return reports[0]
    total=sum(p['summary']['total'] for p in reports);ratings={};daily={}
    for p in reports:
        for r in p['ratings']:ratings[r['rating']]=ratings.get(r['rating'],0)+r['count']
        for r in p['daily']:daily[r['day']]=daily.get(r['day'],0)+r['count']
    return {'page_count':max(1,*[(p['summary']['total']+49)//50 for p in reports]),'items':sorted([r for p in reports for r in p['items']],key=lambda r:r['last_seen'],reverse=True),
            'summary':{'total':total,'apps':sum(p['summary']['apps'] for p in reports),'mean_review_rating':sum(float(p['summary']['mean_review_rating'] or 0)*p['summary']['total'] for p in reports)/total if total else None},
            'ratings':[{'rating':k,'count':v} for k,v in sorted(ratings.items())],
            'daily':[{'day':k,'count':v} for k,v in sorted(daily.items())],
            'next_offset':filters.get('offset',0)+50 if any(p['next_offset'] is not None for p in reports) else None,
            'scope':'每个商店分别分页，每页最多50条/店；评论星级不是商店累计评分。'}


def matching_stores(filters):
    selected=locales.stores(filters['store'])
    if filters.get('app_id'):
        selected=[s for s in selected if valid_id(s,filters['app_id'])]
        if not selected:raise ValueError('应用 ID 不属于所选商店')
    return selected


def market_summary(db,store,country,language):
    scope,args=locales.where(store,country,language)
    sql=' FROM listings WHERE payload IS NOT NULL AND '+scope
    return {'categories':db.execute("SELECT store,JSON_UNQUOTE(JSON_EXTRACT(payload,'$.category')) label,count(*) count"+sql+' GROUP BY store,label ORDER BY count DESC LIMIT 200',args).fetchall(),
            'developers':db.execute("SELECT store,JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer')) label,count(*) count"+sql+' GROUP BY store,label ORDER BY count DESC LIMIT 200',args).fetchall(),
            'activity':db.execute('SELECT kind,FLOOR(observed_at/86400)*86400 day,count(*) count FROM events WHERE '+scope+' AND observed_at>=%s GROUP BY kind,day ORDER BY day',(*args,time.time()-30*86400)).fetchall(),
            'scope':'仅统计所选商店和请求市场的实际观察，不代表全球市场份额。'}


def daily(db,store):
    selected=locales.stores(store);marks=','.join('%s' for _ in selected);since=time.time()-86400
    return {'since':since,'generated_at':time.time(),
        'events':db.execute(f'SELECT kind,count(*) count FROM events WHERE store IN ({marks}) AND observed_at>=%s GROUP BY kind',(*selected,since)).fetchall(),
        'alerts':db.execute(f"SELECT * FROM alerts WHERE JSON_UNQUOTE(JSON_EXTRACT(payload,'$.store')) IN ({marks}) AND observed_at>=%s ORDER BY id DESC LIMIT 1000",(*selected,since)).fetchall(),
        'collections':db.execute(f'SELECT r.outcome,count(*) count FROM observation_runs r JOIN observation_targets t ON t.id=r.target_id WHERE t.store IN ({marks}) AND r.observed_at>=%s GROUP BY r.outcome',(*selected,since)).fetchall(),
        'keywords':db.execute(f'SELECT r.outcome,count(*) count FROM keyword_runs r JOIN keyword_targets t ON t.id=r.target_id WHERE t.store IN ({marks}) AND r.observed_at>=%s GROUP BY r.outcome',(*selected,since)).fetchall()}
