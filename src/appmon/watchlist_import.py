"""Import the publicly published Watchlist watchlists without importing their verdicts."""
import hashlib
import json
import re
import time
from pathlib import Path

from .db import add_apps, dump
from .intelligence import market
from .locales import language_for

GROUP = '现金贷类APP'
SOURCES = {'google': ('monitor_data.js', 'MONITOR_DATA'), 'apple': ('as-data.js', 'AS_DATA')}


def read_snapshot(path, store):
    raw = Path(path).read_bytes()
    text = raw.decode('utf-8-sig')
    variable = SOURCES[store][1]
    match = re.search(r'(?:window\.)?' + variable + r'\s*=\s*', text)
    if not match:
        raise ValueError('缺少关注名单数据声明：' + store)
    # Parse only JSON, never execute source JavaScript.
    value, _ = json.JSONDecoder().raw_decode(text[match.end():])
    return value, hashlib.sha256(raw).hexdigest()


def prepare(snapshots):
    targets, source_rows, summaries = {}, [], {}
    for store in ('google', 'apple'):
        data = snapshots[store]
        rows = data.get('apps')
        if not isinstance(rows, list) or not rows or data.get('isRunning') or data.get('totalApps') != len(rows):
            raise ValueError('来源快照未完成或总数不一致：' + store)
        for index, row in enumerate(rows, 1):
            country = str(row.get('cc', '')).lower()
            target = market(dict(store=store, app_id=str(row.get('pkg', '')), country=country,
                                 language=language_for(store, country)))
            key = tuple(target[k] for k in ('store', 'app_id', 'country', 'language'))
            targets.setdefault(key, target | {'name':row.get('name'), 'group_name':GROUP})
            source_rows.append(target | {'name':row.get('name'), 'source_row':index,
                'developer': row.get('dev'),
                'source_status':row.get('liveStatus'), 'source_generated_at':data.get('generatedAt'),
                'source_type':'watchlist_import', 'group_name':GROUP})
        selected = [t for t in targets.values() if t['store'] == store]
        summaries[store] = dict(source_rows=len(rows), targets=len(selected),
            unique_apps=len({t['app_id'] for t in selected}), countries=len({t['country'] for t in selected}))
    return targets, source_rows, summaries


def import_watchlists(db, cfg, snapshots, source_hashes):
    targets, source_rows, summaries = prepare(snapshots)
    now = time.time()
    folder = Path(cfg['data_dir']) / 'exports' / 'watchlist-import'
    folder.mkdir(parents=True, exist_ok=True)
    batch_id = hashlib.sha256(dump(source_hashes).encode()).hexdigest()[:16]
    # Save provenance before the DB transaction; a failed transaction never loses its input.
    mapping = folder / (batch_id + '-source-rows.jsonl')
    mapping.write_text(''.join(dump(r) + '\n' for r in source_rows), encoding='utf-8')
    with db.named_lock('watchlist-imports'), db:
        before = db.execute('SELECT * FROM watchlist ORDER BY id').fetchall()
        previous = {(r['store'],r['app_id'],r['country'],r['language']):r for r in before}
        before_path=folder / (batch_id + '-before.json')
        if not before_path.exists():before_path.write_text(dump(before), encoding='utf-8')
        values = list(targets.values())
        for offset in range(0, len(values), 500):
            chunk = values[offset:offset+500]
            add_apps(db, [(r['store'],r['app_id'],r['name'],None) for r in chunk], 'watchlist-import')
            db.executemany('''INSERT INTO watchlist(store,app_id,country,language,group_name,refresh_hours,enabled,created_at)
                VALUES(%s,%s,%s,%s,%s,24,1,%s) ON DUPLICATE KEY UPDATE
                group_name=VALUES(group_name),enabled=1''',
                [(*(r[k] for k in ('store','app_id','country','language')),GROUP,now) for r in chunk])
            db.executemany('''INSERT INTO jobs(store,app_id,country,language,due) VALUES(%s,%s,%s,%s,0)
                ON DUPLICATE KEY UPDATE app_id=jobs.app_id''',
                [tuple(r[k] for k in ('store','app_id','country','language')) for r in chunk])
        after = {(r['store'],r['app_id'],r['country'],r['language']):r
                 for r in db.execute('SELECT * FROM watchlist ORDER BY id').fetchall()}
        if any(k not in after or after[k]['group_name'] != GROUP or not after[k]['enabled'] for k in targets):
            raise ValueError('导入行数或分类校验失败')
        if any(after[k] != v for k,v in previous.items() if k not in targets):
            raise ValueError('非目标监控配置发生改变')
        from .app_groups import import_references, sync_watch_category
        import_references(db,source_rows)
        group_id=sync_watch_category(db,GROUP)
        result = dict(group_name=GROUP, imported_at=now, source_hashes=source_hashes, stores=summaries,
                      group_id=group_id,
                      source_rows=len(source_rows), targets=len(targets),
                      added=len(set(targets)-set(previous)), existing=len(set(targets)&set(previous)),
                      unrelated_preserved=len(set(previous)-set(targets)),
                      missing=0, disabled=0, mapping=str(mapping),
                      note='仅导入关注名单与用户指定分类。来源上下架状态保留在映射文件，不写入观星实测状态或事件。')
    (folder / (batch_id + '-summary.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
