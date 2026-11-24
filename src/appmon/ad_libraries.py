"""Read advertiser records from authorized Meta, TikTok and Reddit APIs."""
from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import parse_qsl, quote, urlsplit

from .ad_channels import store_destination, store_link_in
from .ad_sources import link, source_day

META = 'https://graph.facebook.com/v27.0/ads_archive'
TIKTOK = 'https://open.tiktokapis.com/v2/research/adlib/ad/'
REDDIT = 'https://ads-api.reddit.com/api/v3'


def records(data, key):
    rows = data.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('广告接口响应缺少有效的记录列表')
    return rows


def cursor(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('广告来源未返回有效分页标记')
    return value


def meta_page(request, m, after):
    fields = ('id,page_name,ad_creative_bodies,ad_creative_link_captions,ad_creative_link_titles,'
              'ad_creative_link_descriptions,ad_delivery_start_time,ad_delivery_stop_time,publisher_platforms')
    query = {'fields':fields, 'ad_type':'ALL', 'ad_active_status':'ALL', 'limit':50,
             'ad_reached_countries':'["'+m['country'].upper()+'"]',
             'search_terms':m['app_id'][:100], 'search_type':'KEYWORD_EXACT_PHRASE'}
    if after:
        query['after'] = after
    data = request('GET', META, params=query)
    items = []
    for row in records(data, 'data'):
        identifier = str(row['id'])
        if not identifier.isdigit():
            raise ValueError('Meta 广告 ID 无效')
        bodies = row.get('ad_creative_bodies') or []
        titles = row.get('ad_creative_link_titles') or []
        texts = [*bodies, *titles, *(row.get('ad_creative_link_captions') or []), *(row.get('ad_creative_link_descriptions') or [])]
        destination = store_link_in(texts, m)
        if not destination:
            continue
        platforms = {}
        for platform in row.get('publisher_platforms') or []:
            channel = 'Instagram' if platform.lower() == 'instagram' else 'Meta'
            platforms.setdefault(channel, []).append(platform)
        # The public library ID is sufficient to open the actual creative.
        # Snapshot and paging URLs can carry the account's access token.
        evidence = 'https://www.facebook.com/ads/library/?id=' + identifier
        for channel, placements in (platforms or {'Meta': ['版位未披露']}).items():
            items.append({'ad_id':identifier,'channel':channel,'source':'Meta Ad Library','country':m['country'],
                          'placement':' / '.join(placements),'title':' · '.join(titles),'body':'\n'.join(bodies),
                          'advertiser':row.get('page_name'),'ad_url':evidence,'evidence_url':evidence,'store_url':destination,
                          'date_label':'投放日期','first_seen':source_day(row.get('ad_delivery_start_time')),
                          'last_seen':source_day(row.get('ad_delivery_stop_time'))})
    paging = data.get('paging') or {}
    next_page = cursor((paging.get('cursors') or {}).get('after')) if paging.get('next') else None
    return {'items':items,'next_page':next_page}


def tiktok_data(data):
    if (data.get('error') or {}).get('code') != 'ok' or not isinstance(data.get('data'), dict):
        raise ValueError('TikTok 商业内容接口请求未成功，请检查授权与配额')
    return data['data']


def tiktok_page(request, m, page, app_name):
    end = date.fromisoformat(page['end']) if page else datetime.now(timezone.utc).date()-timedelta(days=1)
    term = page['term'] if page else (app_name or m['app_id'])[:50]
    start = end - timedelta(days=29)
    query = {'filters': {'ad_published_date_range': {'min': start.strftime('%Y%m%d'), 'max': end.strftime('%Y%m%d')},
                         'country_code_list':[m['country'].upper()]}, 'search_term':term,'max_count':5}
    if page:
        query['search_id'] = page['after']
    data = tiktok_data(request('POST',TIKTOK+'query/',params={'fields':'ad.id'},json=query))
    items = []
    for entry in records(data,'ads'):
        identifier = str(entry['ad']['id'])
        if not identifier.isdigit():
            raise ValueError('TikTok 广告 ID 无效')
        fields = ('ad.id,ad.title,ad.first_shown_date,ad.last_shown_date,ad.image_urls,ad.videos,'
                  'ad.download_url,ad.external_url,advertiser.business_name')
        detail = tiktok_data(request('POST',TIKTOK+'detail/',params={'fields':fields},json={'ad_id':int(identifier)}))
        ad = detail['ad']
        if str(ad['id']) != identifier:
            raise ValueError('TikTok 广告详情 ID 与请求不符')
        destination = store_destination(ad.get('download_url'), m) or store_destination(ad.get('external_url'), m)
        if not destination:
            continue
        assets = [{'type':'image','url':u} for u in ad.get('image_urls') or [] if link(u)]
        assets += [{'type':'video','url':v['url']} for v in ad.get('videos') or [] if link(v.get('url'))]
        evidence = 'https://library.tiktok.com/ads/detail/?ad_id='+identifier
        items.append({'ad_id':identifier,'channel':'TikTok','source':'TikTok 商业内容库','country':m['country'],
                      'placement':'TikTok','title':ad.get('title'),'advertiser':detail.get('advertiser',{}).get('business_name'),
                      'assets':assets,'ad_url':evidence,'evidence_url':evidence,'store_url':destination,
                      'landing_url':link(ad.get('external_url')),'first_seen':source_day(str(ad['first_shown_date'])) if ad.get('first_shown_date') else None,
                      'last_seen':source_day(str(ad['last_shown_date'])) if ad.get('last_shown_date') else None})
    more = data.get('has_more')
    if more not in (False,True,'false','true'):
        raise ValueError('TikTok 广告分页状态缺失')
    next_page = {'after':cursor(data.get('search_id')),'term':term,'end':end.isoformat()} if more in (True,'true') else None
    return {'items':items,'next_page':next_page,'period_start':start.isoformat(),'period_end':end.isoformat()}


def reddit_page(request, m, page, account):
    path = REDDIT+'/ad_accounts/'+quote(account,safe='')+'/ads'
    if page:
        if page['account'] != account:
            raise ValueError('Reddit 授权账户已变更，请重新采集')
        data = request('GET',path+'?'+page['query'])
    else:
        data = request('GET',path,params={'page.size':5})
    items = []
    for row in records(data,'data'):
        post_id = row.get('post_id')
        if not post_id or row.get('effective_status') not in ('ACTIVE','COMPLETED','PAUSED','AD_GROUP_PAUSED','CAMPAIGN_PAUSED','ARCHIVED'):
            continue
        if not re.fullmatch('t3_[A-Za-z0-9]+',post_id):
            raise ValueError('Reddit 广告帖子 ID 无效')
        response = request('GET',REDDIT+'/structured_posts/'+post_id, missing_ok=True)
        post = response['data'] if response is not None else {}
        if post and post.get('id') != post_id:
            raise ValueError('Reddit 广告帖子与请求不符')
        creative = post.get('creative') or {}
        landing = (creative.get('destination') or {}).get('url')
        click = None if row.get('shopping_creative') else row.get('click_url')
        store_url = store_destination(landing,m) or store_destination(click,m)
        if not store_url:
            continue
        assets = []
        for card in [creative, *(creative.get('cards') or [])]:
            for field in ('image','video','thumbnail'):
                media = (card.get(field) or {}).get('media') or {}
                if link(media.get('url')) and all(a['url'] != media['url'] for a in assets):
                    assets.append({'type':'video' if field=='video' else 'image','url':media['url']})
        evidence = link(post.get('url')) or link(row.get('post_url')) or 'https://www.reddit.com/comments/'+post_id[3:]
        items.append({'ad_id':str(row['id']),'channel':'Reddit','source':'Reddit Ads 授权账户','country':'',
                      'placement':'账户广告 · '+row['effective_status'],'title':creative.get('headline') or row.get('name'),
                      'body':creative.get('body'),'assets':assets,'format':creative.get('type'),
                      'ad_url':evidence,'evidence_url':evidence,'landing_url':link(landing) or link(click),'store_url':store_url})
    next_url = data['pagination'].get('next_url')
    next_page = None
    if next_url:
        parsed = urlsplit(next_url)
        if (parsed.scheme,parsed.netloc,parsed.path) != ('https','ads-api.reddit.com',urlsplit(path).path):
            raise ValueError('Reddit 分页地址与当前账户不符')
        if any(k.casefold() in ('access_token','authorization','client_secret') for k,_ in parse_qsl(parsed.query)):
            raise ValueError('Reddit 分页不能含账户凭据')
        next_page = {'query':cursor(parsed.query),'account':account}
    return {'items':items,'next_page':next_page}


def fetch(client, db, cfg, kind, m, page):
    from .source_registry import ads_credentials
    token = ads_credentials(db,cfg,kind)
    if not token.get('access_token'):
        raise ValueError('请先在数据源配置中保存广告来源授权')
    def request(method,url,missing_ok=False,**options):
        with client.request(m['store'], method, url, headers={'Authorization':'Bearer '+token['access_token'],
                            'Accept':'application/json'}, allow_redirects=False, **options) as response:
            if missing_ok and response.status_code == 404:
                return None
            if response.status_code != 200:
                raise ValueError(f'广告接口返回 HTTP {response.status_code}')
            return response.json()
    if kind == 'ad_meta':
        result = meta_page(request,m,page)
    elif kind == 'ad_tiktok':
        row = db.execute('SELECT name FROM apps WHERE store=%s AND app_id=%s',(m['store'],m['app_id'])).fetchone()
        result = tiktok_page(request,m,page,(row or {}).get('name'))
    else:
        result = reddit_page(request,m,page,token['ad_account_id'])
    if page and result['next_page'] == page:
        raise ValueError('广告来源重复返回同一分页标记')
    return result
