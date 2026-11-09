"""Leased exports with bounded local staging and atomic object publication."""
import json
import os
import time
import uuid
from .db import connect, dump
from .ingest import disk_check

KINDS = {"apps", "events", "listings", "snapshots", "reviews", "alerts", "ad_observations", "ad_reports", "keyword_ranks", "observation_items",
         "aso_drafts", "aso_draft_versions", "comparison_sets", "event_annotations", "collection_plans"}
FILTERS = {"store", "app_id", "country", "language", "kind", "run_id"}


def request_export(db, kind, filters):
    if kind not in KINDS or not isinstance(filters, dict) or set(filters) - FILTERS:
        raise ValueError("Invalid export kind or filters")
    columns = {r["Field"] for r in db.execute(f"SHOW COLUMNS FROM {kind}").fetchall()}
    if set(filters) - columns or any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in filters.values()):
        raise ValueError("Filter unavailable for selected dataset")
    identifier = uuid.uuid4().hex
    db.execute("INSERT INTO exports(id,kind,filters,created_at) VALUES(%s,%s,%s,%s)",
               (identifier, kind, dump(filters), time.time()))
    return {"id": identifier, "status": "queued"}


def work_export(cfg, db):
    with db:
        row = db.claim("SELECT * FROM exports WHERE status IN ('queued','running') AND lease_until<=%s ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED", (time.time(),)).fetchone()
        if not row:
            return {"processed": 0}
        token = uuid.uuid4().hex
        db.execute("UPDATE exports SET status='running',token=%s,lease_until=%s WHERE id=%s", (token, time.time() + 300, row["id"]))
    folder = cfg["data_dir"] / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f"{row['id']}.{token}.part"
    reader = connect(cfg, initialize=False)
    count = 0
    try:
        filters = json.loads(row["filters"])
        where = " WHERE " + " AND ".join(f"`{k}`=%s" for k in filters) if filters else ""
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            for item in reader.iter_rows(f"SELECT * FROM `{row['kind']}`" + where, tuple(filters.values())):
                stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
                count += 1
                if count % 1000 == 0:
                    disk_check(cfg["data_dir"], cfg["min_free_gb"])
                    if not db.execute("UPDATE exports SET rows_written=%s,lease_until=%s WHERE id=%s AND token=%s", (count, time.time() + 300, row["id"], token)).rowcount:
                        raise RuntimeError("Export lease replaced")
        with db:
            owner = db.execute("SELECT token FROM exports WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if owner["token"] != token:
                raise RuntimeError("Export lease replaced")
            from .objects import for_config
            ref=for_config(cfg).put_file(temp)
            db.execute("UPDATE exports SET status='ready',rows_written=%s,finished_at=%s,object_key=%s,lease_until=0,token=NULL WHERE id=%s", (count, time.time(), ref, row["id"]))
    except Exception as exc:
        db.execute("UPDATE exports SET status='error',error=%s,lease_until=0,token=NULL WHERE id=%s AND token=%s", (type(exc).__name__, row["id"], token))
        raise
    finally:
        reader.close()
        temp.unlink(missing_ok=True)
    return {"processed": 1, "id": row["id"], "rows": count}
