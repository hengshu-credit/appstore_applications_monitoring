"""Commit one fetched batch and all its task acknowledgements atomically."""
from contextlib import contextmanager
import re

NETWORK_ERRORS = r'^(HTTP [0-9]{3}|ConnectTimeout|ReadTimeout|ConnectionError|ProxyError|SSLError|整体采集已暂停|等待共享限速窗口)'


def can_batch(row):
    return not row['failures'] or bool(re.match(NETWORK_ERRORS,row.get('last_error') or ''))


def retry_condition(prefix=''):
    return f"({prefix}failures=0 OR {prefix}last_error REGEXP %s)"


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
