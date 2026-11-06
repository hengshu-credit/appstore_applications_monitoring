"""Shared, demand-driven batch dispatch across all configured collection workers."""
import logging
import os
import socket
import time

from .collection_limits import blocked
from .db import connect

log = logging.getLogger(__name__)
LANES = ('details', 'keywords', 'chart', 'reviews', 'featured', 'permissions', 'icons', 'pool', 'sources')


def ready(db, cfg, lane, now):
    if lane == 'sources':
        return db.execute('SELECT 1 FROM collection_source_tasks WHERE enabled=1 AND due<=%s AND lease_until<=%s AND failures<3 LIMIT 1',(now,now)).fetchone() is not None
    if lane == 'details':
        sql = '''SELECT 1 FROM watchlist w JOIN jobs j USING(store,app_id,country,language)
            WHERE w.enabled=1 AND j.due<=%s AND j.lease_until<=%s LIMIT 1'''
        args = (now, now)
    elif lane == 'pool':
        from .pipelines import configured
        enabled, pool = configured(db, cfg, 'pool')
        if not enabled:
            return False
        sql = '''SELECT 1 FROM jobs j WHERE due<=%s AND lease_until<=%s AND language=%s
            AND country IN ('''+','.join('%s' for _ in pool['countries'])+''')
            AND NOT EXISTS (SELECT 1 FROM watchlist w WHERE w.store=j.store AND w.app_id=j.app_id
                AND w.country=j.country AND w.language=j.language) LIMIT 1'''
        args = (now, now, pool['language'], *pool['countries'])
    else:
        table = {'keywords':'keyword_targets','permissions':'app_permission_targets','icons':'app_icons'}.get(lane,'observation_targets')
        sql = f'SELECT 1 FROM {table} WHERE enabled=1 AND due<=%s AND lease_until<=%s'
        args = (now, now)
        if table == 'observation_targets':
            sql += ' AND kind=%s'
            args += (lane,)
        if lane == 'icons':
            sql += ' AND requested=1'
        sql += ' LIMIT 1'
    return db.execute(sql,args).fetchone() is not None


def choose(db, cfg):
    if blocked(db,cfg):
        return None
    with db:
        # Serialize only the scheduling decision. HTTP happens outside this lock.
        db.execute("INSERT INTO schedules VALUES('dispatch:turn',0) ON DUPLICATE KEY UPDATE name=schedules.name")
        turn = db.execute("SELECT next_at FROM schedules WHERE name='dispatch:turn' FOR UPDATE").fetchone()['next_at']
        scores = {r['name'][9:]:r['next_at'] for r in db.execute("SELECT name,next_at FROM schedules WHERE name LIKE 'dispatch:%'").fetchall()}
        for lane in sorted(LANES,key=lambda name:scores.get(name,0)):
            if ready(db,cfg,lane,time.time()):
                db.execute("UPDATE schedules SET next_at=%s WHERE name='dispatch:turn'",(turn+1,))
                db.execute('INSERT INTO schedules VALUES(%s,%s) AS incoming ON DUPLICATE KEY UPDATE next_at=incoming.next_at',
                           ('dispatch:'+lane,turn+1))
                return lane
    return None


def run_batch(db, cfg, lane):
    if lane == 'sources':
        from .source_tasks import work
        return work(db,cfg)
    from .stores import Client
    from .keyword_stores import KeywordClient
    from .observation_stores import ObservationClient
    from .app_permissions import PermissionClient
    client = (PermissionClient if lane=='permissions' else KeywordClient if lane=='keywords' else ObservationClient if lane in ('chart','reviews','featured') else Client)(cfg,db)
    client.lineage_lane = lane
    size = cfg.get('google_rpc_batch_size',20)
    try:
        if lane in ('details','pool'):
            from .detail_batches import work_details
            return work_details(cfg,db,max(size,cfg.get('apple_lookup_batch_size',50)),client,
                                watchlist=lane=='details',managed=lane=='pool',max_requests=1)
        if lane == 'keywords':
            from .keywords import save_search
            from .target_batches import work_targets
            return work_targets(cfg,db,size,client,table='keyword_targets',resource='keywords',single='search',
                                multiple='search_many',save=save_search,max_batches=1)
        if lane in ('chart','reviews','featured'):
            from .intelligence import save_observation
            from .target_batches import work_targets
            return work_targets(cfg,db,size,client,table='observation_targets',resource='targets',single='fetch_observation',
                                multiple='fetch_observations',save=save_observation,max_batches=1,kind=lane)
        if lane == 'permissions':
            from .app_permissions import save_batch_result
            from .target_batches import work_targets
            return work_targets(cfg,db,size,client,table='app_permission_targets',resource='permissions',single='collect',
                                multiple='collect_many',save=save_batch_result,max_batches=1)
        from .app_icons import work_icons
        return work_icons(cfg,db,1,client)
    finally:
        client.session.close()


def dispatch_once(db, cfg, worker_id):
    name = 'collector:'+worker_id
    db.execute('INSERT INTO schedules VALUES(%s,%s) AS incoming ON DUPLICATE KEY UPDATE next_at=incoming.next_at',(name,time.time()))
    lane = choose(db,cfg)
    if lane is None:
        return {'processed':0,'lane':None}
    result = run_batch(db,cfg,lane)
    db.execute('INSERT INTO schedules VALUES(%s,%s) AS incoming ON DUPLICATE KEY UPDATE next_at=incoming.next_at',(name,time.time()))
    return result | {'lane':lane}


def worker(cfg, role='collector'):
    from .collection import work_plans
    worker_id = f'{role}:{socket.gethostname()}:{os.getpid()}'
    while True:
        db = None
        result = {'processed':0}
        try:
            db = connect(cfg,initialize=False)
            work_plans(db,100)
            result = dispatch_once(db,cfg,worker_id)
            if result['processed']:
                log.info('Collection batch %s %s',worker_id,result)
        except Exception as exc:
            log.error('Collection batch failed: %s',type(exc).__name__)
        finally:
            if db is not None:
                db.close()
        if not result['processed']:
            time.sleep(5)


def status(db):
    now = time.time()
    workers = db.execute("SELECT name,next_at FROM schedules WHERE name LIKE %s AND next_at>=%s ORDER BY name",('collector:%',now-1800)).fetchall()
    return {'mode':'dynamic','workers':[{'name':r['name'][10:],'heartbeat':r['next_at']} for r in workers],
            'lanes':list(LANES)}
