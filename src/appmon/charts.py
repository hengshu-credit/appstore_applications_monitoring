"""Store charts and explicitly labeled, locally derived ranking views."""
import json
import time
import gzip
import math
from pathlib import Path
from datetime import datetime, timezone

DEFINITIONS = [
    ('top-free','免费榜','top-free'),('top-paid','付费榜','top-paid'),('top-grossing','畅销榜','top-grossing'),
    ('trending','人气飙升榜','top-free'),('new-free','新应用免费榜','top-free'),
    ('new-grossing','新应用畅销榜','top-grossing'),('new-paid','新应用付费榜','top-paid'),
]


def capabilities(store):
    if store not in ('apple','google'):raise ValueError('未知商店')
    result=[]
    for identifier,label,base in DEFINITIONS:
        direct=identifier!='new-grossing' and not (store=='apple' and identifier=='trending')
        note=(('商店新应用榜，与普通总榜不同；不是同一榜单的最新快照' if identifier.startswith('new-') else '商店公开总榜，按返回顺序记录') if direct else
              '本站派生：同一免费榜相邻两次有效快照，按正向排名升幅排序；不是商店官方人气榜' if identifier=='trending' else
              '本站派生：从基榜已返回应用中筛选相对于该次观察近30天发布的应用，保留基榜顺序；不代表全商店新应用')
        result.append({'id':identifier,'name':label,'mode':'direct' if direct else 'derived',
                       'base_collection':identifier if direct else base,'note':note,
                       'category_supported':not(store=='apple' and identifier in ('new-free','new-paid'))})
    return result


def definition(store,collection):
    result=next((r for r in capabilities(store) if r['id']==collection),None)
    if not result:raise ValueError('未知榜单类型')
    return result


def _snapshot(db,store,country,language,category,collection,cache,as_of=None,compare_days=0):
    key=(store,country,language,category,collection,as_of,compare_days)
    if key in cache:return cache[key]
    categories=['','APPLICATION'] if store=='google' and category in ('','APPLICATION') else [category]
    marks=','.join('%s' for _ in categories)
    join='r.id=t.latest_success_id'; prefix=[]
    if as_of is not None:
        join="r.id=(SELECT h.id FROM observation_runs h WHERE h.target_id=t.id AND h.outcome='ok' AND h.observed_at<=%s ORDER BY h.observed_at DESC,h.id DESC LIMIT 1)"
        prefix=[as_of]
    target=db.execute(f'''SELECT t.*,r.observed_at FROM observation_targets t LEFT JOIN observation_runs r
        ON {join} WHERE t.kind='chart' AND t.store=%s AND t.country=%s AND t.language=%s
        AND t.collection=%s AND t.category IN ({marks}) ORDER BY r.observed_at DESC,t.id DESC LIMIT 1''',
        (*prefix,store,country,language,collection,*categories)).fetchone()
    if not target:cache[key]=(None,None,None,[],{});return cache[key]
    target.pop('token',None)
    current=db.execute('SELECT * FROM observation_runs WHERE id=%s',(target['latest_success_id'],)).fetchone() if target['latest_success_id'] else None
    previous=db.execute('SELECT * FROM observation_runs WHERE id=%s',(target['previous_success_id'],)).fetchone() if target['previous_success_id'] else None
    if as_of is not None or compare_days:
        boundary=as_of if as_of is not None else time.time()
        current=db.execute("SELECT * FROM observation_runs WHERE target_id=%s AND outcome='ok' AND observed_at<=%s ORDER BY observed_at DESC,id DESC LIMIT 1",(target['id'],boundary)).fetchone()
        if current:
            before=current['observed_at']-compare_days*86400
            previous=db.execute("SELECT * FROM observation_runs WHERE target_id=%s AND outcome='ok' AND observed_at<=%s AND id<>%s ORDER BY observed_at DESC,id DESC LIMIT 1",(target['id'],before,current['id'])).fetchone()
            if compare_days and previous and before-previous['observed_at']>36*3600:previous=None
        else:previous=None
    compatible=current and previous and all(current[k]==previous[k] for k in ('source','parser_version','coverage'))
    old={r['item_id']:r['position'] for r in db.execute('SELECT item_id,position FROM observation_items WHERE run_id=%s',(previous['id'],)).fetchall()} if compatible else {}
    items=[]
    if current:
        for row in db.execute('SELECT item_id,position,payload FROM observation_items WHERE run_id=%s ORDER BY position',(current['id'],)).fetchall():
            item=json.loads(row['payload']);position=row['position'];before=old.get(row['item_id'])
            items.append(item|{'app_id':row['item_id'],'position':position,'base_position':position,
                              'previous_position':before,'change':before-position if before is not None else None})
    cache[key]=(target,current,previous,items,old);return cache[key]


def released_at(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):return value
    if not isinstance(value,str):return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:pass
    for fmt in ('%b %d, %Y','%B %d, %Y','%d %b %Y'):
        try:return datetime.strptime(value,fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:pass
    return None


def view(db,store,country,language,collection='top-free',category='',cache=None,as_of=None,compare_days=0):
    if as_of is not None:
        from .growth import cutoff
        as_of=cutoff(as_of)
    if compare_days not in (0,1,7,14,30):raise ValueError('比较窗口为相邻快照或1/7/14/30天')
    spec=definition(store,collection);cache={} if cache is None else cache
    if category and not spec['category_supported']:
        return {'definition':spec,'store':store,'country':country,'language':language,'category':category,'target':None,
                'latest_success':None,'previous_success':None,'items':[],'reason':'该商店公开新应用榜仅支持总榜，不能作为分类榜',
                'unknown_release_count':0,'base_returned_count':0,'unsupported':True}
    target,current,previous,items,old=_snapshot(db,store,country,language,category,spec['base_collection'],cache,as_of,compare_days)
    rows=list(items);unknown=0;reason=''; ready=True
    if collection=='trending' and spec['mode']=='derived':
        rows=sorted((r for r in rows if r['change'] is not None and r['change']>0),key=lambda r:(-r['change'],r['position'],r['app_id']))
        if not previous or not old:
            reason='至少需要同一基榜两次可比较的成功快照；新入榜不计作已知升幅';ready=False
    elif spec['mode']=='derived' and collection.startswith('new-') and current and rows:
        marks=','.join('%s' for _ in rows)
        if as_of is not None:
            records=db.execute(f'''SELECT app_id,payload FROM (SELECT app_id,payload,ROW_NUMBER() OVER(PARTITION BY app_id ORDER BY observed_at DESC,id DESC) rn
                FROM snapshots WHERE store=%s AND country=%s AND language=%s AND observed_at<=%s AND app_id IN ({marks})) s WHERE rn=1''',
                (store,country,language,current['observed_at'],*[r['app_id'] for r in rows])).fetchall()
        else:
            records=db.execute(f'SELECT app_id,payload FROM listings WHERE store=%s AND country=%s AND language=%s AND app_id IN ({marks})',
                (store,country,language,*[r['app_id'] for r in rows])).fetchall()
        releases={r['app_id']:released_at(json.loads(r['payload'] or '{}').get('released')) for r in records}
        for row in rows:
            if releases.get(row['app_id']) is None:releases[row['app_id']]=released_at(row.get('released'))
        unknown=sum(releases.get(r['app_id']) is None for r in rows)
        rows=[r|{'released_at':releases[r['app_id']]} for r in rows if releases.get(r['app_id']) is not None
              and current['observed_at']-30*86400<=releases[r['app_id']]<=current['observed_at']]
        if unknown:reason=f'基榜中 {unknown} 个应用尚无可解析的发布日期，未纳入派生榜'
    if spec['mode']=='derived':rows=[r|{'position':i} for i,r in enumerate(rows,1)]
    compatible=bool(current and previous and all(current[k]==previous[k] for k in ('source','parser_version','coverage')))
    for r in rows:
        r['entry_status']='observed' if compatible else 'missing_baseline'
        if compatible and r['app_id'] not in old:
            r['entry_status']='entered_range' if r['base_position']<=previous['returned_count'] else 'unknown_depth'
    current_ids={r['app_id'] for r in items}
    exited=[{'app_id':identifier,'previous_position':position} for identifier,position in old.items()
            if identifier not in current_ids and current and position<=current['returned_count']]
    boundary=as_of if as_of is not None else time.time()
    quality='missing' if not current else 'insufficient_history' if not ready else 'historical' if as_of is not None else (
        'paused' if not target['enabled'] else 'failed' if target['failures'] else
        'stale' if boundary-current['observed_at']>target['refresh_hours']*3600 else 'partial' if unknown else 'fresh')
    return {'definition':spec,'store':store,'country':country,'language':language,'category':category,'target':target,
            'latest_success':current,'previous_success':previous,'items':rows,'reason':reason,
            'unknown_release_count':unknown,'base_returned_count':len(items),'exited_items':exited,
            'comparison_available':compatible,'compare_days':compare_days,'quality':quality,'ready':bool(current and ready),
            'history':db.execute("SELECT id,observed_at,returned_count FROM observation_runs WHERE target_id=%s AND outcome='ok' ORDER BY observed_at DESC,id DESC LIMIT 100",(target['id'],)).fetchall() if target else []}


def app_rankings(db,store,app_id,country,language,categories):
    cache={};result=[]
    for category in dict.fromkeys(categories):
        for spec in capabilities(store):
            report=view(db,store,country,language,spec['id'],category,cache)
            entry=next((r for r in report['items'] if r['app_id']==app_id),None)
            result.append({'collection':spec['id'],'name':spec['name'],'mode':spec['mode'],'note':spec['note'],
                           'category':category,'target_id':report['target']['id'] if report['target'] else None,
                           'observed_at':report['latest_success']['observed_at'] if report['latest_success'] else None,
                           'source':report['latest_success']['source'] if report['latest_success'] else None,
                           'returned_count':len(report['items']),'base_returned_count':report['base_returned_count'],
                           'position':entry['position'] if entry else None,'base_position':entry['base_position'] if entry else None,
                           'change':entry['change'] if entry else None,'reason':report['reason'],
                           'status':'unsupported' if report.get('unsupported') else 'not_checked' if not report['latest_success'] else 'observed' if entry else 'not_observed'})
    return result


def detail(db,store,country,language,collection='top-free',category='',as_of=None,q='',offset=0,limit=50,cfg=None):
    """Page through the observed chart and attach separately dated market metadata."""
    if type(offset) is not int or offset<0 or offset>10000 or limit not in (25,50,100):raise ValueError('榜单分页参数无效')
    report=view(db,store,country,language,collection,category,as_of=as_of)
    items=report['items'];metadata={};chart_metadata={};metadata_warning=''
    current=report['latest_success']
    if cfg and store=='apple' and current and current['source']=='apple-rss-chart' and current.get('raw_path'):
        try:
            from .objects import for_config
            content=for_config(cfg).get(current['raw_path'], max_bytes=8_000_000)
            if len(content)>8_000_000:raise ValueError('Chart response too large')
            entries=json.loads(content).get('feed',{}).get('entry',[])
            if isinstance(entries,dict):entries=[entries]
            for entry in entries:
                identifier=str(entry['id']['attributes']['im:id'])
                price=entry.get('im:price',{}).get('attributes',{})
                try:amount=float(price['amount'])
                except (KeyError,ValueError,TypeError):amount=None
                if amount is not None and (not math.isfinite(amount) or amount<0):amount=None
                category_info=entry.get('category',{}).get('attributes',{})
                chart_metadata[identifier]={k:v for k,v in {'price':amount,'currency':price.get('currency'),
                    'category_id':category_info.get('im:id'),'category':category_info.get('label'),
                    'released':entry.get('im:releaseDate',{}).get('label')}.items() if v is not None}
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            chart_metadata={};metadata_warning='榜单原始补充字段暂不可读取，仍显示已保存的排名与应用资料。'
    if items:
        ids=[r['app_id'] for r in items];marks=','.join('%s' for _ in ids)
        if as_of is not None:
            rows=db.execute(f'''SELECT app_id,payload,observed_at FROM (
                SELECT app_id,payload,observed_at,ROW_NUMBER() OVER(PARTITION BY app_id ORDER BY observed_at DESC,id DESC) rn
                FROM snapshots WHERE store=%s AND country=%s AND language=%s AND observed_at<=%s AND app_id IN ({marks})
                ) s WHERE rn=1''',(store,country,language,report['latest_success']['observed_at'],*ids)).fetchall()
        else:
            rows=db.execute(f'''SELECT app_id,payload,last_success observed_at,state FROM listings
                WHERE store=%s AND country=%s AND language=%s AND app_id IN ({marks})''',(store,country,language,*ids)).fetchall()
        metadata={r['app_id']:r|{'payload':json.loads(r['payload'] or '{}')} for r in rows}
    enriched=[]
    for item in items:
        saved=metadata.get(item['app_id'],{});p=saved.get('payload',{})
        facts=chart_metadata.get(item['app_id'],{})|{k:v for k,v in p.items() if v is not None}
        row=item|{'name':item.get('name') or p.get('name') or item['app_id'],
                  'icon':item.get('icon') or p.get('icon'),'developer':item.get('developer') or p.get('developer'),
                  'metadata':facts,'metadata_observed_at':saved.get('observed_at') or (current['observed_at'] if chart_metadata.get(item['app_id']) else None),
                  'metadata_origin':'detail' if saved.get('observed_at') else 'chart' if chart_metadata.get(item['app_id']) else None,
                  'listing_state':saved.get('state')}
        if q and q.casefold() not in ' '.join(str(row.get(k) or '') for k in ('name','app_id','developer')).casefold():continue
        enriched.append(row)
    return {k:v for k,v in report.items() if k not in ('items','history','exited_items')}|{
        'items':enriched[offset:offset+limit],'total':len(enriched),'chart_count':len(items),'offset':offset,'limit':limit,
        'has_more':offset+limit<len(enriched),'metadata_warning':metadata_warning,'metadata_note':
        '历史应用资料仅使用不晚于该榜单快照的记录。' if as_of is not None else
        '应用资料优先使用该市场的最近成功观察，缺失字段可由榜单公开字段补充；两类数据分别保留观察时间。Google累计安装数为全球公开指标。'}
