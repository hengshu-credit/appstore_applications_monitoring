"""MySQL 8.4 persistence; no SQLite storage fallback."""
import json
import time
import uuid
import hashlib
from contextlib import contextmanager
import pymysql

APP_KEY = 'store VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, app_id VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL'
REGION = 'country CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, language VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL'
ENGINE = ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
TABLES = {
    'apps': f'''{APP_KEY}, name TEXT, category TEXT, first_seen DOUBLE NOT NULL,
        last_seen DOUBLE NOT NULL, source TEXT NOT NULL, PRIMARY KEY(store,app_id),
        INDEX apps_store_category(store,category(64))''',
    'imports': '''digest CHAR(64) CHARACTER SET ascii PRIMARY KEY, path TEXT NOT NULL,
        source TEXT NOT NULL, processed BIGINT NOT NULL DEFAULT 0, valid BIGINT NOT NULL DEFAULT 0,
        added BIGINT NOT NULL DEFAULT 0, rejected BIGINT NOT NULL DEFAULT 0,
        status VARCHAR(32) NOT NULL DEFAULT 'running', started DOUBLE NOT NULL, finished DOUBLE''',
    'jobs': f'''{APP_KEY}, {REGION}, due DOUBLE NOT NULL DEFAULT 0,
        lease_until DOUBLE NOT NULL DEFAULT 0, token CHAR(32) CHARACTER SET ascii,
        failures INT NOT NULL DEFAULT 0, last_error TEXT,
        PRIMARY KEY(store,app_id,country,language), INDEX jobs_due(due,lease_until)''',
    'listings': f'''{APP_KEY}, {REGION}, state VARCHAR(32) NOT NULL, checked_at DOUBLE NOT NULL,
        last_success DOUBLE, misses INT NOT NULL DEFAULT 0, payload LONGTEXT, digest CHAR(64),
        PRIMARY KEY(store,app_id,country,language)''',
    'snapshots': f'''id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, {APP_KEY}, {REGION},
        observed_at DOUBLE NOT NULL, digest CHAR(64) NOT NULL, raw_path TEXT NOT NULL,
        payload LONGTEXT NOT NULL, INDEX snapshots_app(store,app_id,country,language,id)''',
    'events': f'''id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, {APP_KEY}, {REGION},
        observed_at DOUBLE NOT NULL, kind VARCHAR(64) NOT NULL, changes LONGTEXT NOT NULL''',
    'requests': f'''id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, {APP_KEY}, {REGION},
        observed_at DOUBLE NOT NULL, outcome VARCHAR(32) NOT NULL, error TEXT,
        INDEX requests_app(store,app_id,country,language,id)''',
    'schedules': '''name VARCHAR(512) NOT NULL PRIMARY KEY, next_at DOUBLE NOT NULL''',
    'discovery_runs': '''id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, source TEXT NOT NULL,
        observed_at DOUBLE NOT NULL, count BIGINT NOT NULL, added BIGINT NOT NULL, error TEXT''',
    'migration_progress': '''source_sha256 CHAR(64) CHARACTER SET ascii NOT NULL,
        table_name VARCHAR(64) CHARACTER SET ascii NOT NULL, rows_copied BIGINT NOT NULL DEFAULT 0,
        last_key TEXT, status VARCHAR(32) NOT NULL DEFAULT 'copying',
        PRIMARY KEY(source_sha256,table_name)''',
    'keyword_targets': f'''id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        store VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, {REGION},
        keyword VARCHAR(255) NOT NULL, depth INT NOT NULL, refresh_hours DOUBLE NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE, due DOUBLE NOT NULL DEFAULT 0,
        lease_until DOUBLE NOT NULL DEFAULT 0, token CHAR(32) CHARACTER SET ascii,
        failures INT NOT NULL DEFAULT 0, last_error TEXT,
        latest_attempt_id BIGINT, latest_success_id BIGINT, previous_success_id BIGINT,
        UNIQUE KEY keyword_identity(store,country,language,keyword),
        INDEX keyword_due(enabled,due,lease_until)''',
    'keyword_runs': '''id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        target_id BIGINT NOT NULL, observed_at DOUBLE NOT NULL,
        outcome VARCHAR(16) NOT NULL, source VARCHAR(64) NOT NULL,
        parser_version VARCHAR(32) NOT NULL, requested_depth INT NOT NULL,
        returned_count INT, coverage VARCHAR(32) NOT NULL, raw_path TEXT, error TEXT,
        INDEX keyword_history(target_id,id)''',
    'keyword_ranks': '''run_id BIGINT NOT NULL,
        app_id VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        position INT NOT NULL, name TEXT,
        PRIMARY KEY(run_id,app_id), UNIQUE KEY keyword_position(run_id,position),
        INDEX keyword_app(app_id,run_id)''',
}


class Database:
    def __init__(self, options):
        self.database = options.get('database', '')
        self.connection = pymysql.connect(**options, charset='utf8mb4', autocommit=True,
                                          cursorclass=pymysql.cursors.DictCursor)
        self.cursor = None
        self.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')

    def execute(self, sql, args=None):
        if self.cursor is not None:
            self.cursor.close()
        self.cursor = self.connection.cursor()
        self.cursor.execute(sql, args)
        return self.cursor

    def executemany(self, sql, rows):
        if self.cursor is not None:
            self.cursor.close()
        self.cursor = self.connection.cursor()
        self.cursor.executemany(sql, rows)
        return self.cursor

    def iter_rows(self, sql, args=None):
        with self.connection.cursor(pymysql.cursors.SSDictCursor) as cursor:
            cursor.execute(sql, args)
            yield from cursor

    def begin(self):
        self.connection.begin()

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

    @contextmanager
    def named_lock(self, resource):
        name = 'appmon:' + hashlib.sha256((self.database + ':' + resource).encode()).hexdigest()[:56]
        acquired = self.execute('SELECT GET_LOCK(%s,0) acquired', (name,)).fetchone()['acquired']
        if acquired != 1:
            raise RuntimeError('Another process is already importing this dataset')
        try:
            yield
        finally:
            self.execute('SELECT RELEASE_LOCK(%s)', (name,))

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, kind, value, traceback):
        self.rollback() if kind else self.commit()


def connect(cfg, *, initialize=True, allow_migration=False):
    db = Database(cfg['mysql'])
    try:
        if initialize:
            for table, columns in TABLES.items():
                db.execute(f'CREATE TABLE IF NOT EXISTS `{table}` ({columns}){ENGINE}')
            if not db.execute("SHOW COLUMNS FROM collection_logs LIKE 'details'").fetchone():
                # Multiple services may initialize concurrently on deployment.
                name='appmon:log-details-migration'
                if db.execute('SELECT GET_LOCK(%s,30) acquired',(name,)).fetchone()['acquired']!=1:
                    raise RuntimeError('Log schema migration lock unavailable')
                try:
                    if not db.execute("SHOW COLUMNS FROM collection_logs LIKE 'details'").fetchone():
                        db.execute('ALTER TABLE collection_logs ADD COLUMN details LONGTEXT NULL')
                finally:db.execute('SELECT RELEASE_LOCK(%s)',(name,))
            for table,name,columns in [('apps','apps_store_category','store,category(64)'),
                                       ('requests','requests_app','store,app_id,country,language,id')]:
                if not db.execute(f"SHOW INDEX FROM {table} WHERE Key_name=%s",(name,)).fetchone():
                    with db.named_lock(name):
                        if not db.execute(f"SHOW INDEX FROM {table} WHERE Key_name=%s",(name,)).fetchone():
                            db.execute(f'ALTER TABLE {table} ADD INDEX {name}({columns})')
        if not allow_migration:
            running = db.execute("SELECT 1 FROM migration_progress WHERE status!='verified' LIMIT 1").fetchone()
            if running:
                raise RuntimeError('Migration incomplete; resume and verify before starting workers')
        return db
    except BaseException:
        db.close()
        raise


# Kept in the same registry so initialization, test isolation and exports agree.
from .intelligence_schema import TABLES as INTELLIGENCE_TABLES
TABLES.update(INTELLIGENCE_TABLES)
from .asset_schema import TABLES as ASSET_TABLES
TABLES.update(ASSET_TABLES)
from .discovery_schema import tables as discovery_tables
TABLES.update(discovery_tables(APP_KEY, REGION))


def add_apps(db, rows, source, now=None):
    now = time.time() if now is None else now
    values = [(s, a, n, c, now, now, source) for s, a, n, c in rows]
    if not values:
        return 0
    # No INSERT IGNORE: invalid/truncated input must fail, not be silently altered.
    added = db.executemany('''INSERT INTO apps VALUES(%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE app_id=apps.app_id''', values).rowcount
    db.executemany('''INSERT INTO apps VALUES(%s,%s,%s,%s,%s,%s,%s) AS incoming
        ON DUPLICATE KEY UPDATE last_seen=incoming.last_seen,
        name=COALESCE(NULLIF(apps.name,''),NULLIF(incoming.name,'')),
        category=COALESCE(NULLIF(apps.category,''),NULLIF(incoming.category,''))''', values)
    return added


def enqueue(db, countries, language, batch_size=10000):
    added, last = 0, None
    while True:
        rows = db.execute('SELECT store,app_id FROM apps ' +
                          ('WHERE (store,app_id)>(%s,%s) ' if last else '') +
                          'ORDER BY store,app_id LIMIT %s', (*last, batch_size) if last else (batch_size,)).fetchall()
        if not rows:
            break
        with db:
            added += db.executemany('''INSERT INTO jobs(store,app_id,country,language) VALUES(%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE app_id=jobs.app_id''',
                [(r['store'], r['app_id'], c, language) for r in rows for c in countries]).rowcount
        last = (rows[-1]['store'], rows[-1]['app_id'])
    return added


def claim(db, countries, language, lease_seconds, now=None):
    now = time.time() if now is None else now
    placeholders = ','.join('%s' for _ in countries)
    with db:
        row = db.execute(f'''SELECT * FROM jobs WHERE due<=%s AND lease_until<=%s
            AND country IN ({placeholders}) AND language=%s
            AND NOT EXISTS (SELECT 1 FROM watchlist w WHERE w.store=jobs.store AND w.app_id=jobs.app_id
                AND w.country=jobs.country AND w.language=jobs.language) ORDER BY due LIMIT 1
            FOR UPDATE SKIP LOCKED''', (now, now, *countries, language)).fetchone()
        if not row:
            return None
        job = dict(row)
        job['token'] = uuid.uuid4().hex
        db.execute('''UPDATE jobs SET lease_until=%s,token=%s WHERE store=%s AND app_id=%s
                     AND country=%s AND language=%s''', (now + lease_seconds, job['token'], *key(job)))
        return job


def key(job):
    return tuple(job[k] for k in ('store', 'app_id', 'country', 'language'))


def reserve(db, name, interval, now=None):
    now = time.time() if now is None else now
    with db:
        db.execute('INSERT INTO schedules VALUES(%s,%s) ON DUPLICATE KEY UPDATE name=schedules.name', (name, now))
        row = db.execute('SELECT next_at FROM schedules WHERE name=%s FOR UPDATE', (name,)).fetchone()
        ready = max(now, row['next_at'])
        db.execute('UPDATE schedules SET next_at=%s WHERE name=%s', (ready + interval, name))
        return ready - now


def status(db):
    now = time.time()
    return {
        'at': now, 'storage': 'mysql',
        'server_version': db.execute('SELECT VERSION() version').fetchone()['version'],
        'pool': list(db.execute('SELECT store,count(*) count FROM apps GROUP BY store')),
        'jobs': db.execute('''SELECT count(*) total,
            CAST(coalesce(sum(due<=%s AND lease_until<=%s),0) AS UNSIGNED) due,
            CAST(coalesce(sum(lease_until>%s),0) AS UNSIGNED) leased,
            CAST(coalesce(sum(failures>0),0) AS UNSIGNED) retrying FROM jobs''', (now, now, now)).fetchone(),
        'listings': list(db.execute('''SELECT store,country,language,state,count(*) count,
            max(last_success) newest_success,min(last_success) oldest_success
            FROM listings GROUP BY store,country,language,state''')),
        'freshness': list(db.execute('''SELECT store,country,language,count(*) observed,
            CAST(coalesce(sum(last_success>=%s),0) AS UNSIGNED) succeeded_last_24h,
            CAST(coalesce(sum(last_success IS NULL),0) AS UNSIGNED) never_succeeded
            FROM listings GROUP BY store,country,language''', (now - 86400,))),
        'imports': list(db.execute('SELECT * FROM imports ORDER BY started')),
        'events': list(db.execute('SELECT kind,count(*) count FROM events GROUP BY kind')),
        'requests': list(db.execute('SELECT outcome,count(*) count FROM requests GROUP BY outcome')),
        'discovery': list(db.execute('SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 30')),
        'keywords': db.execute('''SELECT count(*) total,
            CAST(coalesce(sum(enabled=1),0) AS UNSIGNED) enabled,
            CAST(coalesce(sum(enabled=1 AND due<=%s AND lease_until<=%s),0) AS UNSIGNED) due,
            CAST(coalesce(sum(failures>0),0) AS UNSIGNED) retrying
            FROM keyword_targets''', (now, now)).fetchone(),
        'coverage_note': 'Known IDs only. Imported IDs are not live-verified. Region unavailable is not global delisting.',
    }


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
