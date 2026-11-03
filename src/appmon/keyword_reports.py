"""Read Diandian XLSX reports without executing formulas or merging external ranks into observations."""
import base64
import binascii
import hashlib
import io
import json
import posixpath
import re
import time
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET
from .db import dump
from .intelligence import market
from .keywords import validate_target

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
SHEETS=('ASO关键词','ASA竞价词','谷歌付费关键词')
MAX_BYTES=3_500_000


def decode(value):
    content=value.get('content_base64','')
    if not isinstance(content,str) or not 0<len(content)<=4_700_000:raise ValueError('报告限3.5MB，需上传xlsx文件')
    try: raw=base64.b64decode(content,validate=True)
    except (ValueError,binascii.Error) as exc:raise ValueError('报告编码无效') from exc
    return raw


def parse(raw):
    if len(raw)>MAX_BYTES:raise ValueError('报告文件超过3.5MB')
    try:
        archive=zipfile.ZipFile(io.BytesIO(raw))
        if len(archive.infolist())>500 or sum(i.file_size for i in archive.infolist())>80_000_000:
            raise ValueError('报告解压体积超限')
        strings=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            root=ET.fromstring(archive.read('xl/sharedStrings.xml'))
            strings=[''.join(t.text or '' for t in si.findall('.//s:t',NS)) for si in root]
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        summary=[];items=[];inferred={'country':None,'store':None,'observed_at':None}
        for sheet in workbook.findall('s:sheets/s:sheet',NS):
            name=sheet.attrib['name'];target=rels[sheet.attrib['{'+REL+'}id']]
            path=posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
            if not path.startswith('xl/'):raise ValueError('报告工作表路径无效')
            rows=[]
            for _,elem in ET.iterparse(io.BytesIO(archive.read(path)),events=('end',)):
                if elem.tag!='{'+NS['s']+'}row':continue
                values=[]
                for c in elem.findall('s:c',NS):
                    letters=re.sub('[^A-Z]','',c.attrib.get('r','A'));col=0
                    for char in letters:col=col*26+ord(char)-64
                    if col>200:raise ValueError('报告列数超限')
                    while len(values)<col:values.append(None)
                    v=c.find('s:v',NS); text=v.text if v is not None else None
                    if c.attrib.get('t')=='s':value=strings[int(text)] if text else None
                    elif c.attrib.get('t')=='inlineStr':value=''.join(t.text or '' for t in c.findall('.//s:t',NS))
                    elif text is None:value=None
                    else:
                        try:value=float(text) if '.' in text else int(text)
                        except ValueError:value=text
                    if col:values[col-1]=value
                rows.append(values);elem.clear()
                if len(rows)>100000:raise ValueError('报告行数超限')
            title=' '.join(str(v) for v in (rows[0] if rows else []) if v is not None)
            headers=rows[1] if len(rows)>1 else []
            summary.append({'sheet':name,'rows':max(0,len(rows)-2),'title':title,'headers':headers})
            if 'App Store' in title:inferred['store']='apple'
            if 'Google Play' in title:inferred['store']='google'
            if name not in SHEETS:continue
            if not headers or headers[0]!='关键词':raise ValueError('无法识别关键词表头，未导入')
            from .catalog import CATALOG
            country_matches=[c['code'] for c in CATALOG['countries'] if re.search('_'+re.escape(c['name'])+'区?_',title)]
            if len(country_matches)==1:inferred['country']=country_matches[0]
            date=re.search(r'_(\d{8})对比',title)
            if date:
                inferred['observed_at']=datetime.strptime(date[1],'%Y%m%d').replace(tzinfo=timezone(timedelta(hours=8))).timestamp()
            for number,values in enumerate(rows[2:],3):
                if not values or not isinstance(values[0],str) or not values[0].strip():continue
                if values[0].strip() in ('暂无数据','No data','No Data') and not any(v is not None and str(v).strip() for v in values[1:]):continue
                # Keep duplicate/blank header positions intact in original values.
                record=dict(zip((str(h or f'column_{i+1}') for i,h in enumerate(headers)),values))
                rank=record.get('排名',record.get('搜索排名'))
                position=rank if type(rank) is int and rank>0 else None
                items.append({'sheet':name,'source_row':number,'keyword':values[0], 'position':position,
                              'popularity':record.get('流行度'),'search_index':record.get('搜索指数'),
                              'original':{'headers':headers,'values':values}})
        archive.close()
        if not any(s['sheet'] in SHEETS for s in summary):raise ValueError('未发现支持的关键词工作表')
        return {'summary':summary,'inferred':inferred,'items':items,'digest':hashlib.sha256(raw).hexdigest()}
    except (zipfile.BadZipFile,KeyError,ET.ParseError,IndexError,TypeError) as exc:
        raise ValueError('不是可解析的xlsx报告') from exc


def preview(value):
    parsed=parse(decode(value))
    rejected=0
    for r in parsed['items']:
        try:validate_target({'store':'google','country':'us','language':'en','keyword':r['keyword']})
        except ValueError:rejected+=1
    return {k:v for k,v in parsed.items() if k!='items'} | {'keyword_rows':len(parsed['items'])-rejected,'rejected_rows':rejected,'sample':parsed['items'][:12],
        'note':f'导入自然/竞价关键词；{rejected}行不可追踪词单独保留原文，不进词库。其他表和完整原文件保留。来源指标不解释为实测下载或官方热搜。'}


def import_report(cfg,db,value):
    m=market(value);raw=decode(value);parsed=parse(raw);inferred=parsed['inferred']
    for key in ('country','store'):
        if inferred.get(key) and inferred[key]!=m[key]:raise ValueError('报告标注的'+key+'与导入市场不一致')
    at=inferred['observed_at']
    if at is None:raise ValueError('未识别报告关键词日期，需先适配此报告格式')
    if at>time.time()+86400:raise ValueError('报告日期在未来')
    prepared=[];rejected=[]
    for row in parsed['items']:
        try:term=validate_target(m|{'keyword':row['keyword']})['keyword']
        except ValueError:
            rejected.append((row['sheet'],row['source_row'],'关键词含不可追踪字符或超过长度限制，原文保留',dump(row['original'])))
            continue
        prepared.append((row['sheet'],row['source_row'],term,row['position'],row['popularity'],row['search_index'],dump(row['original'])))
    for s in parsed['summary']:
        if s['sheet'] in SHEETS:
            s['rejected_rows']=sum(r[0]==s['sheet'] for r in rejected)
            s['imported_keyword_rows']=sum(r[0]==s['sheet'] for r in prepared)
    folder=Path(cfg['data_dir'])/'keyword_reports';folder.mkdir(exist_ok=True,parents=True)
    dest=folder/(parsed['digest']+'.xlsx')
    if not dest.exists():dest.write_bytes(raw)
    filename=Path(str(value.get('filename','report.xlsx'))).name[:255]
    with db.named_lock('keyword-report:'+parsed['digest'][:40]):
        with db:
            old=db.execute('SELECT id FROM keyword_reports WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND digest=%s',
                (m['store'],m['app_id'],m['country'],m['language'],parsed['digest'])).fetchone()
            if old:return old|{'duplicate':True,'rows':len(prepared),'rejected_rows':len(rejected)}
            db.execute('''INSERT INTO keyword_reports(store,app_id,country,language,digest,filename,source,observed_at,imported_at,raw_path,summary)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(m['store'],m['app_id'],m['country'],m['language'],parsed['digest'],filename,
                 '点点应用报告',at,time.time(),dest.relative_to(cfg['data_dir']).as_posix(),dump(parsed['summary'])))
            identifier=db.cursor.lastrowid
            for start in range(0,len(prepared),1000):
                db.executemany('INSERT INTO keyword_report_rows VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',[(identifier,*r) for r in prepared[start:start+1000]])
            if rejected:db.executemany('INSERT INTO keyword_report_rejections VALUES(%s,%s,%s,%s,%s)',[(identifier,*r) for r in rejected])
    return {'id':identifier,'rows':len(prepared),'rejected_rows':len(rejected),'duplicate':False,'queued':0}


def reports(db,m):
    where='store=%s AND country=%s AND language=%s';args=[m[k] for k in ('store','country','language')]
    if m.get('app_id'):where+=' AND app_id=%s';args.append(m['app_id'])
    rows=db.execute('SELECT id,app_id,filename,source,observed_at,imported_at,summary FROM keyword_reports WHERE '+where+' ORDER BY id DESC LIMIT 100',args).fetchall()
    for row in rows:row['summary']=json.loads(row['summary'])
    return rows


def summary(db,m):
    args=tuple(m[k] for k in ('store','country','language'))
    base=''' FROM keyword_report_rows k JOIN keyword_reports r ON r.id=k.report_id
        WHERE r.store=%s AND r.country=%s AND r.language=%s'''
    counts=db.execute('SELECT count(*) records,count(DISTINCT k.keyword) unique_keywords,count(DISTINCT r.id) reports,count(DISTINCT r.app_id) apps'+base,args).fetchone()
    sheets=db.execute('SELECT k.sheet,count(*) records,count(DISTINCT k.keyword) unique_keywords'+base+' GROUP BY k.sheet',args).fetchall()
    shared=db.execute('''SELECT count(*) n FROM (SELECT k.keyword'''+base+' GROUP BY k.keyword HAVING count(DISTINCT k.sheet)>1) x',args).fetchone()['n']
    cross=db.execute('''SELECT count(*) n FROM (SELECT k.keyword'''+base+' GROUP BY k.keyword HAVING count(DISTINCT r.app_id)>1) x',args).fetchone()['n']
    rejected=db.execute('''SELECT count(*) n FROM keyword_report_rejections k JOIN keyword_reports r ON r.id=k.report_id
        WHERE r.store=%s AND r.country=%s AND r.language=%s''',args).fetchone()['n']
    return {'counts':counts,'rejected_rows':rejected,'sheets':sheets,'shared_between_types':shared,'shared_between_apps':cross,
            'note':'按当前商店、国家、追踪语言汇总全部已导入报告；相同文件按哈希幂等。词数按NFKC和空白规范化去重，保留不同应用/词表/日期来源。报告的追踪语言由导入时指定，不代表原报告已给出语言。'}


def report_rows(db,identifier,sheet='ASO关键词',q='',offset=0):
    if sheet not in SHEETS or not 0<=offset<=100000:raise ValueError('工作表或分页参数无效')
    where='report_id=%s AND sheet=%s';args=[identifier,sheet]
    if q:where+=' AND keyword LIKE %s';args.append('%'+q+'%')
    total=db.execute('SELECT count(*) n FROM keyword_report_rows WHERE '+where,args).fetchone()['n']
    rows=db.execute('SELECT * FROM keyword_report_rows WHERE '+where+' ORDER BY source_row LIMIT 100 OFFSET %s',(*args,offset)).fetchall()
    for r in rows:r['payload']=json.loads(r['payload'])
    return {'items':rows,'total':total,'offset':offset,'limit':100}


def promote(db,value):
    from .keyword_discovery import import_candidates
    identifier=value.get('report_id');sheet=value.get('sheet','ASO关键词'); source_rows=value.get('source_rows')
    if sheet not in SHEETS or not isinstance(source_rows,list) or not 1<=len(source_rows)<=2000 or any(type(v) is not int for v in source_rows):
        raise ValueError('每批选择1–2000行报告关键词')
    report=db.execute('SELECT * FROM keyword_reports WHERE id=%s',(identifier,)).fetchone()
    if not report:raise ValueError('报告不存在')
    rows=db.execute('SELECT keyword FROM keyword_report_rows WHERE report_id=%s AND sheet=%s AND source_row IN ('+
        ','.join('%s' for _ in source_rows)+')',(identifier,sheet,*source_rows)).fetchall()
    if len(rows)!=len(set(source_rows)):raise ValueError('所选报告行不存在')
    return import_candidates(db,{'markets':[report],'words':[r['keyword'] for r in rows], 'source':'点点报告 / '+sheet,
        'source_url':'https://app.diandian.com/vip/download/app-report','license':'用户授权导入，不公开再分发',
        'attribution':'点点数据 / '+report['app_id'],'source_period':report['digest']})
