"""Read-only, market-specific rows shared by application tables across pages."""
import json
import time
from datetime import datetime, timedelta, timezone

from . import app_groups, app_permissions, intelligence
from .db import dump


def rows(db, cfg, targets, days=30):
    if not isinstance(targets, list) or len(targets)>200 or any(not isinstance(t,dict) for t in targets) or type(days) is not int or not 1<=days<=365:
        raise ValueError('应用明细每次最多200个市场目标，期间为1–365天')
    if not targets:
        return {'items':[], 'days':days}
    markets=[intelligence.market(target) for target in targets]
    # A parameterized in-memory relation; never creates group memberships or tasks.
    relation="""JSON_TABLE(%s, '$[*]' COLUMNS (
        id FOR ORDINALITY, store VARCHAR(16) PATH '$.store', app_id VARCHAR(255) PATH '$.app_id',
        country VARCHAR(8) PATH '$.country', language VARCHAR(32) PATH '$.language'))"""
    args=(dump(markets),)
    result=db.execute(f'''SELECT m.*,a.name,a.category,l.payload,l.state,l.last_success,
        w.enabled,w.refresh_hours,j.failures,j.last_error,j.due,j.lease_until,
        p.id permission_id,p.enabled permission_enabled,p.refresh_hours permission_refresh_hours,
        p.success_at permission_success,p.checked_at permission_checked,p.permission_count,
        p.failures permission_failures,p.lease_until permission_lease,p.last_error permission_error,
        JSON_KEYS(p.payload) permission_groups
        FROM {relation} m LEFT JOIN apps a USING(store,app_id)
        LEFT JOIN listings l USING(store,app_id,country,language)
        LEFT JOIN watchlist w USING(store,app_id,country,language)
        LEFT JOIN jobs j USING(store,app_id,country,language)
        LEFT JOIN app_permission_targets p ON p.store=m.store AND p.app_id=m.app_id AND p.country=m.country
        ORDER BY m.id''',args).fetchall()
    now=time.time()
    for row in result:
        payload=json.loads(row.pop('payload') or '{}')
        row['name']=payload.get('name') or row['name']
        for key in ('rating','ratings','installs'):row[key]=app_groups._number(payload.get(key))
        row['permission_groups']=json.loads(row['permission_groups'] or '[]')
        row['permission_overrides']={}
        row['state']=row['state'] or 'unverified'
        row['classification']=row['state']
        row['permission_state']=app_permissions.state(row)
        row['details_state']=('not_scheduled' if row['enabled'] is None or row['due'] is None else 'paused' if not row['enabled']
            else 'running' if row['lease_until']>now else 'retry' if row['failures'] else 'scheduled' if row['due']>now else 'pending')
    app_groups.member_rank_details(db,None,result,now,cfg,source=(relation,args))
    counts=db.execute(f'''SELECT m.id,COUNT(DISTINCT t.id) samples,
        COUNT(DISTINCT CASE WHEN k.app_id IS NOT NULL THEN t.id END) covered,
        COUNT(DISTINCT CASE WHEN k.position<=3 THEN t.id END) top3
        FROM {relation} m LEFT JOIN keyword_targets t ON t.store=m.store AND t.country=m.country
            AND t.language=m.language AND t.enabled=1 AND t.failures=0
            AND EXISTS (SELECT 1 FROM keyword_runs r WHERE r.id=t.latest_success_id AND r.outcome='ok'
                AND r.requested_depth=t.depth AND r.observed_at+t.refresh_hours*3600>%s)
        LEFT JOIN keyword_ranks k ON k.run_id=t.latest_success_id AND k.app_id=m.app_id GROUP BY m.id''',(*args,now)).fetchall()
    for row,count in zip(result,sorted(counts,key=lambda r:r['id'])):
        row['keyword_count']=count['covered'] if count['samples'] else None
        row['keyword_top3_count']=count['top3'] if count['samples'] else None
    end=datetime.fromtimestamp(now,timezone.utc).date()
    start=end-timedelta(days=days-1)
    previous=start-timedelta(days=days)
    facts=db.execute(f'''SELECT f.store,f.app_id,f.country,f.metric,f.currency,f.is_estimate,
        SUM(IF(f.day>=%s,f.value,0)) value,COUNT(IF(f.day>=%s,1,NULL)) days,
        SUM(IF(f.day<%s,f.value,0)) prior,COUNT(IF(f.day<%s,1,NULL)) prior_days
        FROM app_metric_facts f WHERE f.day BETWEEN %s AND %s
        AND EXISTS (SELECT 1 FROM {relation} m WHERE m.store=f.store AND m.app_id=f.app_id AND m.country=f.country)
        GROUP BY f.store,f.app_id,f.country,f.metric,f.currency,f.is_estimate''',
        (start,start,start,start,previous,end,*args)).fetchall()
    app_groups.assign_metric_facts(result,facts,days)
    # Market mix is relative to the explicit batch, not a claim of worldwide share.
    for row in result:
        for fact in row['metrics']:fact['market_mix']=[]
    return {'items':result,'days':days}
