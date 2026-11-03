"""Fixed-window changes in observed cumulative counters, not download estimates."""
import math
import time
import json
import gzip
from pathlib import Path
from .catalog import listing_category, category_aliases
from .charts import released_at


def counter(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def record(db, job, payload, observed_at, raw_path):
    """Called in the successful observation transaction, including unchanged pages."""
    fields = dict(store=job['store'], app_id=job['app_id'], country=job['country'], language=job['language'],
                  day=int(observed_at // 86400) * 86400, observed_at=observed_at,
                  source='google-play-public' if job['store']=='google' else 'apple-lookup', parser_version='metrics-v1',
                  name=payload.get('name'), category=listing_category(job['store'],payload),
                  released_at=released_at(payload.get('released')), installs_min=counter(payload.get('installs')),
                  installs_reported=counter(payload.get('installs_reported')) if job['store']=='google' else None,
                  ratings=counter(payload.get('ratings')), rating=payload.get('rating'), price=payload.get('price'),
                  currency=payload.get('currency'), has_ads=payload.get('has_ads'), has_iap=payload.get('has_iap'),raw_path=raw_path)
    columns=','.join(fields); marks=','.join('%s' for _ in fields)
    # A delayed worker cannot replace a newer daily observation.
    updates=','.join(f'{k}=IF(incoming.observed_at>=app_metrics_daily.observed_at,incoming.{k},app_metrics_daily.{k})'
                     for k in fields if k not in ('store','app_id','country','language','day','observed_at'))
    db.execute(f'''INSERT INTO app_metrics_daily({columns}) VALUES({marks}) AS incoming ON DUPLICATE KEY UPDATE
        {updates}, observed_at=GREATEST(app_metrics_daily.observed_at,incoming.observed_at)''',tuple(fields.values()))


def cutoff(value=None):
    value=time.time() if value is None else value
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<=time.time()+300:
        raise ValueError('观察时间必须为有效过去时间')
    return value


def backfill(cfg,db,after_id=0,limit=500,upper_id=None):
    """Replay retained snapshots only; never interpolate absent dates or fetch the network."""
    if type(after_id) is not int or after_id<0 or type(limit) is not int or not 1<=limit<=5000:raise ValueError('回放游标或批量大小无效')
    upper_id=upper_id if upper_id is not None else db.execute('SELECT COALESCE(MAX(id),0) id FROM snapshots').fetchone()['id']
    rows=db.execute('SELECT * FROM snapshots WHERE id>%s AND id<=%s ORDER BY id LIMIT %s',(after_id,upper_id,limit)).fetchall()
    root=Path(cfg['data_dir']).resolve();written=0;issues=[];skipped=0
    for row in rows:
        try:
            payload=json.loads(row['payload']);key=tuple(row[k] for k in ('store','app_id','country','language'));day=int(row['observed_at']//86400)*86400
            existing=db.execute('SELECT observed_at FROM app_metrics_daily WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND day=%s',(*key,day)).fetchone()
            if existing and existing['observed_at']>=row['observed_at']:skipped+=1;continue
            if row['store']=='google' and payload.get('installs_reported') is None:
                try:
                    raw=(root/row['raw_path']).resolve()
                    if not raw.is_relative_to(root):raise ValueError('invalid path')
                    with gzip.open(raw,'rb') as stream:body=stream.read(8_000_001)
                    if len(body)>8_000_000:raise ValueError('response too large')
                    from appgoblin_play_scraper.features.app import parse_dom
                    parsed=parse_dom(body.decode('utf-8'),row['app_id'],'https://play.google.com/store/apps/details?id='+row['app_id'])
                    payload['installs_reported']=counter(parsed.get('realInstalls'))
                except Exception as exc:issues.append({'snapshot_id':row['id'],'stage':'raw_install_counter','error':type(exc).__name__})
            with db:record(db,row,payload,row['observed_at'],row['raw_path'])
            written+=1
        except Exception as exc:issues.append({'snapshot_id':row['id'],'stage':'snapshot','error':type(exc).__name__})
    cursor=rows[-1]['id'] if rows else after_id
    return {'processed':len(rows),'written':written,'skipped_newer_or_equal':skipped,'issues':issues,'after_id':cursor,'upper_id':upper_id,'has_more':cursor<upper_id and bool(rows)}


def report(db, store, country, language, days=7, metric='ratings', as_of=None, category='', q='', pricing='', monetization='', new_days=0, limit=200):
    if days not in (1,7,14,30): raise ValueError('增长窗口为1、7、14或30天')
    if metric not in ('ratings','installs_reported'): raise ValueError('未知增长指标')
    if metric=='installs_reported' and store!='google':
        return {'items':[],'reason':'Apple 公开来源不提供累计安装量，请查看评分增长或接入授权报表','scope':'unavailable','limit':limit}
    if pricing not in ('','free','paid') or monetization not in ('','ads','iap','both'): raise ValueError('未知商业化筛选')
    if new_days not in (0,7,30,90): raise ValueError('发布时间窗口为7、30或90天')
    at=cutoff(as_of)
    filters=['1=1']; filter_args=[]
    if category:
        cats=category_aliases(store,category)
        marks=','.join('%s' for _ in cats)
        filters.append(f"(c.category IS NULL OR c.category NOT IN ({marks}))" if category=='__unknown__' else f'c.category IN ({marks})')
        filter_args.extend(cats)
    if q:filters.append("LOCATE(LOWER(%s),LOWER(CONCAT(COALESCE(c.name,''),' ',c.app_id)))>0");filter_args.append(q)
    if pricing:filters.append('c.price=0' if pricing=='free' else 'c.price>0')
    if monetization in ('ads','both'):filters.append('c.has_ads=1')
    if monetization in ('iap','both'):filters.append('c.has_iap=1')
    if new_days:filters.append('c.released_at BETWEEN %s AND %s');filter_args.extend([at-new_days*86400,at])
    samples=[]; args=[]
    for label,boundary in (('c',at),('b',at-days*86400),('p',at-days*2*86400)):
        samples.append(f"""{label}_samples AS (SELECT m.*,ROW_NUMBER() OVER(PARTITION BY app_id ORDER BY observed_at DESC) rn
            FROM app_metrics_daily m WHERE store=%s AND country=%s AND language=%s AND day BETWEEN %s AND %s
            AND observed_at BETWEEN %s AND %s)""")
        args.extend([store,country,language,int((boundary-36*3600)//86400)*86400,int(boundary//86400)*86400,boundary-36*3600,boundary])
    same='c.source=b.source AND c.parser_version=b.parser_version'
    prior_same='c.source=p.source AND c.parser_version=p.parser_version'
    status=f"""CASE WHEN c.{metric} IS NULL THEN 'unavailable' WHEN b.{metric} IS NULL THEN 'missing_baseline'
        WHEN NOT ({same}) THEN 'incompatible' WHEN c.observed_at-b.observed_at<%s THEN 'missing_baseline'
        WHEN c.{metric}<b.{metric} THEN 'counter_correction' ELSE 'observed' END"""
    cte='WITH '+','.join(samples)+f""", compared AS (SELECT c.*,b.observed_at baseline_at,
        c.{metric}-b.{metric} raw_delta,{status} status,
        CASE WHEN {same} AND {prior_same} AND b.{metric}>=p.{metric}
            AND b.observed_at-p.observed_at>=%s THEN b.{metric}-p.{metric} ELSE NULL END previous_delta
        FROM c_samples c LEFT JOIN b_samples b ON b.app_id=c.app_id AND b.rn=1
        LEFT JOIN p_samples p ON p.app_id=c.app_id AND p.rn=1
        WHERE c.rn=1 AND {' AND '.join(filters)})"""
    args.extend([days*86400-12*3600,days*86400-12*3600,*filter_args])
    statuses={r['status']:r['n'] for r in db.execute(cte+' SELECT status,count(*) n FROM compared GROUP BY status',args).fetchall()}
    rows=list(db.execute(cte+" SELECT *,IF(status='observed',raw_delta,NULL) delta FROM compared ORDER BY delta IS NULL,delta DESC,app_id LIMIT %s",(*args,limit)).fetchall())
    for r in rows:
        prior=r['previous_delta'];r['growth_pct']=round((r['delta']-prior)/prior*100,2) if r['delta'] is not None and prior and prior>0 else None
        r['actual_days']=round((r['observed_at']-r['baseline_at'])/86400,2) if r['baseline_at'] else None
    return {'items':rows,'total':sum(statuses.values()),'limit':limit,'statuses':statuses,'as_of':at,'days':days,'metric':metric,
            'scope':'global_installs_observed_in_market' if metric=='installs_reported' else 'same_market_public_rating_counter',
            'reason':'安装指标是Google公开全球累计数的观察差值，国家仅限定观察应用池；不是该国下载。评分差值是净变化。基线最多向前取36小时，缺测不填0；增长率与前一个等长窗口比较。'}
