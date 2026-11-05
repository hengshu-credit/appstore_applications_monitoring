"""Web console and independent priority-pool worker CLI."""
import logging
import os
import time
from .db import reserve
from .intelligence import work_watchlist, work_observations
from .keywords import work_keywords
from .alerts import evaluate
from .exports import work_export

log = logging.getLogger(__name__)


def run_once(cfg, db, limit=5, include_icons=True):
    from .collection import work_plans
    plans = work_plans(db)
    result = {"plans": plans, "watchlist": work_watchlist(cfg, db, limit),
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
    # A slow chart or review request must not block visible-app icon requests.
    from threading import Thread
    Thread(target=icon_worker,args=(cfg,),daemon=True,name='app-icons').start()
    Thread(target=appfigures_worker,args=(cfg,),daemon=True,name='appfigures').start()
    while True:
        try:
            log.info("Console cycle %s", run_once(cfg, db,include_icons=False))
        except Exception as exc:
            log.error("Console cycle failed: %s", type(exc).__name__)
        time.sleep(10)


def appfigures_worker(cfg):
    from .db import connect
    from .appfigures_source import work
    while True:
        db=None
        try:
            db=connect(cfg,initialize=False)
            result=work(cfg,db)
            if result['processed'] or result['error']:log.info('Appfigures cycle %s',result)
        except Exception as exc:log.error('Appfigures cycle failed: %s',type(exc).__name__)
        finally:
            if db is not None:db.close()
        time.sleep(3)


def icon_worker(cfg):
    from .db import connect
    from .app_icons import work_icons
    from .stores import Client
    while True:
        db=None;client=None
        try:
            db=connect(cfg,initialize=False)
            client=Client(cfg,db)
            result=work_icons(cfg,db,5,client)
            if result['processed']:log.info('Icon cycle %s',result)
        except Exception as exc:log.error('Icon cycle failed: %s',type(exc).__name__)
        finally:
            if client is not None:client.session.close()
            if db is not None:db.close()
        time.sleep(3)
