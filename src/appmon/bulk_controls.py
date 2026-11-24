"""Durable, bounded pause/resume of a fixed set of configured tasks."""

import json
import logging
import time

import pymysql

from .collection_batch import touch
from .collection_tasks import RESOURCES, TABLES, task_query
from .db import connect, dump

CHUNK_SIZE = 1000
log = logging.getLogger(__name__)


def enqueue(db, action, filters):
    row = db.execute(
        'INSERT INTO collection_bulk_operations(action,filters) VALUES(%s,%s)', (action, dump(filters))
    )
    return {'id': row.lastrowid, 'action': action, 'status': 'queued', 'completed': 0, 'total': 0}


def recent(db):
    return db.execute('''SELECT id,action,status,completed,total,error FROM collection_bulk_operations
        WHERE status<>'complete' OR id=(SELECT MAX(id) FROM collection_bulk_operations WHERE status='complete')
        ORDER BY id DESC''').fetchall()


def retry(db, identifier):
    db.execute(
        """UPDATE collection_bulk_operations SET status=IF(total=0,'queued','running'),
        failures=0,error=NULL WHERE id=%s AND status='error'""",
        (identifier,),
    )


def selection(db, filters, now):
    """Use primary-key pages, including when a state/search filter matches very few rows."""
    filters = {
        key: value for key, value in filters.items() if value and not (key == 'store' and value == 'all')
    }
    for resource, _, kinds in RESOURCES:
        if filters.get('kind') and filters['kind'] not in kinds:
            continue
        last = 0
        requested = ' AND requested=1' if resource == 'icons' else ''
        while True:
            rows = db.execute(
                f'SELECT id FROM {TABLES[resource]} WHERE id>%s{requested} ORDER BY id LIMIT %s',
                (last, CHUNK_SIZE),
            ).fetchall()
            if not rows:
                break
            last = rows[-1]['id']
            if filters:
                refs = [dict(resource=resource, id=r['id']) for r in rows]
                query, args = task_query(**filters, light=True, refs=refs, now=now)
                rows = db.execute(f'SELECT id FROM ({query}) matched ORDER BY id', args).fetchall()
            if rows:
                yield resource, [r['id'] for r in rows]


def snapshot(db, op):
    total = 0
    for sequence, (resource, ids) in enumerate(selection(db, json.loads(op['filters']), time.time())):
        db.execute(
            'INSERT INTO collection_bulk_chunks VALUES(%s,%s,%s,%s)',
            (op['id'], sequence, resource, dump(ids)),
        )
        total += len(ids)
    db.execute(
        'UPDATE collection_bulk_operations SET total=%s,status=%s,failures=0,error=NULL WHERE id=%s',
        (total, 'running' if total else 'complete', op['id']),
    )


def apply_chunk(db, op):
    chunk = db.execute(
        '''SELECT * FROM collection_bulk_chunks WHERE operation_id=%s
        ORDER BY sequence_no LIMIT 1''',
        (op['id'],),
    ).fetchone()
    resource = chunk['resource']
    table = TABLES[resource]
    ids = json.loads(chunk['task_ids'])
    marks = ','.join(['%s'] * len(ids))
    enabled = op['action'] == 'resume'
    assignments = ['enabled=%s']
    if resource != 'watchlist':
        assignments += ['token=NULL', 'lease_until=0']
    if resource == 'media':
        assignments.append('manual_paused=' + ('0' if enabled else '1'))
    eligible = ''
    if enabled and resource=='sources':
        from .source_tasks import admitted
        eligible = ' AND '+admitted()
    db.execute(f'UPDATE {table} SET ' + ','.join(assignments) + f' WHERE id IN ({marks})'+eligible, (enabled, *ids))
    if resource == 'watchlist':
        db.execute(
            f'''UPDATE watchlist w JOIN jobs j USING(store,app_id,country,language)
            SET j.token=NULL,j.lease_until=0 WHERE w.id IN ({marks}) AND (j.token IS NOT NULL OR j.lease_until<>0)''',
            ids,
        )
    message = '任务已恢复，等待到期后领取' if enabled else '任务已暂停，当前租约已失效'
    db.execute(
        f'''INSERT INTO collection_logs(resource,target_key,created_at,level,event,message)
        SELECT %s,CAST(id AS CHAR),%s,'info',%s,%s FROM {table} WHERE id IN ({marks}){eligible}''',
        (resource, time.time(), op['action'], message, *ids),
    )
    db.execute(
        'DELETE FROM collection_bulk_chunks WHERE operation_id=%s AND sequence_no=%s',
        (op['id'], chunk['sequence_no']),
    )
    completed = op['completed'] + len(ids)
    done = completed == op['total']
    db.execute(
        '''UPDATE collection_bulk_operations SET completed=%s,status=%s,failures=0,error=NULL
        WHERE id=%s''',
        (completed, 'complete' if done else 'running', op['id']),
    )
    if done:
        touch(db)


def step(db):
    next_op = db.execute(
        """SELECT id,status,completed FROM collection_bulk_operations WHERE status IN ('queued','running')
        ORDER BY id LIMIT 1"""
    ).fetchone()
    if not next_op:
        return False
    try:
        if next_op['status'] == 'queued':
            # Pin one view of mutable states, labels and media refs for the entire selection.
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        with db:
            op = db.execute(
                'SELECT * FROM collection_bulk_operations WHERE id=%s FOR UPDATE SKIP LOCKED',
                (next_op['id'],),
            ).fetchone()
            if not op or op['status'] != next_op['status']:
                return False
            if op['status'] == 'queued':
                snapshot(db, op)
            else:
                apply_chunk(db, op)
    except Exception as exc:
        error = (
            f'数据库错误 {exc.args[0]}'
            if isinstance(exc, pymysql.MySQLError) and exc.args
            else '批量操作失败：' + type(exc).__name__
        )
        log.exception('Bulk collection operation %s failed', next_op['id'])
        # The failed transaction left its current chunk intact. Bound retries and expose the checkpoint.
        db.execute(
            """UPDATE collection_bulk_operations SET status=IF(failures>=2,'error',status),
            failures=failures+1,error=%s WHERE id=%s AND status=%s AND completed=%s""",
            (error, next_op['id'], next_op['status'], next_op['completed']),
        )
        return False
    return True


def worker(cfg):
    while True:
        db = None
        try:
            db = connect(cfg)
            while True:
                if not step(db):
                    time.sleep(3)
        except Exception:
            log.exception('Bulk collection worker interrupted')
        finally:
            if db is not None:
                db.close()
        time.sleep(3)
