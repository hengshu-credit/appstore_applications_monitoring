"""Keep structured data globally; archive selected applications' raw/media only."""
import shutil
import time
from .db import dump


def focused(db,store,app_id):
    return bool(app_id and db.execute('SELECT 1 FROM app_group_members WHERE store=%s AND app_id=%s LIMIT 1',(store,app_id)).fetchone())


def media_scope_apps():
    """One materialized identity set; never correlate every queued icon to every chart."""
    return '''SELECT store,app_id FROM app_group_members UNION
        SELECT t.store,o.item_id app_id FROM observation_targets t
        JOIN observation_items o ON o.run_id=t.latest_success_id
        WHERE t.kind='chart' AND t.latest_success_id IS NOT NULL AND o.position<=50'''


def media_predicate(alias='i'):
    return f'''(EXISTS(SELECT 1 FROM app_group_members g WHERE g.store={alias}.store AND g.app_id={alias}.app_id)
        OR EXISTS(SELECT 1 FROM observation_targets t JOIN observation_items o ON o.run_id=t.latest_success_id
            WHERE t.kind='chart' AND t.latest_success_id IS NOT NULL
                AND t.store={alias}.store AND o.item_id={alias}.app_id AND o.position<=50))'''


def detail_batch_scope(db,cfg,jobs):
    """Resolve retention once for this same-store commit, including on retries."""
    scoped_media=cfg.get('media_scope','all')=='groups_and_current_charts'
    if cfg.get('raw_response_scope','all')=='all' and not scoped_media:return cfg
    store=jobs[0]['store']
    ids=tuple(job['app_id'] for job in jobs)
    groups={r['app_id'] for r in db.execute("""SELECT DISTINCT app_id FROM app_group_members
        WHERE store=%s AND app_id IN %s""",(store,ids)).fetchall()}
    media=groups.copy()
    remaining=tuple(identifier for identifier in ids if identifier not in groups)
    if scoped_media and remaining:
        media.update(r['item_id'] for r in db.execute("""SELECT DISTINCT o.item_id
            FROM observation_items o JOIN observation_targets t ON t.latest_success_id=o.run_id
            WHERE t.store=%s AND t.kind='chart' AND o.item_id IN %s AND o.position<=50""",(store,remaining)).fetchall())
    return cfg | {'_detail_groups':groups,'_detail_media':media}


def media_allowed(db,cfg,store,app_id):
    if cfg.get('media_scope','all')=='all':return True
    if '_detail_media' in cfg:return app_id in cfg['_detail_media']
    return bool(db.execute('SELECT 1 FROM (SELECT %s store,%s app_id) i WHERE '+media_predicate(),(store,app_id)).fetchone())


def archive_raw(db,cfg,store,app_ids,raw):
    if not raw:return ''
    ids=list(dict.fromkeys(str(v) for v in app_ids if v))
    allowed=cfg.get('raw_response_scope','all')=='all'
    if not allowed and '_detail_groups' in cfg:
        allowed=not cfg['_detail_groups'].isdisjoint(ids)
    elif not allowed and ids:
        allowed=bool(db.execute('SELECT 1 FROM app_group_members WHERE store=%s AND app_id IN ('+
            ','.join('%s' for _ in ids)+') LIMIT 1',(store,*ids)).fetchone())
    if not allowed:return ''
    from .monitor import raw_save
    refs=cfg.get('_batch_raw_refs')
    if refs is None:return raw_save(cfg,raw)
    # Eligibility belongs to the logical target. Only immutable object writes
    # are shared, for the lifetime of this fetched batch (including DB retries).
    if raw not in refs:refs[raw]=raw_save(cfg,raw)
    return refs[raw]


def storage_guard(db,cfg):
    if cfg.get('min_free_gb',0)<=0:return False
    free=shutil.disk_usage(cfg['data_dir']).free
    threshold=int(cfg['min_free_gb']*1024**3)
    if free>=threshold:return False
    from .collection_limits import settings,save
    if not settings(db,cfg)['paused']:
        save(db,cfg,{'paused':True})
        db.execute('''INSERT INTO collection_controls VALUES('storage-guard',%s,%s)
            ON DUPLICATE KEY UPDATE settings=VALUES(settings),updated_at=VALUES(updated_at)''',
            (dump({'free_bytes':free,'minimum_bytes':threshold,'reason':'磁盘余量不足，采集自动暂停；扩容后可恢复'}),time.time()))
        from .task_logs import emit
        emit(db,'pipelines','pool','storage_paused','磁盘余量不足，已自动暂停采集；现有结果和任务进度保留','warning')
    return True
