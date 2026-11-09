import json
import lzma
from pathlib import Path
from unittest.mock import patch

from appmon import app_groups, developer_refs
from appmon.ingest import file_hash
from appmon.watchlist_import import prepare
from test_monitor import OceanBaseTestCase


class DeveloperReferenceTest(OceanBaseTestCase):
    def test_numeric_ids_remain_exact_text(self):
        key=dict(store='google',app_id='com.credicuotas.wallet',country='ar',language='es')
        result=developer_refs.identity(key,{'developer_id':6942553452222258407},({},{}))
        self.assertEqual(result['developer_id'],'6942553452222258407')

    def test_legacy_sources_fill_name_and_id_without_collecting_or_claiming_a_listing(self):
        raw = Path(self.cfg['data_dir']) / 'raw'
        original = raw / 'watchlist-sample' / 'as-data.js'
        original.parent.mkdir(parents=True)
        watch = dict(cc='ao',pkg='123',name='Example',dev='Example Studio',liveStatus='on')
        snapshot = dict(totalApps=1, apps=[watch])
        original.write_text('window.AS_DATA = '+json.dumps(snapshot),encoding='utf-8')
        rows = prepare({'google':dict(totalApps=1,apps=[dict(cc='ao',pkg='com.example.app')]), 'apple':snapshot})[1]
        self.assertEqual(rows[-1]['developer'],'Example Studio')
        exports = raw.parent/'exports'/'watchlist-import'
        exports.mkdir(parents=True)
        summary = exports/'old-summary.json'
        summary.write_text(json.dumps({'source_hashes':{'apple':file_hash(original)}}),encoding='utf-8')
        seed = raw/'store_apps-test.tsv.xz'
        with lzma.open(seed,'wt',encoding='utf-8') as stream:
            stream.write('store\tstore_id\tdeveloper_id\nApple\t123\t5678\nAndroid\tcom.example.app\tExample+Apps\n')
        gid = app_groups.create(self.db,{'name':'Group'})['id']
        app_groups.add_members(self.db,gid,{'members':[dict(store='apple',app_id='123',country='ao',language='auto'),
                                                      dict(store='google',app_id='com.example.app',country='ao',language='auto')]})
        self.db.execute('UPDATE watchlist SET enabled=0')
        developer_refs.refresh(self.db,self.cfg)
        with patch('appmon.developer_refs.lzma.open',side_effect=AssertionError('unchanged seed was rescanned')):
            developer_refs.refresh(self.db,self.cfg)
        apple, google = app_groups.dashboard(self.db,self.cfg,gid)['items']
        self.assertEqual((apple['developer'],apple['developer_id'],apple['developer_source'],apple['developer_id_source']),
                         ('Example Studio','5678','watchlist','seed'))
        self.assertEqual((google['developer'],google['developer_id']),(None,'Example+Apps'))
        self.assertEqual(self.db.execute('SELECT SUM(enabled) n FROM watchlist').fetchone()['n'],0)
        self.assertEqual(self.db.execute('SELECT count(*) n FROM listings').fetchone()['n'],0)
        detail = app_groups.member_detail(self.db,gid,apple['id'],cfg=self.cfg)
        self.assertEqual(detail['member']['developer_id'],'5678')
        self.db.execute('''INSERT INTO listings(store,app_id,country,language,state,checked_at,last_success,payload)
            VALUES('apple','123','ao',%s,'available',1,1,%s)''',(apple['language'],json.dumps({'developer':'New Studio','developer_id':999})))
        current = app_groups.dashboard(self.db,self.cfg,gid)['items'][0]
        self.assertEqual((current['developer'],current['developer_id'],current['developer_id_source']),('New Studio','999','store'))
        original.write_text('window.AS_DATA = '+json.dumps(dict(totalApps=1,apps=[watch|{'dev':'Unverified'}])),encoding='utf-8')
        developer_refs.refresh(self.db,self.cfg)
        self.assertEqual(developer_refs.references(self.cfg)[0],{})
