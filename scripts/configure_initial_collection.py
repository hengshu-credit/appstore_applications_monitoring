"""Apply verified wide detail batches and retain the original per-store limits."""
import argparse,json
from pathlib import Path
from appmon.config import load
from appmon.db import connect
from appmon import source_registry,collection_limits,initial_capacity

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--output',default='data/exports/initial-24h-capacity.json')
    args=parser.parse_args();cfg=load();db=connect(cfg)
    try:
        before={'limits':collection_limits.settings(db,cfg),'sources':source_registry.rows(db)}
        if args.apply:
            source_registry.save(db,cfg,'apple_lookup',{'batch_size':source_registry.CATALOG['apple_lookup']['batch_max']})
            source_registry.save(db,cfg,'google_play',{'batch_size':source_registry.CATALOG['google_play']['batch_max']})
            # Different stores may use their own existing slots concurrently.
            # This does not relax either store's interval, quotas, or pause flag.
            collection_limits.save(db,cfg,{'global_interval_seconds':min(before['limits']['global_interval_seconds'],1)})
        result={'applied':args.apply,'previous':before,'assessment':initial_capacity.report(db,cfg)}
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'applied':args.apply,'assessment':result['assessment']},ensure_ascii=True))
    finally:db.close()

if __name__=='__main__':main()
