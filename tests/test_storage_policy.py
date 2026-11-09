import json,time
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from appmon import app_groups,app_icons,app_media,storage_policy,collection_tasks
from appmon.db import add_apps,enqueue,claim
from appmon.monitor import save_result
from appmon.stores import Result,Client
from appmon.collection_limits import blocked,settings,admission
from test_monitor import OceanBaseTestCase
from test_experience import PNG


class StoragePolicyTest(OceanBaseTestCase):
    def setUp(self):
        super().setUp()
        self.cfg.update(raw_response_scope='groups',media_scope='groups_and_current_charts')
        self.m=dict(store='apple',country='us',language='en',app_id='100')
        group=app_groups.create(self.db,{'name':'关注'})
        app_groups.add_members(self.db,group['id'],{'members':[self.m]})

    def test_scoped_icon_batch_excludes_old_chart_other_store_and_paused_markets(self):
        from appmon.icon_batches import claim
        self.db.execute("INSERT INTO observation_targets(kind,store,app_id,country,language,latest_success_id) VALUES('chart','apple','','us','en',11)")
        for run,appid,rank in [(10,'200',1),(11,'100',1),(11,'300',50),(11,'400',51)]:
            self.db.execute('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',(run,appid,rank,json.dumps({'app_id':appid})))
        for appid in ('100','200','300','400'):
            for country in ('us','gb'):
                app_icons.remember(self.db,'apple',appid,country,'en','https://is1.mzstatic.com/shared.png')
        app_icons.remember(self.db,'google','100','us','en','https://is1.mzstatic.com/shared.png')
        self.db.execute('UPDATE app_icons SET requested=1')
        self.db.execute("UPDATE app_icons SET enabled=0 WHERE app_id='300' AND country='gb'")
        rows=claim(self.db,self.cfg,20,SimpleNamespace())
        self.assertCountEqual([(r['store'],r['app_id'],r['country']) for r in rows],
                              [('apple','100','us'),('apple','100','gb'),('apple','300','us')])
        self.assertEqual(len({r['id'] for r in rows}),3)
        self.assertEqual(len({r['token'] for r in rows}),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_icons WHERE token IS NOT NULL').fetchone()['n'],3)

    def test_scope_sync_schedules_missing_details_and_image_dependencies(self):
        self.db.execute("INSERT INTO observation_targets(kind,store,app_id,country,language,latest_success_id) VALUES('chart','apple','','gb','en',12)")
        self.db.execute('INSERT INTO observation_items VALUES(12,%s,1,%s)',('400',json.dumps({'app_id':'400'})))
        app_icons.remember(self.db,'apple','400','gb','en','https://is1.mzstatic.com/chart.png')
        result=app_media.sync_existing(self.db,self.cfg)
        self.assertEqual(result['listings'],0)
        self.assertEqual(self.db.execute("SELECT enabled FROM watchlist WHERE app_id='400'").fetchone()['enabled'],1)
        self.assertEqual(self.db.execute("SELECT requested FROM app_icons WHERE app_id='400'").fetchone()['requested'],1)
        self.assertTrue(self.db.execute("SELECT 1 FROM jobs WHERE app_id='400' AND country='gb'").fetchone())

    def test_nonfocused_structured_record_saved_without_response_body(self):
        add_apps(self.db,[('apple','200',None,None)],'test');enqueue(self.db,['us'],'en')
        row=claim(self.db,['us'],'en',600)
        self.assertEqual(row['app_id'],'200')
        payload={'name':'公开资料','icon':'https://is1.mzstatic.com/a.png','screenshots':['https://is1.mzstatic.com/s.png'],'url':'https://apps.apple.com/us/app/id200'}
        self.assertTrue(save_result(self.db,self.cfg,row,result=Result('ok',payload,b'raw-response')))
        snapshot=self.db.execute('SELECT payload,raw_path FROM snapshots').fetchone()
        self.assertEqual(json.loads(snapshot['payload']),payload);self.assertEqual(snapshot['raw_path'],'')
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_media_assets').fetchone()['n'],0)
        self.assertFalse((self.cfg['data_dir']/'responses').exists())
        icon=app_icons.request_icon(self.db,self.cfg,self.m|{'app_id':'200'})
        self.assertEqual(icon['status'],'links_only');self.assertEqual(icon['url'],payload['icon'])
        self.assertEqual(self.db.execute("SELECT requested FROM app_icons WHERE app_id='200'").fetchone()['requested'],0)
        self.assertTrue(storage_policy.archive_raw(self.db,self.cfg,'apple',['100'],b'focus-response'))

    def test_scope_sync_keeps_current_top50_all_markets_and_preserves_paused_assets(self):
        self.db.execute("INSERT INTO observation_targets(kind,store,app_id,country,language,latest_success_id) VALUES('chart','apple','','us','en',10)")
        target=self.db.cursor.lastrowid
        for run,appid,rank in [(10,'200',1),(11,'300',50),(11,'400',51)]:
            self.db.execute('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',(run,appid,rank,json.dumps({'app_id':appid})))
        app_media.remember(self.db,self.cfg,self.m|{'app_id':'200'},{'screenshots':['https://is1.mzstatic.com/old.png']})
        self.db.execute('UPDATE observation_targets SET latest_success_id=11 WHERE id=%s',(target,))
        for appid in ('100','200','300','400'):
            for country in ('us','gb'):
                app_icons.remember(self.db,'apple',appid,country,'en','https://is1.mzstatic.com/'+appid+'.png')
        app_icons.remember(self.db,'google','300','us','en','https://play-lh.googleusercontent.com/other-store.png')
        self.db.execute('UPDATE app_icons SET requested=1')
        self.db.execute("UPDATE app_icons SET enabled=0 WHERE app_id='300' AND country='gb'")
        app_media.remember(self.db,self.cfg,self.m|{'app_id':'300','country':'gb'},{'screenshots':['https://is1.mzstatic.com/current.png']})
        self.db.execute("UPDATE app_media_assets SET manual_paused=1,enabled=0 WHERE source_url='https://is1.mzstatic.com/current.png'")
        app_media.sync_existing(self.db,self.cfg)
        rows=self.db.execute('SELECT store,app_id,country,requested,enabled FROM app_icons').fetchall()
        for row in rows:
            self.assertEqual(bool(row['requested']),row['store']=='apple' and row['app_id'] in ('100','300'))
        self.assertEqual(next(r['enabled'] for r in rows if r['app_id']=='300' and r['country']=='gb'),0)
        assets=self.db.execute('SELECT enabled,manual_paused FROM app_media_assets').fetchall()
        self.assertEqual(len(assets),2);self.assertTrue(all(r['enabled']==0 for r in assets))
        self.assertEqual(sum(r['manual_paused'] for r in assets),1)

    def test_focus_and_latest_chart_images_only_no_video_and_shared_url_once(self):
        self.db.execute("INSERT INTO observation_targets(kind,store,app_id,country,language) VALUES('chart','apple','','us','en')")
        target=self.db.cursor.lastrowid
        for run,appid in [(10,'200'),(11,'300')]:
            self.db.execute('INSERT INTO observation_items VALUES(%s,%s,1,%s)',(run,appid,json.dumps({'app_id':appid})))
        self.db.execute('UPDATE observation_targets SET latest_success_id=11 WHERE id=%s',(target,))
        self.assertFalse(storage_policy.media_allowed(self.db,self.cfg,'apple','200'))
        self.assertTrue(storage_policy.media_allowed(self.db,self.cfg,'apple','300'))
        payload={'screenshots':['https://is1.mzstatic.com/shared.png'],'video':'https://example.org/movie.mp4'}
        for appid in ('100','200','300'):app_media.remember(self.db,self.cfg,self.m|{'app_id':appid},payload)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_media_assets').fetchone()['n'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_media_refs').fetchone()['n'],2)
        class Response:
            status_code=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,n):yield PNG
        client=Client(self.cfg,self.db);client.lineage_lane='screenshots'
        with patch.object(client,'request',return_value=Response()) as http:
            result=app_media.work(self.cfg,self.db,client)
        client.session.close();self.assertEqual(result['ok'],1);http.assert_called_once()
        task=collection_tasks.list_tasks(self.db,kind='screenshots',country='us')['items'][0]
        self.assertEqual(task['resource'],'media')
        self.assertEqual(task['task_state'],'scheduled')
        collection_tasks.control_tasks(self.db,{'tasks':[{'resource':'media','id':task['id']}],'action':'pause'})
        app_media.remember(self.db,self.cfg,self.m,payload)
        self.assertEqual(collection_tasks.list_tasks(self.db,kind='screenshots')['items'][0]['task_state'],'paused')

    def test_low_disk_pauses_before_claim_and_does_not_auto_resume(self):
        self.cfg['min_free_gb']=15
        with patch('appmon.storage_policy.shutil.disk_usage',return_value=SimpleNamespace(free=14*1024**3)):
            self.assertTrue(blocked(self.db,self.cfg))
            self.assertIsNone(admission(self.db,self.cfg,'apple'))
        self.assertTrue(settings(self.db,self.cfg)['paused'])
        with patch('appmon.storage_policy.shutil.disk_usage',return_value=SimpleNamespace(free=30*1024**3)):
            self.assertTrue(blocked(self.db,self.cfg))
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM collection_logs WHERE event='storage_paused'").fetchone()['n'],1)
