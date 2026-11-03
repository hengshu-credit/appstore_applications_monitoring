"""Import bound app reports, compare identical files and summarize market-specific word overlap."""
import argparse
import base64
import csv
import hashlib
import json
import sys
from pathlib import Path
from itertools import combinations
from appmon.config import load
from appmon.db import connect
from appmon.keyword_reports import parse, import_report, promote, SHEETS
from appmon.keywords import validate_target


def main():
    sys.stdout.reconfigure(encoding='utf-8');p=argparse.ArgumentParser()
    p.add_argument('folder');p.add_argument('--bindings',required=True);p.add_argument('--output',default='data/keyword-report-comparison.json');args=p.parse_args()
    bindings=json.loads(Path(args.bindings).read_text(encoding='utf-8'));cfg=load();db=connect(cfg)
    seen={};files=[];sets={};groups={};word_sources={}
    for path in sorted(Path(args.folder).glob('*数据报告*.xlsx')):
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest in seen:files.append({'file':path.name,'duplicate_of':seen[digest],'digest':digest});continue
        seen[digest]=path.name;binding=next((b for b in bindings if path.name.startswith(b['prefix'])),None)
        if not binding:raise ValueError('缺少已核实应用绑定：'+path.name)
        data=parse(raw);m=validate_target(binding|{'keyword':'validation'})
        result=import_report(cfg,db,binding|{'filename':path.name,'content_base64':base64.b64encode(raw).decode()})
        valid=[];invalid=[]
        for r in data['items']:
            try:validate_target(m|{'keyword':r['keyword']});valid.append(r)
            except ValueError:invalid.append(r)
        for sheet in SHEETS:
            numbers=[r['source_row'] for r in valid if r['sheet']==sheet]
            for start in range(0,len(numbers),1000):promote(db,{'report_id':result['id'],'sheet':sheet,'source_rows':numbers[start:start+1000]})
        terms={validate_target(m|{'keyword':r['keyword']})['keyword'] for r in valid}
        key=(m['store'],m['country'],m['language']);sets[path.name]=(key,terms);groups.setdefault(key,set()).update(terms)
        for row in valid:
            term=validate_target(m|{'keyword':row['keyword']})['keyword']
            sources=word_sources.setdefault((*key,term),{'apps':set(),'sheets':set(),'reports':set()})
            sources['apps'].add(binding['app_id']);sources['sheets'].add(row['sheet']);sources['reports'].add(str(result['id']))
        files.append({'file':path.name,'digest':digest,'report_id':result['id'],'store':m['store'],'country':m['country'],'language':m['language'],
            'records':len(data['items']),'valid_records':len(valid),'rejected_records':len(invalid),'unique_keywords':len(terms),'observed_at':data['inferred']['observed_at'],
            'sheets':{sheet:sum(r['sheet']==sheet for r in data['items']) for sheet in SHEETS}})
    overlap=[]
    for (a,(ma,sa)),(b,(mb,sb)) in combinations(sets.items(),2):
        if ma==mb:overlap.append({'first':a,'second':b,'shared':len(sa&sb),'only_first':len(sa-sb),'only_second':len(sb-sa),'union':len(sa|sb)})
    result={'files':files,'unique_files':len(seen),'total_keyword_records':sum(f.get('records',0) for f in files),
            'market_unique_keywords':[{'store':k[0],'country':k[1],'language':k[2],'unique_keywords':len(v)} for k,v in groups.items()],
            'overlap':overlap,'note':'语言为后续追踪配置；报告自身未明确搜索请求语言。日期按词表标题，非文件名全时段。占位行不计关键词。'}
    export=Path(cfg['data_dir'])/'exports'/'report-keywords-summary.tsv';export.parent.mkdir(parents=True,exist_ok=True)
    def cell(v):return "'"+v if v.lstrip().startswith(('=','+','-','@')) else v
    with export.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream,delimiter='\t');writer.writerow(['store','country','tracking_language','keyword','source_app_ids','source_types','report_ids'])
        for key,sources in sorted(word_sources.items()):writer.writerow([*key[:3],cell(key[3]),';'.join(sorted(sources['apps'])),';'.join(sorted(sources['sheets'])),';'.join(sorted(sources['reports']))])
    result['export_rows']=len(word_sources);result['export_path']=str(export)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');db.close();print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
