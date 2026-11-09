import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import requests
from appmon.stores import Client,FetchError,CollectionDeferred
from appmon import collection_limits,app_icons
from appmon.db import add_apps,enqueue,claim_details
from appmon.keywords import add_targets,work_keywords
from appmon.keyword_stores import KeywordClient
from appmon.target_batches import claim_targets
from test_monitor import OceanBaseTestCase


class JpegRecoveryTest(unittest.TestCase):
    def test_complete_jpeg_accepts_cdn_trailer_but_rejects_partial_image(self):
        # Minimal segment/scan envelope, with a stuffed byte and a restart marker.
        raw=bytes.fromhex('ffd8 ffc0 000b 08 0001 0001 01 011100 ffda 0008 01 0100 003f00')+b'abc\xff\x00z\xff\xd0data\xff\xd9'
        self.assertEqual(app_icons.media_type(raw+b'cdn-metadata-and-checksum'),('image/jpeg','jpg'))
        for bad in (raw[:-2],b'\xff\xd8\xff\xd9',b'\xff\xd8\xff\xe1\x00\x08\xff\xd9xxxx'):
            with self.assertRaises(FetchError):app_icons.media_type(bad)


class CollectionRecoveryTest(OceanBaseTestCase):
    def test_admission_wait_requeues_without_new_failed_observation(self):
        identifier=add_targets(self.db,[dict(store='google',country='us',language='en',keyword='budget',depth=100)])['ids'][0]
        client=KeywordClient(self.cfg,self.db)
        with patch.object(client,'search_many',side_effect=CollectionDeferred('等待共享限速窗口，稍后重试',120)):
            result=work_keywords(self.cfg,self.db,20,client)
        self.assertEqual((result['deferred'],result['error']),(1,0))
        row=self.db.execute('SELECT * FROM keyword_targets WHERE id=%s',(identifier,)).fetchone()
        self.assertEqual(row['failures'],0);self.assertIsNone(row['token']);self.assertGreater(row['due'],time.time()+110)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_runs').fetchone()['n'],0)
        client.session.close()

    def test_due_retry_batch_is_not_starved_by_zero_due_new_jobs(self):
        add_apps(self.db,[('apple',str(i),None,None) for i in range(1000,1010)],'test');enqueue(self.db,['us'],'en')
        self.db.execute("UPDATE jobs SET failures=2,last_error='SSLError',due=%s WHERE app_id IN ('1008','1009')",(time.time()-1,))
        self.db.execute("INSERT INTO schedules VALUES('claim:details',4)")
        rows=claim_details(self.db,['us'],'en',600,limit=5)
        self.assertEqual(len(rows),5);self.assertEqual({r['app_id'] for r in rows if r['failures']},{'1008','1009'})
        self.assertEqual(len({r['app_id'] for r in rows}),5)

    def test_due_scheduled_work_gets_a_turn_before_new_backlog(self):
        ids=add_targets(self.db,[dict(store='google',country='us',language='en',keyword=str(i)) for i in range(6)])['ids']
        self.db.execute('UPDATE keyword_targets SET due=%s WHERE id=%s',(time.time()-1,ids[-1]))
        self.db.execute("INSERT INTO schedules VALUES('claim:keyword_targets',3)")
        rows=claim_targets(self.db,'keyword_targets',600,3)
        self.assertEqual(rows[0]['id'],ids[-1]);self.assertEqual(len(rows),3)

    def test_long_cooldown_stops_leasing_but_does_not_block_local_file_intake(self):
        from appmon import dispatcher,source_tasks
        spec=dict(store='apple',country='us',language='en',genre='FINANCE',period='weekly',start='2025-01-05',limit=20)
        response={'result':{'rows':[dict(week='2025-01-05',countryOrRegion='US',genre='FINANCE',searchTerm='budget',rankInGenre=3,searchPopularity1to100=88)]}}
        source_tasks.enqueue(self.db,'aso_import',spec|{'response':response})
        self.db.execute("INSERT INTO schedules VALUES('rate:global',%s)",(time.time()+300,))
        self.assertTrue(collection_limits.blocked(self.db,self.cfg))
        with patch('requests.Session.request') as remote:
            result=dispatcher.dispatch_once(self.db,self.cfg,'local-file-test')
        self.assertEqual(result['ok'],1);remote.assert_not_called()

    def test_failed_permission_requests_can_batch_without_pretending_success(self):
        from appmon.app_permissions import enqueue as queue_permissions
        queue_permissions(self.db,[dict(store='google',app_id=f'com.example.app{i}',country='us') for i in range(3)])
        self.db.execute("UPDATE app_permission_targets SET failures=2,last_error='Google RPC returned no payload'")
        rows=claim_targets(self.db,'app_permission_targets',600,20)
        self.assertEqual(len(rows),3);self.assertTrue(all(r['failures']==2 for r in rows))

    def test_transient_tls_retries_one_verified_connection_and_records_both_attempts(self):
        client=Client(self.cfg,self.db)
        response=SimpleNamespace(status_code=200)
        with patch.object(collection_limits,'admission',return_value=0),patch.object(client.session,'close') as close,patch.object(client.session,'get',side_effect=[requests.exceptions.SSLError('connection EOF'),response]) as get:
            self.assertIs(client.get('apple','https://itunes.apple.com/lookup',{'id':'123'}),response)
        self.assertEqual(get.call_count,2);close.assert_called_once()
        self.assertTrue(all('verify' not in c.kwargs for c in get.call_args_list))
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM collection_logs WHERE resource='http'").fetchone()['n'],2)
        with patch.object(collection_limits,'admission',return_value=0),patch.object(client.session,'get',side_effect=requests.exceptions.SSLError('CERTIFICATE_VERIFY_FAILED')) as bad:
            with self.assertRaises(FetchError):client.get('apple','https://itunes.apple.com/lookup')
        bad.assert_called_once();client.session.close()
