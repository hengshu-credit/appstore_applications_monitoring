"""Resumable compaction with full logical-row manifests before and after."""
import hashlib
import json
import shutil
import time

from .content_store import load_many, marker, put

TABLE_KEYS = {'snapshots': ('id',), 'listings': ('store', 'app_id', 'country', 'language')}


def _check(db, cfg):
    from .collection_limits import settings
    if not settings(db, cfg)['paused']:
        raise ValueError('Pause collection before compacting existing history')
    # Migration is explicitly allowed below the collection guard, but never
    # consumes the last 2 GiB. The collection guard itself remains unchanged.
    if shutil.disk_usage(cfg['data_dir']).free < 2 * 1024**3:
        raise OSError('Compaction requires at least 2 GiB of working reserve')


def _rows(db, table, last, size, lock=False):
    keys = TABLE_KEYS[table]
    columns = [r['Field'] for r in db.execute(f'SHOW COLUMNS FROM {table}').fetchall()]
    selected = ','.join('CAST(payload AS BINARY) AS payload' if c == 'payload' else '`'+c+'`' for c in columns)
    condition = ' WHERE (' + ','.join(keys) + ')>(' + ','.join('%s' for _ in keys) + ')' if last else ''
    return db.execute(f'SELECT {selected} FROM {table}' + condition + ' ORDER BY ' + ','.join(keys) + ' LIMIT %s' + (' FOR UPDATE' if lock else ''),
                      (*(last or []), size)).fetchall()


def _originals(db, rows):
    refs = {}
    texts = []
    for row in rows:
        text = row['payload'].decode('utf8') if row['payload'] is not None else None
        info = marker(text)
        texts.append((text, info))
        if info:
            refs[info['sha256']] = info['bytes']
    bodies = load_many(db.connection, refs, cache=False) if refs else {}
    result = []
    for row, (text, info) in zip(rows, texts):
        original = bodies[info['sha256']] if info else text
        checksum = hashlib.sha256(original.encode('utf8')).hexdigest() if original is not None else None
        if row['digest'] != checksum:
            raise ValueError('Original row digest mismatch; no data replaced')
        result.append(original)
    return result


def _audit(previous, row, original):
    # All identity, time, market, state, provenance and body bytes participate.
    value = dict(row)
    value['payload'] = hashlib.sha256(original.encode('utf8')).hexdigest() if original is not None else None
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')
    return hashlib.sha256(bytes.fromhex(previous) + encoded).hexdigest()


def compact(db, cfg, table, *, batch_size=100, max_batches=None, progress=None):
    if table not in TABLE_KEYS or not 1 <= batch_size <= 1000:
        raise ValueError('Unsupported compaction target or batch size')
    _check(db, cfg)
    with db.named_lock('content-compaction:' + table):
        db.execute("INSERT IGNORE INTO content_compactions(name,audit,status,updated_at) VALUES(%s,%s,'running',%s)",
                   (table, '0'*64, time.time()))
        batches = 0
        while max_batches is None or batches < max_batches:
            _check(db, cfg)
            with db:
                state = db.execute('SELECT * FROM content_compactions WHERE name=%s FOR UPDATE', (table,)).fetchone()
                if state['status'] in ('copied', 'verified', 'reclaimed'):
                    return state
                last = json.loads(state['last_key']) if state['last_key'] else None
                rows = _rows(db, table, last, batch_size, lock=True)
                if not rows:
                    db.execute("UPDATE content_compactions SET status='copied',updated_at=%s WHERE name=%s", (time.time(), table))
                    continue
                originals = _originals(db, rows)
                replacements = [put(db, text, project=table == 'listings') if text is not None else None for text in originals]
                refs = {marker(r)['sha256']: marker(r)['bytes'] for r in replacements if r is not None}
                checked = load_many(db.connection, refs, cache=False)
                for row, text, replacement in zip(rows, originals, replacements):
                    if text is not None and checked[marker(replacement)['sha256']] != text:
                        raise ValueError('Read-back mismatch; original payload retained')
                    db.execute(f'UPDATE {table} SET payload=%s WHERE ' + ' AND '.join(k+'=%s' for k in TABLE_KEYS[table]),
                               (replacement, *(row[k] for k in TABLE_KEYS[table])))
                    state['audit'] = _audit(state['audit'], row, text)
                    state['processed'] += 1
                    state['original_bytes'] += len(text.encode('utf8')) if text else 0
                    state['reference_bytes'] += len(replacement.encode('utf8')) if replacement else 0
                last = [rows[-1][k] for k in TABLE_KEYS[table]]
                db.execute('''UPDATE content_compactions SET last_key=%s,processed=%s,original_bytes=%s,
                    reference_bytes=%s,audit=%s,updated_at=%s WHERE name=%s''',
                    (json.dumps(last), state['processed'], state['original_bytes'], state['reference_bytes'], state['audit'], time.time(), table))
            batches += 1
            if progress:
                progress(table, state['processed'])
        return db.execute('SELECT * FROM content_compactions WHERE name=%s', (table,)).fetchone()


def verify(db, cfg, table, *, batch_size=200, progress=None):
    if table not in TABLE_KEYS:
        raise ValueError('Unsupported verification target')
    _check(db, cfg)
    with db.named_lock('content-compaction:' + table):
        expected = db.execute('SELECT * FROM content_compactions WHERE name=%s', (table,)).fetchone()
        if not expected or expected['status'] not in ('copied', 'verified', 'reclaimed'):
            raise ValueError('Finish resumable compaction before verification')
        audit, count, last = '0'*64, 0, None
        while rows := _rows(db, table, last, batch_size):
            for row, text in zip(rows, _originals(db, rows)):
                if text is not None and not marker(row['payload'].decode('utf8')):
                    raise ValueError('Uncompacted body found')
                audit = _audit(audit, row, text)
                count += 1
            last = [rows[-1][k] for k in TABLE_KEYS[table]]
            if progress:
                progress(table + ':verify', count)
        if count != expected['processed'] or audit != expected['audit']:
            raise ValueError('Full logical-row manifest mismatch; storage not reclaimed')
        db.execute("UPDATE content_compactions SET status=IF(status='reclaimed',status,'verified'),updated_at=%s WHERE name=%s", (time.time(), table))
        return {'table': table, 'rows': count, 'logical_sha256': audit, 'verified': True}


def reclaim(db, cfg, table):
    if table not in TABLE_KEYS:
        raise ValueError('Unsupported reclaim target')
    _check(db, cfg)
    state = db.execute('SELECT status FROM content_compactions WHERE name=%s', (table,)).fetchone()
    if not state or state['status'] != 'verified':
        raise ValueError('Full verification required before reclaiming storage')
    size = db.execute(f'SELECT COUNT(*) n,COALESCE(SUM(OCTET_LENGTH(payload)),0) bytes FROM {table}').fetchone()
    required = 2*1024**3 + max(512*1024**2, int(size['bytes'])*3 + size['n']*2048)
    if shutil.disk_usage(cfg['data_dir']).free < required:
        raise OSError('Insufficient working space for verified table rebuild')
    with db.named_lock('content-compaction:' + table):
        db.execute(f'ALTER TABLE {table} FORCE, ALGORITHM=INPLACE, LOCK=NONE')
        # Recheck every logical row after physical reconstruction too.
        result = verify(db, cfg, table)
        db.execute("UPDATE content_compactions SET status='reclaimed',updated_at=%s WHERE name=%s", (time.time(), table))
        return result
