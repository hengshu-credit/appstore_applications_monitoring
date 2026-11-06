import json
import uuid
import zipfile
from pathlib import Path
from appmon.backup import backup, restore, verify
from appmon.db import Database, TABLES, add_apps
from appmon.data_sources import cipher
from decimal import Decimal
from test_monitor import MySQLTestCase


class BackupTest(MySQLTestCase):
    def test_restore_previous_format_initializes_empty_adjustment_table(self):
        add_apps(self.db,[('google','com.legacy.app','Legacy',None)],'fixture')
        current=self.folder/'current.zip';legacy=self.folder/'legacy.zip'
        backup(self.cfg,current,Path(__file__).parents[1]/'config.toml')
        filename='tables/app_group_adjustments.jsonl'
        with zipfile.ZipFile(current) as original,zipfile.ZipFile(legacy,'w') as out:
            manifest=verify(original);self.assertEqual(manifest['format'],2)
            manifest['format']=1;del manifest['tables']['app_group_adjustments'];del manifest['files'][filename]
            out.writestr('manifest.json',json.dumps(manifest))
            for name in manifest['files']:out.writestr(name,original.read(name))
        name='appmon_test_legacy_'+uuid.uuid4().hex
        self.admin.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        target=self.cfg|{'mysql':self.options|{'database':name},'data_dir':self.folder/'legacy-data'}
        try:
            self.assertTrue(restore(target,legacy)['restored']);db=Database(target['mysql'])
            try:
                self.assertEqual(db.execute('SELECT name FROM apps').fetchone()['name'],'Legacy')
                self.assertEqual(db.execute('SELECT COUNT(*) n FROM app_group_adjustments').fetchone()['n'],0)
            finally:db.close()
        finally:self.admin.execute(f'DROP DATABASE `{name}`')

    def test_streamed_roundtrip_all_tables_assets_and_empty_destination_guard(self):
        add_apps(self.db,[('google','com.example.app','多语言 ไทย',None)],'fixture')
        secret=cipher(self.cfg,create=True).encrypt(b'{"email":"test@example.com","password":"fixture"}').decode()
        self.db.execute("INSERT INTO source_accounts(provider,secret,status,updated_at) VALUES('community',%s,'saved',0)",(secret,))
        self.db.execute("""INSERT INTO ad_reports(fingerprint,report_date,app_id,country,campaign,keyword,impressions,taps,installs,spend,currency,source,imported_at)
            VALUES(%s,'2026-01-01','com.example.app','us','campaign','word',2,1,0,123456.123456,'USD','fixture',0)""",('a'*64,))
        for folder in ('responses','icons','secrets','raw','exports'):
            path=self.folder/folder/'fixture';path.parent.mkdir(exist_ok=True);path.write_bytes(b'fixture contents')
        dest=self.folder/'offline.zip'
        result=backup(self.cfg,dest,Path(__file__).parents[1]/'config.toml')
        self.assertEqual(len(result['sha256']),64)
        with zipfile.ZipFile(dest) as archive:
            manifest=verify(archive)
            self.assertEqual(set(manifest['tables']),set(TABLES))
        name='appmon_test_restore_'+uuid.uuid4().hex
        self.admin.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        target=self.cfg|{'mysql':self.options|{'database':name},'data_dir':self.folder/'target'}
        try:
            self.assertTrue(restore(target,dest)['restored'])
            db=Database(target['mysql'])
            try:
                self.assertEqual(db.execute('SELECT name FROM apps').fetchone()['name'],'多语言 ไทย')
                self.assertEqual(db.execute('SELECT spend FROM ad_reports').fetchone()['spend'],Decimal('123456.123456'))
                restored_secret=db.execute('SELECT secret FROM source_accounts').fetchone()['secret']
                self.assertEqual(json.loads(cipher(target).decrypt(restored_secret.encode()))['password'],'fixture')
                for table,info in manifest['tables'].items():
                    expected=info['rows']+(table=='collection_controls')
                    self.assertEqual(db.execute(f'SELECT COUNT(*) n FROM `{table}`').fetchone()['n'],expected,table)
                settings=json.loads(db.execute("SELECT settings FROM collection_controls WHERE name='request-limits'").fetchone()['settings'])
                self.assertTrue(settings['paused'])
            finally:db.close()
            self.assertEqual((target['data_dir']/'secrets/fixture').read_bytes(),b'fixture contents')
            with self.assertRaises(ValueError):restore(target,dest)
            with self.assertRaises(ValueError):backup(self.cfg,dest,'config.toml')
            bad=self.folder/'bad.zip'
            with zipfile.ZipFile(dest) as original,zipfile.ZipFile(bad,'w') as altered:
                for item in original.infolist():
                    altered.writestr(item,b'corrupt' if item.filename=='tables/apps.jsonl' else original.read(item))
            with zipfile.ZipFile(bad) as archive:
                with self.assertRaises(ValueError):verify(archive)
        finally:self.admin.execute(f'DROP DATABASE `{name}`')
