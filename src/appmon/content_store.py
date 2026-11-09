"""Immutable, lossless detail bodies shared by current and historical records.

Only the small SQL projection is repeated. Buffered DB reads resolve references
in batches; binary casts and raw backup streams deliberately expose storage.
"""
import hashlib
import json
import re
import time
import zstandard
from collections import OrderedDict

import pymysql

MARKER = '__appmon_content_v1__'
PREFIX = '{"' + MARKER + '":'
CACHE_BYTES = 8 * 1024 * 1024
SQL_FIELDS = frozenset(('name', 'developer', 'developer_id', 'icon', 'rating', 'ratings',
    'installs', 'version', 'category', 'category_id', 'genre_ids', 'price', 'currency',
    'has_iap', 'languages', 'updated', 'released'))
TABLES = {
    'content_blobs': '''digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
        codec VARCHAR(16) CHARACTER SET ascii NOT NULL, raw_bytes BIGINT NOT NULL,
        object_key VARCHAR(255) CHARACTER SET ascii NOT NULL, created_at DOUBLE NOT NULL''',
    'content_compactions': '''name VARCHAR(32) CHARACTER SET ascii NOT NULL PRIMARY KEY,
        last_key TEXT, processed BIGINT NOT NULL DEFAULT 0, original_bytes BIGINT NOT NULL DEFAULT 0,
        reference_bytes BIGINT NOT NULL DEFAULT 0, audit CHAR(64) CHARACTER SET ascii NOT NULL,
        status VARCHAR(32) NOT NULL, updated_at DOUBLE NOT NULL''',
}


def reference(digest, size, projection=None):
    value = {MARKER: {'sha256': digest, 'bytes': size}}
    value.update({k: v for k, v in (projection or {}).items() if k in SQL_FIELDS})
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def marker(value):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return None
    info = json.loads(value).get(MARKER)
    if not isinstance(info, dict) or set(info) != {'sha256', 'bytes'} or not re.fullmatch('[0-9a-f]{64}', str(info['sha256'])) or type(info['bytes']) is not int or info['bytes'] < 0:
        raise ValueError('Invalid content reference')
    return info


def unpack(row, objects):
    raw = objects.get(row['object_key'])
    if len(raw) != row['raw_bytes'] or hashlib.sha256(raw).hexdigest() != row['digest']:
        raise ValueError('Content checksum mismatch')
    return raw.decode('utf8')


def put(db, text, *, project=False):
    """Caller owns the transaction containing both the blob and its references."""
    if marker(text):
        raise ValueError('Cannot archive a reference as original content')
    raw = text.encode('utf8')
    digest = hashlib.sha256(raw).hexdigest()
    from .objects import store
    # Upload first. SQL rollback may leave an unreferenced immutable object,
    # but can never leave a committed reference pointing at an unuploaded body.
    key = store(db.database).put(raw, kind='content', mime='application/json', compress=True)
    db.execute("INSERT INTO content_blobs(digest,codec,raw_bytes,object_key,created_at) VALUES(%s,'object-v1',%s,%s,%s) ON DUPLICATE KEY UPDATE digest=content_blobs.digest",
               (digest, len(raw), key, time.time()))
    return reference(digest, len(raw), json.loads(text) if project else None)


def load_many(connection, refs, *, cache=True):
    lru = getattr(connection, '_appmon_content_cache', None)
    if lru is None:
        lru = connection._appmon_content_cache = OrderedDict()
        connection._appmon_content_cache_bytes = 0
    found = {}
    for digest in refs:
        if cache and digest in lru:
            found[digest] = lru[digest][0]
            lru.move_to_end(digest)
    missing = [d for d in refs if d not in found]
    for offset in range(0, len(missing), 100):
        keys = missing[offset:offset + 100]
        # A separate buffered cursor leaves the caller's result/lastrowid intact.
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute('SELECT digest,codec,raw_bytes,object_key FROM content_blobs WHERE digest IN (' + ','.join('%s' for _ in keys) + ')', keys)
            for row in cursor.fetchall():
                from .objects import store
                text = unpack(row, store(connection.db.decode() if isinstance(connection.db, bytes) else connection.db))
                found[row['digest']] = text
                cost = len(text) * 4  # conservative bound for Python Unicode memory
                if cache and cost <= CACHE_BYTES:
                    if row['digest'] in lru:
                        connection._appmon_content_cache_bytes -= lru.pop(row['digest'])[1]
                    lru[row['digest']] = (text, cost)
                    connection._appmon_content_cache_bytes += cost
                    while connection._appmon_content_cache_bytes > CACHE_BYTES:
                        connection._appmon_content_cache_bytes -= lru.popitem(last=False)[1][1]
    for digest, size in refs.items():
        if digest not in found or len(found[digest].encode('utf8')) != size:
            raise ValueError('Missing or inconsistent content body: ' + digest)
    return found


def hydrate(connection, rows):
    refs, cells = {}, []
    for row in rows:
        for key, value in row.items():
            info = marker(value)
            if info:
                refs[info['sha256']] = info['bytes']
                cells.append((row, key, info['sha256']))
    if refs:
        values = load_many(connection, refs)
        for row, key, digest in cells:
            row[key] = values[digest]
    return rows


class ContentCursor(pymysql.cursors.DictCursor):
    def fetchone(self):
        row = super().fetchone()
        return hydrate(self.connection, [dict(row)])[0] if row else row

    def fetchmany(self, size=None):
        return hydrate(self.connection, [dict(r) for r in super().fetchmany(size)])

    def fetchall(self):
        return hydrate(self.connection, [dict(r) for r in super().fetchall()])
