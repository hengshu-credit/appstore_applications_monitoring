"""Configure common-country chart boards and collect their first page through normal leases."""
import json
import sys
from pathlib import Path
from appmon.config import load
from appmon.db import connect
from appmon import global_charts
from appmon.collection import work_plans
from appmon.intelligence import claim_observation,save_observation
from appmon.observation_stores import ObservationClient
from appmon.stores import FetchError


def main():
    sys.stdout.reconfigure(encoding='utf-8');cfg=load();db=connect(cfg);client=ObservationClient(cfg,db);out=[]
    for store in ('apple','google'):
        result=global_charts.create_plan(db,{'store':store});print(json.dumps({'store':store,'plan':result}),flush=True)
        for _ in range(100):
            work_plans(db,100)
            if db.execute('SELECT status FROM collection_plans WHERE id=%s',(result['id'],)).fetchone()['status']=='complete':break
        board=global_charts.report(db,store)
        for c in board['countries']:
            for b in c['boards']:
                target=claim_observation(db,300,target_id=b['target_id'],refresh=True)
                if not target:
                    out.append({'store':store,'country':c['country'],'collection':b['collection'],'status':'leased_or_paused'});continue
                try:
                    response=client.chart(target);save_observation(db,cfg,target,response)
                    row={'store':store,'country':c['country'],'collection':b['collection'],'status':'ok','count':len(response.items)}
                except FetchError as exc:
                    save_observation(db,cfg,target,error=exc);row={'store':store,'country':c['country'],'collection':b['collection'],'status':'error','error':str(exc)}
                out.append(row);print(json.dumps(row),flush=True)
    Path('data/global-charts-live.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');db.close()
    return int(any(r['status']=='error' for r in out))


if __name__=='__main__':raise SystemExit(main())
