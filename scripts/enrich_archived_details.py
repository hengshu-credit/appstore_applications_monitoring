"""Reparse archived detail responses for grouped apps with optimistic writes and a backup."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from functools import lru_cache
from appmon.config import load
from appmon.db import connect,dump
from appmon.stores import APPLE_MAPPING,GOOGLE_MAPPING,detail_payload,google_detail
from appgoblin_play_scraper.features.app import parse_dom

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    cfg=load();db=connect(cfg,initialize=False)
    rows=db.execute("""SELECT l.*,s.raw_path FROM listings l JOIN snapshots s
        ON s.id=(SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.store=l.store AND s2.app_id=l.app_id
        AND s2.country=l.country AND s2.language=l.language AND s2.digest=l.digest)
        WHERE EXISTS(SELECT 1 FROM app_group_members m WHERE m.store=l.store AND m.app_id=l.app_id
        AND m.country=l.country AND m.language=l.language)""").fetchall()
    folder=Path(cfg['data_dir'])/'verification'/'detail-repair';folder.mkdir(parents=True,exist_ok=True)
    counts=dict(scanned=0,verified=0,enriched=0,identity_corrected=0,unreadable=0,concurrent=0)
    @lru_cache(maxsize=16)
    def read(path): return gzip.decompress((Path(cfg['data_dir'])/path).read_bytes()).decode('utf-8')
    with (folder/'before.jsonl').open('a',encoding='utf-8') as backup:
        for row in rows:
            counts['scanned']+=1
            try:
                raw=read(row['raw_path'])
                if row['store']=='apple':
                    matches=[a for a in json.loads(raw).get('results',[]) if str(a.get('trackId'))==row['app_id'] and a.get('wrapperType')=='software']
                    if len(matches)!=1: raise ValueError('No unique identity')
                    app=matches[0];new=detail_payload(app,APPLE_MAPPING,row['app_id'])
                elif raw.lstrip().startswith('<'):
                    app=parse_dom(raw,row['app_id'],'https://play.google.com/store/apps/details?id='+row['app_id'])
                    if app.get('appId')!=row['app_id']:raise ValueError('Wrong app')
                    new=detail_payload(app,GOOGLE_MAPPING,row['app_id'])
                else:
                    matches=[]
                    for line in raw.splitlines():
                        try:frames=json.loads(line)
                        except ValueError:continue
                        if not isinstance(frames,list):continue
                        for frame in frames:
                            if not isinstance(frame,list) or len(frame)<3 or frame[0]!='wrb.fr' or frame[1]!='Ws7gDc':continue
                            data=json.loads(frame[2])
                            try:matches.append(google_detail(data,row['app_id']))
                            except Exception:pass
                    if len(matches)!=1:raise ValueError('No unique identity')
                    new=detail_payload(matches[0],GOOGLE_MAPPING,row['app_id'])
                counts['verified']+=1
                old=json.loads(row['payload'])
                fixed=dict(old)
                for key,value in new.items():
                    if value is not None:fixed[key]=value
                changed_identity=any(old.get(k)!=fixed.get(k) for k in ('developer','developer_id','name'))
                if fixed==old:continue
                payload=dump(fixed);digest=hashlib.sha256(payload.encode()).hexdigest()
                if args.apply:
                    backup.write(dump(dict(store=row['store'],app_id=row['app_id'],country=row['country'],language=row['language'],payload=old,digest=row['digest'],raw_path=row['raw_path']))+'\n');backup.flush()
                    with db:
                        updated=db.execute('UPDATE listings SET payload=%s,digest=%s WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND digest=%s',
                            (payload,digest,row['store'],row['app_id'],row['country'],row['language'],row['digest'])).rowcount
                        if not updated:
                            counts['concurrent']+=1;continue
                counts['enriched']+=1
                counts['identity_corrected']+=int(changed_identity)
            except (OSError,ValueError,KeyError,TypeError,IndexError):
                counts['unreadable']+=1
    (folder/'summary.json').write_text(dump(counts),encoding='utf-8')
    print(dump(counts));db.close()
if __name__=='__main__':main()
