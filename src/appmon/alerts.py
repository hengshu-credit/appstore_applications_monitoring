"""Durable in-app alerts. No outbound messages are sent by default."""
import hashlib
import json
import math
import time

from .db import dump
from .intelligence import observation_report, text
from .keywords import ranks

KINDS = {"metadata_changed", "region_unavailable", "reappeared", "keyword_move", "chart_move", "collection_error", "low_rating"}


def add_rule(db, value):
    kind = value.get("kind")
    if kind not in KINDS:
        raise ValueError("Unsupported alert kind")
    threshold = value.get("threshold", 5)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError("threshold must be a finite non-negative number")
    group = value.get("group_name", "")
    if group:
        group = text(group)
    db.execute("INSERT INTO alert_rules(name,kind,threshold,group_name,created_at) VALUES(%s,%s,%s,%s,%s)",
               (text(value.get("name", kind)), kind, threshold, group, time.time()))
    return {"id": db.cursor.lastrowid}


def emit(db, rules, kind, source_id, at, payload, magnitude=None):
    count = 0
    for rule in rules:
        if rule["kind"] != kind or at < rule["created_at"]:
            continue
        if magnitude is not None and ((kind == "low_rating" and magnitude > rule["threshold"]) or
                                      (kind != "low_rating" and magnitude < rule["threshold"])):
            continue
        if rule["group_name"]:
            if not db.execute('''SELECT 1 FROM watchlist WHERE group_name=%s AND store=%s AND app_id=%s
                AND country=%s AND language=%s LIMIT 1''',
                              (rule["group_name"], payload.get("store"), payload.get("app_id"),
                               payload.get("country"), payload.get("language"))).fetchone():
                continue
        fingerprint = hashlib.sha256(f"{rule['id']}:{kind}:{source_id}".encode()).hexdigest()
        count += db.execute('''INSERT INTO alerts(rule_id,fingerprint,observed_at,kind,title,payload)
            VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE fingerprint=alerts.fingerprint''',
                            (rule["id"], fingerprint, at, kind, rule["name"], dump(payload))).rowcount
    return count


def evaluate(db, batch_size=500):
    """Cursor updates and alerts commit together; a restart cannot lose a page."""
    rules = db.execute("SELECT * FROM alert_rules WHERE enabled=1").fetchall()
    total = 0
    for source in ("events", "keyword_runs", "observation_runs", "requests"):
        with db:
            db.execute("INSERT INTO alert_cursors(source) VALUES(%s) ON DUPLICATE KEY UPDATE source=alert_cursors.source", (source,))
            cursor = db.execute("SELECT last_id FROM alert_cursors WHERE source=%s FOR UPDATE", (source,)).fetchone()["last_id"]
            rows = db.execute(f"SELECT * FROM {source} WHERE id>%s ORDER BY id LIMIT %s", (cursor, batch_size)).fetchall()
            for row in rows:
                at = row["observed_at"]
                if source == "events":
                    total += emit(db, rules, row["kind"], row["id"], at, row)
                elif source == "requests":
                    if row["outcome"] == "error":
                        total += emit(db, rules, "collection_error", f"requests:{row['id']}", at, row)
                else:
                    table = "keyword_targets" if source == "keyword_runs" else "observation_targets"
                    target = db.execute(f"SELECT * FROM {table} WHERE id=%s", (row["target_id"],)).fetchone()
                    payload = {k: target[k] for k in ("store", "country", "language")}
                    payload.update(app_id=target.get("app_id"), target_id=target["id"], error=row["error"])
                    if row["outcome"] == "error":
                        total += emit(db, rules, "collection_error", f"{source}:{row['id']}", at, payload)
                        continue
                    if source == "keyword_runs":
                        # Compare this historical run, not the latest pointer (worker may be behind).
                        previous = db.execute("SELECT * FROM keyword_runs WHERE target_id=%s AND outcome='ok' AND id<%s ORDER BY id DESC LIMIT 1",
                                              (target["id"], row["id"])).fetchone()
                        if previous and all(row[k] == previous[k] for k in ("source", "coverage", "parser_version")):
                            changes = db.execute('''SELECT a.app_id,a.position,b.position-a.position change_value FROM keyword_ranks a
                                JOIN keyword_ranks b ON b.app_id=a.app_id WHERE a.run_id=%s AND b.run_id=%s''',
                                                 (row["id"], previous["id"])).fetchall()
                            for change in changes:
                                total += emit(db, rules, "keyword_move", f"{row['id']}:{change['app_id']}", at,
                                              payload | change | {"keyword": target["keyword"]}, abs(change["change_value"]))
                    elif target["kind"] == "chart":
                        report = observation_report(db, target["id"], row["id"])
                        for item in report["items"]:
                            if item["change"] is not None:
                                total += emit(db, rules, "chart_move", f"{row['id']}:{item['id']}", at, payload | item, abs(item["change"]))
                    elif target["kind"] == "reviews":
                        for item in db.execute("SELECT * FROM observation_items WHERE run_id=%s", (row["id"],)).fetchall():
                            data = json.loads(item["payload"])
                            total += emit(db, rules, "low_rating", f"{target['id']}:{data['id']}:{data['rating']}", at,
                                          payload | data, data["rating"])
            if rows:
                db.execute("UPDATE alert_cursors SET last_id=%s WHERE source=%s", (rows[-1]["id"], source))
    return {"created": total}


def daily_report(db, since=None):
    since = time.time() - 86400 if since is None else since
    return {"since": since, "generated_at": time.time(),
            "events": db.execute("SELECT kind,count(*) count FROM events WHERE observed_at>=%s GROUP BY kind", (since,)).fetchall(),
            "alerts": db.execute("SELECT * FROM alerts WHERE observed_at>=%s ORDER BY id DESC LIMIT 1000", (since,)).fetchall(),
            "collections": db.execute("SELECT outcome,count(*) count FROM observation_runs WHERE observed_at>=%s GROUP BY outcome", (since,)).fetchall(),
            "keywords": db.execute("SELECT outcome,count(*) count FROM keyword_runs WHERE observed_at>=%s GROUP BY outcome", (since,)).fetchall()}
