"""Local publisher references for grouped apps. Never enqueues a store request."""
import csv
import hashlib
import json
import lzma
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from .ingest import ALIASES, file_hash
from .locales import language_for
from .watchlist_import import read_snapshot


def field(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    if value.casefold() in ('', '-', '—', 'null', 'none', 'nan', 'n/a', r'\n') or len(value) > 1024:
        return None
    return value


def cache_path(cfg):
    return Path(cfg['data_dir']) / 'exports' / 'group-developer-references.json'


def refresh(db, cfg):
    root = Path(cfg['data_dir'])
    cache = cache_path(cfg)
    members = db.execute('SELECT DISTINCT store,app_id FROM app_group_members ORDER BY store,app_id').fetchall()
    wanted = {(r['store'],r['app_id']) for r in members}
    raw = root / 'raw'
    store_files = [p for p in [raw/'store_apps.tsv.xz', *raw.glob('store_apps-*.tsv.xz')] if p.is_file()]
    seeds = [p for p in (raw/'live_store_apps.tsv.xz', max(store_files,key=lambda p:p.stat().st_mtime_ns,default=None)) if p and p.is_file()]
    seeds.sort(key=lambda p:p.stat().st_mtime_ns,reverse=True)
    imports = root/'exports'/'watchlist-import'
    summaries = sorted(imports.glob('*-summary.json'))
    originals = sorted((root/'raw').glob('*/as-data.js'))
    files = [p for p in seeds + originals + summaries + sorted(imports.glob('*-source-rows.jsonl')) if p.is_file()]
    signature = [1, sorted(wanted), [(str(p.relative_to(root)),p.stat().st_size,p.stat().st_mtime_ns) for p in files]]
    fingerprint = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
    if cache.is_file():
        try:
            if json.loads(cache.read_text(encoding='utf-8'))['fingerprint']==fingerprint:
                return
        except (KeyError, ValueError):
            pass

    names, ids = {}, {}
    if wanted:
        for seed in seeds:
            if not seed.is_file():
                continue
            with lzma.open(seed,'rt',encoding='utf-8-sig',newline='') as stream:
                rows = csv.reader(stream,delimiter='\t')
                header = next(rows,[])
                if not {'store','store_id','developer_id'} <= set(header):
                    continue
                store, app_id, developer_id = (header.index(k) for k in ('store','store_id','developer_id'))
                for row in rows:
                    if len(row) != len(header):
                        continue
                    key = (ALIASES.get(row[store].strip().lower()),row[app_id].strip())
                    if key in wanted and key not in ids:
                        value = field(row[developer_id])
                        if value and (key[0]!='apple' or value.isdigit()):
                            ids[key] = value

        # Older mapping files omitted dev. Only use a raw Apple snapshot whose digest
        # was recorded by a completed watchlist import, including on restored hosts.
        by_digest = {file_hash(p):p for p in originals}
        batches = [(json.loads(p.read_text(encoding='utf-8')),p) for p in summaries]
        for summary, path in sorted(batches,key=lambda batch:batch[0].get('imported_at',0)):
            original = by_digest.get(summary.get('source_hashes',{}).get('apple'))
            if original:
                data,_ = read_snapshot(original,'apple')
                for r in data['apps']:
                    country = r['cc'].lower()
                    app_id = str(r['pkg'])
                    if ('apple',app_id) in wanted and field(r.get('dev')):
                        names['apple',app_id,country,language_for('apple',country)] = field(r['dev'])
            mapping = path.with_name(path.name.replace('-summary.json','-source-rows.jsonl'))
            if mapping.is_file():
                with mapping.open(encoding='utf-8') as stream:
                    for line in stream:
                        r = json.loads(line)
                        key = tuple(r[k] for k in ('store','app_id','country','language'))
                        if key[:2] in wanted and field(r.get('developer')):
                            names[key] = field(r['developer'])
    cache.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=cache.parent,delete=False) as out:
        tmp = Path(out.name)
        try:
            json.dump({'fingerprint':fingerprint,'names':[[*k,v] for k,v in names.items()],
                       'ids':[[*k,v] for k,v in ids.items()]},out,ensure_ascii=False)
        except BaseException:
            out.close();tmp.unlink(missing_ok=True);raise
    try:
        os.replace(tmp,cache)
    finally:
        tmp.unlink(missing_ok=True)


@lru_cache(maxsize=4)
def _load(path, mtime, size):
    data = json.loads(path.read_text(encoding='utf-8'))
    return {tuple(k):v for *k,v in data['names']}, {tuple(k):v for *k,v in data['ids']}


def references(cfg):
    path = cache_path(cfg)
    try:
        stat = path.stat()
        return _load(path,stat.st_mtime_ns,stat.st_size)
    except (OSError,KeyError,ValueError):
        return {},{}


def identity(row, observed, refs):
    names, ids = refs
    key = tuple(row[k] for k in ('store','app_id','country','language'))
    name, developer_id = field(observed.get('developer')),field(observed.get('developer_id'))
    return dict(developer=name or names.get(key),developer_id=developer_id or ids.get(key[:2]),
                developer_source='store' if name else 'watchlist' if key in names else None,
                developer_id_source='store' if developer_id else 'seed' if key[:2] in ids else None)
