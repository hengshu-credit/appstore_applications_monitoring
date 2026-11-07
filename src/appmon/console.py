"""Web console and independent priority-pool worker CLI."""
import logging
import os
import time
from .intelligence import work_watchlist, work_observations
from .keywords import work_keywords
from .alerts import evaluate
from .exports import work_export

log = logging.getLogger(__name__)


def run_once(cfg, db, limit=5, include_icons=True):
    from .collection import work_plans
    from .app_groups import ensure_verification_jobs
    ensure_verification_jobs(db)
    plans = work_plans(db)
    result = {"plans": plans, "watchlist": work_watchlist(cfg, db, max(limit,cfg.get('apple_lookup_batch_size',50)),max_requests=limit),
              "observations": work_observations(cfg, db, limit),
              "keywords": work_keywords(cfg, db, limit), "alerts": evaluate(db)}
    result["export"] = work_export(cfg, db)
    if include_icons:
        from .app_icons import work_icons
        result['icons'] = work_icons(cfg, db, limit)
    from .availability import record_daily
    result["availability_markets"] = record_daily(db)
    db.execute("INSERT INTO schedules VALUES('console-heartbeat',%s) AS incoming ON DUPLICATE KEY UPDATE next_at=incoming.next_at", (time.time(),))
    return result


def serve(cfg, host, port):
    import uvicorn
    from .web import create_app
    uvicorn.run(create_app(cfg), host=host, port=port, proxy_headers=False)


def worker(cfg, db):
    from threading import Thread
    from .dispatcher import worker as collect_worker
    Thread(target=collect_worker,args=(cfg,'console'),daemon=True,name='collection-dispatcher').start()
    Thread(target=marketdata_worker,args=(cfg,),daemon=True,name='marketdata').start()
    Thread(target=developer_reference_worker,args=(cfg,),daemon=True,name='developer-references').start()
    while True:
        try:
            from .app_groups import ensure_verification_jobs
            from .availability import record_daily
            from .collection import work_plans
            ensure_verification_jobs(db)
            work_plans(db,100)
            if cfg.get('media_scope')=='groups_and_current_charts':
                from .db import reserve
                scheduled=db.execute("SELECT next_at FROM schedules WHERE name='media-scope-sync'").fetchone()
                if not scheduled or scheduled['next_at']<=time.time():
                    from .app_media import sync_existing
                    sync_existing(db,cfg)
                    reserve(db,'media-scope-sync',300)
            evaluate(db)
            work_export(cfg,db)
            record_daily(db)
            db.execute("INSERT INTO schedules VALUES('console-heartbeat',%s) AS incoming ON DUPLICATE KEY UPDATE next_at=incoming.next_at",(time.time(),))
        except Exception as exc:
            log.error("Console maintenance failed: %s",type(exc).__name__)
        time.sleep(10)


def developer_reference_worker(cfg):
    from .db import connect
    from .developer_refs import refresh
    while True:
        try:
            db = connect(cfg,initialize=False)
            try:
                with db.named_lock('group-developer-references'):
                    refresh(db,cfg)
            finally:
                db.close()
        except Exception as exc:
            log.error('Developer reference refresh failed: %s',type(exc).__name__)
        time.sleep(3600)


def marketdata_worker(cfg):
    from .db import connect
    from .marketdata_source import work
    while True:
        db=None
        try:
            db=connect(cfg,initialize=False)
            from .storage_policy import storage_guard
            if storage_guard(db,cfg):
                time.sleep(10)
                continue
            result=work(cfg,db)
            if result['processed'] or result['error']:log.info('Marketdata cycle %s',result)
        except Exception as exc:log.error('Marketdata cycle failed: %s',type(exc).__name__)
        finally:
            if db is not None:db.close()
        time.sleep(3)
