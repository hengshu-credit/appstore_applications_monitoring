"""Explicit live verification; never runs in CI or silently queues a whole word pool."""
import argparse
import base64
import json
import sys
import time
from pathlib import Path
from appmon.config import load
from appmon.db import connect
from appmon import growth, keyword_discovery as kd, keyword_reports as kr
from appmon.keywords import add_targets, work_keywords
from appmon.monitor import raw_save
from appmon.stores import Client


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',required=True)
    parser.add_argument('--output',default='data/discovery-live.json')
    args=parser.parse_args();cfg=load();db=connect(cfg);result={'checked_at':time.time(),'keywords':[],'metrics':[],'wordlists':[]}
    path=Path(args.report);raw=path.read_bytes()
    payload=dict(store='apple',app_id='6459478672',country='cn',language='zh-cn',filename=path.name,content_base64=base64.b64encode(raw).decode())
    result['report']=kr.import_report(cfg,db,payload)
    parsed=kr.parse(raw);result['report'].update(digest=parsed['digest'],sheets=parsed['summary'],unique_words=len({r['keyword'] for r in parsed['items']}))
    for sheet in kr.SHEETS:
        numbers=[r['source_row'] for r in parsed['items'] if r['sheet']==sheet]
        for start in range(0,len(numbers),1000):
            kr.promote(db,{'report_id':result['report']['id'],'sheet':sheet,'source_rows':numbers[start:start+1000]})
    for country,language in [('us','en'),('th','th'),('vn','vi'),('id','id')]:
        try:
            outcome=kd.import_frequency(db,{'language':language,'limit':500,'markets':[{'store':s,'country':country,'language':language} for s in ('google','apple')]})
            result['wordlists'].append({'country':country,'language':language,**outcome})
        except Exception as exc:result['wordlists'].append({'country':country,'language':language,'error':str(exc)})
    for store,country,language,term in [('apple','cn','zh-cn','豆包'),('google','us','en','chat'),('apple','us','en','chat'),('google','th','th','แชท')]:
        identifier=add_targets(db,[dict(store=store,country=country,language=language,keyword=term,depth=50)])['ids'][0]
        outcome=work_keywords(cfg,db,1,target_id=identifier,refresh=True)
        row=db.execute('SELECT r.outcome,r.returned_count,r.observed_at,r.source FROM keyword_targets t LEFT JOIN keyword_runs r ON r.id=t.latest_success_id WHERE t.id=%s',(identifier,)).fetchone()
        result['keywords'].append({'store':store,'country':country,'language':language,'keyword':term,'work':outcome,'latest_success':row})
    client=Client(cfg,db)
    for store,app_id,country,language in [('google','com.openai.chatgpt','us','en'),('apple','6459478672','cn','zh-cn')]:
        m=dict(store=store,app_id=app_id,country=country,language=language)
        try:
            response=client.fetch(m)
            if response.outcome!='ok':raise ValueError('Public detail unavailable')
            now=time.time();growth.record(db,m,response.payload,now,raw_save(cfg['data_dir'],response.raw))
            result['metrics'].append(m|{'outcome':'ok','observed_at':now,'installs_min':response.payload.get('installs'),'installs_reported':response.payload.get('installs_reported')})
        except Exception as exc:result['metrics'].append(m|{'error':str(exc)})
    result['coverage_cn']=kd.coverage(db,dict(store='apple',country='cn',language='zh-cn'),'6459478672')['stats']
    result['persisted_report_rows']=db.execute('SELECT sheet,count(*) rows_count FROM keyword_report_rows WHERE report_id=%s GROUP BY sheet',(result['report']['id'],)).fetchall()
    result['candidate_markets']=db.execute('SELECT store,country,language,count(*) count FROM keyword_candidates GROUP BY store,country,language').fetchall()
    db.close();Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,default=str))
    return int(any('error' in row for row in result['wordlists']+result['metrics']) or any(r['work'].get('error') for r in result['keywords']))


if __name__=='__main__':raise SystemExit(main())
