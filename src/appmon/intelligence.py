"""Durable public observations, watchlists and non-estimated competitor evidence."""
import hashlib
import json
import math
import re
import time
import uuid

from .db import add_apps, dump
from .ingest import valid_id, disk_check
from .keywords import validate_target, observation, comparable, app_coverage
from .monitor import raw_save, save_result
from .stores import Client, FetchError


def market(value, require_app=True):
    v = validate_target(dict(value, keyword="validation"))
    app_id = str(value.get("app_id", ""))
    if require_app and not valid_id(v["store"], app_id):
        raise ValueError("Invalid app ID for this store")
    return {k: v[k] for k in ("store", "country", "language")} | {"app_id": app_id}


def hours(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.25 <= value <= 8760:
        raise ValueError("refresh_hours must be between 0.25 and 8760")
    return value


def text(value, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"Expected text of 1-{limit} characters")
    return value.strip()


def watch(db, value):
    m = market(value)
    group = text(value.get("group_name", "重点应用"))
    interval = hours(value.get("refresh_hours", 24))
    with db:
        add_apps(db, [(m["store"], m["app_id"], None, None)], "watchlist")
        db.execute('''INSERT INTO watchlist(store,app_id,country,language,group_name,refresh_hours,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE
            group_name=incoming.group_name,refresh_hours=incoming.refresh_hours''',
                   (m["store"], m["app_id"], m["country"], m["language"], group, interval, time.time()))
        db.execute('''INSERT INTO jobs(store,app_id,country,language) VALUES(%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE app_id=jobs.app_id''',
                   (m["store"], m["app_id"], m["country"], m["language"]))
    return m | {"group_name": group, "refresh_hours": interval}


def add_observation_target(db, value):
    kind = value.get("kind")
    if kind not in ("chart", "reviews", "featured"):
        raise ValueError("kind must be chart, reviews or featured")
    m = market(value, kind == "reviews")
    m["app_id"] = m["app_id"] if kind == "reviews" else ""
    collection = value.get("collection", "top-free") if kind == "chart" else ""
    category = value.get("category", "") if kind == "chart" else ""
    if kind == "featured" and m["store"] != "apple":
        raise ValueError("Featured observations currently support Apple Today")
    if collection not in ("", "top-free", "top-paid", "top-grossing"):
        raise ValueError("Unknown chart collection")
    if not isinstance(category, str) or not re.fullmatch(r"[A-Za-z0-9_]{0,64}", category):
        raise ValueError("Invalid category identifier")
    if m["store"] == "apple" and category and not category.isdigit():
        raise ValueError("Apple categories use numeric genre IDs")
    depth = value.get("depth", 100)
    if type(depth) is not int or not 1 <= depth <= 500:
        raise ValueError("depth must be an integer from 1 to 500")
    interval = hours(value.get("refresh_hours", 24))
    identity = (kind, m["store"], m["app_id"], m["country"], m["language"], collection, category)
    with db:
        db.execute('''INSERT INTO observation_targets(kind,store,app_id,country,language,collection,category,depth,refresh_hours)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS incoming
            ON DUPLICATE KEY UPDATE depth=incoming.depth,refresh_hours=incoming.refresh_hours''',
                   (*identity, depth, interval))
        row = db.execute('''SELECT id FROM observation_targets WHERE kind=%s AND store=%s AND app_id=%s
            AND country=%s AND language=%s AND collection=%s AND category=%s''', identity).fetchone()
    return row


def claim_observation(db, lease_seconds, now=None):
    now = time.time() if now is None else now
    with db:
        row = db.execute('''SELECT * FROM observation_targets WHERE enabled=1 AND due<=%s
            AND lease_until<=%s ORDER BY due,id LIMIT 1 FOR UPDATE SKIP LOCKED''', (now, now)).fetchone()
        if not row:
            return None
        row["token"] = uuid.uuid4().hex
        db.execute("UPDATE observation_targets SET token=%s,lease_until=%s WHERE id=%s",
                   (row["token"], now + lease_seconds, row["id"]))
        return row


def save_observation(db, cfg, target, result=None, error=None, now=None):
    now = time.time() if now is None else now
    if (result is None) == (error is None):
        raise ValueError("Provide one result or error")
    if result:
        seen = set()
        for item in result.items:
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id.isascii() or not 1 <= len(item_id) <= 512 or item_id in seen:
                raise ValueError("Duplicate or invalid observation item ID")
            seen.add(item_id)
            if target["kind"] == "reviews" and (len(item_id) > 255 or type(item.get("rating")) is not int or not 1 <= item["rating"] <= 5):
                raise ValueError("Invalid review")
        if len(result.items) > target["depth"]:
            raise ValueError("Observation exceeds configured depth")
    raw = raw_save(cfg["data_dir"], result.raw) if result else None
    with db:
        current = db.execute("SELECT * FROM observation_targets WHERE id=%s AND token=%s FOR UPDATE",
                             (target["id"], target["token"])).fetchone()
        if current is None:
            return False
        failures = current["failures"] + 1 if error else 0
        due = now + (max(min(86400, 60 * 2 ** min(failures, 10)), getattr(error, "retry_after", 0))
                     if error else current["refresh_hours"] * 3600)
        db.execute('''INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,
            returned_count,raw_path,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                   (target["id"], now, "error" if error else "ok", result.source if result else target["store"],
                    result.parser_version if result else "observations-v1", result.coverage if result else "unknown",
                    len(result.items) if result else None, raw, str(error) if error else None))
        run_id = db.cursor.lastrowid
        if result:
            for i, item in enumerate(result.items, 1):
                db.execute("INSERT INTO observation_items VALUES(%s,%s,%s,%s)", (run_id, item["id"], i, dump(item)))
                if target["kind"] == "reviews":
                    db.execute('''INSERT INTO reviews VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS incoming
                        ON DUPLICATE KEY UPDATE rating=incoming.rating,content=incoming.content,title=incoming.title,
                        version=incoming.version,published_at=incoming.published_at,last_seen=incoming.last_seen,payload=incoming.payload''',
                               (target["store"], target["app_id"], target["country"], target["language"], item["id"],
                                item["rating"], item.get("content", ""), item.get("title"), item.get("version"),
                                item.get("published_at"), now, now, dump(item)))
                elif valid_id(target["store"], item.get("app_id", "")):
                    add_apps(db, [(target["store"], item["app_id"], item.get("name"), target["category"] or None)],
                             f"{target['kind']}:{target['id']}", now)
            db.execute('''UPDATE observation_targets SET previous_success_id=latest_success_id,
                latest_success_id=%s WHERE id=%s''', (run_id, target["id"]))
        db.execute('''UPDATE observation_targets SET latest_attempt_id=%s,failures=%s,last_error=%s,
            due=%s,lease_until=0,token=NULL WHERE id=%s''',
                   (run_id, failures, str(error) if error else None, due, target["id"]))
    return True


def observation_report(db, target_id, run_id=None):
    target = db.execute("SELECT * FROM observation_targets WHERE id=%s", (target_id,)).fetchone()
    if not target:
        raise ValueError("Unknown observation target")
    def run(identifier):
        return db.execute("SELECT * FROM observation_runs WHERE id=%s AND target_id=%s", (identifier, target_id)).fetchone() if identifier else None
    current = run(run_id or target["latest_success_id"])
    if run_id and not current:
        raise ValueError("Unknown run for target")
    previous = (db.execute("SELECT * FROM observation_runs WHERE target_id=%s AND id<%s AND outcome='ok' ORDER BY id DESC LIMIT 1",
                           (target_id, current["id"])).fetchone() if current else None)
    def items(r):
        return [json.loads(x["payload"]) | {"position": x["position"]} for x in db.execute(
            "SELECT * FROM observation_items WHERE run_id=%s ORDER BY position", (r["id"],)).fetchall()] if r else []
    rows, old_rows = items(current), items(previous) if comparable(current, previous) else []
    old = {x["id"]: x for x in old_rows}
    for row in rows:
        before = old.get(row["id"])
        row.update(previous_position=before["position"] if before else None,
                   change=before["position"] - row["position"] if before else None,
                   status="observed" if before or not previous else "entered_observed_range")
    seen = {x["id"] for x in rows}
    return {"target": {k: v for k, v in target.items() if k != "token"}, "latest_success": current,
            "latest_attempt": run(target["latest_attempt_id"]), "previous_success": previous,
            "stale": not current or bool(target["failures"]) or time.time() >= current["observed_at"] + target["refresh_hours"] * 3600,
            "items": rows, "left_observed_range": [x for x in old_rows if x["id"] not in seen],
            "history": db.execute("SELECT * FROM observation_runs WHERE target_id=%s ORDER BY id DESC LIMIT 100", (target_id,)).fetchall()}


def work_watchlist(cfg, db, limit=10, client=None):
    client = client or Client(cfg, db)
    count = dict(processed=0, ok=0, error=0, stale=0)
    for _ in range(limit):
        now = time.time()
        with db:
            job = db.execute('''SELECT j.*,w.refresh_hours FROM jobs j JOIN watchlist w
                USING(store,app_id,country,language) WHERE w.enabled=1 AND j.due<=%s AND j.lease_until<=%s
                ORDER BY j.due LIMIT 1 FOR UPDATE SKIP LOCKED''', (now, now)).fetchone()
            if not job:
                break
            job["token"] = uuid.uuid4().hex
            db.execute("UPDATE jobs SET token=%s,lease_until=%s WHERE store=%s AND app_id=%s AND country=%s AND language=%s",
                       (job["token"], now + cfg["lease_seconds"], job["store"], job["app_id"], job["country"], job["language"]))
        disk_check(cfg["data_dir"], cfg["min_free_gb"])
        local_cfg = cfg | {"refresh_hours": job["refresh_hours"]}
        try:
            result = client.fetch(job)
        except FetchError as exc:
            accepted = save_result(db, local_cfg, job, error=exc)
            outcome = "error"
        else:
            accepted = save_result(db, local_cfg, job, result=result)
            outcome = "ok"
        count[outcome if accepted else "stale"] += 1
        count["processed"] += 1
    return count


def work_observations(cfg, db, limit=10, client=None):
    from .observation_stores import ObservationClient
    client = client or ObservationClient(cfg, db)
    count = dict(processed=0, ok=0, error=0, stale=0)
    for _ in range(limit):
        disk_check(cfg["data_dir"], cfg["min_free_gb"])
        target = claim_observation(db, max(cfg["lease_seconds"], 900))
        if not target:
            break
        try:
            result = client.fetch_observation(target)
        except (FetchError, ValueError, KeyError, TypeError, IndexError) as exc:
            error = exc if isinstance(exc, FetchError) else FetchError(f"Parser error: {type(exc).__name__}")
            accepted = save_observation(db, cfg, target, error=error)
            outcome = "error"
        else:
            accepted = save_observation(db, cfg, target, result=result)
            outcome = "ok"
        count["processed"] += 1
        count[outcome if accepted else "stale"] += 1
    return count


def compare_apps(db, values):
    if not 2 <= len(values) <= 6:
        raise ValueError("Compare 2-6 apps in the same market")
    markets = [market(v) for v in values]
    if len({(m["store"], m["country"], m["language"]) for m in markets}) != 1:
        raise ValueError("Comparisons require the same store, country and language")
    columns, coverage = [], []
    for m in markets:
        listing = db.execute("SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s",
                             (m["store"], m["app_id"], m["country"], m["language"])).fetchone()
        words = app_coverage(db, m["store"], m["app_id"], m["country"], m["language"])["coverage"]
        coverage.append({w["target"]["keyword"]: w for w in words if w["position"] is not None and not w["stale"]})
        columns.append(m | {"listing": listing, "payload": json.loads(listing["payload"]) if listing and listing["payload"] else None})
    union = sorted(set().union(*(set(c) for c in coverage)))
    matrix = [{"keyword": w, "positions": [c[w]["position"] if w in c else None for c in coverage],
               "observed_at": [c[w]["latest_success"]["observed_at"] if w in c else None for c in coverage]} for w in union]
    return {"apps": columns, "keywords": matrix,
            "common": sorted(set.intersection(*(set(c) for c in coverage))),
            "gaps_for_first": [w for w in union if w not in coverage[0]],
            "note": "Only fresh observations in the tracked keyword dictionary; absence is not zero traffic."}
