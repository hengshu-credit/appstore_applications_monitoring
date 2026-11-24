"""Read publicly visible advertiser-side creatives and ranking samples."""
import re
from datetime import date
from urllib.parse import quote, urlsplit

from .stores import Client
from . import ad_channels


KINDS = {
    'ad_creatives': 'AppGoblin 推广素材',
    'ad_rankings': 'AppGoblin 广告主榜',
    'ad_apple': 'Apple 广告资料库',
    'ad_google': 'Google 广告透明度中心',
    **ad_channels.SOCIAL,
}
GOBLIN = 'https://appgoblin.info'
BUYERS = GOBLIN + '/top-mobile-advertisers'
APPLE = 'https://adrepository.apple.com/api/v1/ad-repository-ads'
# Published by /api/v1/countries-or-regions on the same public repository.
APPLE_MARKETS = frozenset('at be bg hr cy cz dk ee fi fr de gr hu ie it lv lu nl pl pt ro sk si es se'.split())
APPLE_PLACEMENTS = {
    'APPSTORE_SEARCH_RESULTS': '搜索结果',
    'APPSTORE_TODAY_TAB': 'Today 标签页',
    'APPSTORE_SEARCH_TAB': '搜索标签页',
    'APPSTORE_PRODUCT_PAGES': '产品页',
}


def available(m):
    kinds = ['ad_creatives', 'ad_rankings', 'ad_google']
    if m['store'] == 'apple' and m['country'] in APPLE_MARKETS:
        kinds.append('ad_apple')
    kinds.extend(k for k in ad_channels.SOCIAL if k not in ad_channels.MARKETS or m['country'] in ad_channels.MARKETS[k])
    return kinds


def configured(db):
    return {r['provider'] for r in db.execute('SELECT provider FROM source_accounts WHERE provider IN %s',
            (tuple(ad_channels.SOCIAL),)).fetchall()}


def runnable(db, m):
    accounts = configured(db)
    return [k for k in available(m) if k not in ad_channels.SOCIAL or k in accounts]


def link(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in ('https', 'http') and url.hostname and url.username is None and '\\' not in value:
            return value
    except ValueError:
        pass
    return None


def source_day(value):
    return date.fromisoformat(value[:10]).isoformat() if value else None


def apple_ad(row, m):
    if str(row['appId']) != m['app_id'] or row['countryOrRegion'].lower() != m['country']:
        raise ValueError('Apple 广告记录的应用或地区与请求不符')
    identifier = row['adId']
    if not isinstance(identifier, str) or not identifier:
        raise ValueError('Apple 广告记录缺少广告 ID')
    banner = row.get('adBanner') or {}
    assets = []
    seen = set()
    def asset(kind, url):
        if link(url) and url not in seen:
            seen.add(url)
            assets.append({'type': kind, 'url': url})
    for item in row.get('adAssets') or []:
        asset('video', item.get('videoUrl'))
        asset('image', item.get('pictureUrl'))
    asset('image', banner.get('iconPictureUrl'))
    return {
        'ad_id': identifier, 'channel': 'App Store', 'source': KINDS['ad_apple'],
        'country': m['country'], 'placement': APPLE_PLACEMENTS.get(row['placement'], row['placement']),
        'format': row.get('format'), 'creative_language': row.get('defaultLanguageTag'),
        'advertiser': row.get('developerName'), 'title': banner.get('subtitle') or row.get('appName'),
        'body': banner.get('promotionalText') or banner.get('shortDescription'), 'assets': assets,
        'first_seen': source_day(row.get('firstImpressionDate')), 'last_seen': source_day(row.get('lastImpressionDate')),
        'evidence_url': APPLE + '/' + quote(identifier, safe='') + '?datePreset=LAST_90_DAYS',
        'store_url': f"https://apps.apple.com/{m['country']}/app/id{m['app_id']}",
    }


def apple_page(get, m, offset):
    query = f"type==APP;id=={m['app_id']};countryOrRegion=in=({m['country'].upper()});datePreset==LAST_90_DAYS"
    # The public API echoes a zero-based page number, not a record offset.
    page = get(APPLE, {'ql': query, 'offset': offset, 'limit': 50})
    rows, pagination = page['data'], page['pagination']
    size, total = pagination['itemsPerPage'], pagination['totalResults']
    if (not isinstance(rows, list) or type(total) is not int or total < 0
            or type(size) is not int or size <= 0 or pagination['startIndex'] != offset):
        raise ValueError('Apple 广告分页信息异常')
    if len(rows) != min(size, max(0, total - offset * size)):
        raise ValueError('Apple 广告分页条数与来源声明的范围不符')
    return {'items': [apple_ad(row, m) for row in rows], 'total': total,
            'next_page': offset+1 if (offset+1)*size < total else None,
            'period_start': page.get('dataStartDate'), 'period_end': page.get('dataEndDate')}


def public_page_node(page):
    """Decode the public SvelteKit page's indexed JSON, without evaluating scripts."""
    if page.get('type') != 'data':
        raise ValueError('AppGoblin 没有返回公开页面数据')
    for node in reversed(page.get('nodes') or []):
        values = (node or {}).get('data')
        if not values or not isinstance(values[0], dict) or 'creatives' not in values[0]:
            continue
        cache = {}
        def resolve(index, depth=0):
            if index == -1:  # SvelteKit's undefined sentinel.
                return None
            if type(index) is not int or not 0 <= index < len(values) or depth > 24:
                raise ValueError('AppGoblin 页面数据引用无效')
            if index not in cache:
                item = values[index]
                if isinstance(item, dict):
                    item = {k: resolve(v, depth+1) for k, v in item.items()}
                elif isinstance(item, list):
                    item = [resolve(v, depth+1) for v in item]
                cache[index] = item
            return cache[index]
        return {k: resolve(values[0][k]) for k in ('myapp', 'creatives') if k in values[0]}
    raise ValueError('AppGoblin 公开页面缺少广告数据')


def goblin_creatives(page, m, evidence_url):
    data = public_page_node(page)
    app, creatives = data['myapp'], data['creatives']
    expected_store = {'apple': 'Apple App Store', 'google': 'Google Play'}[m['store']]
    if str(app['store_id']) != m['app_id'] or app['store'] != expected_store:
        raise ValueError('AppGoblin 推广素材的应用标识与请求不符')
    if creatives is None and not app.get('ad_creative_count'):
        return {'items': [], 'coverage': '来源尚无公开推广素材'}
    if not isinstance(creatives, dict) or not isinstance(creatives.get('by_vhash'), list) or not isinstance(creatives.get('by_creative'), list):
        raise ValueError('AppGoblin 推广素材集合不可读取')
    # The page publishes thumbnails by file hash. Network observations can
    # repeat a file across different networks and publishing apps.
    ads = {}
    attributed = {row['md5_hash'] for row in creatives['by_vhash']}
    rows = creatives['by_vhash'] + [row for row in creatives['by_creative'] if row['md5_hash'] not in attributed]
    for row in rows:
        file_id = row['md5_hash']
        if not isinstance(file_id, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', file_id):
            raise ValueError('AppGoblin 素材缺少有效的文件标识')
        channel = ad_channels.canonical(row.get('ad_domain_company_name') or row.get('ad_domain'))
        key = (file_id, channel)
        if key not in ads:
            extension = (row.get('file_extension') or '').lower()
            kind = 'video' if extension in ('mp4','webm','mov','m4v') else 'image' if extension in ('png','jpg','jpeg','gif','webp','avif') else 'other'
            ads[key] = {
                'ad_id': file_id, 'channel': channel, 'source': 'AppGoblin', 'country': '',
                'placement': '应用内广告', 'advertiser': app.get('developer_name'), 'format': extension,
                'assets': [{'type': kind, 'preview_url': f'https://media.appgoblin.info/creatives/thumbs/{file_id}.jpg'}],
                'publishers': set(), 'last_seen': None, 'evidence_url': evidence_url, 'store_url': link(app.get('store_link')),
            }
        ad = ads[key]
        day = source_day(row.get('run_at'))
        if day and (not ad['last_seen'] or ad['last_seen'] < day):
            ad['last_seen'] = day
        for publisher in row.get('pubs') or []:
            name = publisher.get('name') or publisher.get('store_id')
            if isinstance(name, str) and name:
                ad['publishers'].add(name)
    for ad in ads.values():
        ad['publishers'] = sorted(ad['publishers'])
    return {'items': list(ads.values())}


def buyer_rankings(page, m):
    rows = public_page_node(page)['creatives']
    if not isinstance(rows, list) or not rows:
        raise ValueError('AppGoblin 广告主榜未返回可比较的样本')
    ranks = {}
    target = next((r for r in rows if str(r['store_id']) == m['app_id']), None)
    if target:
        for metric in ('unique_creatives', 'unique_publishers'):
            values = [r[metric] for r in rows if type(r.get(metric)) is int and r[metric] >= 0]
            if type(target.get(metric)) is int and target[metric] >= 0:
                ranks[metric] = {'position': 1 + sum(v > target[metric] for v in values),
                                 'value': target[metric], 'sample': len(values)}
    return {'sample_size': len(rows), 'listed': target is not None, 'ranks': ranks, 'evidence_url': BUYERS}


def fetch(db, cfg, kind, m, page_number=0, *, request_guard=None):
    client = Client(cfg, db)
    client.request_guard = request_guard
    client.lineage_lane = kind
    def get(url, params=None):
        with client.request(m['store'], 'GET', url, params=params, headers={'Accept': 'application/json'},
                            allow_redirects=False) as response:
            if kind == 'ad_creatives' and response.status_code == 404:
                return None
            if response.status_code != 200:
                raise ValueError(f'{KINDS[kind]} 返回 HTTP {response.status_code}')
            return response.json()
    try:
        if kind in ad_channels.SOCIAL:
            from . import ad_libraries
            return ad_libraries.fetch(client, db, cfg, kind, m, page_number)
        if kind == 'ad_google':
            from . import google_ad_transparency
            return google_ad_transparency.fetch(client, db, m)
        if kind == 'ad_apple':
            return apple_page(get, m, page_number)
        if kind == 'ad_rankings':
            return buyer_rankings(get(BUYERS + '/__data.json'), m)
        page = f"{GOBLIN}/apps/{m['app_id']}/ad-creatives"
        data = get(page + '/__data.json')
        if data is None or any(node and node.get('type')=='error' and node.get('status')==404 for node in data.get('nodes', [])):
            return {'items': [], 'coverage': '来源未收录此应用'}
        return goblin_creatives(data, m, page)
    finally:
        client.session.close()
