"""Rename private source identifiers using an operator-supplied local manifest.

No supplier identifiers or endpoints are shipped in this migration utility.
Run with API/collector writers stopped; rows and encrypted secrets are preserved.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from appmon.config import load
from appmon.db import Database, TABLES, dump


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',value):
        raise ValueError('Invalid SQL identifier')
    return '`'+value+'`'


def migrate(cfg, manifest):
    db=Database(cfg['mysql']);root=Path(cfg['data_dir']).resolve()
    try:
        with db.named_lock('source-identifier-migration'):
            mapping=manifest.get('tables',{})
            tables={next(iter(r.values())) for r in db.execute('SHOW TABLES').fetchall()}
            changes=[];counts={}
            for old,new in mapping.items():
                identifier(old);identifier(new)
                if new not in TABLES:raise ValueError('Unknown destination table')
                if old in tables and new in tables:raise ValueError('Both source and destination table exist')
                current=old if old in tables else new
                counts[new]=db.execute('SELECT COUNT(*) n FROM '+identifier(current)).fetchone()['n']
                if old in tables:changes.append(identifier(old)+' TO '+identifier(new))
            before=root/'secrets'/'source-migration-before.json'
            if not before.exists():
                backup={}
                for table in list(mapping)+['source_accounts']:
                    if table in tables:backup[table]=db.execute('SELECT * FROM '+identifier(table)).fetchall()
                before.parent.mkdir(parents=True,exist_ok=True)
                before.write_text(dump(backup),encoding='utf-8')
            for old,new in manifest.get('asset_folders',{}).items():
                source=(root/old).resolve();target=(root/new).resolve()
                if not source.is_relative_to(root) or not target.is_relative_to(root) or source==root or target==root:
                    raise ValueError('Asset path escapes data directory')
                if source.exists() and target.exists():raise ValueError('Both asset directories exist')
            if changes:db.execute('RENAME TABLE '+','.join(changes))
            for old,new in manifest.get('asset_folders',{}).items():
                source=(root/old).resolve();target=(root/new).resolve()
                if source.exists():target.parent.mkdir(parents=True,exist_ok=True);source.rename(target)
            with db:
                for old,new in manifest.get('providers',{}).items():
                    if db.execute('SELECT 1 FROM source_accounts WHERE provider=%s',(old,)).fetchone():
                        if db.execute('SELECT 1 FROM source_accounts WHERE provider=%s',(new,)).fetchone():
                            raise ValueError('Destination account already configured')
                        db.execute('UPDATE source_accounts SET provider=%s WHERE provider=%s',(new,old))
                for old,new in manifest.get('asset_folders',{}).items():
                    db.execute('UPDATE marketdata_artifacts SET path=REPLACE(path,%s,%s)',(old+'/',new+'/'))
                for update in manifest.get('field_updates',[]):
                    table,column=identifier(update['table']),identifier(update['column'])
                    for old,new in update['values'].items():
                        db.execute('UPDATE '+table+' SET '+column+'=REPLACE('+column+',%s,%s) WHERE LOCATE(%s,'+column+')>0',(old,new,old))
                # Keep subsequent promotion/import idempotent after metadata changes.
                for row in db.execute('SELECT DISTINCT source_key,source,source_url,source_period FROM keyword_candidates').fetchall():
                    key=hashlib.sha256(dump([row['source'],row['source_url'],row['source_period']]).encode()).hexdigest()
                    if key!=row['source_key']:
                        db.execute('UPDATE keyword_candidates SET source_key=%s WHERE source_key=%s',(key,row['source_key']))
                for row in db.execute('SELECT id,provenance FROM marketdata_artifacts').fetchall():
                    value=json.loads(row['provenance'])
                    value.pop('url',None)
                    value.pop('source',None)
                    db.execute('UPDATE marketdata_artifacts SET provenance=%s WHERE id=%s',(dump(value),row['id']))
            for table,count in counts.items():
                if db.execute('SELECT COUNT(*) n FROM '+identifier(table)).fetchone()['n']!=count:
                    raise ValueError('Row count changed during migration')
            for row in db.execute('SELECT path,sha256 FROM marketdata_artifacts').fetchall():
                import gzip
                path=(root/row['path']).resolve()
                if not path.is_relative_to(root):raise ValueError('Invalid archive path')
                if hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest()!=row['sha256']:
                    raise ValueError('Archive checksum mismatch')
            return {'table_counts':counts,'accounts':db.execute('SELECT provider,status FROM source_accounts').fetchall(),
                    'archive_checksums_verified':True}
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest');parser.add_argument('--config',default='config.toml')
    args=parser.parse_args()
    print(json.dumps(migrate(load(args.config),json.loads(Path(args.manifest).read_text(encoding='utf-8'))),ensure_ascii=False,indent=2))
