"""Persistent keyword targets, leased collection and reverse app coverage."""
import json
import logging
import math
import re
import time
import unicodedata
import uuid

from .db import add_apps
from .ingest import disk_check
from .keyword_stores import APPLE_MARKETS, PARSER_VERSION, SOURCES, KeywordClient, ordered_apps
from .monitor import raw_save
from .stores import FetchError
from .catalog import APPLE_COUNTRIES
from .locales import language_for

log = logging.getLogger(__name__)


def validate_target(value):
    store = value.get("store")
    country = str(value.get("country", "")).lower()
    language = language_for(store,country,value.get("language", "auto"))
    keyword = value.get("keyword")
    if not isinstance(keyword, str):
        raise ValueError("keyword must be text")
    keyword = " ".join(unicodedata.normalize("NFKC", keyword).split())
    if not keyword or len(keyword) > 255 or any(unicodedata.category(c).startswith("C") for c in keyword):
        raise ValueError("keyword must contain 1-255 visible characters")
    if store not in SOURCES or not re.fullmatch(r"[a-z]{2}", country):
        raise ValueError("store must be google/apple and country must be a two-letter code")
    if store == "apple" and country not in APPLE_COUNTRIES:
        raise ValueError("该地区未列入 Apple 官方 App Store 市场；请选择其他地区，不会自动切换至美国")
    depth = value.get("depth", 50)
    hours = value.get("refresh_hours", 24)
    if type(depth) is not int or not 1 <= depth <= 200:
        raise ValueError("depth must be an integer from 1 to 200")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
        raise ValueError("refresh_hours must be a finite positive number")
    return dict(store=store, country=country, language=language, keyword=keyword,
                depth=depth, refresh_hours=hours)


def add_targets(db, values):
    targets = [validate_target(value) for value in values]
    ids = []
    with db:
        for target in targets:
            # Reimports update settings, preserving pause state, history and due time.
            db.execute('''INSERT INTO keyword_targets(store,country,language,keyword,depth,refresh_hours)
                VALUES(%s,%s,%s,%s,%s,%s) AS incoming
                ON DUPLICATE KEY UPDATE depth=incoming.depth,refresh_hours=incoming.refresh_hours''',
                       tuple(target.values()))
            row = db.execute('''SELECT id FROM keyword_targets
                WHERE store=%s AND country=%s AND language=%s AND keyword=%s''',
                             tuple(target[k] for k in ("store", "country", "language", "keyword"))).fetchone()
            ids.append(row["id"])
    return {"targets": len(ids), "ids": ids}


def claim_target(db, lease_seconds, now=None, target_id=None, refresh=False):
    now = time.time() if now is None else now
    if refresh and target_id is None:
        raise ValueError("Immediate refresh requires one target ID")
    where, args = "enabled=1 AND lease_until<=%s", [now]
    if not refresh:
        where += " AND due<=%s"
        args.append(now)
    if target_id is not None:
        where += " AND id=%s"
        args.append(target_id)
    with db:
        row = db.execute(f'''SELECT * FROM keyword_targets WHERE {where}
            ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED''', args).fetchone()
        if row is None:
            return None
        row["token"] = uuid.uuid4().hex
        db.execute("UPDATE keyword_targets SET token=%s,lease_until=%s WHERE id=%s",
                   (row["token"], now + lease_seconds, row["id"]))
        return row


def save_search(db, cfg, target, result=None, error=None, now=None):
    now = time.time() if now is None else now
    if (result is None) == (error is None):
        raise ValueError("Provide either a search result or an error")
    apps = ordered_apps(target["store"], result.apps) if result else []
    if len(apps) > target["depth"]:
        raise ValueError("Search exceeded requested depth")
    raw_path = raw_save(cfg["data_dir"], result.raw) if result else None
    web_search=target['store']=='apple' and target['country'] not in APPLE_MARKETS
    with db:
        current = db.execute("SELECT * FROM keyword_targets WHERE id=%s AND token=%s FOR UPDATE",
                             (target["id"], target["token"])).fetchone()
        if current is None:
            return False
        failures = current["failures"] + 1 if error else 0
        due = now + (max(min(86400, 60 * 2 ** min(failures, 10)), getattr(error, "retry_after", 0))
                     if error else current["refresh_hours"] * 3600)
        db.execute('''INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,
            requested_depth,returned_count,coverage,raw_path,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                   (target["id"], now, "error" if error else "ok",
                    result.source if result else 'apple-web-search' if web_search else SOURCES[target["store"]],
                    result.parser_version if result else 'apple-web-v1' if web_search else PARSER_VERSION, target["depth"],
                    None if error else len(apps), result.coverage if result else "first_response",
                    raw_path, str(error) if error else None))
        run_id = db.cursor.lastrowid
        if not error:
            add_apps(db, [(target["store"], a["app_id"], a["name"], None) for a in apps],
                     f"keyword:{target['id']}", now)
            from .app_icons import remember
            for app in apps:
                if app.get('icon'):remember(db,target['store'],app['app_id'],target['country'],target['language'],app['icon'],app.get('name'))
            if apps:
                db.executemany("INSERT INTO keyword_ranks(run_id,app_id,position,name) VALUES(%s,%s,%s,%s)",
                               [(run_id, a["app_id"], n, a["name"]) for n, a in enumerate(apps, 1)])
            db.execute('''UPDATE keyword_targets SET previous_success_id=latest_success_id,
                latest_success_id=%s WHERE id=%s''', (run_id, target["id"]))
        db.execute('''UPDATE keyword_targets SET latest_attempt_id=%s,failures=%s,last_error=%s,
            due=%s,lease_until=0,token=NULL WHERE id=%s''',
                   (run_id, failures, str(error) if error else None, due, target["id"]))
    return True


def work_keywords(cfg, db, limit=200, client=None, target_id=None, refresh=False):
    if limit <= 0:
        raise ValueError("limit must be positive")
    client = client or KeywordClient(cfg, db)
    counts = dict(processed=0, ok=0, error=0, stale=0)
    # An explicit refresh is one observation, not a tight repeat loop.
    for _ in range(1 if target_id is not None else limit):
        disk_check(cfg["data_dir"], cfg["min_free_gb"])
        target = claim_target(db, cfg["lease_seconds"], target_id=target_id, refresh=refresh)
        if target is None:
            break
        from .task_logs import execution
        with execution(db,client,'keywords',target['id'],f"{target['store']} / {target['country']} / {target['language']}") as task_log:
            failure = None
            try:
                result = client.search(target)
            except FetchError as exc:
                failure = exc
                accepted = save_search(db, cfg, target, error=exc)
                outcome = "error"
                detail = str(exc) + f"；失败次数 {target['failures']+1}，已安排退避重试"
            else:
                accepted = save_search(db, cfg, target, result=result)
                outcome = "ok"
                detail = f"返回应用 {len(result.apps)} 条"
            task_log.finish(outcome,accepted,detail,error=failure)
        counts["processed"] += 1
        counts[outcome if accepted else "stale"] += 1
        log.info("Keyword %s %s/%s/%s: %s", target["id"], target["store"],
                 target["country"], target["language"], outcome)
    return counts


def keyword_daemon(cfg, db):
    while True:
        counts = work_keywords(cfg, db, cfg["batch_size"])
        log.info("Keyword batch %s", counts)
        if not counts["processed"]:
            time.sleep(10)


def target_detail(db, target_id):
    target = db.execute("SELECT * FROM keyword_targets WHERE id=%s", (target_id,)).fetchone()
    if target is None:
        raise ValueError("Unknown keyword target ID")
    return target


def set_enabled(db, target_id, enabled):
    with db:
        target_detail(db, target_id)
        # Invalidate any in-flight collector when pausing.
        db.execute("UPDATE keyword_targets SET enabled=%s,token=NULL,lease_until=0 WHERE id=%s",
                   (enabled, target_id))
    return {"id": target_id, "enabled": enabled}


def comparable(current, previous):
    return bool(current and previous and all(current[k] == previous[k]
                for k in ("source", "parser_version", "coverage")))


def observation(db, target):
    runs = {}
    for field in ("latest_attempt_id", "latest_success_id", "previous_success_id"):
        run_id = target[field]
        runs[field] = db.execute("SELECT * FROM keyword_runs WHERE id=%s", (run_id,)).fetchone() if run_id else None
    success = runs["latest_success_id"]
    return {"target": {k: v for k, v in target.items() if k != "token"},
            "latest_attempt": runs["latest_attempt_id"], "latest_success": success,
            "previous_success": runs["previous_success_id"],
            "stale": success is None or bool(target["failures"]) or
                     time.time() >= success["observed_at"] + target["refresh_hours"] * 3600}


def ranks(db, target_id):
    report = observation(db, target_detail(db, target_id))
    current, previous = report["latest_success"], report["previous_success"]
    rows = []
    old = {}
    if comparable(current, previous):
        old = {r["app_id"]: r["position"] for r in db.execute(
            "SELECT app_id,position FROM keyword_ranks WHERE run_id=%s", (previous["id"],)).fetchall()}
    if current:
        rows = db.execute("SELECT app_id,position,name FROM keyword_ranks WHERE run_id=%s ORDER BY position",
                          (current["id"],)).fetchall()
        for row in rows:
            row["previous_position"] = old.get(row["app_id"])
            row["change"] = old[row["app_id"]] - row["position"] if row["app_id"] in old else None
    report["ranks"] = rows
    return report


def app_coverage(db, store, app_id, country=None, language=None):
    filters, args = "t.store=%s AND r.app_id=%s", [store, app_id]
    if country:
        filters += " AND t.country=%s"
        args.append(country.lower())
    if language:
        filters += " AND t.language=%s"
        args.append(language.lower().replace("_", "-"))
    targets = db.execute(f'''SELECT DISTINCT t.* FROM keyword_ranks r
        JOIN keyword_runs s ON s.id=r.run_id JOIN keyword_targets t ON t.id=s.target_id
        WHERE {filters} ORDER BY t.country,t.language,t.keyword''', args).fetchall()
    coverage = []
    for target in targets:
        report = observation(db, target)
        current, previous = report["latest_success"], report["previous_success"]
        def position(run):
            row = db.execute("SELECT position FROM keyword_ranks WHERE run_id=%s AND app_id=%s",
                             (run["id"], app_id)).fetchone() if run else None
            return row["position"] if row else None
        current_position, previous_position = position(current), position(previous)
        report.update(position=current_position, previous_position=previous_position,
                      status="observed" if current_position is not None else "not_observed",
                      change=previous_position - current_position if comparable(current, previous)
                      and current_position is not None and previous_position is not None else None)
        coverage.append(report)
    return {"store": store, "app_id": app_id, "coverage": coverage,
            "note": "Only tracked keywords where this app has been observed. Missing means not in the returned results, not delisted."}


def history(db, target_id, app_id=None, limit=100):
    target = target_detail(db, target_id)
    if limit <= 0 or limit > 10000:
        raise ValueError("history limit must be from 1 to 10000")
    rows = db.execute("SELECT * FROM keyword_runs WHERE target_id=%s ORDER BY id DESC LIMIT %s",
                      (target_id, limit)).fetchall()
    for row in rows:
        if app_id is not None:
            rank = db.execute("SELECT position FROM keyword_ranks WHERE run_id=%s AND app_id=%s",
                              (row["id"], app_id)).fetchone()
            row["position"] = rank["position"] if rank else None
            row["status"] = "error" if row["outcome"] == "error" else "observed" if rank else "not_observed"
    return {"target": {k: v for k, v in target.items() if k != "token"}, "history": rows}


def configure_cli(sub):
    parser = sub.add_parser("keywords", help="Keyword ranking tracking and app coverage")
    commands = parser.add_subparsers(dest="keyword_command", required=True)
    add = commands.add_parser("add")
    add.add_argument("store", choices=["google", "apple"])
    add.add_argument("keyword")
    add.add_argument("--country", required=True)
    add.add_argument("--language", required=True)
    add.add_argument("--depth", type=int, default=50)
    add.add_argument("--refresh-hours", type=float, default=24)
    imp = commands.add_parser("import", help="Import a JSON array of keyword targets")
    imp.add_argument("path")
    report = commands.add_parser('report-import', help='Import an authorized Keyword report XLSX app report')
    report.add_argument('path')
    report.add_argument('--store',choices=['google','apple'],required=True)
    report.add_argument('--app-id',required=True)
    report.add_argument('--country',required=True)
    report.add_argument('--language',required=True)
    commands.add_parser("list")
    run = commands.add_parser("run")
    run.add_argument("--limit", type=int, default=200)
    run.add_argument("--target-id", type=int)
    run.add_argument("--refresh", action="store_true")
    commands.add_parser("daemon")
    for action in ("ranks", "history", "pause", "resume"):
        child = commands.add_parser(action)
        child.add_argument("target_id", type=int)
        if action == "history":
            child.add_argument("--app-id")
            child.add_argument("--limit", type=int, default=100)
    app = commands.add_parser("app")
    app.add_argument("store", choices=["google", "apple"])
    app.add_argument("app_id")
    app.add_argument("--country")
    app.add_argument("--language")


def execute_cli(args, cfg, db):
    from pathlib import Path
    action = args.keyword_command
    if action == 'report-import':
        import base64
        from .keyword_reports import import_report, MAX_BYTES
        path=Path(args.path)
        if path.stat().st_size>MAX_BYTES:raise ValueError('报告限3.5MB')
        return import_report(cfg,db,vars(args)|{'filename':path.name,'content_base64':base64.b64encode(path.read_bytes()).decode()})
    if action == "add":
        return add_targets(db, [vars(args)])
    if action == "import":
        values = json.loads(Path(args.path).read_text(encoding="utf-8-sig"))
        if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
            raise ValueError("Keyword import must be a JSON array of objects")
        return add_targets(db, values)
    if action == "list":
        return [{k: v for k, v in row.items() if k != "token"}
                for row in db.execute("SELECT * FROM keyword_targets ORDER BY id").fetchall()]
    if action == "run":
        return work_keywords(cfg, db, args.limit, target_id=args.target_id, refresh=args.refresh)
    if action == "daemon":
        return keyword_daemon(cfg, db)
    if action == "ranks":
        return ranks(db, args.target_id)
    if action == "history":
        return history(db, args.target_id, args.app_id, args.limit)
    if action in ("pause", "resume"):
        return set_enabled(db, args.target_id, action == "resume")
    return app_coverage(db, args.store, args.app_id, args.country, args.language)
