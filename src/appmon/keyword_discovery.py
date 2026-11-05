"""Market-specific word pools and coverage, with separate external popularity evidence."""
import hashlib
import json
import math
import time
from urllib.parse import urlsplit
import requests
from .keywords import validate_target, add_targets
from .locales import PREFERENCES
from .db import dump
from .growth import cutoff

FREQUENCY_SHA='525f9b560de45753a5ea01069454e72e9aa541c6'
FREQUENCY_LANGUAGES={'en':'en','zh-cn':'zh_cn','zh-tw':'zh_tw','ja':'ja','ko':'ko','th':'th','vi':'vi',
                     'id':'id','ms':'ms','ru':'ru','kk':'kk','ar':'ar','hi':'hi','es':'es','pt':'pt','pt-br':'pt_br',
                     'fr':'fr','de':'de','it':'it','tr':'tr','uk':'uk','fil':'tl'}


def source_catalog():
    return {'frequency_languages':list(FREQUENCY_LANGUAGES),'frequency_version':FREQUENCY_SHA,
            'sources':[{'name':'FrequencyWords','url':'https://github.com/hermitdave/FrequencyWords',
                        'license':'CC BY-SA 4.0（内容）','period':'OpenSubtitles 2018','kind':'corpus_frequency'},
                       {'name':'wordfreq','url':'https://github.com/rspeer/wordfreq','license':'Apache-2.0代码 / CC BY-SA 4.0数据',
                        'period':'约2021年语料快照','kind':'research_only'}],
            'note':'词频用于候选词筛选，不是国家搜索量或商店热搜。多语言市场需分别建设词库。'}


def url(value):
    if not isinstance(value,str) or len(value)>2000: raise ValueError('需要有效来源链接')
    p=urlsplit(value)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password: raise ValueError('来源链接须为无凭据的HTTP(S)网址')
    return value


def markets(value):
    rows=value.get('markets')
    if not isinstance(rows,list) or not 1<=len(rows)<=50 or not all(isinstance(v,dict) for v in rows):
        raise ValueError('每批需提供1–50个商店/国家/语言组合')
    result={}
    for row in rows:
        m=validate_target(row|{'keyword':'validation'})
        result[tuple(m[k] for k in ('store','country','language'))]=m
    return list(result.values())


def import_candidates(db, value):
    scope=markets(value); words=value.get('words')
    if not isinstance(words,list) or not 1<=len(words)<=50000: raise ValueError('每批导入1–50000个候选词')
    if len(scope)*len(words)>100000: raise ValueError('单批词×市场组合不得超过100000')
    source=value.get('source','手工词库'); link=url(value['source_url']) if value.get('source_url') else ''
    license_name=value.get('license','用户提供'); attribution=value.get('attribution',source); period=value.get('source_period','用户提供')
    if any(not isinstance(x,str) or not x.strip() or len(x)>limit for x,limit in ((source,255),(license_name,128),(attribution,255),(period,64))):
        raise ValueError('来源、许可、署名与期间不能为空且最长255字符')
    source_key=hashlib.sha256(dump([source,link,period]).encode()).hexdigest(); prepared=[]
    for m in scope:
        for w in words:
            w={'keyword':w} if isinstance(w,str) else w
            if not isinstance(w,dict): raise ValueError('词条必须为文本或对象')
            term=validate_target(m|{'keyword':w.get('keyword')})['keyword']
            frequency=w.get('frequency')
            if frequency is not None and (type(frequency) is not int or not 0<=frequency<2**63): raise ValueError('语料词频必须为非负整数')
            prepared.append((m['store'],m['country'],m['language'],term,source_key,source,link,license_name,attribution,frequency,period,time.time()))
    with db:
        for start in range(0,len(prepared),1000):
            db.executemany('''INSERT INTO keyword_candidates(store,country,language,keyword,source_key,source,source_url,
                license_name,attribution,corpus_frequency,source_period,imported_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                AS incoming ON DUPLICATE KEY UPDATE corpus_frequency=incoming.corpus_frequency,imported_at=incoming.imported_at''',prepared[start:start+1000])
    return {'processed':len(prepared),'markets':len(scope),'queued':0,'note':'候选词已入库；选择需要的词后再创建追踪，未自动启动全量采集'}


def import_frequency(db,value):
    language=value.get('language'); code=FREQUENCY_LANGUAGES.get(language)
    if not code: raise ValueError('该词库语言尚未配置，可使用文件导入')
    scope=markets(value)
    if any(m['language']!=language for m in scope): raise ValueError('词库语言必须与每个目标市场的请求语言一致；不自动翻译')
    limit=value.get('limit',500)
    if type(limit) is not int or not 1<=limit<=5000: raise ValueError('每次下载取1–5000个候选词')
    suffix='full' if code in ('ja','kk','hi','tl') else '50k'
    link=f'https://raw.githubusercontent.com/hermitdave/FrequencyWords/{FREQUENCY_SHA}/content/2018/{code}/{code}_{suffix}.txt'
    try:
        r=requests.get(link,timeout=30);r.raise_for_status()
    except requests.RequestException as exc: raise ValueError('开源词库下载失败：'+type(exc).__name__) from exc
    words=[]
    for line in r.text.splitlines():
        term,_,frequency=line.rpartition(' ')
        if term and frequency.isdigit():
            words.append({'keyword':term,'frequency':int(frequency)})
        if len(words)>=limit: break
    return import_candidates(db,value|{'words':words,'source':'FrequencyWords / OpenSubtitles 2018','source_url':link,
        'license':'CC BY-SA 4.0','attribution':'FrequencyWords by hermitdave; OpenSubtitles 2018','source_period':'2018 / '+FREQUENCY_SHA})


def candidates(db,m,q='',offset=0,grouped=True):
    if not 0<=offset<=1000000: raise ValueError('分页偏移无效')
    where='store=%s AND country=%s AND language=%s';args=[m[k] for k in ('store','country','language')]
    if q: where+=' AND LOCATE(LOWER(%s),LOWER(keyword))>0';args.append(q)
    totals=db.execute('SELECT count(*) records,count(DISTINCT keyword) terms FROM keyword_candidates WHERE '+where,args).fetchone()
    if grouped:
        grouped_sql='SELECT MIN(id) id,count(*) source_count FROM keyword_candidates WHERE '+where+' GROUP BY keyword'
        rows=db.execute('SELECT c.*,g.source_count,t.id tracked_id,t.enabled tracked_enabled FROM ('+grouped_sql+
            ') g JOIN keyword_candidates c ON c.id=g.id LEFT JOIN keyword_targets t ON t.store=c.store AND t.country=c.country AND t.language=c.language AND t.keyword=c.keyword ORDER BY c.keyword,c.id LIMIT 100 OFFSET %s',(*args,offset)).fetchall()
    else:
        rows=db.execute('SELECT *,1 source_count FROM keyword_candidates WHERE '+where+' ORDER BY id DESC LIMIT 100 OFFSET %s',(*args,offset)).fetchall()
    versions={r['digest']:r['observed_at'] for r in db.execute('SELECT digest,observed_at FROM keyword_reports WHERE store=%s AND country=%s AND language=%s',tuple(m[k] for k in ('store','country','language'))).fetchall()}
    for row in rows:row['report_observed_at']=versions.get(row['source_period'])
    return {'items':rows,'total':totals['terms'] if grouped else totals['records'],'source_records':totals['records'],'grouped':grouped,'offset':offset,'limit':100}


def candidate_sources(db,identifier):
    candidate=db.execute('SELECT * FROM keyword_candidates WHERE id=%s',(identifier,)).fetchone()
    if not candidate:raise ValueError('候选词不存在')
    rows=db.execute('SELECT * FROM keyword_candidates WHERE store=%s AND country=%s AND language=%s AND keyword=%s ORDER BY id LIMIT 100',
        tuple(candidate[k] for k in ('store','country','language','keyword'))).fetchall()
    reports=db.execute('SELECT app_id,digest,observed_at FROM keyword_reports WHERE store=%s AND country=%s AND language=%s',
        tuple(candidate[k] for k in ('store','country','language'))).fetchall()
    evidence={(r['digest'],'外部报告数据 / '+r['app_id']):r for r in reports}
    for row in rows:
        report=evidence.get((row['source_period'],row['attribution']),{})
        row['source_app_id']=report.get('app_id');row['report_observed_at']=report.get('observed_at')
    return {'keyword':candidate['keyword'],'items':rows,'limit':100}


def track(db,value):
    ids=value.get('ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=2000 or any(type(v) is not int or v<=0 for v in ids): raise ValueError('选择1–2000个候选词')
    rows=db.execute('SELECT * FROM keyword_candidates WHERE id IN ('+','.join('%s' for _ in ids)+')',ids).fetchall()
    if len(rows)!=len(set(ids)): raise ValueError('候选词不存在')
    prepared={}
    for row in rows:
        t=validate_target(row|{'depth':value.get('depth',100),'refresh_hours':value.get('refresh_hours',24)})
        prepared[tuple(t[k] for k in ('store','country','language','keyword'))]=t
    created=[];existing=0
    with db:
        for identity,t in sorted(prepared.items()):
            result=db.execute('''INSERT INTO keyword_targets(store,country,language,keyword,depth,refresh_hours)
                VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)''',(*identity,t['depth'],t['refresh_hours']))
            if result.rowcount==1:created.append(result.lastrowid)
            else:existing+=1
    return {'targets':len(created),'ids':created,'existing':existing,'unique_targets':len(prepared),'selected_sources':len(rows)}


def fresh_sql():
    return "t.enabled=1 AND t.failures=0 AND r.outcome='ok' AND r.requested_depth=t.depth AND r.observed_at+t.refresh_hours*3600>%s"


def coverage(db,m,app_id='',app_offset=0,keyword_offset=0,app_keyword_offset=0,q='',limit=100):
    if any(type(v) is not int or not 0<=v<=1000000 for v in (app_offset,keyword_offset,app_keyword_offset)) or not 1<=limit<=200:
        raise ValueError('分页参数无效')
    now=time.time();args=tuple(m[k] for k in ('store','country','language'))
    scope='t.store=%s AND t.country=%s AND t.language=%s'
    base=' FROM keyword_targets t LEFT JOIN keyword_runs r ON r.id=t.latest_success_id WHERE '+scope
    stats=db.execute('SELECT count(*) tracked,COALESCE(SUM('+fresh_sql()+'),0) fresh'+base,(now,*args)).fetchone()
    join=' FROM keyword_targets t JOIN keyword_runs r ON r.id=t.latest_success_id JOIN keyword_ranks k ON k.run_id=r.id WHERE '+scope+' AND '+fresh_sql()
    app_total=db.execute('SELECT count(DISTINCT k.app_id) total'+join,(*args,now)).fetchone()['total']
    apps=db.execute('''SELECT k.app_id,MAX(k.name) name,count(*) covered,SUM(k.position<=3) top3,
        SUM(k.position<=10) top10,SUM(k.position<=50) top50,SUM(k.position<=100) top100,MIN(k.position) best'''+join+
        ' GROUP BY k.app_id ORDER BY covered DESC,top10 DESC,k.app_id LIMIT %s OFFSET %s',(*args,now,limit,app_offset)).fetchall()
    missing=[r['app_id'] for r in apps if not r['name']]
    if missing:
        names={r['app_id']:r['name'] for r in db.execute('SELECT app_id,name FROM apps WHERE store=%s AND app_id IN ('+','.join('%s' for _ in missing)+')',(m['store'],*missing)).fetchall()}
        for row in apps:
            if not row['name']:row['name']=names.get(row['app_id'])
    term_filter=' AND LOCATE(LOWER(%s),LOWER(t.keyword))>0' if q else ''
    term_args=(*args,q) if q else args
    term_total=db.execute('SELECT count(*) total'+base+term_filter,term_args).fetchone()['total']
    terms=db.execute('''SELECT t.id,t.keyword,t.depth,r.observed_at,r.returned_count,r.source,
        CASE WHEN '''+fresh_sql()+''' THEN 1 ELSE 0 END fresh,t.last_error'''+base+term_filter+' ORDER BY t.keyword,t.id LIMIT %s OFFSET %s',(now,*term_args,limit,keyword_offset)).fetchall()
    app_terms=[]
    if app_id:
        app_terms=db.execute('''SELECT t.id,t.keyword,k.position,r.observed_at,r.returned_count,r.source,'''+fresh_sql()+''' fresh
            FROM keyword_targets t LEFT JOIN keyword_runs r ON r.id=t.latest_success_id
            LEFT JOIN keyword_ranks k ON k.run_id=r.id AND k.app_id=%s WHERE '''+scope+term_filter+
            ' ORDER BY k.position IS NULL,k.position,t.keyword,t.id LIMIT %s OFFSET %s',(now,app_id,*term_args,limit,app_keyword_offset)).fetchall()
    page=lambda total,offset:{'total':total,'offset':offset,'limit':limit,'has_more':offset+limit<total}
    return {'stats':stats,'apps':apps,'keywords':terms,'app_keywords':app_terms,
            'pages':{'apps':page(app_total,app_offset),'keywords':page(term_total,keyword_offset),'app_keywords':page(term_total if app_id else 0,app_keyword_offset)},
            'limits':{'apps':limit,'keywords':limit,'app_keywords':limit},
            'note':'覆盖分母是本市场已跟踪词库；仅最新成功、未过期、深度一致且未暂停的结果计入覆盖。关键词筛选只影响词表，不改变覆盖榜。未命中不代表商店没有收录。'}


def market_matrix(db,store,app_id=''):
    now=time.time()
    rows=db.execute('''SELECT t.country,t.language,count(*) tracked,COALESCE(SUM('''+fresh_sql()+'''),0) fresh,
        COALESCE(SUM(CASE WHEN '''+fresh_sql()+''' AND k.position IS NOT NULL THEN 1 ELSE 0 END),0) covered,
        COALESCE(SUM(CASE WHEN '''+fresh_sql()+''' AND k.position<=10 THEN 1 ELSE 0 END),0) top10
        FROM keyword_targets t LEFT JOIN keyword_runs r ON r.id=t.latest_success_id
        LEFT JOIN keyword_ranks k ON k.run_id=r.id AND k.app_id=%s WHERE t.store=%s
        GROUP BY t.country,t.language ORDER BY t.country,t.language''',(now,now,now,app_id,store)).fetchall()
    return {'items':rows,'recommended_languages':{c:v.get(store,{}) for c,v in PREFERENCES['countries'].items()},
            'note':'仅列已配置市场；国家不是语言，同一国家可建多语言词库。未配置市场不等于零覆盖。'}


def import_signals(db,value):
    if value.get('observed_at') is None:raise ValueError('必须提供数据来源的观察时间')
    m=validate_target(value|{'keyword':'validation'}); at=cutoff(value.get('observed_at'))
    kind=value.get('kind');source=value.get('source');unit=value.get('score_unit');link=url(value.get('source_url'))
    if kind not in ('hot_search','search_popularity'):raise ValueError('仅支持有来源的热搜榜或搜索热度快照')
    if not all(isinstance(v,str) and 0<len(v)<=128 for v in (source,unit)):raise ValueError('必须注明来源及分数口径')
    rows=value.get('items')
    if not isinstance(rows,list) or not 1<=len(rows)<=2000:raise ValueError('每次快照需1–2000个词')
    prepared=[];seen=set();positions=set()
    for i,r in enumerate(rows,1):
        if not isinstance(r,dict):raise ValueError('词条必须是对象')
        term=validate_target(m|{'keyword':r.get('keyword')})['keyword'];score=r.get('score');position=r.get('position',i)
        if type(position) is not int or not 1<=position<=1000000 or position in positions:raise ValueError('来源排名必须为不重复的正整数')
        positions.add(position)
        if term in seen: raise ValueError('同一榜单不能有重复关键词')
        seen.add(term)
        if score is not None and (type(score) not in (int,float) or not math.isfinite(score)):raise ValueError('分数必须为有限数值或空')
        prepared.append((term,position,score))
    digest=hashlib.sha256(dump([m,at,kind,source,unit,link,prepared]).encode()).hexdigest()
    with db:
        old=db.execute('SELECT id FROM keyword_signal_runs WHERE fingerprint=%s',(digest,)).fetchone()
        if old:return old|{'duplicate':True}
        db.execute('''INSERT INTO keyword_signal_runs(store,country,language,source,source_url,kind,observed_at,score_unit,fingerprint,imported_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(m['store'],m['country'],m['language'],source,link,kind,at,unit,digest,time.time()))
        identifier=db.cursor.lastrowid
        db.executemany('INSERT INTO keyword_signals VALUES(%s,%s,%s,%s)',[(identifier,*r) for r in prepared])
    return {'id':identifier,'count':len(prepared)}


def signals(db,m,run_id=None):
    args=tuple(m[k] for k in ('store','country','language'))
    runs=db.execute('''SELECT * FROM keyword_signal_runs WHERE store=%s AND country=%s AND language=%s
        ORDER BY observed_at DESC,id DESC LIMIT 100''',args).fetchall()
    selected=next((r for r in runs if r['id']==run_id),None) if run_id else (runs[0] if runs else None)
    items=db.execute('SELECT * FROM keyword_signals WHERE run_id=%s ORDER BY position',(selected['id'],)).fetchall() if selected else []
    return {'runs':runs,'selected':selected,'items':items,'note':'外部来源快照；保留原始顺序与分数口径，不合并不同来源。无数据表示尚未接入热搜来源。'}


def promote_signals(db,run_id):
    run=db.execute('SELECT * FROM keyword_signal_runs WHERE id=%s',(run_id,)).fetchone()
    if not run:raise ValueError('来源快照不存在')
    rows=db.execute('SELECT keyword FROM keyword_signals WHERE run_id=%s ORDER BY position',(run_id,)).fetchall()
    return import_candidates(db,{'markets':[run],'words':[r['keyword'] for r in rows],'source':run['source'],
        'source_url':run['source_url'],'license':'来源授权范围内使用','attribution':run['source'],
        'source_period':str(run['observed_at'])})
