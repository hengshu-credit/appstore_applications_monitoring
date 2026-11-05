import json
from unittest.mock import patch
import requests
from fastapi.testclient import TestClient
from appmon import data_sources
from appmon.web import create_app
from test_monitor import MySQLTestCase


class Response:
    def __init__(self,status=200,value=None,location=''):
        self.status_code=status;self.value=value;self.headers={'Location':location}
    def json(self):return self.value


class SourceAccountsTest(MySQLTestCase):
    def test_directory_has_separate_encrypted_configuration_and_session(self):
        def brain_login(client,saved):
            self.assertEqual(saved['email'],'brain@example.com')
            self.assertEqual(saved['password'],'brain-secret')
            client.cookies.set('brain-session','brain-cookie',domain='.directory.example',path='/')
            client.cookies.set('unrelated','do-not-store',domain='other.example',path='/')
            return 'authorized'
        with patch('appmon.directory_source.authorize',side_effect=brain_login):
            result=data_sources.save(self.db,self.cfg,{'email':'brain@example.com','password':'brain-secret'},'directory')
        self.assertEqual(result['directory']['status'],'authorized')
        self.assertEqual(result['community']['status'],'not_configured')
        row=self.db.execute("SELECT secret FROM source_accounts WHERE provider='directory'").fetchone()
        self.assertNotIn('brain-secret',row['secret'])
        self.assertNotIn('brain-cookie',json.dumps(result))
        saved=json.loads(data_sources.cipher(self.cfg).decrypt(row['secret'].encode()))
        self.assertEqual([c['name'] for c in saved['cookies']],['brain-session'])

    def test_credentials_and_cookies_encrypted_and_expired_session_reauthorized(self):
        client=requests.Session();calls=[];expired=False
        def send(method,url,**kwargs):
            calls.append((method,url,kwargs))
            self.assertFalse(kwargs['allow_redirects'])
            if method=='POST':
                self.assertEqual(url,'https://community.example/auth/login')
                self.assertEqual(kwargs['data']['password'],'source-secret')
                client.cookies.set('session','source-cookie',domain='community.example',path='/')
                return Response(302,location='/free-app-datasets')
            return Response(value={'user':None if expired and sum(c[0]=='POST' for c in calls)<2 else {'username':'tester','emailVerified':True}})
        with patch.object(data_sources,'session',return_value=client),patch.object(client,'request',side_effect=send):
            state=data_sources.save(self.db,self.cfg,{'email':'test@example.com','password':'source-secret'})
            self.assertEqual(state['community']['status'],'authorized')
            secret=self.db.execute('SELECT secret FROM source_accounts').fetchone()['secret']
            for text in ('source-secret','source-cookie','test@example.com'):
                self.assertNotIn(text,secret);self.assertNotIn(text,json.dumps(state))
            saved=json.loads(data_sources.cipher(self.cfg).decrypt(secret.encode()))
            self.assertEqual(saved['password'],'source-secret')
            expired=True
            self.assertEqual(data_sources.authorize(self.db,self.cfg)['community']['status'],'authorized')
            self.assertEqual(sum(c[0]=='POST' for c in calls),2)

    def test_invalid_password_backoff_and_admin_only_api(self):
        client=requests.Session()
        with patch.object(data_sources,'session',return_value=client),patch.object(client,'request',return_value=Response(400)) as send:
            state=data_sources.save(self.db,self.cfg,{'email':'test@example.com','password':'wrong-secret'})
            self.assertEqual(state['community']['status'],'invalid_credentials')
            data_sources.authorize(self.db,self.cfg)
            self.assertEqual(send.call_count,1)
        api=TestClient(create_app(self.cfg));self.assertEqual(api.get('/api/data-sources').status_code,401)
        self.login_admin(api)
        self.assertEqual(api.get('/api/data-sources').status_code,200)
        member=TestClient(create_app(self.cfg))
        member.post('/api/auth/register',json={'username':'source-viewer','password':'password-123'})
        login=member.post('/api/auth/login',json={'username':'source-viewer','password':'password-123'})
        member.headers['X-CSRF-Token']=login.json()['csrf_token']
        self.assertEqual(member.get('/api/data-sources').status_code,403)
        self.assertEqual(member.post('/api/data-sources/community/authorize').status_code,403)

    def test_fallback_preserves_original_observation_and_does_not_invent_revenue(self):
        self.db.execute("INSERT INTO listings(store,app_id,country,language,state,checked_at,last_success,payload) VALUES('google','com.example.app','us','en','available',125,123,%s)",(json.dumps({'installs':1000}),))
        with patch('appmon.public_sources.collect',side_effect=ValueError('source unavailable')):
            result=data_sources.app_metrics(self.db,self.cfg,'google','com.example.app')
        self.assertEqual(result['observed_at'],123)
        self.assertEqual(result['values_display'],{'installs':'1000'})
        self.assertEqual(result['scope'],'worldwide')
        self.assertIsNone(result['country'])
