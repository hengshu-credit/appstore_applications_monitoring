import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon import source_registry,data_sources
from appmon.source_profiles import profile
from appmon.web import create_app
from test_monitor import MySQLTestCase


class SourceRegistryTest(MySQLTestCase):
    def test_legacy_bindings_roundtrip_and_admin_edits_are_not_overwritten(self):
        rows=source_registry.report(self.db,self.cfg)
        self.assertEqual(next(r for r in rows if r['id']=='community')['origin'],'https://community.example')
        changed=source_registry.save(self.db,self.cfg,'community',{'origin':'https://new.example/v2','hosts':['new.example']})
        self.assertEqual(changed['origin'],'https://new.example/v2')
        source_registry.report(self.db,self.cfg)
        self.assertEqual(profile(self.cfg,'community',self.db)['origin'],'https://new.example/v2')
        with self.assertRaises(ValueError):source_registry.save(self.db,self.cfg,'community',{'origin':'https://user:secret@new.example'})

    def test_batch_settings_apply_to_details_without_expanding_unprobed_review_payloads(self):
        source_registry.save(self.db,self.cfg,'google_play',{'batch_size':100})
        source_registry.save(self.db,self.cfg,'apple_lookup',{'batch_size':200})
        details=source_registry.collector_config(self.db,self.cfg,'pool')
        self.assertEqual((details['google_rpc_batch_size'],details['apple_lookup_batch_size']),(100,200))
        self.assertEqual(source_registry.collector_config(self.db,self.cfg,'reviews')['google_rpc_batch_size'],20)
        with self.assertRaises(ValueError):source_registry.save(self.db,self.cfg,'google_play',{'batch_size':101})

    def test_endpoint_change_blocks_old_credentials_and_ads_token_never_roundtrips(self):
        secret=data_sources.cipher(self.cfg,create=True).encrypt(b'{"email":"me@example.org","password":"old-password","cookies":[]}').decode()
        self.db.execute("INSERT INTO source_accounts(provider,secret,status,updated_at) VALUES('community',%s,'authorized',1)",(secret,))
        source_registry.save(self.db,self.cfg,'community',{'origin':'https://new.example','hosts':['new.example']})
        with patch('requests.Session.request') as network:
            with self.assertRaises(ValueError):data_sources.account_session(self.db,self.cfg)
        network.assert_not_called()
        result=source_registry.save(self.db,self.cfg,'apple_ads',{'access_token':'private-fixture-token','ad_account_id':'123'})
        self.assertNotIn('private-fixture-token',json.dumps(result))
        self.assertEqual(result['ad_account_id'],'123')
        source_registry.save(self.db,self.cfg,'apple_ads',{'access_token':'','ad_account_id':'123'})
        self.assertEqual(source_registry.ads_credentials(self.db,self.cfg)['access_token'],'private-fixture-token')
        public=data_sources.public_status(self.db,self.cfg)
        self.assertEqual(public['community']['email'],'me@example.org')
        self.assertNotIn('old-password',json.dumps(public))

    def test_configuration_api_is_admin_only_and_returns_saved_values(self):
        client=TestClient(create_app(self.cfg))
        self.assertEqual(client.get('/api/data-sources').status_code,401)
        self.login_admin(client)
        result=client.put('/api/data-sources/config/apple_lookup',json={'batch_size':200})
        self.assertEqual(result.status_code,200,result.text)
        rows=client.get('/api/data-sources').json()['connections']
        self.assertEqual(next(r for r in rows if r['id']=='apple_lookup')['batch_size'],200)

    def test_saved_detail_batch_capacity_is_used_by_next_dispatch(self):
        from appmon.db import add_apps,enqueue
        from appmon.dispatcher import run_batch
        from appmon.stores import Client,Result
        add_apps(self.db,[('google','com.example.app'+str(i),None,None) for i in range(105)],'fixture')
        enqueue(self.db,['us'],'en')
        source_registry.save(self.db,self.cfg,'google_play',{'batch_size':100})
        def fetch(client,jobs):return [Result('ok',{'app_id':j['app_id'],'name':j['app_id'],'store_fields':{'retained':'all supplied fields'}}) for j in jobs]
        with patch.object(Client,'fetch_google_many',autospec=True,side_effect=fetch) as upstream:
            result=run_batch(self.db,self.cfg,'pool')
        self.assertEqual((result['processed'],result['ok']),(100,100))
        upstream.assert_called_once();self.assertEqual(len(upstream.call_args.args[1]),100)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM listings').fetchone()['n'],100)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM jobs WHERE due=0').fetchone()['n'],5)
