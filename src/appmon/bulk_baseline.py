"""Checkpointed, zero-network imports of verified public source files.

These are attributed historical field baselines, never current store observations.
"""
import csv,json,lzma,time
from datetime import datetime,timezone
from pathlib import Path
from .db import dump
from .ingest import ALIASES,valid_id,file_hash,disk_check

FILES={'catalog':'store_apps.tsv.xz','live':'live_store_apps.tsv.xz','metrics':'store_apps_metrics.tsv.xz','descriptions':'appgoblin_descriptions_*.tsv.xz'}
SOURCE='https://github.com/appgoblin-dev/appgoblin-data'

class ImportPaused(Exception):pass

def control(db,digest,action):
    if action not in ('pause','resume'):raise ValueError('未知数据包操作')
    row=db.execute('SELECT status FROM source_bulk_imports WHERE digest=%s',(digest,)).fetchone()
    if not row:raise ValueError('数据包任务不存在')
    if row['status'] not in ('complete','complete_with_rejections'):
        db.execute("UPDATE source_bulk_imports SET status=%s,error=NULL,updated_at=%s WHERE digest=%s AND status NOT IN ('complete','complete_with_rejections')",('paused_user' if action=='pause' else 'queued',time.time(),digest))
    return {'saved':True}

def dataset_path(cfg,dataset):
    root=(Path(cfg['data_dir'])/'raw').resolve()
    patterns=(["appgoblin_store-apps-metrics_*.tsv.xz"] if dataset=='metrics' else [])+[FILES[dataset]]
    for pattern in patterns:
        for path in sorted(root.glob(pattern),reverse=True):
            if path.is_file() and path.resolve().is_relative_to(root):return path
    return None

def queue(db,cfg,dataset):
    if not isinstance(dataset,str) or dataset not in FILES:raise ValueError('未知公开数据包')
    path=dataset_path(cfg,dataset)
    if path is None:raise ValueError('尚未下载此公开数据包')
    digest=file_hash(path);now=time.time()
    db.execute('''INSERT INTO source_bulk_imports(digest,dataset,file_name,status,created_at,updated_at)
        VALUES(%s,%s,%s,'queued',%s,%s) ON DUPLICATE KEY UPDATE
        status=IF(source_bulk_imports.status IN ('complete','complete_with_rejections'),source_bulk_imports.status,'queued'),error=NULL,updated_at=VALUES(updated_at)''',
        (digest,dataset,path.name,now,now))
    return {'digest':digest,'dataset':dataset}

def report(db,cfg):
    return {'source':SOURCE,'license':'CC BY 4.0','coverage':'历史字段基线，不代表实时详情、全球各市场、全量评论或收入已采完',
        'files':[{'dataset':key,'file_name':path.name if (path:=dataset_path(cfg,key)) else name,'available':path is not None} for key,name in FILES.items()],
        'imports':db.execute('SELECT * FROM source_bulk_imports ORDER BY created_at DESC LIMIT 20').fetchall()}

def run(db,cfg,chunk_size=2000):
    with db.named_lock('public-baseline-import'):
        job=db.execute("SELECT * FROM source_bulk_imports WHERE status IN ('queued','running') ORDER BY created_at LIMIT 1").fetchone()
        if not job:return {'processed':0}
        root=(Path(cfg['data_dir'])/'raw').resolve();path=(root/job['file_name']).resolve()
        if not path.is_relative_to(root):raise ValueError('数据包路径无效')
        processed=job['processed'];valid=job['valid'];rejected=job['rejected'];batch=[];now=time.time()
        def flush():
            disk_check(cfg['data_dir'],cfg.get('min_free_gb',15))
            with db:
                state=db.execute('SELECT status FROM source_bulk_imports WHERE digest=%s FOR UPDATE',(job['digest'],)).fetchone()
                if state['status']=='paused_user':raise ImportPaused()
                if job['dataset']=='descriptions':
                    from .content_store import put
                    batch[:]=[(dataset,store,app_id,put(db,payload),observed,imported,digest) for dataset,store,app_id,payload,observed,imported,digest in batch]
                if batch:db.executemany('''INSERT INTO public_app_baselines VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                    payload=IF(public_app_baselines.source_observed_at IS NULL OR VALUES(source_observed_at) IS NULL OR VALUES(source_observed_at)>=public_app_baselines.source_observed_at,VALUES(payload),public_app_baselines.payload),
                    file_digest=IF(public_app_baselines.source_observed_at IS NULL OR VALUES(source_observed_at) IS NULL OR VALUES(source_observed_at)>=public_app_baselines.source_observed_at,VALUES(file_digest),public_app_baselines.file_digest),
                    source_observed_at=IF(public_app_baselines.source_observed_at IS NULL OR VALUES(source_observed_at) IS NULL OR VALUES(source_observed_at)>=public_app_baselines.source_observed_at,VALUES(source_observed_at),public_app_baselines.source_observed_at),
                    imported_at=VALUES(imported_at)''',batch)
                db.execute("UPDATE source_bulk_imports SET processed=%s,valid=%s,rejected=%s,updated_at=%s WHERE digest=%s",(processed,valid,rejected,time.time(),job['digest']))
            batch.clear()
        try:
            if file_hash(path)!=job['digest']:raise ValueError('数据包内容已变化，需按新版本重新入队')
            with db:
                current=db.execute('SELECT processed,valid,rejected,status FROM source_bulk_imports WHERE digest=%s FOR UPDATE',(job['digest'],)).fetchone()
                if current['status'] not in ('queued','running'):return dict(current)
                db.execute("UPDATE source_bulk_imports SET status='running',error=NULL WHERE digest=%s",(job['digest'],))
            with lzma.open(path,'rt',encoding='utf-8-sig',newline='') as stream:
                reader=csv.DictReader(stream,delimiter='\t',strict=True)
                if 'store_id' not in (reader.fieldnames or []) or not {'store','appstore'}&set(reader.fieldnames or []):raise ValueError('数据包缺少应用身份字段')
                if len(set(reader.fieldnames))!=len(reader.fieldnames):raise ValueError('数据包存在重复字段名')
                for number,row in enumerate(reader,1):
                    if number<=job['processed']:continue
                    processed=number;store=ALIASES.get((row.get('store') or row.get('appstore') or '').strip().lower());app_id=(row.get('store_id') or '').strip()
                    if None in row or len(app_id)>512 or not valid_id(store,app_id):rejected+=1
                    else:
                        observed=None
                        source_date=row.get('appgoblin_updated_at') or row.get('description_last_updated')
                        if source_date:
                            try:
                                stamp=datetime.fromisoformat(source_date)
                                observed=(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
                                if observed>time.time()+300:observed=None
                            except ValueError:pass
                        batch.append((job['dataset'],store,app_id,dump(row),observed,now,job['digest']));valid+=1
                    if number%chunk_size==0:flush()
                flush()
            status='complete_with_rejections' if rejected else 'complete'
            db.execute('UPDATE source_bulk_imports SET status=%s,updated_at=%s WHERE digest=%s',(status,time.time(),job['digest']))
            return {'processed':processed,'valid':valid,'rejected':rejected,'status':status}
        except ImportPaused:
            row=db.execute('SELECT processed,valid,rejected,status FROM source_bulk_imports WHERE digest=%s',(job['digest'],)).fetchone()
            return dict(row)
        except Exception as exc:
            # Only committed checkpoints are resumable. No source rows are marked as store success.
            status='paused_disk' if isinstance(exc,OSError) and 'Insufficient free space' in str(exc) else 'error'
            db.execute('UPDATE source_bulk_imports SET status=%s,error=%s,updated_at=%s WHERE digest=%s',(status,type(exc).__name__,time.time(),job['digest']))
            raise

def app(db,store,app_id):
    if not valid_id(store,app_id):raise ValueError('应用 ID 无效')
    rows=db.execute("SELECT dataset,payload,source_observed_at,imported_at,file_digest FROM public_app_baselines WHERE dataset IN ('catalog','live','metrics','descriptions') AND store=%s AND app_id=%s",(store,app_id)).fetchall()
    return {'source':SOURCE,'scope':'来源未提供逐国家观察，不能替代具体市场状态','items':[r|{'payload':json.loads(r['payload'])} for r in rows]}
