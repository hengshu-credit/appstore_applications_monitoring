"""Commit one fetched batch and all its task acknowledgements atomically."""
from contextlib import contextmanager
from itertools import groupby
import re
import time
import logging
import pymysql

NETWORK_ERRORS = r'^(HTTP [0-9]{3}|ConnectTimeout|ReadTimeout|ConnectionError|ProxyError|SSLError|ChunkedEncodingError|ContentDecodingError|整体采集已暂停|等待共享限速窗口)'


# Explicit refresh uses a negated Unix timestamp. Page submissions sort between
# those requests and ordinary due times (zero for new work).
PAGE_DUE = -1
IMMEDIATE_DUE_MAX = -2


def pending_due_on_insert(table):
    """A page that shares an already pending target may promote its next claim."""
    enabled = '' if table == 'jobs' else f'{table}.enabled=1 AND '
    return (f'IF(VALUES(due)<0 AND {enabled}{table}.failures=0 AND {table}.lease_until<=UNIX_TIMESTAMP() '
            f'AND {table}.due<=UNIX_TIMESTAMP(),LEAST({table}.due,VALUES(due)),{table}.due)')


def target_settings_update(table, initial_due, merge_existing, preserve_existing=False):
    due = pending_due_on_insert(table) if initial_due < 0 else f'{table}.due'
    if preserve_existing:
        return 'due=' + due
    if merge_existing:
        deadline = 'VALUES(due)' if initial_due < 0 else 'UNIX_TIMESTAMP()'
        return (f'due=IF(VALUES(depth)>{table}.depth OR VALUES(refresh_hours)<{table}.refresh_hours,'
                f'LEAST({table}.due,{deadline}),{due}),'
                f'depth=GREATEST({table}.depth,VALUES(depth)),refresh_hours=LEAST({table}.refresh_hours,VALUES(refresh_hours))')
    return 'due=' + due + ',depth=VALUES(depth),refresh_hours=VALUES(refresh_hours)'


def batch_due_limit(first_due, now):
    if first_due < PAGE_DUE:
        return IMMEDIATE_DUE_MAX
    return PAGE_DUE if first_due == PAGE_DUE else now


def can_batch(row):
    return not row['failures'] or bool(re.match(NETWORK_ERRORS,row.get('last_error') or ''))


def retry_condition(prefix=''):
    return f"({prefix}failures=0 OR {prefix}last_error REGEXP %s)"


def claim_mode(db,key):
    """Give due retries and scheduled/deferred tasks a turn beside first-time work."""
    row=db.execute('SELECT next_at FROM schedules WHERE name=%s FOR UPDATE',(key,)).fetchone()
    turn=int(row['next_at'])+1
    db.execute('UPDATE schedules SET next_at=%s WHERE name=%s',(turn,key))
    return 'retry' if turn%5==0 else 'scheduled' if turn%5==4 else 'fresh'


def defer(db,table,row,error):
    if table not in ('jobs','keyword_targets','observation_targets','app_permission_targets','app_icons','app_media_assets'):
        raise ValueError('Unknown deferred task table')
    due=time.time()+max(1,error.retry_after)
    if table=='jobs':
        return db.execute('''UPDATE jobs SET due=%s,token=NULL,lease_until=0 WHERE store=%s AND app_id=%s
            AND country=%s AND language=%s AND token=%s''',
            (due,row['store'],row['app_id'],row['country'],row['language'],row['token'])).rowcount
    return db.execute(f'UPDATE {table} SET due=%s,token=NULL,lease_until=0'+(",status='queued'" if table=='app_icons' else '')+
        ' WHERE id=%s AND token=%s AND enabled=1',(due,row['id'],row['token'])).rowcount


class BatchWriter:
    """Existing per-target saves join the enclosing batch's transaction."""
    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db,name)

    def begin(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        # A stale target exits before writing. Exceptions propagate to the batch.
        pass

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        return False


def revision(db):
    row = db.execute("SELECT next_at FROM schedules WHERE name='collection:data-revision'").fetchone()
    return int(row['next_at']) if row else 0


def touch(db):
    db.execute("""INSERT INTO schedules VALUES('collection:data-revision',1)
        ON DUPLICATE KEY UPDATE next_at=schedules.next_at+1""")


@contextmanager
def write_batch(db):
    with db:
        yield BatchWriter(db)
        touch(db)


def persist_batch(db,save,app_keys=(),attempts=3):
    """Retry only rolled-back lock conflicts, retaining the fetched response in memory."""
    keys=sorted(set(app_keys))
    for attempt in range(attempts):
        try:
            with write_batch(db) as writer:
                # Claiming network work never holds these locks. Existing app
                # identities are locked in a common order only at commit time.
                # A large tuple-IN can become a full scan on OceanBase. Fix the
                # first PK component so each batch uses exact app identity gets.
                for store, group in groupby(keys, key=lambda pair: pair[0]):
                    ids=[pair[1] for pair in group]
                    for start in range(0,len(ids),500):
                        batch=ids[start:start+500]
                        writer.execute('SELECT store,app_id FROM apps FORCE INDEX(PRIMARY) WHERE store=%s AND app_id IN ('+
                            ','.join('%s' for _ in batch)+') ORDER BY app_id FOR UPDATE',
                            (store,*batch)).fetchall()
                return save(writer)
        except pymysql.OperationalError as exc:
            if not exc.args or exc.args[0] not in (1205,1213) or attempt+1>=attempts:raise
            logging.getLogger(__name__).warning('Retrying database commit without HTTP: code=%s attempt=%s',exc.args[0],attempt+2)
            time.sleep(0.05*2**attempt)
