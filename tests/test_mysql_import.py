import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zlib
from decimal import Decimal
import concurrent.futures
from unittest.mock import patch
import pymysql
from appmon.mysql_import import Importer,decode_legacy_blob,digest_rows,chain,ZERO
from test_monitor import OceanBaseTestCase


class MySQLImportUnitTest(unittest.TestCase):
    def test_legacy_blob_requires_exact_original_hash_length_and_complete_stream(self):
        raw='完整正文 ไทย 🐈'.encode()
        row={'codec':'zlib6','raw_bytes':len(raw),'body':zlib.compress(raw),'digest':hashlib.sha256(raw).hexdigest()}
        self.assertEqual(decode_legacy_blob(row),raw)
        for change in ({'digest':'0'*64},{'raw_bytes':len(raw)-1},{'body':row['body']+b'extra'},{'codec':'unknown'}):
            with self.assertRaises(ValueError):decode_legacy_blob(row|change)

    def test_row_hashes_preserve_unicode_binary_decimal_and_batch_order(self):
        row={'text':'ไทย','decimal':Decimal('1.230000'),'binary':b'\0\xff','null':None}
        columns=list(row);digest=digest_rows([row],columns)
        self.assertEqual(digest,digest_rows([dict(reversed(list(row.items())))],columns))
        self.assertNotEqual(digest,digest_rows([row|{'decimal':Decimal('1.230001')}],columns))
        first=chain(ZERO,digest);self.assertNotEqual(first,chain(first,digest))

    def test_transform_preserves_history_and_resets_only_inflight_leases(self):
        job=Importer.__new__(Importer)
        row={'id':7,'token':'old-worker','lease_until':999,'due':123,'failures':2,'observed_at':42}
        mapped=job.transform('jobs',row,list(row))
        self.assertEqual(mapped,row|{'token':None,'lease_until':0})
        self.assertIsInstance(mapped['lease_until'],float)
        self.assertEqual(row['token'],'old-worker')
        plan={'id':2,'specification':json.dumps({'input_source':'crm','request_id':'a'}),'status':'running'}
        mapped=job.transform('collection_plans',plan,list(plan)+['request_source','request_id'])
        self.assertEqual(mapped['request_source'],'crm');self.assertEqual(mapped['request_id'],'a')
        self.assertEqual(mapped['status'],'queued')

    def test_asset_paths_cannot_escape_source_or_reuse_changed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            job=Importer.__new__(Importer);job.root=(Path(folder)/'source').resolve();job.root.mkdir();job.state=Path(folder)
            job.file_lock=threading.Lock();job.files={}
            with self.assertRaises(ValueError):job.file('../outside','media')
            with self.assertRaises(ValueError):job.file('secrets/private.key','media')
            with self.assertRaises(FileNotFoundError):job.file('icons/missing.png','media')
            path=job.root/'icons/image.png';path.parent.mkdir();path.write_bytes(b'original')
            job.files['icons/image.png']={'size':path.stat().st_size,'mtime':path.stat().st_mtime_ns,'ref':'media/aa/ref.png'}
            self.assertEqual(job.file('icons/image.png','media'),'media/aa/ref.png')
            path.write_bytes(b'changed-content')
            with self.assertRaises(ValueError):job.file('icons/image.png','media')


class MemorySource:
    """Read-only source cursor; the destination below is real OceanBase."""
    def __init__(self,rows):self.rows=rows;self.result=[]
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def close(self):pass
    def execute(self,sql,args=()):
        if sql.startswith('SHOW KEYS'):
            self.result=[{'Column_name':name,'Seq_in_index':i+1} for i,name in enumerate(['store','app_id'])]
        elif sql.startswith('SELECT COUNT'):self.result=[{'n':len(self.rows)}]
        elif sql.startswith('SELECT *'):
            after=tuple(args[-3:-1]) if len(args)>1 else ();self.result=[r for r in self.rows if not after or (r['store'],r['app_id'])>after][:args[-1]]
        else:raise AssertionError('Unexpected source mutation/query: '+sql)
    def fetchone(self):return self.result[0] if self.result else None
    def fetchall(self):return self.result


class MySQLImportCheckpointTest(OceanBaseTestCase):
    def job(self):
        job=Importer.__new__(Importer);job.identity='d'*64;job.batch_size=1;job.cancel=threading.Event();job.first_error=None;job.print_lock=threading.Lock()
        job.pool=concurrent.futures.ThreadPoolExecutor(max_workers=2);self.addCleanup(job.pool.shutdown)
        self.rows=[dict(store='apple',app_id=str(i),name='测试 '+str(i),category=None,first_seen=0.0,last_seen=2.5,source='fixture') for i in range(3)]
        job.source=lambda:MemorySource(self.rows)
        job.destination=lambda:pymysql.connect(**self.options,charset='utf8mb4',autocommit=True,cursorclass=pymysql.cursors.DictCursor)
        job.say=lambda *args:None
        return job

    def interrupted(self,job):
        original=pymysql.cursors.DictCursor.executemany;calls=0
        def write(cursor,sql,values):
            nonlocal calls
            if sql.startswith('INSERT INTO `apps`'):
                calls+=1
                if calls==2:raise RuntimeError('simulated interruption')
            return original(cursor,sql,values)
        with patch.object(pymysql.cursors.DictCursor,'executemany',write):
            with self.assertRaisesRegex(RuntimeError,'simulated interruption'):job.copy_table('apps')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM apps').fetchone()['n'],1)
        self.assertEqual(self.db.execute('SELECT rows_copied FROM migration_progress WHERE table_name=%s',('apps',)).fetchone()['rows_copied'],1)

    def test_resume_keeps_rows_and_checkpoint_atomic_then_verifies_every_field(self):
        job=self.job();self.interrupted(job);job.cancel.clear();job.copy_table('apps')
        self.assertEqual(self.db.execute('SELECT * FROM apps ORDER BY store,app_id').fetchall(),self.rows)
        row=self.db.execute('SELECT status,rows_copied FROM migration_progress WHERE table_name=%s',('apps',)).fetchone()
        self.assertEqual(row,{'status':'verified','rows_copied':3})
        job.copy_table('apps');self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM apps').fetchone()['n'],3)

    def test_resume_refuses_a_changed_source(self):
        job=self.job();self.interrupted(job);job.cancel.clear()
        self.rows.append(self.rows[-1]|{'app_id':'9'})
        with self.assertRaisesRegex(ValueError,'Source row count changed'):job.copy_table('apps')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM apps').fetchone()['n'],1)
