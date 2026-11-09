import base64
import hashlib
import json
from appmon import keyword_library as kl, keyword_reports as kr, keyword_discovery as kd
from test_discovery import workbook_bytes
from test_monitor import OceanBaseTestCase


class KeywordLibraryTest(OceanBaseTestCase):
    def setUp(self):
        super().setUp()
        self.m = dict(store='apple', app_id='123', country='cn', language='zh-cn')
        self.input = self.folder / 'input'
        self.input.mkdir()
        self.raw = workbook_bytes([['  ＡＩ  助手 ', 2, 10, 0], ['AI 助手', 3, 0, 0], ['\u200bhidden', 1, 0, 0]])
        (self.input / '示例_数据报告.xlsx').write_bytes(self.raw)
        (self.input / '示例_数据报告 (1).xlsx').write_bytes(self.raw)
        self.bindings = [self.m | {'prefix': '示例'}]

    def test_build_complete_reproducible_and_idempotent_without_jobs(self):
        kd.import_candidates(self.db, {'markets': [self.m], 'words': ['人工词'], 'source': 'manual'})
        first = kl.build(self.cfg, self.db, self.input, self.bindings, self.folder / 'v1')
        self.assertEqual(len(first['reports']), 1)
        self.assertEqual(len(first['duplicate_files']), 1)
        self.assertEqual((first['reports'][0]['records'], first['reports'][0]['valid_records'], first['reports'][0]['rejected_records']), (5,4,1))
        self.assertEqual((first['keyword_library']['terms'], first['aso_library']['terms']), (4,3))
        self.assertEqual(first['aso_library']['source_records'], 4)
        aso = [json.loads(line) for line in (self.folder / 'v1/aso-library.jsonl').read_text(encoding='utf-8').splitlines()]
        ai = next(r for r in aso if r['keyword'] == 'AI 助手')
        self.assertEqual(len(ai['sources']), 2)
        self.assertEqual(ai['sources'][1]['search_index'], 0)
        self.assertEqual(ai['sources'][0]['digest'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_targets').fetchone()['n'], 0)
        second = kl.build(self.cfg, self.db, self.input, self.bindings, self.folder / 'v2')
        for name in ('keyword_library', 'aso_library'):
            self.assertEqual(first[name]['sha256'], second[name]['sha256'])
        with self.assertRaisesRegex(ValueError, '已存在'):
            kl.build(self.cfg, self.db, self.input, self.bindings, self.folder / 'v1')

    def test_preflight_missing_or_conflicting_binding_does_not_import(self):
        for bindings in ([], self.bindings * 2, [self.m | {'prefix': '示例', 'country': 'us'}]):
            with self.assertRaises(ValueError):
                kl.build(self.cfg, self.db, self.input, bindings, self.folder / 'v1')
        self.assertEqual(self.db.execute('SELECT count(*) n FROM keyword_reports').fetchone()['n'], 0)
        self.assertFalse((self.folder / 'v1').exists())

    def test_aso_excludes_paid_terms_keeps_market_app_filters_and_pagination(self):
        r = kr.import_report(self.cfg, self.db, self.m | {'content_base64': base64.b64encode(self.raw).decode()})
        self.db.execute("INSERT INTO keyword_report_rows VALUES(%s,'ASA竞价词',100,'付费词',1,'0','0','{}')", (r['id'],))
        kr.promote(self.db, {'report_id': r['id'], 'source_rows': [3,4,5,6]})
        result = kr.library(self.db, self.m)
        self.assertEqual((result['total'], result['counts']['records']), (3,4))
        self.assertNotIn('付费词', [r['keyword'] for r in result['items']])
        self.assertTrue(all(r['candidate_id'] for r in result['items']))
        self.assertEqual(kr.library(self.db, self.m | {'app_id': '456'})['total'], 0)
        self.assertEqual(kr.library(self.db, self.m | {'country': 'us'})['total'], 0)
        self.assertEqual(kr.library(self.db, self.m | {'language': 'en'})['total'], 0)
        self.assertEqual(kr.library(self.db, self.m, q='助手')['total'], 1)
        pages = [kr.library(self.db, self.m, offset=i, limit=1)['items'][0]['keyword'] for i in range(3)]
        self.assertEqual(len(set(pages)), 3)
        with self.assertRaises(ValueError): kr.library(self.db, self.m, offset=-1)

    def test_catalog_exposes_populated_markets_without_relabeling_empty_market(self):
        value = self.m | {'content_base64': base64.b64encode(self.raw).decode()}
        first = kr.import_report(self.cfg, self.db, value)
        kr.import_report(self.cfg, self.db, value | {'app_id': '456', 'language': 'en'})
        self.db.execute("INSERT INTO keyword_report_rows VALUES(%s,'ASA竞价词',100,'仅竞价',1,'0','0','{}')", (first['id'],))
        empty = kr.library(self.db, dict(store='google', country='us', language='en'))
        self.assertEqual(empty['items'], [])
        self.assertEqual((empty['total'], empty['market_terms']), (0,0))
        self.assertEqual(empty['catalog']['total_terms'], 6)
        self.assertEqual({(r['store'],r['country'],r['language'],r['terms']) for r in empty['catalog']['items']},
                         {('apple','cn','zh-cn',3), ('apple','cn','en',3)})
        filtered = kr.library(self.db, self.m, q='no match')
        self.assertEqual((filtered['total'], filtered['market_terms']), (0,3))
        self.assertEqual(filtered['catalog']['total_terms'], 3)  # same app only in workbench
        self.assertEqual(kr.library(self.db, self.m | {'app_id': '999'})['catalog']['items'], [])
