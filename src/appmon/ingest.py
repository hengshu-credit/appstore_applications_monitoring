"""Streaming imports with atomic row checkpoints and content-addressed provenance."""
import csv
import hashlib
import json
import lzma
import re
import shutil
import time
from pathlib import Path

import requests

from .db import add_apps

ALIASES = {"google": "google", "android": "google", "1": "google", "google play": "google",
           "apple": "apple", "ios": "apple", "2": "apple", "app store": "apple"}


def valid_id(store, app_id):
    pattern = r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+" if store == "google" else r"[0-9]+"
    return store in ("google", "apple") and bool(re.fullmatch(pattern, app_id))


def disk_check(path, min_free_gb):
    if shutil.disk_usage(path).free < min_free_gb * 1024**3:
        raise OSError(f"Insufficient free space under {path}; require {min_free_gb} GiB")


def file_hash(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def import_file(db, path, source, min_free_gb=2, batch_size=5000, progress=None):
    path = Path(path).resolve()
    digest = file_hash(path)
    with db.named_lock("import:" + digest):
        return _import_file(db, path, source, min_free_gb, batch_size, digest, progress)


def _import_file(db, path, source, min_free_gb, batch_size, digest, progress=None):
    previous = db.execute("SELECT * FROM imports WHERE digest=%s", (digest,)).fetchone()
    if previous and previous["status"] == "complete":
        return dict(previous)
    with db:
        db.execute("INSERT INTO imports(digest,path,source,started) VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE digest=imports.digest",
                   (digest, str(path), source, time.time()))
        db.execute("UPDATE imports SET status='running' WHERE digest=%s", (digest,))
    previous = db.execute("SELECT * FROM imports WHERE digest=%s", (digest,)).fetchone()
    checkpoint = previous["processed"]
    opener = lzma.open if path.suffix == ".xz" else open
    processed, valid, rejected, batch = checkpoint, 0, 0, []

    def flush():
        nonlocal valid, rejected, batch
        disk_check(path.parent, min_free_gb)
        with db:
            added = add_apps(db, batch, source)
            db.execute("""UPDATE imports SET processed=%s,valid=valid+%s,added=added+%s,
                rejected=rejected+%s WHERE digest=%s""", (processed, valid, added, rejected, digest))
        valid, rejected, batch = 0, 0, []
        if progress and (processed % 100000 == 0):
            progress('import_progress', f'应用池导入进度：已处理 {processed} 行')

    try:
        with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t" if ".tsv" in path.name else ",")
            fields = reader.fieldnames or []
            if "store_id" not in fields or not (set(fields) & {"store", "appstore"}):
                raise ValueError("Expected columns store/appstore and store_id")
            for index, row in enumerate(reader, 1):
                if index <= checkpoint:
                    continue
                processed = index
                store = ALIASES.get((row.get("store") or row.get("appstore") or "").strip().lower())
                app_id = (row.get("store_id") or "").strip()
                if None in row or not valid_id(store, app_id):
                    rejected += 1
                else:
                    batch.append((store, app_id, row.get("app_name"), row.get("app_category")))
                    valid += 1
                if (valid + rejected) >= batch_size:
                    flush()
            if valid or rejected:
                flush()
        with db:
            db.execute("UPDATE imports SET status='complete',finished=%s WHERE digest=%s", (time.time(), digest))
    except BaseException:
        with db:
            db.execute("UPDATE imports SET status='interrupted' WHERE digest=%s", (digest,))
        raise
    return dict(db.execute("SELECT * FROM imports WHERE digest=%s", (digest,)).fetchone())


def sync_seed(cfg, db, progress=None):
    """Use public repository releases only; never bypass hosted account requirements."""
    folder = cfg["data_dir"] / "raw"
    folder.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "appstore-applications-monitoring/0.1"
    url = "https://api.github.com/repos/appgoblin-dev/appgoblin-data/git/trees/main?recursive=1"
    r = session.get(url, timeout=cfg["timeout_seconds"])
    r.raise_for_status()
    tree = r.json()
    if tree.get("truncated"):
        raise ValueError("GitHub tree response truncated")
    item = next(x for x in tree["tree"] if x["path"] == "data/store_apps.tsv.xz")
    if progress: progress('source_checked', f'已核对源文件版本；压缩包大小 {item["size"]} 字节')
    target = folder / f"store_apps-{item['sha']}.tsv.xz"
    # Reuse a manually bootstrapped file after verifying its exact blob content.
    local = folder / "store_apps.tsv.xz"
    if not target.exists() and local.exists() and local.stat().st_size == item["size"]:
        git_hash = hashlib.sha1(f"blob {item['size']}\0".encode())
        with local.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                git_hash.update(chunk)
        if git_hash.hexdigest() == item["sha"]:
            target = local
    if not target.exists():
        if progress: progress('downloading', '开始下载公开种子包')
        disk_check(folder, max(cfg["min_free_gb"], item["size"] / 1024**3 + 1))
        raw_url = f"https://raw.githubusercontent.com/appgoblin-dev/appgoblin-data/{tree['sha']}/{item['path']}"
        tmp = target.with_suffix(".part")
        with session.get(raw_url, stream=True, timeout=cfg["timeout_seconds"]) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(1024 * 1024):
                    disk_check(folder, cfg["min_free_gb"])
                    f.write(chunk)
        if tmp.stat().st_size != item["size"]:
            raise ValueError("Incomplete seed download")
        git_hash = hashlib.sha1(f"blob {item['size']}\0".encode())
        with tmp.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                git_hash.update(chunk)
        if git_hash.hexdigest() != item["sha"]:
            raise ValueError("Seed Git blob SHA does not match")
        tmp.replace(target)
        if progress: progress('downloaded', '种子包下载完成，文件大小与 Git blob 校验通过')
    manifest = {"repository_tree": tree["sha"], "git_blob": item["sha"],
                "sha256": file_hash(target), "bytes": target.stat().st_size,
                "download_checked_at": time.time(), "license": "CC-BY-4.0",
                "attribution": "Data provided by AppGoblin. https://github.com/appgoblin-dev/appgoblin-data"}
    target.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if progress: progress('importing', '开始校验或续传应用池导入；相同文件会复用已有结果')
    return import_file(db, target, f"appgoblin-tree:{tree['sha']}", cfg["min_free_gb"], progress=progress)
