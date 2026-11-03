"""Fill missing Apple search names from retained responses; never alter ranks/timestamps."""
import gzip
import json
from pathlib import Path
from appmon.config import load
from appmon.db import connect
from appmon.keyword_stores import parse_apple


def repair(cfg,db):
    root=Path(cfg['data_dir']).resolve();count=0;failed=[]
    rows=db.execute("SELECT r.*,t.country FROM keyword_runs r JOIN keyword_targets t ON t.id=r.target_id WHERE r.source='apple-native-search' AND r.outcome='ok' AND EXISTS(SELECT 1 FROM keyword_ranks k WHERE k.run_id=r.id AND (k.name IS NULL OR k.name='')) ORDER BY r.id").fetchall()
    for row in rows:
        try:
            path=(root/row['raw_path']).resolve()
            if not path.is_relative_to(root):raise ValueError('response outside data directory')
            with gzip.open(path,'rb') as stream:raw=stream.read(8_000_001)
            if len(raw)>8_000_000:raise ValueError('response exceeds limit')
            apps=parse_apple(json.loads(raw),row['country'])
            with db:
                for app in apps:
                    if app['name']:
                        count+=db.execute("UPDATE keyword_ranks SET name=%s WHERE run_id=%s AND app_id=%s AND (name IS NULL OR name='')",(app['name'],row['id'],app['app_id'])).rowcount
                        db.execute("UPDATE apps SET name=%s WHERE store='apple' AND app_id=%s AND (name IS NULL OR name='')",(app['name'],app['app_id']))
        except Exception as exc:failed.append({'run_id':row['id'],'error':type(exc).__name__})
    return {'runs':len(rows),'updated_names':count,'failures':failed}


if __name__=='__main__':
    cfg=load();db=connect(cfg)
    try:
        result=repair(cfg,db);Path('data/keyword-name-repair.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    finally:db.close()
