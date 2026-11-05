"""Anonymous public observations; scope and source units are never inferred."""
import hashlib
import re
import time
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
import requests
from .ingest import valid_id
from .db import dump


class PageText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)


def community_metrics(html,store,app_id):
    parser=PageText();parser.feed(html);text=' '.join(' '.join(parser.parts).split())
    if not valid_id(store,app_id) or app_id not in text:raise ValueError('页面未确认所请求应用')
    number=r'([\d.,]+[KMB]?[+]?)'
    patterns={'total_installs':number+r'\s+installs','monthly_active_users_estimate':number+r'\s+monthly active users',
        'monthly_revenue_estimate':r'(\$[\d.,]+[KMB]?[+]?)\s+monthly revenue est\.',
        'weekly_install_change':r'Weekly\s+\+'+number,'monthly_install_change':r'Monthly\s+\+'+number}
    values={k:m.group(1) for k,p in patterns.items() if (m:=re.search(p,text))}
    if not values:raise ValueError('页面未返回公开指标，未填充替代数值')
    return {'source':'公开元数据 public app page','store':store,'app_id':app_id,'scope':'worldwide',
        'country':None,'values_display':values,'observed_at':time.time(),
        'note':'第三方全球指标；iOS安装及收入/MAU为来源估算。保留原显示值和区间，不是分国家下载量或精确收入。'}


def web_trends(xml,country):
    # Reject entity declarations before parsing third-party XML.
    if b'<!ENTITY' in xml.upper() or b'<!DOCTYPE' in xml.upper():raise ValueError('不支持带实体声明的XML')
    root=ET.fromstring(xml)
    if root.tag!='rss':raise ValueError('来源没有返回RSS')
    rows=[]
    for item in root.findall('./channel/item'):
        keyword=item.findtext('title','').strip()
        if not keyword:continue
        traffic=next((e.text for e in item if e.tag.endswith('}approx_traffic')),None)
        rows.append({'keyword':keyword,'feed_position':len(rows)+1,'traffic_display':traffic,'published_at':item.findtext('pubDate')})
    return {'source':'网页趋势 RSS','scope':'web_search','country':country.lower(),
        'observed_at':time.time(),'items':rows,
        'note':'该国家网页搜索热点；不是Google Play或App Store官方热搜，RSS仅覆盖来源返回条目。'}


def collect(output,*,store=None,app_id=None,country=None,proxy=None,client=None,origin=None):
    if app_id is not None:
        if not valid_id(store,app_id):raise ValueError('应用ID无效')
        origin=origin or getattr(client,'source_profile',{}).get('origin')
        if not origin:raise ValueError('尚未配置公开指标接入地址')
        url=origin+'/apps/'+app_id;name=f'community-{store}-{app_id}'
    else:
        if not isinstance(country,str) or not re.fullmatch('[A-Za-z]{2}',country):raise ValueError('国家代码无效')
        if not origin:raise ValueError('尚未配置网页趋势接入地址')
        url=origin+'?geo='+country.upper();name='web-trends-'+country.lower()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    session=client or requests.Session();session.trust_env=False
    if client is None:
        from .stores import TLS12Adapter
        session.mount('https://',TLS12Adapter())
    if proxy:session.proxies={'http':proxy,'https':proxy}
    try:
        with session.get(url,timeout=(10,25),stream=True,allow_redirects=False) as response:
            if response.status_code!=200:raise ValueError(f'公开来源返回HTTP {response.status_code}；未尝试登录或绕过限制')
            raw=bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw)>2_000_000:raise ValueError('来源响应超过2MB')
            body=bytes(raw)
    finally:
        if client is None:session.close()
    result=community_metrics(body.decode('utf-8'),store,app_id) if app_id is not None else web_trends(body,country)
    digest=hashlib.sha256(body).hexdigest()
    raw_path=output/(digest+'.raw')
    if client is None:raw_path.write_bytes(body)
    result.update(raw_sha256=digest,raw_file=raw_path.name if client is None else None,anonymous=client is None)
    destination=output/(name+'.json');temporary=destination.with_suffix('.'+uuid.uuid4().hex+'.tmp');temporary.write_text(dump(result),encoding='utf-8');temporary.replace(destination)
    return result
