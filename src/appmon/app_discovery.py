"""On-demand public-store search; imports verify canonical identities server-side."""
import re
import time
from urllib.parse import urlsplit, parse_qs

from .db import add_apps
from .ingest import valid_id
from .intelligence import market, text
from .keyword_stores import KeywordClient, ordered_apps
from .stores import Client
from .app_collection import collect


def identity(store, query):
    query = text(query, 1000)
    if '://' in query:
        url = urlsplit(query)
        if url.scheme != 'https' or url.username or url.password or url.port not in (None, 443):
            raise ValueError('请输入 HTTPS 官方商店链接')
        if store == 'apple' and url.hostname == 'apps.apple.com':
            match = re.search(r'/id([0-9]+)/?$', url.path)
            query = match[1] if match else ''
        elif store == 'google' and url.hostname == 'play.google.com' and url.path == '/store/apps/details':
            query = parse_qs(url.query).get('id', [''])[0]
        else:
            raise ValueError('链接与所选商店不匹配，请使用官方应用详情链接')
        if not valid_id(store, query):
            raise ValueError('链接中没有有效应用 ID')
    return query if len(query) <= 512 and valid_id(store, query) else None


def search(db, cfg, value, client=None):
    m = market(value, require_app=False)
    query = text(value.get('query'), 1000)
    app_id = identity(m['store'], query)
    if app_id:
        result = (client or Client(cfg, db)).fetch(m | {'app_id': app_id})
        items = [dict(app_id=app_id, **(result.payload or {}))] if result.outcome == 'ok' else []
        source = 'public-store-detail'
    else:
        if len(query) > 255:
            raise ValueError('名称搜索最多255字')
        result = (client or KeywordClient(cfg, db)).search(m | {'keyword': query, 'depth': 30})
        items, source = ordered_apps(m['store'], result.apps)[:30], result.source
    for item in items:
        item['in_library'] = bool(db.execute('SELECT 1 FROM apps WHERE store=%s AND app_id=%s',
                                             (m['store'], item['app_id'])).fetchone())
    return {'items': items, 'market': m, 'source': source, 'observed_at': time.time(),
            'scope': '公开商店返回最多30条；搜索预览不会入库。选择应用后验证当前市场并安排采集。'}


def import_app(db, cfg, value, client=None):
    m = market(value)
    result = (client or Client(cfg, db)).fetch(m)
    if result.outcome != 'ok' or not result.payload or not result.payload.get('name'):
        raise ValueError('当前市场未查到可验证的应用，未执行入库；请核对地区和 ID')
    p = result.payload
    with db:
        added = add_apps(db, [(m['store'], m['app_id'], p['name'], p.get('category_id') or p.get('category'))], 'manual-store-search')
    # A durable plan schedules the normal snapshot/raw-response pipeline. Repeated
    # clicks reuse the active plan and the application's unique store/id key.
    return {'app': m, 'added': added, 'collection': collect(db, m)}
