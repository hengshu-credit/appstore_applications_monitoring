"""Official store category IDs with explicit legacy aliases; no inferred classes."""
import json
from importlib.resources import files

CATALOG = json.loads(files('appmon').joinpath('store_catalog.json').read_text(encoding='utf-8'))
APPLE_MARKETS = json.loads(files('appmon').joinpath('apple_markets.json').read_text(encoding='utf-8'))
APPLE_COUNTRIES = set(json.loads(files('appmon').joinpath('apple_countries.json').read_text(encoding='utf-8'))['countries'])
BY_ID = {store: {c['id']: c for c in rows} for store, rows in CATALOG['categories'].items()}
BY_ALIAS = {store: {alias.casefold(): c['id'] for c in rows for alias in c['aliases']}
            for store, rows in CATALOG['categories'].items()}


def category_id(store, value):
    if value is None:
        return None
    return BY_ALIAS.get(store, {}).get(str(value).strip().casefold())


def listing_category(store, payload):
    primary = category_id(store, payload.get('category_id')) or category_id(store, payload.get('category'))
    if store == 'apple' and primary == '6014':
        for value in payload.get('genre_ids') or []:
            candidate = BY_ID['apple'].get(str(value))
            if candidate and candidate['parent_id'] == '6014':
                return candidate['id']
    return primary or payload.get('category')


def category_aliases(store, value):
    if store not in BY_ID:
        raise ValueError('分类筛选必须选择 Google Play 或 App Store')
    if value == '__apps__' or (store == 'google' and value == 'APPLICATION'):
        chosen = [c for c in BY_ID[store].values() if c['group'] == 'apps']
    elif value == '__unknown__':
        chosen = list(BY_ID[store].values())
    else:
        identifier = category_id(store, value)
        if not identifier:
            raise ValueError('该商店不存在此官方分类')
        chosen = [c for c in BY_ID[store].values() if c['id'] == identifier or c['parent_id'] == identifier]
    return sorted({a for c in chosen for a in c['aliases']})


def public_catalog():
    from .charts import capabilities
    return CATALOG | {'countries': [c | {'apple_supported': c['code'] in APPLE_COUNTRIES} for c in CATALOG['countries']],
                      'charts':{store:capabilities(store) for store in ('apple','google')},
                      'country_scope': '国家/地区是公开商店请求地区；可用性以实际采集结果为准。Apple 仅开放当前支持的地区。'}


def category_counts(db, store):
    if store not in BY_ID:
        raise ValueError('Unknown store')
    counts = {identifier: 0 for identifier in BY_ID[store]}
    total, unknown = 0, 0
    for row in db.execute('SELECT category,count(*) count FROM apps WHERE store=%s GROUP BY category', (store,)).fetchall():
        total += row['count']
        identifier = category_id(store, row['category'])
        if identifier:
            counts[identifier] += row['count']
        else:
            unknown += row['count']
    direct = dict(counts)
    for identifier, count in direct.items():
        parent = BY_ID[store][identifier]['parent_id']
        if parent:
            counts[parent] += count
    return {'store': store, 'counts': counts, 'total': total, 'unknown': unknown,
            'apps': sum(direct[c['id']] for c in BY_ID[store].values() if c['group']=='apps'),
            'scope': '已知应用池，按商店统计；不限定国家，不代表当前在架量。'}
