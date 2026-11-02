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


def run_once(cfg, db, limit=5):
    from .collection import work_plans
    plans = work_plans(db)
    result = {"plans": plans, "watchlist": work_watchlist(cfg, db, limit),
              "observations": work_observations(cfg, db, limit),
              "keywords": work_keywords(cfg, db, limit), "alerts": evaluate(db)}
    result["export"] = work_export(cfg, db)
    db.execute("INSERT INTO schedules VALUES('console-heartbeat',%s) AS incoming ON DUPLICATE KEY UPDATE next_at=incoming.next_at", (time.time(),))
    return result


def serve(cfg, host, port):
    import uvicorn
    from .web import create_app
    if host not in ("localhost", "127.0.0.1", "::1") and len(os.environ.get("APPMON_API_TOKEN", "")) < 32:
        raise ValueError("Set APPMON_API_TOKEN to at least 32 characters before binding remotely")
    uvicorn.run(create_app(cfg), host=host, port=port, proxy_headers=False)


def worker(cfg, db):
    while True:
        try:
            log.info("Console cycle %s", run_once(cfg, db))
        except Exception as exc:
            log.error("Console cycle failed: %s", type(exc).__name__)
        time.sleep(10)
