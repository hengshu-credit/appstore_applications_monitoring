import gzip
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from appmon.source_profiles import profile
from appmon.data_sources import cipher
from test_monitor import MySQLTestCase


class SourceProfilesTest(unittest.TestCase):
    def test_private_binding_requires_https_and_an_explicit_allowed_host(self):
        self.assertEqual(profile({'source_profiles':{'x':{'origin':'https://data.example/v2','hosts':['data.example']}}},'x')['origin'],'https://data.example/v2')
        for origin in ('http://data.example','https://user:pass@data.example','https://data.example?token=x','https://data.example:8443'):
            with self.assertRaises(ValueError):profile({'source_profiles':{'x':{'origin':origin}}},'x')
        with self.assertRaises(ValueError):profile({'source_profiles':{'x':{'origin':'https://data.example','hosts':['other.example']}}},'x')


class SourceMigrationTest(MySQLTestCase):
    def test_migration_preserves_secrets_rows_and_archive_bytes_and_is_repeatable(self):
        path=Path(__file__).parents[1]/'scripts/migrate_source_profiles.py'
        spec=importlib.util.spec_from_file_location('source_migration',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.db.execute('CREATE TABLE prior_budget LIKE marketdata_budget')
        self.db.execute("INSERT INTO prior_budget VALUES('2026-01-01',12,1000,0)")
        self.db.execute('DROP TABLE marketdata_budget')
        secret=cipher(self.cfg,create=True).encrypt(b'{"access_token":"fixture"}').decode()
        self.db.execute("INSERT INTO source_accounts VALUES('prior-provider',%s,'authorized',NULL,0,0)",(secret,))
        raw=b'{"value":1}'
        folder=self.folder/'public-sources'/'prior';folder.mkdir(parents=True)
        (folder/'data.json.gz').write_bytes(gzip.compress(raw))
        self.db.execute('''INSERT INTO marketdata_artifacts(sha256,path,kind,provenance,records,created_at)
            VALUES(%s,'public-sources/prior/data.json.gz','metadata',%s,1,0)''',
            (hashlib.sha256(raw).hexdigest(),json.dumps({'url':'https://prior.example/report','params':{'id':'1'}})))
        manifest={'tables':{'prior_budget':'marketdata_budget'},'providers':{'prior-provider':'marketdata'},
                  'asset_folders':{'public-sources/prior':'public-sources/marketdata'}}
        for _ in range(2):
            result=module.migrate(self.cfg,manifest)
            self.assertEqual(result['table_counts'],{'marketdata_budget':1})
            self.assertTrue(result['archive_checksums_verified'])
        row=self.db.execute("SELECT secret FROM source_accounts WHERE provider='marketdata'").fetchone()
        self.assertEqual(row['secret'],secret)
        self.assertEqual(self.db.execute('SELECT used FROM marketdata_budget').fetchone()['used'],12)
        artifact=self.db.execute('SELECT * FROM marketdata_artifacts').fetchone()
        self.assertEqual(json.loads(artifact['provenance']),{'params':{'id':'1'}})
        self.assertEqual(gzip.decompress((self.folder/artifact['path']).read_bytes()),raw)
