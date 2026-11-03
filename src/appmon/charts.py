"""Store charts and explicitly labeled, locally derived ranking views."""
import json
from datetime import datetime, timezone

DEFINITIONS = [
    ('top-free','免费榜','top-free'),('top-paid','付费榜','top-paid'),('top-grossing','畅销榜','top-grossing'),
    ('trending','人气飙升榜','top-free'),('new-free','最新免费榜','top-free'),
    ('new-grossing','最新畅销榜','top-grossing'),('new-paid','最新付费榜','top-paid'),
]


def capabilities(store):
    if store not in ('apple','google'):raise ValueError('未知商店')
    result=[]
    for identifier,label,base in DEFINITIONS:
        direct=identifier!='new-grossing' and not (store=='apple' and identifier=='trending')
        note=('商店公开榜单，按返回顺序记录' if direct else
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


def _snapshot(db,store,country,language,category,collection,cache):
    key=(store,country,language,category,collection)
    if key in cache:return cache[key]
    categories=['','APPLICATION'] if store=='google' and category in ('','APPLICATION') else [category]
    marks=','.join('%s' for _ in categories)
    target=db.execute(f'''SELECT t.*,r.observed_at FROM observation_targets t LEFT JOIN observation_runs r
        ON r.id=t.latest_success_id WHERE t.kind='chart' AND t.store=%s AND t.country=%s AND t.language=%s
        AND t.collection=%s AND t.category IN ({marks}) ORDER BY r.observed_at DESC,t.id DESC LIMIT 1''',
        (store,country,language,collection,*categories)).fetchone()
    if not target:cache[key]=(None,None,None,[],{});return cache[key]
    target.pop('token',None)
    current=db.execute('SELECT * FROM observation_runs WHERE id=%s',(target['latest_success_id'],)).fetchone() if target['latest_success_id'] else None
    previous=db.execute('SELECT * FROM observation_runs WHERE id=%s',(target['previous_success_id'],)).fetchone() if target['previous_success_id'] else None
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


def view(db,store,country,language,collection='top-free',category='',cache=None):
    spec=definition(store,collection);cache={} if cache is None else cache
    if category and not spec['category_supported']:
        return {'definition':spec,'store':store,'country':country,'language':language,'category':category,'target':None,
                'latest_success':None,'previous_success':None,'items':[],'reason':'该商店公开新应用榜仅支持总榜，不能作为分类榜',
                'unknown_release_count':0,'base_returned_count':0,'unsupported':True}
    target,current,previous,items,old=_snapshot(db,store,country,language,category,spec['base_collection'],cache)
    rows=list(items);unknown=0;reason=''
    if collection=='trending' and spec['mode']=='derived':
        rows=sorted((r for r in rows if r['change'] is not None and r['change']>0),key=lambda r:(-r['change'],r['position'],r['app_id']))
        if not previous or not old:reason='至少需要同一基榜两次可比较的成功快照；新入榜不计作已知升幅'
    elif spec['mode']=='derived' and collection.startswith('new-') and current and rows:
        marks=','.join('%s' for _ in rows)
        releases={r['app_id']:released_at(json.loads(r['payload'] or '{}').get('released')) for r in db.execute(
            f'SELECT app_id,payload FROM listings WHERE store=%s AND country=%s AND language=%s AND app_id IN ({marks})',
            (store,country,language,*[r['app_id'] for r in rows])).fetchall()}
        for row in rows:
            if releases.get(row['app_id']) is None:releases[row['app_id']]=released_at(row.get('released'))
        unknown=sum(releases.get(r['app_id']) is None for r in rows)
        rows=[r|{'released_at':releases[r['app_id']]} for r in rows if releases.get(r['app_id']) is not None
              and current['observed_at']-30*86400<=releases[r['app_id']]<=current['observed_at']]
        if unknown:reason=f'基榜中 {unknown} 个应用尚无可解析的发布日期，未纳入派生榜'
    if spec['mode']=='derived':rows=[r|{'position':i} for i,r in enumerate(rows,1)]
    return {'definition':spec,'store':store,'country':country,'language':language,'category':category,'target':target,
            'latest_success':current,'previous_success':previous,'items':rows,'reason':reason,
            'unknown_release_count':unknown,'base_returned_count':len(items)}


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
