"""One-way, resumable legacy SQLite -> MySQL import; SQLite is opened read-only."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .db import TABLES, connect
from .ingest import file_hash


def _digest(rows, columns):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(json.dumps([row[c] for c in columns], ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")
        count += 1
    return count, digest.hexdigest()


def migrate(cfg, source_path, *, batch_size=5000, report_path=None):
    source_path = Path(source_path).resolve(strict=True)
    wal = Path(str(source_path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Legacy WAL is nonempty; stop old writers and checkpoint before migration")
    source_sha = file_hash(source_path)
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")
    target = connect(cfg, allow_migration=True)
    report = {"storage": "mysql", "source_sha256": source_sha, "started": time.time(), "tables": {}}
    # The legacy schema predates keyword tracking. New tables must remain empty,
    # but must not be required in a historical SQLite source.
    names = [name for name in TABLES if name != "migration_progress" and not name.startswith("keyword_")]
    try:
        lock_name = "appmon:migrate:" + hashlib.sha256(cfg["mysql"]["database"].encode()).hexdigest()[:32]
        lock = target.execute("SELECT GET_LOCK(%s,0) acquired", (lock_name,)).fetchone()
        if lock["acquired"] != 1:
            raise RuntimeError("Another migration is running")
        previous = target.execute("SELECT DISTINCT source_sha256 FROM migration_progress").fetchall()
        if previous and any(r["source_sha256"] != source_sha for r in previous):
            raise ValueError("Migration source differs from the recorded source")
        if not previous:
            if any(target.execute(f"SELECT 1 FROM `{name}` LIMIT 1").fetchone()
                   for name in TABLES if name != "migration_progress"):
                raise ValueError("Initial migration requires empty destination tables; existing data is never overwritten")
            with target:
                target.executemany("INSERT INTO migration_progress(source_sha256,table_name) VALUES(%s,%s)",
                                   [(source_sha, name) for name in names])
        for name in names:
            info = source.execute(f"PRAGMA table_info(`{name}`)").fetchall()
            if not info:
                raise ValueError(f"Legacy table missing: {name}")
            columns = [r["name"] for r in info]
            expected = [r["Field"] for r in target.execute(f"SHOW COLUMNS FROM `{name}`").fetchall()]
            if columns != expected:
                raise ValueError(f"Legacy columns do not match the target schema: {name}")
            keys = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
            order = ",".join(f"`{c}`" for c in keys)
            column_sql = ",".join(f"`{c}`" for c in columns)
            state = target.execute("SELECT * FROM migration_progress WHERE source_sha256=%s AND table_name=%s",
                                   (source_sha, name)).fetchone()
            copied = state["rows_copied"]
            if state["status"] == "copying":
                last = json.loads(state["last_key"]) if state["last_key"] else None
                if last:
                    condition = f" WHERE ({order}) > ({','.join('?' for _ in keys)})"
                else:
                    condition = ""
                reader = source.execute(f"SELECT {column_sql} FROM `{name}`{condition} ORDER BY {order}", last or ())
                while rows := reader.fetchmany(batch_size):
                    values = [tuple(r[c] for c in columns) for r in rows]
                    last = [rows[-1][k] for k in keys]
                    with target:
                        target.executemany(f"INSERT INTO `{name}`({column_sql}) VALUES({','.join('%s' for _ in columns)})", values)
                        target.execute("""UPDATE migration_progress SET rows_copied=%s,last_key=%s
                            WHERE source_sha256=%s AND table_name=%s""",
                            (copied + len(rows), json.dumps(last), source_sha, name))
                    copied += len(rows)
                    if copied % 100000 == 0:
                        print(f"{name}: copied {copied:,}", flush=True)
                with target:
                    target.execute("UPDATE migration_progress SET status='copied' WHERE source_sha256=%s AND table_name=%s",
                                   (source_sha, name))
            # Compare every field in every row, ordered by the original primary key.
            source_count, source_digest = _digest(source.execute(f"SELECT {column_sql} FROM `{name}` ORDER BY {order}"), columns)
            target_count, target_digest = _digest(target.iter_rows(f"SELECT {column_sql} FROM `{name}` ORDER BY {order}"), columns)
            if (source_count, source_digest) != (target_count, target_digest):
                raise ValueError(f"Full-table row count/content verification failed: {name}")
            report["tables"][name] = {"rows": source_count, "sha256": source_digest, "verified": True}
            print(f"{name}: verified {source_count:,} rows", flush=True)
        if file_hash(source_path) != source_sha:
            raise ValueError("Legacy source file changed during migration")
        with target:
            target.execute("UPDATE migration_progress SET status='verified' WHERE source_sha256=%s", (source_sha,))
        report["finished"] = time.time()
        if report_path:
            Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        source.close()
        target.close()  # Releases the server advisory lock even on interruption.
