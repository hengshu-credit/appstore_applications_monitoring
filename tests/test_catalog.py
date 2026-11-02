import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from appmon.catalog import category_id, listing_category, category_aliases, category_counts, public_catalog
from appmon.db import add_apps
from appmon.intelligence import add_observation_target
from appmon.web import create_app
from test_monitor import MySQLTestCase


class CatalogTest(unittest.TestCase):
    def test_legacy_aliases_and_store_boundaries(self):
        for value in ('6015','Finance','finance','财务'):
            self.assertEqual(category_id('apple',value),'6015')
        self.assertEqual(category_id('google','finance'),'FINANCE')
        self.assertIsNone(category_id('apple','ART_AND_DESIGN'))
        self.assertIsNone(category_id('google','6015'))
        self.assertEqual(category_id('apple','book'),'6018')
        self.assertEqual(category_id('apple','game_arcade'),'7003')
        self.assertEqual(category_id('apple','Music'),'6011')
        self.assertEqual(category_id('apple','game_music'),'7011')
        self.assertEqual(category_id('google','sports'),'SPORTS')
        self.assertEqual(category_id('google','game_sports'),'GAME_SPORTS')
        self.assertIsNone(category_id('google','AI'))

    def test_game_children_and_new_payload_ids(self):
        self.assertIn('game_puzzle',category_aliases('apple','6014'))
        self.assertNotIn('game_puzzle',category_aliases('apple','__apps__'))
        self.assertEqual(listing_category('apple',dict(category='本地化名称',category_id=6015)),'6015')
        self.assertEqual(listing_category('apple',dict(category='Games',category_id=6014,genre_ids=['6014','7012'])),'7012')
        self.assertEqual(listing_category('google',dict(category='财经',category_id='FINANCE')),'FINANCE')

    def test_offline_country_catalog_support_is_explicit(self):
        catalog=public_catalog()
        self.assertEqual(len(catalog['countries']),249)
        self.assertEqual(len({c['code'] for c in catalog['countries']}),249)
        china=next(c for c in catalog['countries'] if c['code']=='cn')
        self.assertEqual(china['region'],'east_asia')
        self.assertTrue(china['apple_supported'])
        self.assertTrue(any(not c['apple_supported'] for c in catalog['countries']))


class CatalogDatabaseTest(MySQLTestCase):
    def test_counts_and_filters_match_legacy_and_numeric_records_without_double_counting(self):
        rows=[('apple','101','A','finance'),('apple','102','B','6015'),('apple','103','C','Finance'),
              ('apple','104','D','game_puzzle'),('apple','105','E',None),('apple','106','F','not-a-category'),
              ('apple','107','G','Music'),('apple','108','H','game_music'),
              ('google','com.a.app','I','finance'),('google','com.b.app','J','FINANCE'),('google','com.c.app','K','game_sports')]
        add_apps(self.db,rows,'fixture')
        apple=category_counts(self.db,'apple')
        self.assertEqual((apple['total'],apple['counts']['6015'],apple['counts']['6014'],apple['unknown'],apple['apps']),(8,3,2,2,4))
        with patch.dict(os.environ,{'APPMON_API_TOKEN':''}):
            client=TestClient(create_app(self.cfg))
        result=client.get('/api/apps?store=apple&category=6015').json()
        self.assertEqual({r['app_id'] for r in result['items']},{'101','102','103'})
        self.assertTrue(all(r['category_name']=='财务' for r in result['items']))
        self.assertEqual(len(client.get('/api/apps?store=apple&category=6014').json()['items']),2)
        self.assertEqual(len(client.get('/api/apps?store=apple&category=__unknown__').json()['items']),2)
        self.assertEqual(len(client.get('/api/apps?store=google&category=FINANCE').json()['items']),2)
        self.assertEqual(client.get('/api/apps?store=apple&category=FINANCE').status_code,200) # Legacy alias remains accepted.
        self.assertEqual(client.get('/api/apps?store=apple&category=ART_AND_DESIGN').status_code,400)
        self.assertEqual(client.get('/api/categories/counts?store=apple').json()['counts']['6015'],3)
        self.assertEqual(client.get('/api/catalog').status_code,200)

    def test_chart_target_rejects_mixed_store_and_nonofficial_classifications(self):
        base=dict(kind='chart',store='apple',country='us',language='en')
        self.assertTrue(add_observation_target(self.db,base|{'category':'6026'})['id'])
        for category in ('AI','FINANCE','6022','9999'):
            with self.assertRaises(ValueError):
                add_observation_target(self.db,base|{'category':category})
        with self.assertRaises(ValueError):
            add_observation_target(self.db,base|{'store':'google','category':'6015'})


if __name__=='__main__':
    unittest.main()
