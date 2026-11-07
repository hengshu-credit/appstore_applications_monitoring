"""Queue configured global Top-50 charts and their ongoing app enrichment."""
import json
import time
from appmon.config import load
from appmon.db import connect
from appmon import chart_enrichment,collection_limits
from appmon.storage_policy import storage_guard


def main():
    cfg=load();db=connect(cfg)
    try:
        if cfg.get('archive_videos',True):raise ValueError('Video archives must stay disabled')
        storage_guard(db,cfg)
        plan=chart_enrichment.start(db)
        first=chart_enrichment.sync(db,cfg,20)
        result={'at':time.time(),'plan':plan,'first_followups':first,'progress':chart_enrichment.report(db),
                'requests_paused':collection_limits.settings(db,cfg)['paused']}
        path=cfg['data_dir']/'exports'/'global-top50-start.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=True),flush=True)
    finally:db.close()


if __name__=='__main__':main()
