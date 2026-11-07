"""Requeue a bounded set of active failure samples without clearing history."""
import json,time
from appmon.config import load
from appmon.db import connect
from appmon.storage_policy import media_allowed
from appmon.collection_batch import touch
from appmon.task_logs import emit


def main():
    cfg=load();db=connect(cfg,initialize=False);now=time.time();result={'at':now,'jobs':[],'icons':[]}
    try:
        with db:
            for store in ('apple','google'):
                rows=db.execute('''SELECT j.* FROM jobs j FORCE INDEX (jobs_retry) WHERE j.store=%s AND j.country='us' AND j.language='en'
                    AND j.failures>0 AND j.last_error='SSLError' AND j.lease_until<=%s
                    AND NOT EXISTS(SELECT 1 FROM watchlist w WHERE w.store=j.store AND w.app_id=j.app_id AND w.country=j.country AND w.language=j.language)
                    ORDER BY j.due LIMIT 10 FOR UPDATE SKIP LOCKED''',(store,now)).fetchall()
                for row in rows:
                    key=tuple(row[k] for k in ('store','app_id','country','language'))
                    db.execute('UPDATE jobs SET due=%s WHERE store=%s AND app_id=%s AND country=%s AND language=%s',(-now,*key))
                    result['jobs'].append({k:row[k] for k in ('store','app_id','country','language','failures','last_error')})
                if rows:emit(db,'pipelines','pool','repair_requeued','历史 TLS 失败样本已重排，失败计数与已有数据保留',details={'store':store,'tasks':len(rows)})
            rows=db.execute('''SELECT * FROM app_icons WHERE enabled=1 AND requested=1 AND failures>0 AND lease_until<=%s
                AND last_error=%s ORDER BY id LIMIT 10 FOR UPDATE SKIP LOCKED''',(now,'图标不是支持的位图格式')).fetchall()
            for row in rows:
                if not media_allowed(db,cfg,row['store'],row['app_id']):continue
                db.execute('UPDATE app_icons SET due=%s WHERE id=%s',(-now,row['id']))
                result['icons'].append({k:row[k] for k in ('id','store','app_id','country','language','failures','last_error')})
                emit(db,'icons',row['id'],'repair_requeued','图片格式校验已修复，安排重新获取并保留历史失败记录')
            touch(db)
        path=cfg['data_dir']/'exports'/'collection-recovery-samples.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'jobs':len(result['jobs']),'icons':len(result['icons']),'cleared_failures':0}),flush=True)
    finally:db.close()


if __name__=='__main__':main()
