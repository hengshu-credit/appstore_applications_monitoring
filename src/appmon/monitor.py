import gzip
import hashlib
import json
import logging
import os
import time
import uuid

from .db import add_apps, claim, dump, enqueue, key, reserve
from .ingest import disk_check, sync_seed, valid_id
from .stores import Client, FetchError
from .catalog import listing_category

log = logging.getLogger(__name__)


def raw_save(folder, raw):
    digest = hashlib.sha256(raw).hexdigest()
    path = folder / "responses" / digest[:2] / (digest + ".gz")
    path.parent.mkdir(exist_ok=True, parents=True)
    if not path.exists():
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.part")
        with gzip.open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
    return path.relative_to(folder).as_posix()


def save_result(db, cfg, job, result=None, error=None, now=None):
    now = time.time() if now is None else now
    k = key(job)
    raw_path = raw_save(cfg["data_dir"], result.raw) if result is not None else ""
    db.begin()
    try:
        current = db.execute("""SELECT * FROM jobs WHERE store=%s AND app_id=%s AND country=%s
                             AND language=%s AND token=%s FOR UPDATE""", (*k, job["token"])).fetchone()
        if current is None:
            db.rollback()
            return False  # A replacement worker owns the lease; discard stale results.
        previous = db.execute("SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s", k).fetchone()
        events = []
        if error is not None:
            failures = current["failures"] + 1
            due = now + max(min(86400, 60 * 2 ** min(failures, 10)), getattr(error, "retry_after", 0))
            error_text = str(error)
            outcome = "error"
        else:
            failures, error_text = 0, None
            outcome = result.outcome
            due = now + cfg["refresh_hours"] * 3600
            last_success = previous["last_success"] if previous else None
            payload = previous["payload"] if previous else None
            digest = previous["digest"] if previous else None
            if outcome == "ok":
                from .growth import record
                record(db, job, result.payload, now, raw_path)
                from .app_icons import remember
                remember(db,job['store'],job['app_id'],job['country'],job['language'],result.payload.get('icon'),result.payload.get('name'))
                new_payload = dump(result.payload)
                new_digest = hashlib.sha256(new_payload.encode()).hexdigest()
                state, misses, last_success = "available", 0, now
                if not previous or previous["digest"] is None:
                    events.append(("first_verified", {}))
                elif previous["state"] == "region_unavailable":
                    events.append(("reappeared", {}))
                if digest != new_digest:
                    if payload:
                        old = json.loads(payload)
                        changes = {f: {"old": old.get(f), "new": result.payload.get(f)}
                                   for f in old.keys() | result.payload.keys() if old.get(f) != result.payload.get(f)}
                        events.append(("metadata_changed", changes))
                    db.execute("""INSERT INTO snapshots(store,app_id,country,language,observed_at,digest,raw_path,payload)
                                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""", (*k, now, new_digest, raw_path, new_payload))
                payload, digest = new_payload, new_digest
                db.execute("UPDATE apps SET name=%s,category=%s WHERE store=%s AND app_id=%s",
                           (result.payload.get("name"), listing_category(job["store"], result.payload), job["store"], job["app_id"]))
            elif outcome == "missing":
                misses = (previous["misses"] if previous else 0)
                # Repeated manual invocations cannot accelerate confirmation.
                if not previous or not misses or now - previous["checked_at"] >= cfg["missing_recheck_hours"] * 3600:
                    misses += 1
                state = "region_unavailable" if misses >= cfg["missing_confirmations"] else "suspected_missing"
                due = now + cfg["missing_recheck_hours"] * 3600
                if state == "region_unavailable" and (not previous or previous["state"] != state):
                    events.append(("region_unavailable", {"confirmations": misses, "evidence": raw_path}))
            else:
                raise ValueError(f"Unknown result outcome: {outcome}")
            db.execute("""INSERT INTO listings VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS incoming
                ON DUPLICATE KEY UPDATE state=incoming.state,checked_at=incoming.checked_at,
                last_success=incoming.last_success,misses=incoming.misses,
                payload=incoming.payload,digest=incoming.digest""",
                       (*k, state, now, last_success, misses, payload, digest))
        db.execute("""INSERT INTO requests(store,app_id,country,language,observed_at,outcome,error)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""", (*k, now, outcome, error_text))
        for kind, changes in events:
            db.execute("""INSERT INTO events(store,app_id,country,language,observed_at,kind,changes)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)""", (*k, now, kind, dump(changes)))
        db.execute("""UPDATE jobs SET due=%s,lease_until=0,token=NULL,failures=%s,last_error=%s
                      WHERE store=%s AND app_id=%s AND country=%s AND language=%s""",
                   (due, failures, error_text, *k))
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise


def work(cfg, db, limit, client=None, managed=False):
    from .detail_batches import work_details
    return work_details(cfg,db,limit,client or Client(cfg,db),managed=managed)


def discover(cfg, db, client=None, max_sources=None, managed=False):
    client = client or Client(cfg, db)
    sources = []
    for country in cfg["countries"]:
        sources += [("apple", country, "chart", term) for term in ("top-free", "top-paid")]
        sources += [("google", country, "chart", term) for term in cfg["google_categories"]]
        sources += [(store, country, "search", term) for store in ("apple", "google") for term in cfg["search_terms"]]
        developers = db.execute("""SELECT store,NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer_id')),'null') developer
                FROM listings WHERE payload IS NOT NULL AND country=%s AND state='available'
                GROUP BY store,developer ORDER BY MAX(checked_at) DESC LIMIT 100""", (country,)).fetchall()
        sources += [(r["store"], country, "developer", str(r["developer"])) for r in developers if r["developer"]]
    results = []
    for source in sources[:max_sources]:
        if managed:
            from .pipelines import settings
            if not settings(db,cfg,'discovery')['enabled']:
                break
        store, country, kind, term = source
        name = ":".join(source)
        schedule = db.execute("SELECT next_at FROM schedules WHERE name=%s", ("discover:" + name,)).fetchone()
        if schedule and schedule["next_at"] > time.time():
            continue
        from .task_logs import execution
        with execution(db,client,'pipelines','discovery',name) as task_log:
            failure = None
            try:
                rows = client.discover(*source)
                if any(not valid_id(row[0], row[1]) for row in rows):
                    raise ValueError("Invalid app ID in discovery response")
                with db:
                    added = add_apps(db, rows, name)
                    # Enqueue only the newly observed set, without re-scanning millions of IDs.
                    db.executemany("INSERT INTO jobs(store,app_id,country,language,due) VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE app_id=jobs.app_id",
                                   [(s, a, c, cfg["language"], -time.time()) for s, a, _, _ in rows for c in cfg["countries"]])
                    db.execute("INSERT INTO discovery_runs(source,observed_at,count,added) VALUES(%s,%s,%s,%s)",
                               (name, time.time(), len(rows), added))
                reserve(db, "discover:" + name, cfg["discovery_hours"] * 3600)
                results.append({"source": name, "count": len(rows), "added": added})
                task_log.finish("ok",detail=f"发现 {len(rows)} 个应用，新增 {added} 个")
            except (FetchError, ValueError, KeyError, TypeError) as e:
                message = str(e) if isinstance(e, FetchError) else type(e).__name__
                with db:
                    db.execute("INSERT INTO discovery_runs(source,observed_at,count,added,error) VALUES(%s,%s,0,0,%s)",
                               (name, time.time(), message))
                reserve(db, "discover:" + name, 3600)
                results.append({"source": name, "error": message})
                task_log.finish("error",detail=message+"；1小时后重试",error=e)
        log.info("Discovery %s: %s", name, results[-1])
    return results


def daemon(cfg, db):
    from .keywords import work_keywords
    from .pipelines import configured
    from .task_logs import execution, emit
    while True:
        keyword_counts = work_keywords(cfg, db, cfg["batch_size"])
        if keyword_counts["processed"]:
            log.info("Keyword batch %s", keyword_counts)
        row = db.execute("SELECT next_at FROM schedules WHERE name='seed-sync'").fetchone()
        seed_enabled, seed_cfg = configured(db,cfg,'seed')
        if seed_enabled and (not row or row["next_at"] <= time.time()):
            try:
                with execution(db,None,'pipelines','seed','AppGoblin GitHub 公开种子包') as seed_log:
                    imported = sync_seed(seed_cfg, db, progress=seed_log.write)
                    seed_log.finish('ok',detail=f"已处理 {imported['processed']} 行，新增 {imported['added']} 个应用")
                reserve(db, "seed-sync", seed_cfg["seed_sync_hours"] * 3600)
            except Exception as e:
                log.error("Seed sync failed: %s", type(e).__name__)
                reserve(db, "seed-sync", 3600)
                emit(db,'pipelines','seed','retry','种子同步失败，已安排1小时后重试','warning')
        discovery_enabled, discovery_cfg = configured(db,cfg,'discovery')
        if discovery_enabled:
            discover(discovery_cfg, db, managed=True)
        pool_enabled, pool_cfg = configured(db,cfg,'pool')
        # Expand regional jobs once per configuration (and periodically for new IDs).
        if pool_enabled:
            signature = dump({'countries':pool_cfg['countries'],'language':pool_cfg['language']})
            queue_name = 'pool-enqueue:' + hashlib.sha256(signature.encode()).hexdigest()
            queued = db.execute('SELECT next_at FROM schedules WHERE name=%s',(queue_name,)).fetchone()
            if not queued or queued['next_at'] <= time.time():
                enqueue(db,pool_cfg['countries'],pool_cfg['language'])
                reserve(db,queue_name,cfg['seed_sync_hours']*3600)
        counts = work(pool_cfg, db, cfg["batch_size"], managed=True)
        log.info("Batch %s", counts)
        if not counts["processed"]:
            time.sleep(30)
