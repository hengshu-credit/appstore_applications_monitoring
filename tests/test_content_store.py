import hashlib
import json
import zstandard
from unittest.mock import patch

from appmon import content_compaction, content_store, collection_limits, scoped_views
from appmon.db import add_apps, connect
from test_monitor import OceanBaseTestCase


class ContentStoreTest(OceanBaseTestCase):
    def seed(self):
        add_apps(self.db, [('apple','1000','样本',None)], 'fixture')
        bodies = [json.dumps({'name':'样本','developer':'完整开发者','developer_id':123,'rating':4.8,
            'ratings':100,'price':0,'category':'Finance','version':str(i),'languages':['en','zh'],
            'description':'原始正文 ไทย\n'*200,'unknown_future_field':{'nested':[None,False,12345678901234567890]},
            'store_fields':{'all_fields':['preserved']*100}}, ensure_ascii=False, indent=2) for i in range(2)]
        for i, (country, version) in enumerate((('us',0),('us',1),('cn',1))):
            text=bodies[version];digest=hashlib.sha256(text.encode()).hexdigest()
            self.db.execute('''INSERT INTO snapshots(store,app_id,country,language,observed_at,digest,raw_path,payload)
                VALUES('apple','1000',%s,'en',%s,%s,%s,%s)''',(country,100+i,digest,'responses/evidence-'+str(i),text))
        for country in ('us','cn'):
            text=bodies[1];digest=hashlib.sha256(text.encode()).hexdigest()
            self.db.execute("INSERT INTO listings VALUES('apple','1000',%s,'en','available',102,102,0,%s,%s)",(country,text,digest))
        return bodies

    def test_resume_full_history_manifest_projection_and_streamed_export(self):
        bodies=self.seed()
        before={t:self.db.execute('SELECT * FROM '+t+' ORDER BY '+','.join(keys)).fetchall() for t,keys in content_compaction.TABLE_KEYS.items()}
        collection_limits.save(self.db,self.cfg,{'paused':True})
        state=content_compaction.compact(self.db,self.cfg,'snapshots',batch_size=1,max_batches=1)
        self.assertEqual(state['processed'],1)
        with self.assertRaises(ValueError):content_compaction.verify(self.db,self.cfg,'snapshots')
        for table in ('snapshots','listings'):
            content_compaction.compact(self.db,self.cfg,table,batch_size=2)
            self.assertTrue(content_compaction.verify(self.db,self.cfg,table)['verified'])
            keys=content_compaction.TABLE_KEYS[table]
            self.assertEqual(self.db.execute('SELECT * FROM '+table+' ORDER BY '+','.join(keys)).fetchall(),before[table])
            self.assertEqual(list(self.db.iter_rows('SELECT * FROM '+table+' ORDER BY '+','.join(keys))),before[table])
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM content_blobs').fetchone()['n'],2)
        physical=list(self.db.iter_rows('SELECT payload FROM snapshots',resolve_content=False))
        self.assertTrue(all(content_store.marker(r['payload']) for r in physical))
        projected=self.db.execute("SELECT JSON_EXTRACT(payload,'$.rating') rating,JSON_EXTRACT(payload,'$.languages') languages FROM listings WHERE country='us'").fetchone()
        self.assertEqual(float(projected['rating']),4.8);self.assertEqual(json.loads(projected['languages']),['en','zh'])
        page=scoped_views.apps(self.db,store='apple',country='us',language='en',min_rating=4,developer='完整开发者',pricing='free')
        self.assertEqual(page['items'][0]['payload'],json.loads(bodies[1]))
        self.assertLess(sum(len(r['payload']) for r in physical),sum(len(r['payload']) for r in before['snapshots'])/10)
        self.db.execute("UPDATE snapshots SET observed_at=999 ORDER BY id LIMIT 1")
        with self.assertRaisesRegex(ValueError,'manifest mismatch'):content_compaction.verify(self.db,self.cfg,'snapshots')

    def test_transaction_rollback_and_corruption_fail_closed(self):
        text=json.dumps({'description':'data'*1000,'unknown':[None,True,'中文']})
        with self.assertRaises(RuntimeError):
            with self.db:
                content_store.put(self.db,text)
                raise RuntimeError('rollback')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM content_blobs').fetchone()['n'],0)
        with self.db:
            ref=content_store.put(self.db,text)
        info=content_store.marker(ref)
        self.assertEqual(content_store.load_many(self.db.connection,{info['sha256']:info['bytes']},cache=False)[info['sha256']],text)
        from appmon.objects import store
        objects=store(self.db.database)
        row=self.db.execute('SELECT object_key FROM content_blobs').fetchone()
        objects.client.put_object(Bucket=objects.bucket,Key=objects.key(row['object_key']),Body=b'broken',Metadata={'raw-bytes':str(info['bytes']),'sha256':info['sha256'],'codec':'identity'})
        with self.assertRaises((ValueError,zstandard.ZstdError)):content_store.load_many(self.db.connection,{info['sha256']:info['bytes']},cache=False)
        self.db.execute('DELETE FROM content_blobs')
        with self.assertRaisesRegex(ValueError,'Missing'):content_store.load_many(self.db.connection,{info['sha256']:info['bytes']},cache=False)

    def test_batch_reads_share_one_blob_query_and_preserve_outer_cursor(self):
        with self.db:
            ref=content_store.put(self.db,json.dumps({'description':'x'*10000}))
        connection=self.db.connection
        execute=content_store.pymysql.cursors.DictCursor.execute
        calls=[]
        def counted(cursor,query,args=None):
            calls.append(query)
            return execute(cursor,query,args)
        with patch.object(content_store.pymysql.cursors.DictCursor,'execute',counted):
            cursor=self.db.execute('SELECT %s payload UNION ALL SELECT %s payload',(ref,ref))
            rows=cursor.fetchall()
            self.assertEqual(cursor.rowcount,2)
        self.assertEqual(len(rows),2)
        self.assertEqual(len([q for q in calls if 'FROM content_blobs' in q]),1)
        self.assertEqual(rows[0]['payload'],rows[1]['payload'])
