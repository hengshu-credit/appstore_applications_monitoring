"""Read-only 市场数据 API archive, durable backfill queue and CSV intake.

Credentials stay in source_accounts. No website password is sent to the API.
All network paths are generated here; redirects and arbitrary URLs are refused.
"""
import csv
import gzip
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from .db import add_apps, dump
from .ingest import disk_check, valid_id

KINDS = ('metadata', 'estimates', 'ranks', 'reviews')


class SourceError(ValueError):
    def __init__(self, code, message, delay=0):
        super().__init__(message)
        self.code, self.delay = code, delay


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def tomorrow():
    return datetime.combine(date.fromisoformat(utc_day()) + timedelta(days=1),
                            datetime.min.time(), timezone.utc).timestamp() + 1


def retry_delay(value):
    try:
        return max(60, int(value))
    except (ValueError, TypeError):
        try:
            return max(60, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 900


def get(db, client, path, params=None, daily_limit=1000):
    if not re.fullmatch(r'/(?:usage|data/(?:countries|categories|stores)|products/(?:mine|[0-9]+|(?:apple|google_play)/[A-Za-z0-9_.]+)|reports/estimates|reviews|ranks/[A-Za-z0-9_.,:/-]+)', path):
        raise ValueError('不支持的 市场数据 API 路径')
    day, now = utc_day(), time.time()
    with db:
        db.execute('INSERT INTO marketdata_budget(day) VALUES(%s) ON DUPLICATE KEY UPDATE day=marketdata_budget.day', (day,))
        budget = db.execute('SELECT * FROM marketdata_budget WHERE day=%s FOR UPDATE', (day,)).fetchone()
        cooldown = db.execute('SELECT MAX(next_at) next_at FROM marketdata_budget').fetchone()['next_at']
        if cooldown > now:
            raise SourceError('rate_limited', '市场数据 请求冷却中', cooldown - now)
        if budget['used'] >= min(daily_limit, budget['source_limit']):
            raise SourceError('rate_limited', '市场数据 今日请求额度已用完', tomorrow() - now)
        db.execute('UPDATE marketdata_budget SET used=used+1,next_at=%s WHERE day=%s', (now + 2, day))
    started=time.monotonic()
    from .collection_lineage import record_http
    try:
        response = client.get(client.source_profile['origin'] + path, params=params or {}, timeout=(10, 90), allow_redirects=False)
    except requests.RequestException:
        record_http(db,'all','',elapsed=time.monotonic()-started,source='marketdata')
        raise SourceError('network_error', '市场数据 网络请求失败', 300) from None
    record_http(db,'all','',response.status_code,time.monotonic()-started,source='marketdata')
    try:
        # Official account-wide usage includes requests made outside this collector.
        usage = response.headers.get('X-Request-Usage')
        limit = response.headers.get('X-Request-Limit')
        if usage is not None and limit is not None and int(usage) >= 0 and int(limit) > 0:
            db.execute('UPDATE marketdata_budget SET used=GREATEST(used,%s),source_limit=%s WHERE day=%s', (int(usage), int(limit), day))
    except (ValueError, TypeError):
        pass
    if response.status_code in (420, 429):
        delay = tomorrow() - now if response.status_code == 420 else retry_delay(response.headers.get('Retry-After'))
        db.execute('UPDATE marketdata_budget SET next_at=GREATEST(next_at,%s) WHERE day=%s', (time.time() + delay, day))
        raise SourceError('rate_limited', '市场数据 额度或频率限制，等待续采', delay)
    errors = {401: ('invalid_token', '市场数据 Token 无效或已过期'),
              402: ('access_denied', '市场数据 当前额度或套餐不支持该数据'),
              403: ('access_denied', '市场数据 当前账户无该数据权限'),
              404: ('not_found', '市场数据 无该数据或榜单快照已过期')}
    if response.status_code in errors:
        raise SourceError(*errors[response.status_code])
    if response.status_code != 200:
        raise SourceError('network_error' if response.status_code >= 500 or response.status_code == 202 else 'source_changed',
                          '市场数据 返回 HTTP ' + str(response.status_code), 300)
    try:
        value = response.json()
        if not isinstance(value, (dict, list)) or (isinstance(value, dict) and 'status' in value and 'message' in value):
            raise ValueError()
    except ValueError:
        raise SourceError('source_changed', '市场数据 响应不是有效数据') from None
    client._marketdata_raw = response.content
    return value


def configure(client, saved):
    token = saved.get('access_token')
    if not token:
        raise SourceError('api_token_required', '账密已保存；API 采集还需 Personal Access Token')
    client.headers.update(Authorization='Bearer ' + token, **{'User-Agent': 'Guanxing-MarketData/1.0'})


def authorize(db, client, saved):
    try:
        configure(client, saved)
        get(db, client, '/usage')
        return 'authorized'
    except SourceError as exc:
        return exc.code


def client_for(db, cfg):
    with db.named_lock('data-source-marketdata'):
        from .data_sources import cipher, session
        from cryptography.fernet import InvalidToken
        row = db.execute("SELECT secret,status FROM source_accounts WHERE provider='marketdata'").fetchone()
        if not row:
            raise SourceError('api_token_required', '请先配置 市场数据 Token')
        if row['status']=='endpoint_changed':raise SourceError('api_token_required','接入地址变更后须重新保存凭据')
        try:
            saved = json.loads(cipher(cfg).decrypt(row['secret'].encode()))
        except (ValueError, OSError, InvalidToken):
            raise SourceError('key_unavailable', '市场数据 凭据密钥不可用') from None
        client = session(cfg)
        try:
            from .source_profiles import bind
            bind(client,cfg,'marketdata',db)
            configure(client, saved)
        except Exception:
            client.close()
            raise
        return client

def archive(db, cfg, kind, value, provenance, records, csv_text=False, raw_bytes=None):
    raw = raw_bytes if isinstance(raw_bytes, bytes) else value.encode('utf-8') if csv_text else dump(value).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    from .objects import for_config
    relative=for_config(cfg).put(raw,kind='marketdata',mime='text/csv' if csv_text else 'application/json',compress=True)
    return db.execute('''INSERT INTO marketdata_artifacts(sha256,path,kind,provenance,records,created_at)
        VALUES(%s,%s,%s,%s,%s,%s)''', (digest, relative, kind, dump(provenance), records, time.time())).lastrowid


def artifact_path(db, cfg, identifier):
    row = db.execute('SELECT * FROM marketdata_artifacts WHERE id=%s', (identifier,)).fetchone()
    if not row:
        raise ValueError('归档不存在')
    from .objects import for_config
    return for_config(cfg).get(row['path'])


def enqueue(db, run_id, kind, path, params=None):
    enqueue_many(db, run_id, [(kind, path, params or {})])


def enqueue_many(db, run_id, requests):
    blocks = {r['kind']:r['error'] for r in db.execute('SELECT kind,error FROM marketdata_blocks WHERE run_id=%s', (run_id,)).fetchall()}
    sql = '''INSERT INTO marketdata_jobs(run_id,fingerprint,kind,path,params,status,error) VALUES(%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE fingerprint=marketdata_jobs.fingerprint'''
    batch = []
    for kind, path, params in requests:
        fingerprint = hashlib.sha256(json.dumps([kind, path, params], sort_keys=True).encode()).hexdigest()
        batch.append((run_id, fingerprint, kind, path, dump(params), 'blocked' if kind in blocks else 'pending', blocks.get(kind)))
        if len(batch) >= 500:
            db.executemany(sql, batch)
            batch = []
    if batch:db.executemany(sql, batch)


def validate_plan(value):
    try:
        start, end = date.fromisoformat(value['start_date']), date.fromisoformat(value['end_date'])
        stop = datetime.fromisoformat(value['stop_at'].replace('Z', '+00:00'))
        if stop.tzinfo is None or stop.timestamp() <= time.time():
            raise ValueError()
        if not date(2008, 1, 1) <= start <= end <= date.fromisoformat(utc_day()):
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError('请填写有效历史日期范围和带时区的未来试用截止时间') from None
    result = dict(start_date=start.isoformat(), end_date=end.isoformat(), stop_at=stop.isoformat())
    for key, pattern, maximum in [('products', r'[1-9][0-9]{0,19}', 10000), ('categories', r'[1-9][0-9]{0,9}', 1000), ('countries', r'[a-z]{2}', 300)]:
        entries = value.get(key, [])
        if not isinstance(entries, list) or len(entries) > maximum or any(not isinstance(x, str) or not re.fullmatch(pattern, x) for x in entries):
            raise ValueError('无效的 ' + key)
        result[key] = list(dict.fromkeys(entries))
    kinds = value.get('kinds', list(KINDS))
    if not isinstance(kinds, list) or not kinds or any(k not in KINDS for k in kinds):
        raise ValueError('请选择有效的数据类型')
    result['kinds'] = list(dict.fromkeys(kinds))
    for key in ('charts', 'daily_charts', 'expand_products'):
        result[key] = value.get(key, True)
        if type(result[key]) is not bool:
            raise ValueError('启用状态须为布尔值')
    result['daily_limit'] = value.get('daily_limit', 1000)
    if type(result['daily_limit']) is not int or not 1 <= result['daily_limit'] <= 50000:
        raise ValueError('每日请求上限须为 1–50000')
    return result, stop.timestamp()


def windows(start, end):
    # Newest historical periods first. 31-day units are independently resumable.
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    while end >= start:
        first = max(start, end - timedelta(days=30))
        yield first.isoformat(), end.isoformat()
        end = first - timedelta(days=1)


def product_jobs(db, run_id, settings, identifier):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(identifier)):
        return
    identifier = str(identifier)
    inserted = db.execute('''INSERT INTO marketdata_products(run_id,product_id) VALUES(%s,%s)
        ON DUPLICATE KEY UPDATE product_id=marketdata_products.product_id''', (run_id, identifier)).rowcount
    if not inserted:
        return
    jobs=[]
    if 'metadata' in settings['kinds']:
        jobs.append(('metadata', '/products/' + identifier, {}))
    for start, end in windows(settings['start_date'], settings['end_date']):
        countries = {'countries': ';'.join(settings['countries'])} if settings['countries'] else {}
        if 'estimates' in settings['kinds']:
            jobs.append(('estimates', '/reports/estimates', dict(products=identifier, start_date=start, end_date=end, group_by='date,country', **countries)))
        if 'ranks' in settings['kinds']:
            # An omitted ranks country defaults to US, so use the API country catalog.
            resolved = settings.get('resolved_countries', settings['countries'])
            if resolved:
                jobs.append(('ranks', f'/ranks/{identifier}/daily/{start}/{end}', dict(countries=';'.join(resolved), tz='utc', filter=1000)))
        if 'reviews' in settings['kinds']:
            params = dict(products=identifier, start=start, end=end, count=500, page=1, sort='date')
            if settings['countries']:
                params['countries'] = ','.join(settings['countries'])
            jobs.append(('reviews', '/reviews', params))
    enqueue_many(db, run_id, jobs)


def create_run(db, value):
    settings, stop = validate_plan(value)
    with db.named_lock('market-data-plan'), db:
        if db.execute("SELECT 1 FROM marketdata_runs WHERE status IN ('running','paused') AND stop_at>%s LIMIT 1", (time.time(),)).fetchone():
            raise ValueError('已有采集计划，请先停止或继续现有计划')
        identifier = db.execute("INSERT INTO marketdata_runs(settings,status,created_at,stop_at) VALUES(%s,'running',%s,%s)", (dump(settings), time.time(), stop)).lastrowid
        for kind, path in [('countries','/data/countries'), ('categories','/data/categories'), ('stores','/data/stores'), ('mine','/products/mine')]:
            enqueue(db, identifier, kind, path, {'include_inactive':'true'} if kind == 'mine' else {})
    return report(db)


def catalog(db, cfg, run_id, kind):
    row = db.execute("SELECT artifact_id FROM marketdata_jobs WHERE run_id=%s AND kind=%s AND status='done' ORDER BY id DESC LIMIT 1", (run_id, kind)).fetchone()
    if not row:
        return None
    return json.loads(artifact_path(db, cfg, row['artifact_id']))


def objects(value):
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return [v for v in value.values() if isinstance(v, dict)] if isinstance(value, dict) else []


def schedule(db, cfg, run):
    settings = json.loads(run['settings'])
    countries = catalog(db, cfg, run['id'], 'countries')
    categories = catalog(db, cfg, run['id'], 'categories')
    if countries is None and not settings['countries']:
        return settings
    resolved = settings['countries'] or sorted({v['iso'].lower() for v in objects(countries) if re.fullmatch('[A-Za-z]{2}', str(v.get('iso', '')))})
    if not resolved:
        raise SourceError('source_changed', '国家目录为空，无法展开采集范围')
    if 'resolved_countries' not in settings:
        settings['resolved_countries'] = resolved
        for product in settings['products']:
            product_jobs(db, run['id'], settings, product)
        # Mine may have completed before country discovery on a retried run.
        for product in objects(catalog(db, cfg, run['id'], 'mine') or {}):
            product_jobs(db, run['id'], settings, product.get('id'))
        db.execute('UPDATE marketdata_runs SET settings=%s WHERE id=%s', (dump(settings), run['id']))
    day = utc_day()
    if settings['charts'] and categories is not None and run['last_chart_day'] != day and (not run['last_chart_day'] or settings['daily_charts']):
        selected = [str(c.get('id')) for c in objects(categories) if c.get('store') in ('apple', 'google_play') and c.get('active', True) and re.fullmatch(r'[1-9][0-9]*', str(c.get('id', '')))]
        selected = settings['categories'] or sorted(set(selected), key=int)
        if not selected:
            raise SourceError('source_changed', '分类目录为空，无法展开榜单')
        pending = {r['path'] for r in db.execute("SELECT path FROM marketdata_jobs WHERE run_id=%s AND kind='charts' AND status='pending'", (run['id'],)).fetchall()}
        subcategories = {str(c.get('id')):c.get('subcategories', ['free','paid','topgrossing']) for c in objects(categories)}
        # Keep unvisited targets ahead of daily refreshes. _day is the queue date;
        # actual snapshot time always comes from the source response.
        def chart_jobs():
            for country in resolved:
                for category in selected:
                    for sub in subcategories.get(category, ['free','paid','topgrossing']):
                        if sub not in ('free','paid','topgrossing'):continue
                        path=f'/ranks/snapshots/current/{country}/{category}/{sub}'
                        if path not in pending:
                            yield 'charts', path, {'count':1000, 'start':0, '_day':day}
        enqueue_many(db, run['id'], chart_jobs())
        db.execute('UPDATE marketdata_runs SET last_chart_day=%s WHERE id=%s', (day, run['id']))
    return settings


def import_products(db, products):
    rows = [];links=[]
    for p in products:
        store = {'apple':'apple', 'google_play':'google'}.get(p.get('store'))
        identifier = str(p.get('ref_no') or p.get('vendor_identifier') or '') if store == 'apple' else str(p.get('package_name') or p.get('vendor_identifier') or '')
        if store and valid_id(store, identifier):
            rows.append((store, identifier, p.get('name'), None))
            if re.fullmatch(r'[1-9][0-9]{0,19}',str(p.get('id',''))):links.append((str(p['id']),store,identifier))
    if links:db.executemany('''INSERT INTO marketdata_product_links VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE store=VALUES(store),app_id=VALUES(app_id)''',links)
    return add_apps(db, rows, 'marketdata') if rows else 0


def expand(db, run, settings, job, value, currency=None):
    params = json.loads(job['params'])
    kind = job['kind']
    products = []
    if kind=='estimates':save_estimate_facts(db,params.get('products'),value,currency)
    if kind == 'charts':
        if not isinstance(value, dict) or not {'entries','page_start','total_count'} <= value.keys():
            raise SourceError('source_changed', '榜单响应缺少列表或分页信息')
        products = value.get('entries', [])
        if not isinstance(products, list) or any(not isinstance(p, dict) for p in products):
            raise SourceError('source_changed', '榜单响应缺少应用列表')
        start = int(value.get('page_start', params['start']))
        total = int(value.get('total_count', len(products)))
        if start != params['start'] or total < start + len(products) or (not products and start < total):
            raise SourceError('source_changed', '榜单分页未前进')
        if start + len(products) < total:
            timestamp = str(value.get('timestamp', ''))[:13]
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}', timestamp):
                raise SourceError('source_changed', '榜单缺少快照时间，无法安全翻页')
            path=job['path'].replace('/snapshots/current/', '/snapshots/'+timestamp+'/')
            enqueue(db, run['id'], kind, path, params | {'start':start + len(products)})
    elif kind == 'mine':
        products = objects(value)
    elif kind == 'metadata':
        if not isinstance(value, dict) or not value.get('id'):
            raise SourceError('source_changed', '应用元数据缺少 ID')
        products = [value]
    elif kind == 'reviews':
        if not isinstance(value, dict) or not {'reviews','this_page','pages'} <= value.keys() or not isinstance(value.get('reviews'), list):
            raise SourceError('source_changed', '评论响应缺少列表或分页信息')
        page, pages = int(value.get('this_page', params['page'])), int(value.get('pages', 0))
        if page != params['page'] or pages < 0 or (pages == 0 and value['reviews']) or (page < pages and not value['reviews']):
            raise SourceError('source_changed', '评论分页未前进')
        if pages > 500 and params['start'] < params['end']:
            start, end = date.fromisoformat(params['start']), date.fromisoformat(params['end'])
            middle = start + (end-start)//2
            enqueue(db, run['id'], kind, job['path'], params | {'end':middle.isoformat(), 'page':1})
            enqueue(db, run['id'], kind, job['path'], params | {'start':(middle+timedelta(days=1)).isoformat(), 'page':1})
            return record_count(kind, value)
        if page < pages:
            if page >= 500:
                raise SourceError('source_changed', '评论超过 API 500 页限制；请缩小日期范围重新采集')
            enqueue(db, run['id'], kind, job['path'], params | {'page':page + 1})
    import_products(db, products)
    if kind in ('mine', 'charts') and settings['expand_products'] and 'resolved_countries' in settings:
        for product in products:
            product_jobs(db, run['id'], settings, product.get('id'))
    return record_count(kind, value)


def save_estimate_facts(db, product_id, value, currency=None):
    from . import metric_facts
    product=db.execute('SELECT store,app_id FROM marketdata_product_links WHERE product_id=%s',(str(product_id),)).fetchone()
    if not product:return 0
    rows=[]
    def walk(node,day=None,country=None):
        if not isinstance(node,dict):return
        day=node.get('date',day);country=node.get('iso',country)
        if ('downloads' in node or 'revenue' in node) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(day or '')) and re.fullmatch('[A-Za-z]{2}',str(country or '')):
            row=product|dict(country=country.lower(),date=day,is_estimate=True)
            if node.get('downloads') is not None:row['downloads']=node['downloads']
            unit=node.get('currency',currency)
            # Do not label an unspecified revenue currency as USD or sum it with known currencies.
            if node.get('revenue') is not None and isinstance(unit,str) and re.fullmatch('[A-Z]{3}',unit):row.update(revenue=node['revenue'],currency=unit)
            if 'downloads' in row or 'revenue' in row:rows.append(row)
            return
        for key,child in node.items():
            walk(child,key if re.fullmatch(r'\d{4}-\d{2}-\d{2}',key) else day,key if re.fullmatch('[A-Z]{2}',key) else country)
    walk(value)
    if rows:metric_facts.save(db,metric_facts.prepare({'rows':rows}))
    return len(rows)


def record_count(kind, value):
    if kind == 'metadata':return 1
    if kind == 'charts':return len(value['entries'])
    if kind == 'reviews':return len(value['reviews'])
    if kind == 'ranks':
        if not isinstance(value, dict) or not isinstance(value.get('data'), list):
            raise SourceError('source_changed', '历史排名响应缺少 data 列表')
        return sum(sum(p is not None for p in s.get('positions', [])) for s in value['data'])
    if kind == 'estimates':
        if not isinstance(value, (dict, list)):
            return 0
        if isinstance(value, dict) and ('downloads' in value or 'revenue' in value):return 1
        return sum(record_count(kind, v) for v in (value.values() if isinstance(value, dict) else value))
    return len(value)


def work(cfg, db, limit=5):
    from .collection_limits import settings as request_settings
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('批次请求数须为 1–1000')
    result = dict(processed=0, error=0)
    try:
        with db.named_lock('marketdata-worker'):
            db.execute("INSERT INTO schedules VALUES('marketdata-heartbeat',%s) ON DUPLICATE KEY UPDATE next_at=VALUES(next_at)", (time.time(),))
            for _ in range(limit):
                db.execute("UPDATE marketdata_runs SET status='expired' WHERE status IN ('running','paused') AND stop_at<=%s", (time.time(),))
                run = db.execute("SELECT * FROM marketdata_runs WHERE status='running' ORDER BY id LIMIT 1").fetchone()
                if not run or request_settings(db, cfg)['paused']:
                    break
                try:
                    with db:
                        settings = schedule(db, cfg, run)
                except (SourceError, OSError, ValueError, TypeError, KeyError) as exc:
                    message=str(exc) if isinstance(exc,SourceError) else '目录归档不可读取或格式异常，请检查归档文件'
                    db.execute("UPDATE marketdata_runs SET status='paused',last_error=%s WHERE id=%s", (message, run['id']))
                    break
                budget = db.execute('SELECT used FROM marketdata_budget WHERE day=%s', (utc_day(),)).fetchone()
                preferred = ('charts', 'metadata', 'estimates', 'ranks', 'reviews')[(budget or {'used':0})['used'] % 5]
                job = db.execute("""SELECT * FROM marketdata_jobs WHERE run_id=%s AND status='pending' AND due<=%s
                    ORDER BY CASE WHEN kind IN ('countries','categories','stores','mine') THEN 0 WHEN kind=%s THEN 1 ELSE 2 END,id LIMIT 1""", (run['id'], time.time(), preferred)).fetchone()
                if not job:
                    if not (settings['charts'] and settings['daily_charts']) and not db.execute("SELECT 1 FROM marketdata_jobs WHERE run_id=%s AND status!='done' LIMIT 1", (run['id'],)).fetchone():
                        db.execute("UPDATE marketdata_runs SET status='complete' WHERE id=%s", (run['id'],))
                    break
                params = json.loads(job['params'])
                if run['stop_at'] <= time.time() or request_settings(db, cfg)['paused']:
                    break
                try:
                    with client_for(db, cfg) as client:
                        value = get(db, client, job['path'], {k:v for k,v in params.items() if not k.startswith('_')}, settings['daily_limit'])
                        raw = getattr(client, '_marketdata_raw', None)
                    with db:
                        records = expand(db, run, settings, job, value, client.source_profile.get('currency'))
                        artifact = archive(db, cfg, job['kind'], value, dict(route=job['path'], params=params, run_id=run['id']), records, raw_bytes=raw)
                        db.execute("UPDATE marketdata_jobs SET status='done',artifact_id=%s,error=NULL,attempts=attempts+1 WHERE id=%s", (artifact, job['id']))
                    result['processed'] += 1
                except (SourceError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    code = exc.code if isinstance(exc, SourceError) else 'source_changed'
                    message = str(exc) if isinstance(exc, SourceError) else '归档或数据解析失败，请检查磁盘和数据格式'
                    if code in ('invalid_token', 'api_token_required', 'key_unavailable'):
                        db.execute("UPDATE marketdata_runs SET status='paused',last_error=%s WHERE id=%s", (message, run['id']))
                        db.execute("UPDATE source_accounts SET status=%s WHERE provider='marketdata'", (code,))
                    elif code == 'access_denied':
                        db.execute('INSERT INTO marketdata_blocks VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE error=marketdata_blocks.error', (run['id'], job['kind'], message))
                        db.execute("UPDATE marketdata_jobs SET status='blocked',error=%s WHERE run_id=%s AND kind=%s AND status='pending'", (message, run['id'], job['kind']))
                    elif code == 'rate_limited':
                        db.execute('UPDATE marketdata_jobs SET due=%s,error=%s WHERE id=%s', (time.time()+exc.delay, message, job['id']))
                        break
                    else:
                        retry = code == 'network_error' and job['attempts'] < 4
                        db.execute('UPDATE marketdata_jobs SET status=%s,due=%s,error=%s,attempts=attempts+1 WHERE id=%s', ('pending' if retry else 'error', time.time()+300, message, job['id']))
                    result['error'] += 1
                    if code in ('invalid_token', 'api_token_required', 'key_unavailable'):
                        break
    except RuntimeError:
        result['busy'] = True
    return result


def control(db, identifier, action):
    if action not in ('pause', 'resume', 'stop', 'retry'):
        raise ValueError('未知操作')
    # Serialize changes with requests; no post-pause request can start.
    with db.named_lock('marketdata-worker'), db:
        run = db.execute('SELECT * FROM marketdata_runs WHERE id=%s', (identifier,)).fetchone()
        if not run:
            raise ValueError('采集计划不存在')
        if run['status'] in ('stopped','complete','expired') and action!='stop':
            raise ValueError('计划已结束，请新建计划')
        if action in ('resume', 'retry') and (run['stop_at'] <= time.time() or run['status'] in ('stopped', 'complete')):
            raise ValueError('计划已结束，请新建计划')
        if action == 'retry':
            db.execute('DELETE FROM marketdata_blocks WHERE run_id=%s', (identifier,))
            db.execute("UPDATE marketdata_jobs SET status='pending',attempts=0,due=0,error=NULL WHERE run_id=%s AND status IN ('error','blocked')", (identifier,))
        db.execute('UPDATE marketdata_runs SET status=%s,last_error=NULL WHERE id=%s', ({'pause':'paused','resume':'running','stop':'stopped','retry':'running'}[action], identifier))
    return report(db)


def report(db):
    runs = db.execute('SELECT * FROM marketdata_runs ORDER BY id DESC LIMIT 10').fetchall()
    for run in runs:
        run['settings'] = json.loads(run['settings'])
        run['counts'] = db.execute('SELECT kind,status,COUNT(*) count FROM marketdata_jobs WHERE run_id=%s GROUP BY kind,status', (run['id'],)).fetchall()
    heartbeat=db.execute("SELECT next_at FROM schedules WHERE name='marketdata-heartbeat'").fetchone()
    return dict(runs=runs, heartbeat=heartbeat['next_at'] if heartbeat else None, budget=db.execute('SELECT * FROM marketdata_budget WHERE day=%s', (utc_day(),)).fetchone(),
                artifacts=db.execute('SELECT id,kind,records,sha256,created_at,provenance FROM marketdata_artifacts ORDER BY id DESC LIMIT 50').fetchall(),
                errors=db.execute("SELECT run_id,kind,status,error FROM marketdata_jobs WHERE error IS NOT NULL ORDER BY id DESC LIMIT 20").fetchall())


def import_csv(db, cfg, value):
    content = value.get('content')
    if not isinstance(content, str) or len(content.encode('utf-8')) > 10 * 1024 * 1024:
        raise ValueError('请选择不超过 10 MB 的 UTF-8 CSV 文件')
    reader = csv.reader(io.StringIO(content.lstrip('\ufeff')), strict=True)
    try:
        header = next(reader)
        if len(header) < 2 or len(set(header)) != len(header):
            raise ValueError('CSV 须包含至少两列且列名不重复')
        count = 0
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError('CSV 列数不一致')
            count += 1
        if not count:
            raise ValueError('CSV 没有数据行')
    except (StopIteration, csv.Error):
        raise ValueError('无法解析 CSV 数据集') from None
    with db:
        identifier = archive(db, cfg, 'csv', content, dict(source='市场数据 user export', columns=header), count, csv_text=True)
    return dict(id=identifier, records=count, note='已完整归档 CSV 原始列；未将估算值混入官方实测指标')
