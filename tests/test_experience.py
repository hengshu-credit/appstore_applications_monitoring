import base64
import json
import os
import time
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon import app_icons, collection_tasks, locales, scoped_views, task_logs
from appmon.db import add_apps
from appmon.keywords import add_targets, work_keywords
from appmon.stores import FetchError
from appmon.web import create_app
from test_monitor import MySQLTestCase

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1cAAAAASUVORK5CYII=')

class ExperienceTest(MySQLTestCase):
    def test_automatic_locale_and_store_scope_paginate_without_loss(self):
        self.assertEqual(locales.language_for('google','kz'),'kk')
        self.assertEqual(locales.language_for('apple','kz'),'ru')
        self.assertEqual(locales.language_for('apple','th'),'th')
        self.assertEqual(locales.language_for('google','ph'),'fil')
        self.assertEqual(locales.language_for('google','th','en'),'en')
        with self.assertRaises(ValueError):locales.language_for('google','kz','kz')
        rows=[('google',f'com.example.app{i}',f'Google {i}',None) for i in range(7)]+[('apple',str(i+100),f'Apple {i}',None) for i in range(3)]
        add_apps(self.db,rows,'test')
        seen=[];cursor={}
        for i in range(10):
            data=scoped_views.apps(self.db,store='all',country='kz',language='auto',limit=4,**cursor)
            if not i:self.assertEqual([r['store'] for r in data['items']],['google','apple','google','apple'])
            seen.extend((r['store'],r['app_id']) for r in data['items'])
            for r in data['items']:self.assertEqual(r['language'],'kk' if r['store']=='google' else 'ru')
            if not data['next']:break
            cursor=data['next']
        self.assertEqual(len(seen),10);self.assertEqual(len(set(seen)),10)
        add_targets(self.db,[dict(store=s,country='kz',language='auto',keyword='loan') for s in ('apple','google')])
        self.assertEqual(scoped_views.overview(self.db,'apple')['counts']['keyword_targets'],1)
        self.assertEqual(scoped_views.overview(self.db,'all')['counts']['keyword_targets'],2)

    def test_icons_only_queue_when_requested_cache_and_control_with_logs(self):
        m=dict(store='apple',app_id='123',country='us',language='en')
        app_icons.remember(self.db,**m,url='https://is1-ssl.mzstatic.com/image.png',name='Example')
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,1)['processed'],0)
        requested=app_icons.request_icon(self.db,self.cfg,m);identifier=requested['id']
        self.assertTrue(requested['pending'])
        class Response:
            status_code=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,size):yield PNG
        class Images:
            def request(self,*args,**kwargs):
                self_args=args
                assert kwargs['allow_redirects'] is False
                return Response()
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,1,Images())['ok'],1)
        ready=app_icons.request_icon(self.db,self.cfg,m)
        self.assertFalse(ready['pending']);self.assertIn('/api/icons/',ready['url'])
        self.assertEqual(collection_tasks.list_tasks(self.db,kind='icons')['items'][0]['task_state'],'scheduled')
        self.assertEqual(task_logs.read_logs(self.db,'icons',str(identifier))['items'][-1]['event'],'ok')
        with patch.dict(os.environ,{'APPMON_API_TOKEN':''}):client=TestClient(create_app(self.cfg))
        r=client.get(ready['url']);self.assertEqual(r.status_code,200);self.assertEqual(r.content,PNG)
        self.assertEqual(r.headers['content-type'],'image/png')
        collection_tasks.control_tasks(self.db,{'tasks':[{'resource':'icons','id':identifier}],'action':'pause'})
        self.assertEqual(app_icons.request_icon(self.db,self.cfg,m)['status'],'paused')
        app_icons.request_icon(self.db,self.cfg,m,True)
        class Broken:
            def request(self,*args,**kwargs):raise FetchError('HTTP 503',details={'status_code':503,'host':'is1-ssl.mzstatic.com'})
        self.assertEqual(app_icons.work_icons(self.cfg,self.db,1,Broken())['error'],1)
        failed=app_icons.request_icon(self.db,self.cfg,m)
        self.assertEqual(failed['url'],ready['url'])
        row=task_logs.read_logs(self.db,'icons',str(identifier))['items'][-1]
        self.assertEqual(row['details']['status_code'],503)

    def test_failure_logs_include_safe_diagnostics_and_scoped_apis(self):
        a=add_targets(self.db,[dict(store=s,country='us',language='en',keyword='example') for s in ('apple','google')])['ids']
        class Broken:
            def search(self,target):
                raise FetchError('ReadTimeout',details={'host':'itunes.apple.com','timeout_seconds':30,'token':'secret-value'})
        work_keywords(self.cfg,self.db,1,Broken(),target_id=a[0],refresh=True)
        last=task_logs.read_logs(self.db,'keywords',str(a[0]))['items'][-1]
        self.assertEqual(last['details']['timeout_seconds'],30)
        self.assertIn('search',last['details']['stack']);self.assertNotIn('secret-value',json.dumps(last))
        with patch.dict(os.environ,{'APPMON_API_TOKEN':''}):client=TestClient(create_app(self.cfg))
        for endpoint in ('keywords','targets','watchlist','overview','daily','market','alerts','ads','availability','research/events','research/reviews','research/developers','research/comparisons'):
            response=client.get('/api/'+endpoint+'?store=all&country=us&language=auto')
            self.assertEqual(response.status_code,200,(endpoint,response.text))
        rows=client.get('/api/keywords?store=apple').json();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['store'],'apple')
        for endpoint in ('research/events','research/reviews'):
            self.assertEqual(client.get('/api/'+endpoint+'?store=all&app_id=123&language=auto').status_code,200)

    def test_icon_stale_worker_cannot_overwrite_a_paused_task(self):
        m=dict(store='google',app_id='com.example.app',country='us',language='en',source_url='https://play-lh.googleusercontent.com/test')
        row=app_icons.request_icon(self.db,self.cfg,m)
        db=self.db
        class Stale:
            def request(self,*args,**kwargs):
                collection_tasks.control_tasks(db,{'tasks':[{'resource':'icons','id':row['id']}],'action':'pause'})
                raise FetchError('timeout')
        result=app_icons.work_icons(self.cfg,self.db,1,Stale())
        self.assertEqual(result['stale'],1)
        final=self.db.execute('SELECT * FROM app_icons WHERE id=%s',(row['id'],)).fetchone()
        self.assertEqual(final['failures'],0);self.assertFalse(final['enabled'])
