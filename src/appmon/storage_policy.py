"""Keep structured data globally; archive selected applications' raw/media only."""
import shutil
import time
from .db import dump


def focused(db,store,app_id):
    return bool(app_id and db.execute('SELECT 1 FROM app_group_members WHERE store=%s AND app_id=%s LIMIT 1',(store,app_id)).fetchone())


def media_predicate(alias='i'):
    return f'''(EXISTS(SELECT 1 FROM app_group_members g WHERE g.store={alias}.store AND g.app_id={alias}.app_id)
        OR EXISTS(SELECT 1 FROM observation_targets t JOIN observation_items o ON o.run_id=t.latest_success_id
            WHERE t.kind='chart' AND t.store={alias}.store AND o.item_id={alias}.app_id AND o.position<=50))'''


def media_allowed(db,cfg,store,app_id):
    if cfg.get('media_scope','all')=='all':return True
    return bool(db.execute('SELECT 1 FROM (SELECT %s store,%s app_id) i WHERE '+media_predicate(),(store,app_id)).fetchone())


def archive_raw(db,cfg,store,app_ids,raw):
    if not raw:return ''
    ids=list(dict.fromkeys(str(v) for v in app_ids if v))
    allowed=cfg.get('raw_response_scope','all')=='all'
    if not allowed and ids:
        allowed=bool(db.execute('SELECT 1 FROM app_group_members WHERE store=%s AND app_id IN ('+
            ','.join('%s' for _ in ids)+') LIMIT 1',(store,*ids)).fetchone())
    if not allowed:return ''
    from .monitor import raw_save
    return raw_save(cfg['data_dir'],raw)


def storage_guard(db,cfg):
    if cfg.get('min_free_gb',0)<=0:return False
    free=shutil.disk_usage(cfg['data_dir']).free
    threshold=int(cfg['min_free_gb']*1024**3)
    if free>=threshold:return False
    from .collection_limits import settings,save
    if not settings(db,cfg)['paused']:
        save(db,cfg,{'paused':True})
        db.execute('''INSERT INTO collection_controls VALUES('storage-guard',%s,%s) AS incoming
            ON DUPLICATE KEY UPDATE settings=incoming.settings,updated_at=incoming.updated_at''',
            (dump({'free_bytes':free,'minimum_bytes':threshold,'reason':'磁盘余量不足，采集自动暂停；扩容后可恢复'}),time.time()))
        from .task_logs import emit
        emit(db,'pipelines','pool','storage_paused','磁盘余量不足，已自动暂停采集；现有结果和任务进度保留','warning')
    return True
