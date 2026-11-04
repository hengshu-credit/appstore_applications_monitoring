import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import load
from .db import add_apps, connect, enqueue, status
from .ingest import import_file, sync_seed, valid_id
from .monitor import daemon, discover, work
from .keywords import configure_cli, execute_cli


def main():
    # Windows redirected consoles may default to GBK, which cannot print Thai
    # keywords. JSON output is UTF-8 on every platform, including redirected files.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Global known app pool and regional store monitoring")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    configure_cli(sub)
    accounts = sub.add_parser('accounts', help='Local operator account recovery')
    accounts.add_argument('action', choices=['recover-admin'])
    web = sub.add_parser("web", help="Start the local observation console")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    console = sub.add_parser("console", help="Priority monitoring, charts, reviews, alerts and exports")
    console.add_argument("action", choices=["run", "daemon"])
    console.add_argument("--limit", type=int, default=5)
    sub.add_parser("init")
    backfill=sub.add_parser('metrics-backfill',help='Replay retained metric snapshots without network requests')
    backfill.add_argument('--after-id',type=int,default=0)
    backfill.add_argument('--limit',type=int,default=500)
    backfill.add_argument('--upper-id',type=int)
    sub.add_parser("sync-seed")
    imp = sub.add_parser("import")
    imp.add_argument("path")
    imp.add_argument("--source", required=True)
    sub.add_parser("enqueue")
    migration = sub.add_parser("migrate-sqlite")
    migration.add_argument("path")
    migration.add_argument("--report", default="data/mysql-migration.json")
    add = sub.add_parser("add")
    add.add_argument("store", choices=["apple", "google"])
    add.add_argument("ids", nargs="+")
    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=200)
    discovery = sub.add_parser("discover")
    discovery.add_argument("--max-sources", type=int)
    sub.add_parser("daemon")
    report = sub.add_parser("status")
    report.add_argument("--output")
    exp = sub.add_parser("export")
    exp.add_argument("table", choices=["apps", "events", "snapshots", "listings", "requests",
                                       "keyword_targets", "keyword_runs", "keyword_ranks"])
    exp.add_argument("path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load(args.config)
    if args.command == "web":
        from .console import serve
        serve(cfg, args.host, args.port)
        return 0
    if args.command == "migrate-sqlite":
        from .migrate import migrate
        result = migrate(cfg, args.path, report_path=args.report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    db = connect(cfg)
    try:
        if args.command == 'accounts':
            from .account_recovery import run as recover_account
            try:
                recover_account(db)
            except ValueError as exc:
                parser.error(str(exc))
            return 0
        if args.command == "console":
            from .console import run_once, worker
            if args.limit <= 0:
                parser.error("limit must be positive")
            if args.action == "daemon":
                worker(cfg, db)
                return 0
            result = run_once(cfg, db, args.limit)
        elif args.command == "keywords":
            try:
                result = execute_cli(args, cfg, db)
            except ValueError as exc:
                parser.error(str(exc))
        elif args.command == 'metrics-backfill':
            from .growth import backfill
            result=backfill(cfg,db,args.after_id,args.limit,args.upper_id)
        elif args.command == "init":
            result = {"database": cfg["mysql"]["database"], "storage": "mysql"}
        elif args.command == "sync-seed":
            result = sync_seed(cfg, db)
        elif args.command == "import":
            result = import_file(db, args.path, args.source, cfg["min_free_gb"])
        elif args.command == "enqueue":
            result = {"added": enqueue(db, cfg["countries"], cfg["language"])}
        elif args.command == "add":
            if not all(valid_id(args.store, a) for a in args.ids):
                parser.error("Invalid store app ID")
            with db:
                added = add_apps(db, [(args.store, a, None, None) for a in args.ids], "manual")
                db.executemany("INSERT INTO jobs(store,app_id,country,language,due) VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE app_id=jobs.app_id",
                               [(args.store, a, c, cfg["language"], -time.time()) for a in args.ids for c in cfg["countries"]])
            result = {"added": added}
        elif args.command == "run":
            if args.limit <= 0:
                parser.error("limit must be positive")
            result = work(cfg, db, args.limit)
        elif args.command == "discover":
            if args.max_sources is not None and args.max_sources <= 0:
                parser.error("max-sources must be positive")
            result = discover(cfg, db, max_sources=args.max_sources)
        elif args.command == "daemon":
            daemon(cfg, db)
            return
        elif args.command == "status":
            result = status(db)
            if args.output:
                Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            count = 0
            target = Path(args.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(target.suffix + ".part")
            with temp.open("w", encoding="utf-8") as out:
                for row in db.iter_rows(f"SELECT * FROM {args.table}"):
                    out.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                    count += 1
            temp.replace(target)
            result = {"rows": count, "path": str(target)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "run" and result["error"]:
            return 2
        if args.command == "console" and any(result.get(k,{}).get("error",0) for k in ("plans", "watchlist", "observations", "keywords", "icons")):
            return 2
        if args.command == "keywords" and args.keyword_command == "run" and result["error"]:
            return 2
        if args.command == "discover" and any("error" in r for r in result):
            return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
