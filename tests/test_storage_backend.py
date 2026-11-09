import concurrent.futures
import hashlib
import json
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

import requests
from unittest.mock import patch
from fastapi.testclient import TestClient
from botocore.exceptions import ClientError
from appmon.web import create_app

from appmon.db import connect
from appmon.keywords import claim_target
from appmon.objects import for_config
from test_monitor import OceanBaseTestCase


class StorageBackendTest(OceanBaseTestCase):
    def test_storage_outage_returns_retryable_error_without_provider_secrets(self):
        objects=for_config(self.cfg)
        ref=objects.put(b'image-fixture',kind='media',extension='png',mime='image/png')
        identifier=self.db.execute("INSERT INTO app_icons(store,app_id,country,language,file_path,mime) VALUES('apple','123','us','en',%s,'image/png')",(ref,)).lastrowid
        client=self.login_admin(TestClient(create_app(self.cfg)))
        failure=ClientError({'Error':{'Code':'InternalError','Message':'secret-testmarker'}},'GetObject')
        with patch.object(objects,'get',side_effect=failure):
            response=client.get('/api/icons/'+str(identifier))
        self.assertEqual(response.status_code,503)
        self.assertNotIn('secret-testmarker',response.text)

    def test_lossless_objects_deduplicate_reject_corruption_and_bound_reads(self):
        objects=for_config(self.cfg)
        raw=('完整正文 ไทย 🐈\n'*4000).encode()
        ref=objects.put(raw,compress=True)
        self.assertEqual(objects.put(raw,compress=True),ref)
        self.assertEqual(objects.get(ref),raw)
        self.assertEqual(objects.verify(ref),len(raw))
        self.assertEqual(list(objects.inventory()),[ref])
        with self.assertRaises(ValueError):objects.get(ref,max_bytes=10)
        with self.assertRaises(ValueError):objects.get('../secret')
        objects.client.put_object(Bucket=objects.bucket,Key=objects.key(ref),Body=b'bad',Metadata={
            'raw-bytes':'3','codec':'identity','sha256':hashlib.sha256(raw).hexdigest()})
        with self.assertRaisesRegex(ValueError,'checksum'):objects.get(ref)

    def test_private_download_signature_and_range_preserve_original_bytes(self):
        objects=for_config(self.cfg);raw=bytes(range(256))*1000
        ref=objects.put(raw,kind='media',extension='mp4',mime='video/mp4')
        url=objects.url(ref)
        response=requests.get(url,headers={'Range':'bytes=100-999'},timeout=10)
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,raw[100:1000])
        parts=urlsplit(url)
        anonymous=requests.get(urlunsplit((parts.scheme,parts.netloc,parts.path,'','')),timeout=10)
        self.assertEqual(anonymous.status_code,403)
        query=urlencode([(k,'0'*64 if k=='X-Amz-Signature' else v) for k,v in parse_qsl(parts.query)])
        self.assertEqual(requests.get(urlunsplit((parts.scheme,parts.netloc,parts.path,query,'')),timeout=10).status_code,403)

    def test_queue_paginates_past_more_than_one_page_of_locked_rows(self):
        self.db.executemany("INSERT INTO keyword_targets(store,country,language,keyword,depth,refresh_hours) VALUES('google','us','en',%s,50,24)",[(str(i),) for i in range(100)])
        ids=[r['id'] for r in self.db.execute('SELECT id FROM keyword_targets ORDER BY id').fetchall()]
        other=connect(self.cfg,initialize=False)
        try:
            self.db.begin()
            self.db.execute('SELECT id FROM keyword_targets WHERE id IN ('+','.join(['%s']*70)+') FOR UPDATE',ids[:70]).fetchall()
            claimed=claim_target(other,60,100)
            self.assertEqual(claimed['id'],ids[70])
        finally:self.db.rollback();other.close()

    def test_concurrent_real_claims_never_duplicate_or_skip_unlocked_work(self):
        self.db.executemany("INSERT INTO keyword_targets(store,country,language,keyword,depth,refresh_hours) VALUES('google','us','en',%s,50,24)",[(str(i),) for i in range(100)])
        def worker(_):
            db=connect(self.cfg,initialize=False);ids=[]
            try:
                while row:=claim_target(db,60,100):ids.append(row['id'])
                return ids
            finally:db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids=[n for group in pool.map(worker,range(8)) for n in group]
        self.assertEqual(len(ids),100)
        self.assertEqual(len(set(ids)),100)
