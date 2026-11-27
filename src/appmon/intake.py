"""Stable request aliases and normalized identities for collection submissions."""
import hashlib
import json
from .db import dump


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def find_alias(db,resource,source,request_id):
    return db.execute("""SELECT task_id,fingerprint FROM collection_request_aliases
        WHERE resource=%s AND source=%s AND request_id=%s""",(resource,source,request_id)).fetchone()


def record_alias(db,resource,source,request_id,task_id,fingerprint,legacy_id=None):
    db.execute("""INSERT INTO collection_request_aliases(resource,source,request_id,task_id,fingerprint,legacy_id)
        VALUES(%s,%s,%s,%s,%s,%s)""",(resource,source,request_id,task_id,fingerprint,legacy_id))


def resolve_id(db,resource,identifier):
    row = db.execute("SELECT task_id FROM collection_request_aliases WHERE resource=%s AND legacy_id=%s",
                     (resource,identifier)).fetchone()
    return row['task_id'] if row else identifier


def lock_source_intake(db):
    # Held by the caller's transaction, including multi-task ad submissions.
    db.execute("""INSERT INTO advisory_mutexes(name) VALUES('source-task-intake')
        ON DUPLICATE KEY UPDATE name=advisory_mutexes.name""")
    db.execute("SELECT name FROM advisory_mutexes WHERE name='source-task-intake' FOR UPDATE")


def index_existing_plans(db):
    from .collection import unique_actions, plan_key
    from . import ad_sources
    # A restored or upgraded database can have plans without identity keys.
    cursor = 0
    marker = 'indexed-legacy-plans'
    if db.execute('SELECT 1 FROM collection_plan_keys WHERE fingerprint=%s',(marker,)).fetchone():
        return
    if not db.execute('SELECT 1 FROM collection_plans LIMIT 1').fetchone():
        return
    with db.named_lock('collection-plans',timeout=30):
        while True:
            with db:
                rows = db.execute("""SELECT id,request_source,request_id,status,completed,actions,specification
                    FROM collection_plans WHERE id>%s ORDER BY id LIMIT 50 FOR UPDATE""",(cursor,)).fetchall()
                if not rows:break
                for row in rows:
                    spec = json.loads(row['specification'])
                    actions = unique_actions(json.loads(row['actions']))
                    if any(a['kind'] in ad_sources.KINDS for a in actions) and not spec.get('ad_requested_by'):
                        continue
                    mode = 'collect' if row['request_source'] is not None else 'configure'
                    if mode=='configure' and 'app_request' in spec:mode='app'
                    key = plan_key(mode,actions,spec)
                    current = db.execute("""SELECT p.id,p.status FROM collection_plan_keys k
                        JOIN collection_plans p ON p.id=k.plan_id WHERE k.fingerprint=%s""",(key,)).fetchone()
                    if (current and current['id'] < row['id'] and current['status'] in ('queued','running')
                            and row['status']=='queued' and row['completed']==0):
                        db.execute("""UPDATE collection_request_aliases SET task_id=%s
                            WHERE resource='plans' AND task_id=%s""",(current['id'],row['id']))
                        record_alias(db,'plans',row['request_source'] or '',row['request_id'] or str(row['id']),
                                     current['id'],key,row['id'])
                        db.execute('DELETE FROM collection_plans WHERE id=%s',(row['id'],))
                    else:
                        db.execute("""INSERT INTO collection_plan_keys VALUES(%s,%s)
                            ON DUPLICATE KEY UPDATE plan_id=GREATEST(plan_id,VALUES(plan_id))""",(key,row['id']))
            cursor = rows[-1]['id']
    db.execute('INSERT IGNORE INTO collection_plan_keys VALUES(%s,0)',(marker,))
