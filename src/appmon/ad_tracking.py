"""App-scoped advertising evidence, queue receipts and account reports."""
import hashlib
import json
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import ad_sources, ad_channels, source_tasks
from .db import dump
from .collection_batch import BatchWriter, touch, PAGE_DUE
from .intelligence import market, hours

MARKET_FIELDS = ('store','app_id','country','language')
MARKET_WHERE = 'store=%s AND app_id=%s AND country=%s AND language=%s'


def app_market(db, value):
    m = market(value)
    if not db.execute('SELECT 1 FROM apps WHERE store=%s AND app_id=%s', (m['store'], m['app_id'])).fetchone():
        raise ValueError('应用尚未进入应用池')
    return m


def schedule_for(db, m):
    row = db.execute(f'''SELECT id,kinds,refresh_hours,enabled,next_due,last_queued_at,run_number
        FROM ad_tracking_schedules WHERE {MARKET_WHERE}''',tuple(m[k] for k in MARKET_FIELDS)).fetchone()
    if row:
        row['kinds'] = json.loads(row['kinds'])
    return row


def queue_round(db, m, kinds, actor, *, schedule=None, due=PAGE_DUE):
    """The caller holds the app submit lock or the schedule's row lock and transaction."""
    writer = BatchWriter(db)
    tasks = []
    for kind in kinds:
        if schedule:
            task = source_tasks.enqueue(writer,kind,m,source=schedule['receipt_source'],
                request_id=f"{schedule['run_number']+1}:{kind}",submitted_by=actor,due=due)
        else:
            active = db.execute("""SELECT id FROM collection_source_tasks WHERE batch_key=%s AND source='app-ads'
                AND enabled=1 AND due IS NOT NULL AND failures<3 ORDER BY id DESC LIMIT 1""",(source_tasks.batch_key(kind,m),)).fetchone()
            task = source_tasks.receipt(db,active['id']) if active else source_tasks.enqueue(writer,kind,m,source='app-ads',submitted_by=actor,due=due)
        tasks.append({k: task[k] for k in ('id','kind','status')})
    if schedule:
        now = time.time()
        db.execute('''UPDATE ad_tracking_schedules SET next_due=%s,last_queued_at=%s,run_number=run_number+1
            WHERE id=%s''',(now+schedule['refresh_hours']*3600,now,schedule['id']))
    return tasks


def enqueue(db, value, *, submitted_by):
    m = app_market(db, value)
    mode = value.get('mode','once')
    if mode not in ('once','periodic') or mode=='once' and value.get('refresh_hours') is not None:
        raise ValueError('请选择一次性或周期性广告任务，只有周期任务需要间隔')
    interval = hours(value.get('refresh_hours')) if mode=='periodic' else None
    if type(submitted_by) is not int or submitted_by <= 0:
        raise ValueError('广告任务需要用户明确提交')
    key = tuple(m[k] for k in MARKET_FIELDS)
    # Global-source receipts can be shared across markets on a single app.
    with db.named_lock('submit-app-ads:'+dump(key[:2]),timeout=10), db:
        kinds = ad_sources.runnable(db,m)
        if mode=='once':
            return {'mode':mode,'tasks':queue_round(db,m,kinds,submitted_by)}
        row = db.execute(f'SELECT * FROM ad_tracking_schedules WHERE {MARKET_WHERE} FOR UPDATE',key).fetchone()
        if row and row['enabled']:
            if row['refresh_hours'] != interval or json.loads(row['kinds']) != kinds:
                db.execute('''UPDATE ad_tracking_schedules SET refresh_hours=%s,kinds=%s,submitted_by=%s,next_due=%s WHERE id=%s''',
                           (interval,dump(kinds),submitted_by,time.time()+interval*3600,row['id']))
                touch(db)
            return {'mode':mode,'schedule':schedule_for(db,m),'tasks':[]}
        # Every activation has a distinct source identity. Revoked executions
        # cannot be re-admitted by resuming an old receipt or creating a new schedule.
        source = 'ad-schedule-'+uuid.uuid4().hex
        db.execute('''INSERT INTO ad_tracking_schedules(store,app_id,country,language,receipt_source,kinds,refresh_hours,submitted_by,enabled)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1) ON DUPLICATE KEY UPDATE receipt_source=VALUES(receipt_source),
            kinds=VALUES(kinds),refresh_hours=VALUES(refresh_hours),submitted_by=VALUES(submitted_by),enabled=1,run_number=0''',
            (*key,source,dump(kinds),interval,submitted_by))
        row = db.execute(f'SELECT * FROM ad_tracking_schedules WHERE {MARKET_WHERE}',key).fetchone()
        tasks = queue_round(db,m,kinds,submitted_by,schedule=row)
        return {'mode':mode,'schedule':schedule_for(db,m),'tasks':tasks}


def stop_schedule(db, value):
    m = app_market(db,value)
    key = tuple(m[k] for k in MARKET_FIELDS)
    with db.named_lock('submit-app-ads:'+dump(key[:2]),timeout=10), db:
        row = db.execute(f'SELECT * FROM ad_tracking_schedules WHERE {MARKET_WHERE} FOR UPDATE',key).fetchone()
        if row and row['enabled']:
            db.execute('UPDATE ad_tracking_schedules SET enabled=0,next_due=NULL WHERE id=%s',(row['id'],))
            db.execute('''UPDATE collection_source_tasks SET enabled=0,token=NULL,lease_until=0
                WHERE source=%s AND due IS NOT NULL''',(row['receipt_source'],))
            touch(db)
        return {'schedule':schedule_for(db,m)}


def schedule_due(db):
    now = time.time()
    if not db.execute('SELECT 1 FROM ad_tracking_schedules WHERE enabled=1 AND next_due<=%s LIMIT 1',(now,)).fetchone():
        return
    with db:
        row = db.claim('''SELECT * FROM ad_tracking_schedules s WHERE s.enabled=1 AND s.next_due<=%s
            AND NOT EXISTS(SELECT 1 FROM collection_source_tasks t WHERE t.source=s.receipt_source
                           AND t.due IS NOT NULL AND t.failures<3)
            ORDER BY s.next_due,s.id LIMIT 1 FOR UPDATE SKIP LOCKED''',(now,)).fetchone()
        if not row:
            return
        m = {k:row[k] for k in MARKET_FIELDS}
        configured = ad_sources.runnable(db,m)
        kinds = [k for k in json.loads(row['kinds']) if k in configured]
        queue_round(db,m,kinds,row['submitted_by'],schedule=row,due=0)


def save_page(db, kind, m, data, previous, progress):
    if kind == 'ad_rankings':
        return data | {'returned': int(data['listed'])}
    if kind == 'ad_apple' and progress.get('page') and progress.get('period_end') != data.get('period_end'):
        # The rolling source window moved while the task was waiting. Re-read
        # its first page before declaring coverage for the new date range.
        return previous | {'progress': {'page': 0, 'returned': 0, 'total': data['total']}}
    at = time.time()
    values = []
    versions = []
    for ad in data['items']:
        identity = [kind, m['store'], m['app_id'], ad['country'], ad['ad_id'], ad['channel']]
        digest = hashlib.sha256(dump(identity).encode()).hexdigest()
        payload = dump(ad)
        values.append((digest, m['store'], m['app_id'], ad['country'], '', at, ad['source'],
                       ad['evidence_url'], ad['placement'], payload))
        content = {k:ad.get(k) for k in ('title','body','format','landing_url','store_url')}
        content['assets'] = [{key: asset_identity(value) if key in ('url','preview_url') else value
                              for key,value in asset.items()} for asset in ad.get('assets') or []]
        versions.append((digest,hashlib.sha256(dump(content).encode()).hexdigest(),at,at,payload))
    if values:
        db.executemany('''INSERT INTO ad_observations
            (fingerprint,store,app_id,country,keyword,observed_at,source,evidence_url,placement,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            payload=IF(VALUES(observed_at)>=ad_observations.observed_at,VALUES(payload),ad_observations.payload),
            placement=IF(VALUES(observed_at)>=ad_observations.observed_at,VALUES(placement),ad_observations.placement),
            evidence_url=IF(VALUES(observed_at)>=ad_observations.observed_at,VALUES(evidence_url),ad_observations.evidence_url),
            observed_at=GREATEST(ad_observations.observed_at,VALUES(observed_at))''', values)
        db.executemany('''INSERT INTO ad_creative_versions(fingerprint,version_hash,first_observed_at,last_observed_at,payload)
            VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            payload=IF(VALUES(last_observed_at)>=ad_creative_versions.last_observed_at,VALUES(payload),ad_creative_versions.payload),
            first_observed_at=LEAST(ad_creative_versions.first_observed_at,VALUES(first_observed_at)),
            last_observed_at=GREATEST(ad_creative_versions.last_observed_at,VALUES(last_observed_at))''', versions)
    count = progress.get('returned', 0) + len(values)
    if data.get('next_page') is not None:
        return previous | {'progress': {'returned': count, 'page': data['next_page'], 'total': data.get('total'),
                                        'period_end': data.get('period_end')}}
    return {k: v for k, v in data.items() if k not in ('items','next_page')} | {'returned': count}


def asset_identity(url):
    if not url:
        return url
    parts = urlsplit(url)
    query = [(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True)
             if k.lower() not in ('x-expires','x-signature','expires','signature') and not k.lower().startswith('x-amz-')]
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),''))


def versions(db,m,identifier,offset):
    if offset < 0:
        raise ValueError('素材版本分页参数无效')
    m = app_market(db,m)
    row = db.execute("""SELECT fingerprint FROM ad_observations WHERE id=%s AND store=%s AND app_id=%s
        AND country IN (%s,'')""",(identifier,m['store'],m['app_id'],m['country'])).fetchone()
    if not row:raise ValueError('当前应用或地区没有这条广告')
    rows = db.execute('''SELECT first_observed_at,last_observed_at,payload FROM ad_creative_versions WHERE fingerprint=%s
        ORDER BY last_observed_at DESC,version_hash LIMIT 13 OFFSET %s''',(row['fingerprint'],offset)).fetchall()
    return {'items':[json.loads(r.pop('payload'))|r for r in rows[:12]],'has_more':len(rows)>12}


def source_status(db, m, kind):
    key = source_tasks.batch_key(kind, m)
    latest = db.execute('SELECT id FROM collection_source_tasks WHERE batch_key=%s ORDER BY id DESC LIMIT 1', (key,)).fetchone()
    task = source_tasks.receipt(db, latest['id']) if latest else None
    saved = db.execute('''SELECT success_at,result FROM collection_source_tasks
        WHERE batch_key=%s AND success_at IS NOT NULL ORDER BY success_at DESC,id DESC LIMIT 1''', (key,)).fetchone()
    progress = (task['result'] or {}).get('progress') if task else None
    return {'kind': kind, 'name': ad_sources.KINDS[kind], 'status': task['status'] if task else 'not_checked',
            'failures': task['failures'] if task else 0, 'error': task['last_error'] if task else None,
            'progress': {k:progress[k] for k in ('returned','total')} if progress else None,
            'success_at': saved['success_at'] if saved else None,
            'result': {k:v for k,v in json.loads(saved['result']).items() if k!='progress'} if saved else None}


def report(db, value, channel='', offset=0, limit=12):
    m = app_market(db, value)
    if offset < 0 or limit not in (12, 24, 48) or len(channel) > 255:
        raise ValueError('广告分页或渠道参数无效')
    # Empty country is reserved for observations whose source publishes no geography.
    scope = "store=%s AND app_id=%s AND country IN (%s,'')"
    args = (m['store'], m['app_id'], m['country'])
    channel_sql = "COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.channel')),'null'),'渠道未披露')"
    groups = db.execute(f'''SELECT {channel_sql} channel,COUNT(*) count FROM ad_observations
        WHERE {scope} GROUP BY {channel_sql} ORDER BY count DESC,channel''', args).fetchall()
    eligible = ad_sources.available(m)
    configured = ad_sources.configured(db)
    sources = {}
    for kind in ad_sources.KINDS:
        item = source_status(db,m,kind)
        if kind not in eligible:
            item['status'] = 'unsupported'
        elif kind in ad_channels.SOCIAL and kind not in configured:
            item['status'] = 'needs_auth'
        sources[kind] = item
    counts = dict.fromkeys(ad_channels.PRIMARY,0)
    for group in groups:
        name = ad_channels.canonical(group['channel'])
        counts[name] = counts.get(name,0) + group['count']
    channel = ad_channels.canonical(channel)
    if channel:
        matches = [g['channel'] for g in groups if ad_channels.canonical(g['channel'])==channel]
        scope += f' AND {channel_sql} IN %s' if matches else ' AND 1=0'
        if matches:args += (tuple(matches),)
    observations = db.execute(f'''SELECT id,country,source,keyword,observed_at,placement,evidence_url,payload,
        (SELECT COUNT(*) FROM ad_creative_versions v WHERE v.fingerprint=a.fingerprint) version_count
        FROM ad_observations a WHERE {scope} ORDER BY COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.last_seen')),'null'),
        FROM_UNIXTIME(observed_at,'%%Y-%%m-%%d')) DESC,observed_at DESC,id DESC LIMIT %s OFFSET %s''',
        (*args, limit, offset)).fetchall()
    items = [json.loads(row.pop('payload')) | row for row in observations]
    for item in items:
        item['channel'] = ad_channels.canonical(item.get('channel'))
    reports = []
    if m['store'] == 'apple':
        reports = db.execute('''SELECT report_date,campaign,keyword,impressions,taps,installs,spend,currency,source
            FROM ad_reports WHERE app_id=%s AND country=%s ORDER BY report_date DESC,id DESC LIMIT 30''',
            (m['app_id'], m['country'])).fetchall()
    channels = [{'channel':name,'count':count,'providers':ad_channels.PRIMARY.get(name,())} for name,count in counts.items()]
    return {'items': items, 'channels': channels, 'total': counts.get(channel,0) if channel else sum(counts.values()),
            'sources': list(sources.values()), 'reports': reports, 'schedule':schedule_for(db,m)}
