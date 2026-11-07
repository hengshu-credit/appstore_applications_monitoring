"""Start the approved first collection phase with durable, scoped settings."""
import json
import time
from pathlib import Path
from appmon.config import load
from appmon.db import connect,enqueue,dump
from appmon import pipelines,app_media,collection_limits
from appmon.storage_policy import media_predicate,storage_guard
from appmon.task_logs import emit


def main():
    cfg=load();db=connect(cfg)
    try:
        if cfg.get('raw_response_scope')!='groups' or cfg.get('media_scope')!='groups_and_current_charts' or cfg.get('archive_videos',True):
            raise ValueError('Configure the approved raw/image scope and disable video first')
        if cfg['min_free_gb']<15 or storage_guard(db,cfg):raise ValueError('At least 15 GiB free-space protection is required')
        with db.named_lock('public-collection-start',timeout=10):
            pool=pipelines.settings(db,cfg,'pool')
            if len(pool['countries'])!=1:raise ValueError('First phase requires one baseline market; global expansion is staged')
            before=dict(pool=pool,limits=collection_limits.settings(db,cfg))
            started=time.time()
            print('Preparing the current group/chart image scope...',flush=True)
            media=app_media.sync_existing(db,cfg,wait=120)
            print('Completing the baseline application queue...',flush=True)
            added=enqueue(db,pool['countries'],pool['language'])
            if storage_guard(db,cfg):raise ValueError('Storage guard reached while preparing queues')
            with db:
                # Explicit start applies to the chosen focus/current-chart dependencies.
                resumed=db.execute('UPDATE watchlist w SET enabled=1 WHERE '+media_predicate('w')).rowcount
                icons=db.execute('UPDATE app_icons i SET requested=1,enabled=1 WHERE '+media_predicate()).rowcount
                excluded=db.execute('UPDATE app_icons i SET requested=0,token=NULL,lease_until=0 WHERE NOT '+media_predicate()).rowcount
                charts=db.execute("UPDATE observation_targets SET enabled=1 WHERE kind='chart'").rowcount
            pipelines.save(db,cfg,'pool',{'enabled':True})
            collection_limits.save(db,cfg,{'paused':False})
            counts=db.execute('SELECT store,COUNT(*) apps FROM apps GROUP BY store').fetchall()
            result=dict(started_at=started,phase='全池公开主档首轮',inventory=counts,
                baseline_markets=pool['countries'],baseline_language=pool['language'],added_jobs=added,
                priority_targets_enabled=resumed,image_targets_updated=icons,out_of_scope_images_unrequested=excluded,
                chart_targets_enabled=charts,media=media,raw_scope='groups',media_scope='groups_and_current_charts',
                videos=False,min_free_gib=cfg['min_free_gb'],previous=before,
                later_phases={'global_expansion':'按覆盖缺口分阶段扩展；需先解决磁盘容量','history_commercial':'待对应来源覆盖和额度确认'})
            db.execute('''INSERT INTO collection_controls VALUES('public-collection-start',%s,%s) AS incoming
                ON DUPLICATE KEY UPDATE settings=incoming.settings,updated_at=incoming.updated_at''',(dump(result),time.time()))
            emit(db,'pipelines','pool','started','全池首轮已启动；原始响应仅关注应用，图片仅关注及最新榜单应用，不下载视频；磁盘不足自动暂停')
            path=cfg['data_dir']/'exports'/'public-collection-start.json';path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps(result,ensure_ascii=True),flush=True)
    finally:db.close()


if __name__=='__main__':main()
