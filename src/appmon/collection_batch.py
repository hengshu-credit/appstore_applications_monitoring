"""Commit one fetched batch and all its task acknowledgements atomically."""
from contextlib import contextmanager
import re
import time

NETWORK_ERRORS = r'^(HTTP [0-9]{3}|ConnectTimeout|ReadTimeout|ConnectionError|ProxyError|SSLError|整体采集已暂停|等待共享限速窗口)'


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
