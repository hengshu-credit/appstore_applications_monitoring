import concurrent.futures
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon.db import connect
from appmon.keywords import add_targets, work_keywords
from appmon.keyword_stores import SearchResult
from appmon.stores import Client, FetchError
from appmon.task_logs import emit, execution, read_logs, safe_message
from appmon.web import create_app
from test_monitor import OceanBaseTestCase


class TaskLogTest(OceanBaseTestCase):
    def target(self, word='loan'):
        return add_targets(self.db,[dict(store='google',country='th',language='th',keyword=word)])['ids'][0]

    def test_started_log_is_visible_while_request_is_still_running(self):
        identifier=self.target();entered=threading.Event();release=threading.Event()
        class Blocking:
            def search(self,target):
                entered.set()
                if not release.wait(20):raise RuntimeError('test release missing')
                return SearchResult([dict(app_id='com.example.app')],b'raw','test')
        def run():
            db=connect(self.cfg)
            try:return work_keywords(self.cfg,db,1,Blocking())
            finally:db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(run)
            try:
                self.assertTrue(entered.wait(15))
                live=read_logs(self.db,'keywords',str(identifier))
                self.assertEqual(live['task']['status'],'running')
                self.assertEqual([r['event'] for r in live['items']],['started'])
            finally:release.set()
            self.assertEqual(future.result(timeout=20)['ok'],1)
        after=read_logs(self.db,'keywords',str(identifier),after_id=live['next_cursor'])
        self.assertEqual(after['task']['status'],'scheduled')
        self.assertEqual([r['event'] for r in after['items']],['ok'])
        self.assertEqual(after['items'][0]['execution_id'],live['items'][0]['execution_id'])

    def test_log_cursors_isolate_tasks_and_offer_older_pages(self):
        a=self.target('a');b=self.target('b')
        self.assertEqual(read_logs(self.db,'keywords',str(a))['items'],[])
        for n in range(5):emit(self.db,'keywords',a,'progress',str(n))
        emit(self.db,'keywords',b,'error','unrelated')
        latest=read_logs(self.db,'keywords',str(a),limit=2)
        self.assertEqual([r['message'] for r in latest['items']],['3','4']);self.assertTrue(latest['has_older'])
        older=read_logs(self.db,'keywords',str(a),before_id=latest['oldest_cursor'],limit=2)
        self.assertEqual([r['message'] for r in older['items']],['1','2'])
        for n in range(5,8):emit(self.db,'keywords',a,'progress',str(n))
        new=read_logs(self.db,'keywords',str(a),after_id=latest['next_cursor'],limit=2)
        self.assertEqual([r['message'] for r in new['items']],['5','6']);self.assertTrue(new['has_more'])
        tail=read_logs(self.db,'keywords',str(a),after_id=new['next_cursor'])
        self.assertEqual([r['message'] for r in tail['items']],['7'])
        self.assertEqual(read_logs(self.db,'keywords',str(a),after_id=tail['next_cursor'])['items'],[])

    def test_failure_and_retry_have_separate_execution_ids(self):
        identifier=self.target()
        class Broken:
            def search(self,target):raise FetchError('HTTP 429',900)
        work_keywords(self.cfg,self.db,1,Broken())
        class Good:
            def search(self,target):return SearchResult([],b'empty','test')
        work_keywords(self.cfg,self.db,1,Good(),identifier,True)
        rows=read_logs(self.db,'keywords',str(identifier))['items']
        self.assertEqual([r['event'] for r in rows],['started','error','started','ok'])
        self.assertEqual(rows[1]['level'],'error');self.assertNotEqual(rows[0]['execution_id'],rows[2]['execution_id'])

    def test_http_progress_and_secret_redaction(self):
        identifier=self.target();client=Client(self.cfg,self.db)
        with patch.object(client.session,'get',return_value=SimpleNamespace(status_code=200)):
            with execution(self.db,client,'keywords',identifier,'sample') as log:
                client.get('google','https://username:secret@example.com/search?token=hidden')
                log.finish('ok')
        rows=read_logs(self.db,'keywords',str(identifier))['items']
        self.assertEqual([r['event'] for r in rows],['started','request','response','ok'])
        self.assertIn('example.com',rows[1]['message']);self.assertNotIn('secret',str(rows));self.assertNotIn('hidden',str(rows))
        self.assertIsNone(client.progress)
        redacted=safe_message('password=secret-value token:private-value Bearer credential-value https://a.test/?token=url-value')
        for value in ['secret-value','private-value','credential-value','url-value']:self.assertNotIn(value,redacted)

    def test_log_api_requires_auth_and_validates_resource_and_cursors(self):
        identifier=self.target();emit(self.db,'keywords',identifier,'started','Started')
        token='test-token-for-log-api-authentication'
        with patch.dict(os.environ,{'APPMON_API_TOKEN':token}):client=TestClient(create_app(self.cfg))
        path=f'/api/research/collection/logs/keywords/{identifier}'
        self.assertEqual(client.get(path).status_code,401)
        self.login_admin(client)
        headers={}
        self.assertEqual(client.get(path,headers=headers).json()['items'][0]['message'],'Started')
        self.assertEqual(client.get(path+'?after_id=1&before_id=2',headers=headers).status_code,400)
        self.assertEqual(client.get('/api/research/collection/logs/files/1',headers=headers).status_code,400)
