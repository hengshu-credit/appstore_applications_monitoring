"""Adopt existing targets into dynamic dispatch without deleting targets or history."""
import argparse
import json
import time
from pathlib import Path

from appmon.config import load
from appmon.db import connect
from appmon import pipelines

TABLES = ('watchlist','keyword_targets','observation_targets','app_icons','app_permission_targets','jobs')


def inventory(db):
    result = {}
    for table in TABLES:
        enabled = '' if table=='jobs' else ',SUM(enabled=1) enabled,SUM(enabled=0) paused,SUM(refresh_hours) interval_sum'
        if table in ('keyword_targets','observation_targets'):
            enabled += ',SUM(depth) depth_sum'
        leases = '' if table=='watchlist' else ',SUM(token IS NOT NULL) token_count'
        result[table] = db.execute(f'SELECT count(*) total{enabled}{leases} FROM {table}').fetchone()
    return result


def reconcile(db, cfg, apply=False, pool_stopped=False):
    before = inventory(db)
    changed = {}
    if apply:
        with db.named_lock('collection-reconciliation'), db:
            for table in TABLES:
                if table=='watchlist':
                    continue
                changed[table] = (db.execute(f'UPDATE {table} SET token=NULL,lease_until=0 WHERE token IS NOT NULL AND lease_until<=%s',(time.time(),)).rowcount
                                  if before[table]['token_count'] else 0)
            changed['missing_jobs'] = db.execute('''INSERT INTO jobs(store,app_id,country,language)
                SELECT w.store,w.app_id,w.country,w.language FROM watchlist w LEFT JOIN jobs j USING(store,app_id,country,language)
                WHERE j.app_id IS NULL ON DUPLICATE KEY UPDATE app_id=jobs.app_id''').rowcount
            if pool_stopped:
                value = pipelines.settings(db,cfg,'pool') | {'enabled':False}
                db.execute('''INSERT INTO collection_controls VALUES('pool',%s,%s) AS incoming
                    ON DUPLICATE KEY UPDATE settings=incoming.settings,updated_at=incoming.updated_at''',(json.dumps(value),time.time()))
    after = inventory(db)
    for table in TABLES:
        if table!='jobs':
            # Compare only durable identity and enablement; stale lease cleanup is expected.
            for key in ('total','enabled','paused','interval_sum','depth_sum'):
                if before[table].get(key)!=after[table].get(key):
                    raise RuntimeError('Task identity/enablement changed unexpectedly: '+table)
    return dict(applied=apply,at=time.time(),before=before,after=after,changed=changed,
                pool_enabled=pipelines.settings(db,cfg,'pool')['enabled'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--pool-service-stopped',action='store_true')
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    cfg=load();db=connect(cfg,initialize=False)
    try:
        result=reconcile(db,cfg,args.apply,args.pool_service_stopped)
        Path(args.output).write_text(json.dumps(result,default=int,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,default=int,ensure_ascii=False))
    finally:
        db.close()


if __name__=='__main__':
    main()
