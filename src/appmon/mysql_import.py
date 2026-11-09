"""One-time, checkpointed read-only MySQL import into a separate OceanBase DB.

This is not a runtime backend. The source must be super_read_only and the
destination must be newly created by this importer. No active DB is overwritten.
"""
import base64
import concurrent.futures
from datetime import date, datetime
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import zlib

import pymysql
from dotenv import dotenv_values
from .config import load
from .db import TABLES, create_table_sql
from .objects import store

TRANSIENT = {'auth_sessions','auth_limits','collection_request_waiters'}
ZERO = '0'*64

def json_default(value):
    if isinstance(value,bytes):return {'bytes':base64.b64encode(value).decode()}
    if isinstance(value,Decimal):return {'decimal':str(value)}
    if isinstance(value,(date,datetime)):return {'date':value.isoformat()}
    raise TypeError(type(value).__name__)

def digest_rows(rows, columns):
    digest=hashlib.sha256()
    for row in rows:
        digest.update((json.dumps([row[c] for c in columns],ensure_ascii=False,separators=(',',':'),allow_nan=False,default=json_default)+'\n').encode())
    return digest.hexdigest()

def chain(previous, digest):
    return hashlib.sha256(bytes.fromhex(previous)+bytes.fromhex(digest)).hexdigest()

def after_predicate(keys, values):
    # MySQL can scan from the beginning for a tuple comparison even when the
    # tuple matches the PK. Expanded lexicographic ranges avoid quadratic paging.
    terms=[];args=[]
    for i,key in enumerate(keys):
        terms.append('('+' AND '.join([f'`{prefix}`=%s' for prefix in keys[:i]]+[f'`{key}`>%s'])+')')
        args.extend(values[:i+1])
    return '('+' OR '.join(terms)+')',args

def decode_legacy_blob(row):
    if row['codec']!='zlib6':raise ValueError('Unsupported legacy content codec')
    decoder=zlib.decompressobj();raw=decoder.decompress(row['body'],row['raw_bytes']+1)
    if len(raw)!=row['raw_bytes'] or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or hashlib.sha256(raw).hexdigest()!=row['digest']:
        raise ValueError('Legacy content checksum mismatch')
    return raw

class Importer:
    def __init__(self,target,root='data',state='data/mysql-import',batch_size=2000):
        self.cfg=load();self.active=self.cfg['oceanbase']['database']
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,64}',target) or target==self.active:
            raise ValueError('Use a separate staging database name')
        self.target=target;self.root=Path(root).resolve();self.state=Path(state).resolve()
        self.state.mkdir(parents=True,exist_ok=True);self.batch_size=batch_size
        if not 1<=batch_size<=5000:raise ValueError('Batch size must be 1..5000')
        env=dotenv_values('.env')
        self.source_options=dict(host=env.get('MYSQL_HOST','127.0.0.1'),port=int(env.get('MYSQL_PORT',3307)),
            user='root',password=env['MYSQL_ROOT_PASSWORD'],database=env['MYSQL_DATABASE'],
            charset='utf8mb4',autocommit=True,cursorclass=pymysql.cursors.DictCursor,read_timeout=300,write_timeout=300)
        self.target_options=self.cfg['oceanbase']|{'database':target,'charset':'utf8mb4','autocommit':True,'cursorclass':pymysql.cursors.DictCursor}
        self.objects=store(target);self.objects.initialize()
        self.file_lock=threading.Lock();self.files={};self.print_lock=threading.Lock()
        self.cancel=threading.Event()
        self.first_error=None
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=12)
        log=self.state/'files.jsonl'
        if log.exists():
            for line in log.read_text(encoding='utf8').splitlines():
                row=json.loads(line);self.files[row['path']]=row
        self.source_id=None

    def source(self):
        c=pymysql.connect(**self.source_options)
        with c.cursor() as q:
            q.execute('SELECT @@server_uuid uuid,@@super_read_only ro')
            row=q.fetchone()
            if row['ro']!=1:c.close();raise ValueError('Source must remain super_read_only')
            if self.source_id and row['uuid']!=self.source_id:c.close();raise ValueError('Source server changed')
            q.execute('SET SESSION TRANSACTION READ ONLY')
        return c

    def destination(self):
        c=pymysql.connect(**self.target_options)
        with c.cursor() as q:
            q.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
            q.execute('SET ob_query_timeout=300000000')
            q.execute('SET ob_trx_timeout=600000000')
        return c

    def prepare(self):
        source=self.source()
        with source.cursor() as q:
            q.execute('SELECT @@server_uuid uuid');self.source_id=q.fetchone()['uuid']
            q.execute('SHOW TABLES');self.tables=[next(iter(r.values())) for r in q.fetchall()]
            if set(self.tables)-set(TABLES):raise ValueError('Unknown source tables; refusing incomplete import')
            schema={}
            for table in self.tables:
                q.execute(f'SHOW CREATE TABLE `{table}`');schema[table]=q.fetchone()['Create Table']
        source.close()
        self.identity=hashlib.sha256(json.dumps([self.source_id,self.source_options['database'],schema],sort_keys=True).encode()).hexdigest()
        manifest=dict(source_uuid=self.source_id,source_database=self.source_options['database'],target=self.target,
                      identity=self.identity,batch_size=self.batch_size,tables=self.tables,
                      transient_tables=sorted(TRANSIENT),started=time.time(),source_root=str(self.root),
                      target_model=hashlib.sha256(json.dumps([create_table_sql(t) for t in sorted(TABLES)]).encode()).hexdigest(),
                      objects_identity=hashlib.sha256(json.dumps([self.objects.endpoint,self.objects.bucket,self.objects.prefix]).encode()).hexdigest())
        path=self.state/'manifest.json'
        if path.exists():
            old=json.loads(path.read_text(encoding='utf8'))
            for field in ['identity','target','batch_size','source_root','objects_identity','target_model']:
                if old[field]!=manifest[field]:raise ValueError('Import manifest differs: '+field)
        else:path.write_text(json.dumps(manifest,indent=2),encoding='utf8')
        opts=dict(self.target_options);opts.pop('database');admin=pymysql.connect(**opts)
        with admin.cursor() as q:
            q.execute('SELECT VERSION() v')
            if 'OceanBase' not in q.fetchone()['v']:raise ValueError('Target must be OceanBase')
            q.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s',(self.target,))
            exists=q.fetchone() is not None
            if exists:
                q.execute(f'SELECT COUNT(*) n FROM `{self.target}`.migration_progress WHERE source_sha256=%s',(self.identity,))
                if not q.fetchone()['n']:raise ValueError('Existing target is not this import')
            else:
                q.execute(f'CREATE DATABASE `{self.target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
                q.execute(f'USE `{self.target}`')
                q.execute(create_table_sql('migration_progress'))
                q.execute("INSERT INTO migration_progress(source_sha256,table_name,status) VALUES(%s,'__mysql_import__','copying')",(self.identity,))
            q.execute(f'USE `{self.target}`')
            for table in TABLES:q.execute(create_table_sql(table,if_not_exists=True))
        admin.close()

    def file(self,value,kind):
        if not value:return value
        path=(self.root/value).resolve()
        if not path.is_relative_to(self.root) or path.is_symlink():raise ValueError('Unsafe legacy asset path')
        prefixes={'responses':('responses',),'media':('icons',),'keyword-reports':('keyword_reports',),
                  'marketdata':('public-sources','marketdata'),'exports':('exports',)}
        prefix=prefixes[kind]
        if path.relative_to(self.root).parts[:len(prefix)]!=prefix:
            raise ValueError('Legacy asset is outside its allowed directory')
        stat=path.stat()
        with self.file_lock:cached=self.files.get(value)
        if cached:
            if (cached['size'],cached['mtime'])!=(stat.st_size,stat.st_mtime_ns):raise ValueError('Legacy asset changed')
            return cached['ref']
        if kind in ('responses','marketdata'):
            with gzip.open(path,'rb') as f:raw=f.read(128*1024*1024+1)
            if len(raw)>128*1024*1024:raise ValueError('Legacy response exceeds migration limit')
            mime='text/csv' if '.csv.' in path.name else 'application/json' if kind=='marketdata' else 'application/octet-stream'
            ref=self.objects.put(raw,kind=kind,mime=mime,compress=True)
        else:
            ext=path.suffix.lower().lstrip('.')
            mime={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp','gif':'image/gif','mp4':'video/mp4','webm':'video/webm','xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','jsonl':'application/x-ndjson'}.get(ext,'application/octet-stream')
            ref=self.objects.put_file(path,kind=kind,extension=ext or 'bin',mime=mime)
        expected=path.name.split('.')[0]
        if re.fullmatch('[0-9a-f]{64}',expected) and ref.rsplit('/',1)[1].split('.')[0]!=expected:
            raise ValueError('Legacy asset content does not match its SHA256 filename')
        self.objects.verify(ref)
        entry={'path':value,'ref':ref,'size':stat.st_size,'mtime':stat.st_mtime_ns}
        with self.file_lock:
            self.files[value]=entry
            with (self.state/'files.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(entry)+'\n')
        return ref

    def transform(self,table,row,columns):
        row=dict(row)
        if table=='content_blobs':
            raw=decode_legacy_blob(row)
            ref=self.objects.put(raw,kind='content',mime='application/json',compress=True)
            self.objects.verify(ref)
            row.pop('body');row.update(codec='object-v1',object_key=ref)
        if 'raw_path' in row and row['raw_path']:
            row['raw_path']=self.file(row['raw_path'],'keyword-reports' if table=='keyword_reports' else 'responses')
        if 'file_path' in row and row['file_path']:row['file_path']=self.file(row['file_path'],'media')
        if table=='marketdata_artifacts':row['path']=self.file(row['path'],'marketdata')
        if table=='exports':
            row['object_key']=self.file('exports/'+row['id']+'.jsonl','exports') if row['status']=='ready' else None
            if row['status']=='running':row['status']='queued'
        if table=='collection_plans':
            spec=json.loads(row['specification']);row['request_source']=spec.get('input_source');row['request_id']=spec.get('request_id') if row['request_source'] else None
            if row['status']=='running':row['status']='queued'
        if 'lease_until' in row:
            row['lease_until']=0.0
            if 'token' in row:row['token']=None
        missing=set(columns)-set(row)
        extra=set(row)-set(columns)
        if missing or extra:raise ValueError(f'Unsupported schema mapping: {table}, missing={missing}, extra={extra}')
        return row

    def copy_table(self,table):
        if self.cancel.is_set():raise RuntimeError('Import cancelled after another table failed')
        source=self.source()
        try:dest=self.destination()
        except BaseException:source.close();raise
        try:
            with source.cursor() as sq, dest.cursor() as dq:
                sq.execute(f'SHOW KEYS FROM `{table}` WHERE Key_name=\'PRIMARY\'')
                keys=[r['Column_name'] for r in sorted(sq.fetchall(),key=lambda x:x['Seq_in_index'])]
                if not keys:raise ValueError('Source table lacks a primary key: '+table)
                dq.execute(f'SHOW COLUMNS FROM `{table}`');columns=[r['Field'] for r in dq.fetchall()]
                sq.execute(f'SELECT COUNT(*) n FROM `{table}`');total=sq.fetchone()['n']
                dq.execute('SELECT * FROM migration_progress WHERE source_sha256=%s AND table_name=%s',(self.identity,table));progress=dq.fetchone()
                if progress and progress['status']=='verified':
                    if json.loads(progress['last_key'])['source_rows']!=total:
                        raise ValueError('Source row count changed: '+table)
                    return
                if table in TRANSIENT:
                    dq.execute("INSERT INTO migration_progress(source_sha256,table_name,rows_copied,last_key,status) VALUES(%s,%s,0,%s,'verified') ON DUPLICATE KEY UPDATE status='verified'",(self.identity,table,json.dumps({'source_rows':total,'excluded':'live session/limiter/waiter state; current sessions preserved at activation'})))
                    self.say(table,0,total,'transient excluded');return
                state=json.loads(progress['last_key']) if progress and progress['last_key'] else {'key':None,'chain':ZERO,'source_rows':total}
                copied=progress['rows_copied'] if progress else 0
                if state['source_rows']!=total:raise ValueError('Source row count changed: '+table)
                order=','.join('`'+k+'`' for k in keys)
                insert='INSERT INTO `'+table+'` ('+','.join('`'+c+'`' for c in columns)+') VALUES ('+','.join(['%s']*len(columns))+')'
                while True:
                    if self.cancel.is_set():raise RuntimeError('Import cancelled after another table failed')
                    params=[];where=''
                    if state['key'] is not None:
                        predicate,params=after_predicate(keys,state['key']);where=' WHERE '+predicate
                    sq.execute(f'SELECT * FROM `{table}`'+where+' ORDER BY '+order+' LIMIT %s',(*params,self.batch_size));raw=sq.fetchall()
                    if not raw:break
                    needs_objects=table=='content_blobs' or any('raw_path' in r or 'file_path' in r for r in raw) or table in ('exports','marketdata_artifacts')
                    converted=list(self.pool.map(lambda r:self.transform(table,r,columns),raw)) if needs_objects else [self.transform(table,r,columns) for r in raw]
                    next_state={'key':[raw[-1][k] for k in keys],'chain':chain(state['chain'],digest_rows(converted,columns)),'source_rows':total}
                    dest.begin()
                    try:
                        dq.executemany(insert,[tuple(r[c] for c in columns) for r in converted])
                        dq.execute("INSERT INTO migration_progress(source_sha256,table_name,rows_copied,last_key,status) VALUES(%s,%s,%s,%s,'copying') ON DUPLICATE KEY UPDATE rows_copied=VALUES(rows_copied),last_key=VALUES(last_key),status='copying'",(self.identity,table,copied+len(raw),json.dumps(next_state)))
                        dest.commit()
                    except BaseException:dest.rollback();raise
                    copied+=len(raw);state=next_state
                    if copied%20000<self.batch_size or copied==total:self.say(table,copied,total,'copied')
                if copied!=total:raise ValueError('Source copy count mismatch: '+table)
                verify_key=None;verified=0;checksum=ZERO
                while True:
                    where='';params=[]
                    # The destination also contains this import's checkpoints.
                    if table=='migration_progress':where=' WHERE source_sha256<>%s';params=[self.identity]
                    if verify_key is not None:
                        predicate,extra=after_predicate(keys,verify_key);where+=(' AND ' if where else ' WHERE ')+predicate;params+=extra
                    dq.execute(f'SELECT * FROM `{table}`'+where+' ORDER BY '+order+' LIMIT %s',(*params,self.batch_size));batch=dq.fetchall()
                    if not batch:break
                    checksum=chain(checksum,digest_rows(batch,columns));verified+=len(batch);verify_key=[batch[-1][k] for k in keys]
                if verified!=total or checksum!=state['chain']:raise ValueError('Full target checksum mismatch: '+table)
                dq.execute("INSERT INTO migration_progress(source_sha256,table_name,rows_copied,last_key,status) VALUES(%s,%s,%s,%s,'verified') ON DUPLICATE KEY UPDATE status='verified'",(self.identity,table,copied,json.dumps(state)))
                self.say(table,copied,total,'verified')
        except BaseException as exc:
            with self.print_lock:
                if self.first_error is None:self.first_error=exc
            self.cancel.set()
            raise
        finally:source.close();dest.close()

    def say(self,table,copied,total,status):
        with self.print_lock:print(json.dumps({'table':table,'rows':copied,'source_rows':total,'status':status}),flush=True)

    def run(self):
        self.prepare()
        try:
            # Tables have no enforced foreign keys. Their independent transactions
            # and checkpoints can overlap; references are checked before activation.
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
                futures=[workers.submit(self.copy_table,t) for t in self.tables]
                try:
                    for future in concurrent.futures.as_completed(futures):future.result()
                except BaseException as exc:
                    self.cancel.set()
                    for future in futures:future.cancel()
                    raise (self.first_error or exc)
            dest=self.destination()
            with dest.cursor() as q:
                for table in ('listings','snapshots'):
                    q.execute(f'''SELECT COUNT(*) n FROM `{table}` s LEFT JOIN content_blobs b
                        ON b.digest=JSON_UNQUOTE(JSON_EXTRACT(s.payload,'$."__appmon_content_v1__".sha256'))
                        WHERE s.payload LIKE '{{"__appmon_content_v1__":%%' AND b.digest IS NULL''')
                    if q.fetchone()['n']:raise ValueError('Missing content references: '+table)
                q.execute("UPDATE migration_progress SET status='verified' WHERE source_sha256=%s AND table_name='__mysql_import__'",(self.identity,))
                q.execute('SELECT table_name,rows_copied,status,last_key FROM migration_progress WHERE source_sha256=%s',(self.identity,));report=q.fetchall()
            dest.close();(self.state/'verified.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            return report
        finally:self.pool.shutdown(wait=True)

    def activate(self):
        """Overlay current login state. Caller must stop all active API/workers.

        Current business records cause a hard stop instead of an implicit merge.
        The current database is never modified and remains a rollback source.
        """
        self.prepare()
        current=pymysql.connect(**(self.target_options|{'database':self.active}))
        dest=self.destination()
        allowed={'advisory_mutexes','schedules','collection_controls','collection_logs','alert_cursors',
                 'auth_settings','auth_users','auth_sessions','auth_roles','auth_limits',
                 'auth_audit','auth_audit_heads','auth_user_groups','auth_overrides'}
        try:
            with current.cursor() as cq,dest.cursor() as q:
                q.execute('SELECT table_name FROM migration_progress WHERE source_sha256=%s AND status<>%s',(self.identity,'verified'))
                if q.fetchall():raise ValueError('Import verification is not complete')
                for table in set(TABLES)-allowed:
                    cq.execute(f'SELECT COUNT(*) n FROM `{table}`')
                    if cq.fetchone()['n']:raise ValueError('Current database has new business data; refusing to overwrite: '+table)
                live={}
                for table in allowed:
                    cq.execute(f'SELECT * FROM `{table}`')
                    live[table]=sorted(cq.fetchall(),key=lambda row:json.dumps(row,sort_keys=True,default=json_default))
                signature=hashlib.sha256(json.dumps(live,sort_keys=True,default=json_default).encode()).hexdigest()
                q.execute("SELECT last_key FROM migration_progress WHERE source_sha256=%s AND table_name='__overlay__'",(self.identity,));previous=q.fetchone()
                if previous:
                    saved=json.loads(previous['last_key'])
                    if saved['current_digest']!=signature:raise ValueError('Current login state changed after overlay')
                    return saved
                result={'current_database':self.active,'target_database':self.target,'current_digest':signature,'users':{},'sessions':len(live['auth_sessions'])}
                dest.begin()
                def insert(table,row,update=False):
                    cols=list(row);sql=f'INSERT INTO `{table}` ('+','.join('`'+c+'`' for c in cols)+') VALUES ('+','.join(['%s']*len(cols))+')'
                    if update:sql+=' ON DUPLICATE KEY UPDATE '+','.join('`'+c+'`=VALUES(`'+c+'`)' for c in cols)
                    q.execute(sql,tuple(row[c] for c in cols))
                q.execute('SELECT COALESCE(MAX(id),0) n FROM auth_users');next_id=q.fetchone()['n']
                for user in live['auth_users']:
                    q.execute('SELECT id,created_at FROM auth_users WHERE username=%s',(user['username'],));old=q.fetchone()
                    row=dict(user)
                    if old:row.update(id=old['id'],created_at=old['created_at'])
                    else:next_id+=1;row['id']=next_id
                    result['users'][str(user['id'])]=row['id'];insert('auth_users',row,True)
                def mapped(value):return result['users'].get(str(value),value)
                for role in live['auth_roles']:
                    q.execute('SELECT role FROM auth_roles WHERE role=%s',(role['role'],))
                    if q.fetchone() is None:insert('auth_roles',role)
                for table in ('auth_sessions','auth_limits'):
                    for row in live[table]:
                        if 'user_id' in row:row=row|{'user_id':mapped(row['user_id'])}
                        insert(table,row,True)
                for table in ('auth_overrides','auth_user_groups'):
                    for row in live[table]:insert(table,row|{'user_id':mapped(row['user_id'])},True)
                for table in ('auth_audit','collection_logs'):
                    q.execute(f'SELECT COALESCE(MAX(id),0) n FROM `{table}`');offset=q.fetchone()['n']
                    result[table+'_offset']=offset;result[table+'_added']=len(live[table])
                    for row in live[table]:
                        row=row|{'id':offset+row['id']}
                        for field in ('actor_id','target_id'):
                            if field in row and row[field] is not None:row[field]=mapped(row[field])
                        insert(table,row)
                for row in live['auth_audit_heads']:
                    insert('auth_audit_heads',row|{'actor_key':mapped(row['actor_key']),'last_id':row['last_id']+result['auth_audit_offset'] if row['last_id'] is not None else None},True)
                # Do not replay in-flight leases or resurrect old browser sessions.
                # Imported collection pause/settings remain exactly as in the source.
                q.execute("INSERT INTO migration_progress(source_sha256,table_name,last_key,status) VALUES(%s,'__overlay__',%s,'verified')",(self.identity,json.dumps(result)))
                dest.commit()
                (self.state/'activation.json').write_text(json.dumps(result,indent=2),encoding='utf8')
                return result
        except BaseException:dest.rollback();raise
        finally:current.close();dest.close();self.pool.shutdown(wait=True)
