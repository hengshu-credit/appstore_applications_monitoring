"""Portable, streamed OceanBase + object snapshots. Restore only into an empty database."""
import hashlib
import base64
import json
import os
import shutil
import time
import zipfile
from decimal import Decimal
from pathlib import Path, PurePosixPath
from .db import Database, TABLES, ENGINE, create_table_sql
from .content_store import TABLES as CONTENT_TABLES

ASSETS = ('exports', 'raw', 'secrets')


def json_value(value):
    if isinstance(value, bytes):
        return {'$appmon_binary_v1': base64.b64encode(value).decode('ascii')}
    if isinstance(value, Decimal):
        return str(value)  # OceanBase restores the exact decimal, without float rounding.
    raise TypeError(f'Unsupported database type: {type(value).__name__}')


def digest(stream):
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024*1024), b''):
        h.update(chunk)
    return h.hexdigest()


def backup(cfg, destination, config_path):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('备份文件已存在，拒绝覆盖')
    temp = destination.with_suffix(destination.suffix+'.part')
    if temp.exists():
        raise ValueError('存在未完成备份，请使用新的输出路径')
    manifest = dict(format=4, created_at=time.time(), tables={}, files={})
    db = Database(cfg['oceanbase'])
    try:
        db.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        db.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
        actual = {next(iter(r.values())) for r in db.execute('SHOW TABLES').fetchall()}
        if actual != set(TABLES):
            raise ValueError('数据库表结构与当前程序不一致，请使用匹配版本备份')
        if db.execute("SELECT 1 FROM migration_progress WHERE status!='verified' LIMIT 1").fetchone():
            raise ValueError('迁移尚未完成，不能备份')
        with zipfile.ZipFile(temp, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for table in TABLES:
                columns = [r['Field'] for r in db.execute(f'SHOW COLUMNS FROM `{table}`').fetchall()]
                name = f'tables/{table}.jsonl'
                h, count = hashlib.sha256(), 0
                with archive.open(name,'w',force_zip64=True) as out:
                    for row in db.iter_rows(f'SELECT * FROM `{table}`', resolve_content=False):
                        line = (json.dumps([row[c] for c in columns],ensure_ascii=False,allow_nan=False,default=json_value)+'\n').encode()
                        out.write(line); h.update(line); count += 1
                manifest['tables'][table] = dict(columns=columns, rows=count)
                manifest['files'][name] = h.hexdigest()
                print(f'Backed up {table}: {count} rows', flush=True)
            db.commit()
            root = Path(cfg['data_dir']).resolve()
            files = [(Path(config_path), 'config.toml')]
            for folder in ASSETS:
                if (root/folder).is_symlink():
                    raise ValueError('备份目录不支持符号链接')
                for path in sorted((root/folder).rglob('*')):
                    if path.is_symlink():
                        raise ValueError('备份目录不支持符号链接')
                    if path.is_file() and path.suffix != '.part':
                        files.append((path, 'data/'+path.relative_to(root).as_posix()))
            for path, name in files:
                h = hashlib.sha256()
                with path.open('rb') as source, archive.open(name,'w',force_zip64=True) as out:
                    for chunk in iter(lambda: source.read(1024*1024), b''):
                        h.update(chunk); out.write(chunk)
                manifest['files'][name] = h.hexdigest()
            from .objects import for_config
            objects=for_config(cfg)
            for ref in objects.inventory():
                response=objects.client.get_object(Bucket=objects.bucket,Key=objects.key(ref))
                name='objects/'+ref
                h=hashlib.sha256()
                try:
                    with archive.open(name,'w',force_zip64=True) as out:
                        for chunk in response['Body'].iter_chunks(1024*1024):h.update(chunk);out.write(chunk)
                finally:response['Body'].close()
                manifest['files'][name]=h.hexdigest()
                meta_name='object-metadata/'+ref+'.json'
                meta=json.dumps({'Metadata':response['Metadata'],'ContentType':response['ContentType']}).encode()
                archive.writestr(meta_name,meta);manifest['files'][meta_name]=hashlib.sha256(meta).hexdigest()
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        os.replace(temp,destination)
        with destination.open('rb') as stream:
            checksum = digest(stream)
        destination.with_suffix(destination.suffix+'.sha256').write_text(checksum+'  '+destination.name+'\n',encoding='ascii')
        return dict(path=str(destination),sha256=checksum,rows=sum(t['rows'] for t in manifest['tables'].values()))
    finally:
        db.close()


def verify(archive):
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError('备份存在重复路径')
    manifest = json.loads(archive.read('manifest.json'))
    expected_tables = set(TABLES)
    if manifest.get('format') != 4 or set(manifest['tables']) != expected_tables:
        raise ValueError('备份版本或表清单不匹配，请使用当前 OceanBase/S3 备份格式')
    if {name for name in names if name.startswith('tables/')} != {f'tables/{table}.jsonl' for table in expected_tables}:
        raise ValueError('备份表文件不完整')
    if set(names) != set(manifest['files']) | {'manifest.json'}:
        raise ValueError('备份文件清单不匹配')
    for name, expected in manifest['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
            raise ValueError('备份包含非法路径')
        if not (name == 'config.toml' or name in {f'tables/{t}.jsonl' for t in TABLES}
                or name.startswith('objects/') or name.startswith('object-metadata/')
                or len(path.parts)>2 and path.parts[0]=='data' and path.parts[1] in ASSETS):
            raise ValueError('备份包含未知文件')
        with archive.open(name) as stream:
            if digest(stream) != expected:
                raise ValueError('备份校验失败: '+name)
    return manifest


def restore(cfg, source):
    root = Path(cfg['data_dir']).resolve()
    with zipfile.ZipFile(source) as archive:
        manifest = verify(archive)  # Check everything before touching the destination.
        if any((root/folder).exists() and any((root/folder).iterdir()) for folder in ASSETS):
            raise ValueError('目标数据目录非空，拒绝覆盖')
        from .objects import for_config
        objects=for_config(cfg)
        objects.initialize()
        if next(objects.inventory(),None) is not None:
            raise ValueError('目标对象空间非空，拒绝覆盖')
        db = Database(cfg['oceanbase'])
        marker = '0'*64
        try:
            existing = [next(iter(r.values())) for r in db.execute('SHOW TABLES').fetchall()]
            if existing:
                raise ValueError('目标数据库必须无表；禁止覆盖或合并已有数据库')
            db.execute(f'CREATE TABLE migration_progress ({TABLES["migration_progress"]}){ENGINE}')
            db.execute("INSERT INTO migration_progress(source_sha256,table_name,status) VALUES(%s,'offline-restore','copying')",(marker,))
            for table, schema in TABLES.items():
                if table != 'migration_progress':
                    db.execute(create_table_sql(table))
            for table, info in manifest['tables'].items():
                description = db.execute(f'SHOW COLUMNS FROM `{table}`').fetchall()
                columns = [r['Field'] for r in description]
                binary_columns = [i for i, r in enumerate(description) if 'blob' in r['Type'].lower() or 'binary' in r['Type'].lower()]
                if columns != info['columns']:
                    raise ValueError('表字段不匹配: '+table)
                sql = f'INSERT INTO `{table}` ('+','.join(f'`{c}`' for c in columns)+') VALUES ('+','.join(['%s']*len(columns))+')'
                count, batch, batch_bytes = 0, [], 0
                with archive.open(f'tables/{table}.jsonl') as stream:
                    for line in stream:
                        row = json.loads(line)
                        if not isinstance(row,list) or len(row)!=len(columns):
                            raise ValueError('备份行结构不匹配')
                        for index in binary_columns:
                            if row[index] is not None:
                                value = row[index]
                                if not isinstance(value, dict) or set(value) != {'$appmon_binary_v1'}:
                                    raise ValueError('备份二进制字段格式无效')
                                row[index] = base64.b64decode(value['$appmon_binary_v1'], validate=True)
                        batch.append(row); count += 1; batch_bytes += len(line)
                        if len(batch)>=1000 or batch_bytes>=4*1024*1024:
                            with db: db.executemany(sql,batch)
                            batch=[]; batch_bytes=0
                    if batch:
                        with db: db.executemany(sql,batch)
                actual = db.execute(f'SELECT COUNT(*) n FROM `{table}`').fetchone()['n']
                if table=='migration_progress': actual-=1
                if count != info['rows'] or actual!=count:
                    raise ValueError('恢复行数校验失败: '+table)
                print(f'Restored {table}: {count} rows verified',flush=True)
            for name in manifest['files']:
                if name.startswith('data/'):
                    target = root.joinpath(*PurePosixPath(name).parts[1:])
                    if not target.resolve().is_relative_to(root):
                        raise ValueError('目标路径越界')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with archive.open(name) as stream, target.open('xb') as out:
                        shutil.copyfileobj(stream,out)
            for name in manifest['files']:
                if name.startswith('objects/'):
                    ref=name[len('objects/'):]
                    meta=json.loads(archive.read('object-metadata/'+ref+'.json'))
                    with archive.open(name) as stream:
                        objects.client.upload_fileobj(stream,objects.bucket,objects.key(ref),ExtraArgs=meta)
                    # Validate the original hash after restoring, before releasing the runtime guard.
                    objects.verify(ref)
            # New host starts read-only with respect to store requests, until explicitly resumed.
            from .collection_limits import save
            save(db,cfg,{'paused':True})
            db.execute("DELETE FROM migration_progress WHERE source_sha256=%s AND table_name='offline-restore'",(marker,))
            return dict(restored=True,tables=len(TABLES),rows=sum(t['rows'] for t in manifest['tables'].values()),collection_paused=True)
        finally:
            db.close()
