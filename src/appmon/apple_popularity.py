"""Read-only Apple Ads Platform search-popularity adapter. No campaign mutations."""
import calendar
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
import requests
from .db import dump
from .keywords import validate_target
from .keyword_discovery import import_signals
from .monitor import raw_save

ENDPOINT='https://api.ads.apple.com/v1/insights/apps/search-term-popularity/query'
DOC='https://developer.apple.com/documentation/apple-ads-platform-api/query-app-search-term-popularity-data'
GENRES=['BUSINESS','EDUCATION','ENTERTAINMENT','FINANCE','FOOD_DRINK','GAMES','HEALTH_FITNESS','LIFESTYLE',
        'NEW_PUBLICATION','PHOTO_VIDEO','PRODUCTIVITY_UTILITIES','SHOPPING','SOCIAL_NETWORKING','SPORTS','TRAVEL']


def configuration():
    today=datetime.now(timezone.utc).date();last_sunday=today-timedelta(days=(today.weekday()+1)%7)
    return {'configured':bool(os.environ.get('APPLE_ADS_ACCESS_TOKEN') and os.environ.get('APPLE_ADS_AD_ACCOUNT_ID')),
            'genres':GENRES,'default_start':(last_sunday-timedelta(days=7)).isoformat(),
            'doc_url':DOC,'note':'Apple官方周/月搜索热度，需要Apple Ads Platform授权。不是实时热搜；查询语言为本地追踪配置，源数据按国家和分类提供。'}


def specification(value):
    if value.get('store')!='apple':raise ValueError('官方搜索热度接口仅适用于App Store')
    m=validate_target(value|{'keyword':'validation'})
    genre=value.get('genre');period=value.get('period','weekly');limit=value.get('limit',200)
    if genre not in GENRES or period not in ('weekly','monthly') or type(limit) is not int or not 1<=limit<=2000:raise ValueError('分类、统计周期或数量无效')
    try:start=date.fromisoformat(value['start'])
    except (KeyError,TypeError,ValueError) as exc:raise ValueError('请提供统计开始日期') from exc
    if (period=='weekly' and start.weekday()!=6) or (period=='monthly' and start.day!=1):raise ValueError('周统计从星期日开始，月统计从每月1日开始')
    end=start+timedelta(days=6) if period=='weekly' else start.replace(day=calendar.monthrange(start.year,start.month)[1])
    if end>=datetime.now(timezone.utc).date():raise ValueError('请选择已结束的完整统计周期')
    payload={'filters':[{'field':'countryOrRegion','operator':'EQUALS','value':m['country'].upper()},
                        {'field':'genre','operator':'EQUALS','value':genre}],
             'fields':['rankInGenre','searchPopularity1to100','searchPopularity1to5','searchPopularityInGenre'],
             'timeRange':{'start':start.isoformat(),'end':end.isoformat(),'timeZone':'UTC','granularity':'WEEKLY_SUN_SAT' if period=='weekly' else 'MONTHLY'},
             'sorting':[{'field':'rankInGenre','order':'ASC'}],'pagination':{'offset':0,'pageSize':limit}}
    return m,genre,period,start,end,payload


def ingest(cfg,db,value,data,origin='用户导入Apple格式响应'):
    m,genre,period,start,end,payload=specification(value)
    result=data.get('result') if isinstance(data,dict) else None
    rows=result.get('rows') if isinstance(result,dict) else None
    if not isinstance(rows,list) or len(rows)>2000:raise ValueError('响应必须包含最多2000行result.rows')
    if not rows:raise ValueError('该周期没有返回数据；未创建空热度榜，请核对官方发布时间')
    expected=start.isoformat() if period=='weekly' else start.strftime('%Y-%m');date_field='week' if period=='weekly' else 'month'
    items=[];safe_rows=[]
    for row in rows:
        if not isinstance(row,dict) or row.get('countryOrRegion')!=m['country'].upper() or row.get('genre')!=genre or row.get(date_field)!=expected:
            raise ValueError('响应国家、分类或统计周期与请求不一致，未入库')
        score=row.get('searchPopularity1to100');rank=row.get('rankInGenre')
        if type(score) is not int or not 1<=score<=100 or type(rank) is not int or rank<1:raise ValueError('官方排名或1–100热度分数字段无效')
        items.append({'keyword':row.get('searchTerm'),'position':rank,'score':score})
        safe_rows.append({k:row.get(k) for k in (date_field,'countryOrRegion','genre','searchTerm','rankInGenre','searchPopularity1to100','searchPopularity1to5','searchPopularityInGenre')})
    stamp=datetime.combine(end,datetime.max.time(),tzinfo=timezone.utc).timestamp()
    result=import_signals(db,m|{'kind':'search_popularity','source':origin+' / '+genre,'source_url':DOC,'observed_at':stamp,
        'score_unit':f'Apple searchPopularity1to100; {period} {start}/{end}; rankInGenre','items':items})
    raw=raw_save(cfg['data_dir'],dump({'request':payload,'response':{'result':{'rows':safe_rows}}}).encode())
    db.execute('''INSERT INTO keyword_source_fetches(run_id,provider,observed_at,raw_path,metadata)
        VALUES(%s,%s,%s,%s,%s)''',(result['id'],'apple_ads_platform' if origin=='Apple Ads官方接口' else 'aso_file',time.time(),raw,dump({'period':period,'start':str(start),'end':str(end),'genre':genre,'returned':len(rows),'requested':payload['pagination']['pageSize']})))
    return result|{'returned':len(rows),'possibly_truncated':len(rows)>=payload['pagination']['pageSize'],'period_start':str(start),'period_end':str(end)}


def request_data(cfg,db,value):
    *_,payload=specification(value)
    token=os.environ.get('APPLE_ADS_ACCESS_TOKEN','');account=os.environ.get('APPLE_ADS_AD_ACCOUNT_ID','')
    if not token or not account:raise ValueError('尚未配置Apple Ads授权；请在服务端环境设置访问令牌和广告账户ID，不要填写到聊天或浏览器表单')
    if not re.fullmatch(r'[0-9]+',account) or '\n' in token or '\r' in token:raise ValueError('Apple Ads服务端授权配置格式无效')
    from .collection_lineage import record_http
    started=time.monotonic()
    try:
        response=requests.post(ENDPOINT,headers={'Authorization':'Bearer '+token,'X-AP-Context':'adAccountId='+account},json=payload,timeout=30,allow_redirects=False)
    except requests.RequestException as exc:
        record_http(db,'apple',ENDPOINT,elapsed=time.monotonic()-started,source='ads',lane='aso_popularity')
        raise ValueError('Apple搜索热度请求失败：'+type(exc).__name__) from None
    record_http(db,'apple',ENDPOINT,response.status_code,time.monotonic()-started,source='ads',lane='aso_popularity')
    if response.status_code in (403,429) or response.status_code>=500:
        from .collection_limits import cooldown
        try:delay=float(response.headers.get('Retry-After','0'))
        except (ValueError,TypeError):delay=0
        cooldown(db,cfg,'apple',delay)
    if response.status_code in (401,403):raise ValueError('Apple Ads授权已过期或当前账户无权访问该数据')
    if response.status_code==429:raise ValueError('Apple Ads请求受限，请遵守服务端配额后重试')
    if response.status_code!=200:raise ValueError('Apple搜索热度接口返回HTTP '+str(response.status_code))
    try:data=response.json()
    except ValueError:raise ValueError('Apple搜索热度响应不是有效JSON') from None
    return data


def fetch(cfg,db,value):
    return ingest(cfg,db,value,request_data(cfg,db,value),origin='Apple Ads官方接口')
