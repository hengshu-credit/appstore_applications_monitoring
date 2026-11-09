import csv,json,lzma,time
from unittest.mock import patch
from appmon import bulk_baseline,initial_capacity,source_registry
from appmon.db import add_apps
from test_monitor import OceanBaseTestCase


class BulkBaselineTest(OceanBaseTestCase):
    def write_file(self):
        root=self.cfg['data_dir']/'raw';root.mkdir()
        path=root/'live_store_apps.tsv.xz'
        with lzma.open(path,'wt',encoding='utf8',newline='') as stream:
            writer=csv.writer(stream,delimiter='\t');writer.writerow(['store','store_id','app_name','developer_id','appgoblin_updated_at','future_source_field'])
            for i in range(3):writer.writerow(['Apple',str(100+i),'Historical '+str(i),'developer-'+str(i),'2025-11-13','preserved'])
        return path

    def test_checkpoint_resume_preserves_all_fields_and_never_marks_store_tasks_complete(self):
        self.write_file();receipt=bulk_baseline.queue(self.db,self.cfg,'live')
        self.db.execute("INSERT INTO listings(store,app_id,country,language,state,checked_at,last_success,payload) VALUES('apple','100','us','en','available',99,99,%s)",(json.dumps({'name':'Current'}),))
        with patch('appmon.bulk_baseline.disk_check',side_effect=[None,OSError('Insufficient free space')]):
            with self.assertRaises(OSError):bulk_baseline.run(self.db,self.cfg,chunk_size=2)
        row=self.db.execute('SELECT * FROM source_bulk_imports').fetchone()
        self.assertEqual((row['processed'],row['valid'],row['status']),(2,2,'paused_disk'))
        bulk_baseline.queue(self.db,self.cfg,'live')
        result=bulk_baseline.run(self.db,self.cfg,chunk_size=2)
        self.assertEqual((result['processed'],result['valid'],result['status']),(3,3,'complete'))
        row=bulk_baseline.app(self.db,'apple','100')['items'][0]
        self.assertEqual(row['payload']['future_source_field'],'preserved')
        self.assertLess(row['source_observed_at'],time.time()-86400)
        official=self.db.execute('SELECT payload,last_success FROM listings').fetchone()
        self.assertEqual((json.loads(official['payload'])['name'],official['last_success']),('Current',99))
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM requests').fetchone()['n'],0)
        bulk_baseline.queue(self.db,self.cfg,'live')
        self.assertEqual(bulk_baseline.run(self.db,self.cfg)['processed'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM public_app_baselines').fetchone()['n'],3)

    def test_24h_assessment_never_counts_partial_baselines_as_full_scope(self):
        add_apps(self.db,[('apple',str(i),None,None) for i in range(1000,1201)]+[('google','com.example.app'+str(i),None,None) for i in range(101)],'fixture')
        source_registry.save(self.db,self.cfg,'apple_lookup',{'batch_size':200});source_registry.save(self.db,self.cfg,'google_play',{'batch_size':100})
        result=initial_capacity.report(self.db,self.cfg)
        self.assertEqual(result['detail_requests_lower_bound'],4)
        self.assertEqual(result['target_hours'],24)
        self.assertFalse(result['full_scope_24h_ready'])

    def test_hosted_description_headers_and_case_sensitive_identities_are_preserved(self):
        root=self.cfg['data_dir']/'raw';root.mkdir()
        with lzma.open(root/'appgoblin_descriptions_2026_03_19.tsv.xz','wt',encoding='utf8',newline='') as stream:
            writer=csv.writer(stream,delimiter='\t');writer.writerow(['appstore','store_id','description','description_last_updated'])
            writer.writerow(['Android','com.Example','Upper\nmultiline','2026-03-19']);writer.writerow(['Android','com.example','Lower','2026-03-19'])
        bulk_baseline.queue(self.db,self.cfg,'descriptions');bulk_baseline.run(self.db,self.cfg)
        self.assertEqual(bulk_baseline.app(self.db,'google','com.Example')['items'][0]['payload']['description'],'Upper\nmultiline')
        self.assertEqual(bulk_baseline.app(self.db,'google','com.example')['items'][0]['payload']['description'],'Lower')

    def test_pause_keeps_committed_checkpoint_and_resume_finishes_remaining_rows(self):
        self.write_file();receipt=bulk_baseline.queue(self.db,self.cfg,'live');checks=[]
        def disk(*args):
            checks.append(True)
            if len(checks)==2:bulk_baseline.control(self.db,receipt['digest'],'pause')
        with patch('appmon.bulk_baseline.disk_check',side_effect=disk):result=bulk_baseline.run(self.db,self.cfg,chunk_size=2)
        self.assertEqual((result['status'],result['processed']),('paused_user',2))
        self.assertEqual(bulk_baseline.run(self.db,self.cfg)['processed'],0)
        bulk_baseline.control(self.db,receipt['digest'],'resume')
        self.assertEqual(bulk_baseline.run(self.db,self.cfg)['valid'],3)
