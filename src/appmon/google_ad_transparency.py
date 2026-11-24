"""Google Transparency Center domain samples with explicit app attribution."""
from datetime import datetime, timezone
import html
import json
import re
from urllib.parse import unquote, urlsplit

from .ad_channels import store_link_in
from .ad_sources import link
from .stores import CollectionDeferred, FetchError

ORIGIN = 'https://adstransparency.google.com'
SEARCH = ORIGIN + '/anji/_/rpc/SearchService/SearchCreatives'
SAMPLE = 8


def developer_domain(db, client, m):
    row = db.execute('''SELECT payload FROM listings WHERE store=%s AND app_id=%s AND country=%s
        AND language=%s AND last_success IS NOT NULL''', tuple(m[k] for k in ('store','app_id','country','language'))).fetchone()
    if row and row['payload']:
        payload = json.loads(row['payload'])
    else:
        observation = client.fetch(m)
        payload = observation.payload or {}
    website = payload.get('developer_website' if m['store']=='google' else 'seller_url')
    if not link(website):
        return None
    domain = urlsplit(website).hostname.removeprefix('www.')
    if domain in ('play.google.com','apps.apple.com','itunes.apple.com','facebook.com','instagram.com','google.com'):
        return None
    return domain


def date_field(row, key):
    stamp = (row.get(key) or {}).get('1')
    return datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat() if stamp else None


def preview_data(text, m):
    # Inspect public renderer text. Its scripts are never executed.
    text = re.sub(r'\\+(?:x|u00)([a-fA-F0-9]{2})', lambda m:chr(int(m[1],16)), text).replace(r'\/', '/')
    text = html.unescape(text)
    destination = store_link_in([text, unquote(text)], m)
    copy = dict(re.findall(r"""['"](headline|description|body|video_id)['"]\s*:\s*['"]([^'"\n]{1,4000})['"]""", text))
    assets = []
    match = re.search(r'https://(?:tpc\.googlesyndication\.com|pagead2\.googlesyndication\.com)/simgad/[0-9]+', text)
    if match:
        assets.append({'type':'image','url':match[0]})
    if re.fullmatch('[a-zA-Z0-9_-]{11}',copy.get('video_id','')):
        assets.append({'type':'video','url':'https://www.youtube.com/watch?v='+copy['video_id'],'external_only':True})
    return {'title':copy.get('headline'), 'body':copy.get('description') or copy.get('body'),
            'assets':assets, 'store_url':destination}


def fetch(client, db, m):
    domain = developer_domain(db,client,m)
    if not domain:
        return {'items': [], 'coverage':'商店资料未提供可查询的开发者官网'}
    body = {'1':domain,'2':SAMPLE,'3':{'12':{'1':domain}},'7':{'1':1}}
    with client.request(m['store'],'POST',SEARCH,params={'authuser':''},data={'f.req':json.dumps(body)},
                        allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f'Google 广告透明度中心返回 HTTP {response.status_code}')
        data = response.json()
    if not isinstance(data,dict) or data and '1' not in data:
        raise ValueError('Google 广告透明度中心响应格式已变化')
    items, candidates = [], []
    preview_error = None
    rows = data.get('1',[])
    if not isinstance(rows,list):
        raise ValueError('Google 广告透明度中心未返回广告列表')
    for row in rows[:SAMPLE]:
        advertiser, creative = row['1'],row['2']
        if not re.fullmatch('AR[0-9]+',advertiser) or not re.fullmatch('CR[0-9]+',creative):
            raise ValueError('Google 广告标识无效')
        evidence = f'{ORIGIN}/advertiser/{advertiser}/creative/{creative}'
        ad = {'ad_id':creative,'channel':'Google','source':'Google Ads Transparency Center','country':'',
              'placement':'Google 广告','advertiser':row.get('12'),'evidence_url':evidence,'ad_url':evidence,
              'first_seen':date_field(row,'6'),'last_seen':date_field(row,'7'),
              'format':{1:'文字',2:'图片',3:'视频'}.get(row.get('4'))}
        for variant in (row.get('3') or {}).values():
            preview = variant.get('4') if isinstance(variant,dict) else None
            if not link(preview):
                continue
            parsed = urlsplit(preview)
            if parsed.scheme == 'https' and parsed.netloc == 'displayads-formats.googleusercontent.com' and parsed.path.startswith('/ads/preview/') and not preview_error:
                try:
                    with client.request(m['store'],'GET',preview,allow_redirects=False,stream=True) as response:
                        if response.status_code==200:
                            raw = bytearray()
                            for block in response.iter_content(65536):
                                raw.extend(block)
                                if len(raw)>2_000_000:break
                            ad.update(preview_data(raw.decode('utf8',errors='replace'),m))
                except CollectionDeferred:
                    raise
                except FetchError as exc:
                    preview_error = str(exc)
            elif parsed.scheme=='https' and parsed.netloc=='tpc.googlesyndication.com' and parsed.path.startswith('/simgad/'):
                ad['assets'] = [{'type':'image','url':preview}]
            if ad.get('store_url'):
                break
        (items if ad.get('store_url') else candidates).append(ad)
    return {'items':items,'candidates':candidates,'domain':domain,
            'coverage':f'开发者域名 {domain} 的公开样本 {len(rows[:SAMPLE])} 条，每轮最多 {SAMPLE} 条，已核实 {len(items)} 条'
                       + (f'。部分预览暂不可读（{preview_error}）' if preview_error else '')}
