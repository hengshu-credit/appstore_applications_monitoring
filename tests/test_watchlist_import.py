import json
import unittest
from pathlib import Path
from appmon import watchlist_import, availability
from appmon.intelligence import watch
from appmon.db import add_apps
from appmon.web import create_app
from fastapi.testclient import TestClient
from test_monitor import MySQLTestCase


def snapshots():
    return {'google':dict(totalApps=2,isRunning=False,generatedAt='source time',apps=[
        dict(pkg='com.loan.example',name='Loan',cc='TH',liveStatus='off'),
        dict(pkg='com.loan.example',name='Loan',cc='VN',liveStatus='on')]),
        'apple':dict(totalApps=1,isRunning=False,apps=[dict(pkg='123456',name='Loan iOS',cc='TH',liveStatus='off')])}


class WatchlistValidationTest(unittest.TestCase):
    def test_retains_country_identity_and_rejects_partial_or_invalid_input(self):
        targets,rows,summary=watchlist_import.prepare(snapshots())
        self.assertEqual(len(targets),3)
        self.assertEqual(summary['google']['unique_apps'],1)
        self.assertEqual(summary['google']['countries'],2)
        self.assertEqual(rows[0]['source_status'],'off')
        for change in ('count','running','id'):
            value=snapshots()
            if change=='count':value['google']['totalApps']=3
            elif change=='running':value['apple']['isRunning']=True
            else:value['apple']['apps'][0]['pkg']='../../123'
            with self.assertRaises(ValueError):watchlist_import.prepare(value)


class WatchlistImportTest(MySQLTestCase):
    def test_idempotent_import_preserves_existing_settings_and_never_creates_status(self):
        watch(self.db,dict(store='google',app_id='com.existing.app',country='us',language='en',group_name='原分组',refresh_hours=3))
        old=self.db.execute('SELECT * FROM watchlist').fetchone()
        result=watchlist_import.import_watchlists(self.db,self.cfg,snapshots(),{'google':'a','apple':'b'})
        self.assertEqual((result['added'],result['targets'],result['missing']),(3,3,0))
        self.assertEqual(self.db.execute('SELECT * FROM watchlist WHERE id=%s',(old['id'],)).fetchone(),old)
        again=watchlist_import.import_watchlists(self.db,self.cfg,snapshots(),{'google':'a','apple':'b'})
        self.assertEqual((again['added'],again['existing']),(0,3))
        for table in ('listings','events','snapshots'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) n FROM {table}').fetchone()['n'],0)
        rows=availability.watched(self.db,group_name=watchlist_import.GROUP)
        self.assertEqual(rows['total'],3)
        self.assertTrue(all(r['state'] is None and r['enabled'] for r in rows['items']))
        self.assertEqual(availability.watched(self.db,store='apple',country='TH',group_name=watchlist_import.GROUP)['total'],1)
        self.assertEqual(availability.watched(self.db,q='com.loan')['total'],2)
        self.assertEqual(len(Path(result['mapping']).read_text(encoding='utf-8').splitlines()),3)

    def test_pagination_reaches_beyond_old_2000_limit_and_api_requires_login(self):
        rows=[('apple',str(100000+i),'Cash '+str(i),None) for i in range(2105)]
        add_apps(self.db,rows,'test')
        self.db.executemany('''INSERT INTO watchlist(store,app_id,country,language,group_name,created_at)
            VALUES('apple',%s,'th','th',%s,0)''',[(r[1],watchlist_import.GROUP) for r in rows])
        data=availability.watched(self.db,group_name=watchlist_import.GROUP,offset=2000,limit=100)
        self.assertEqual((data['total'],len(data['items']),data['has_more']),(2105,100,True))
        end=availability.watched(self.db,group_name=watchlist_import.GROUP,offset=2100,limit=100)
        self.assertEqual((len(end['items']),end['has_more']),(5,False))
        api=TestClient(create_app(self.cfg))
        self.assertEqual(api.get('/api/availability/watched').status_code,401)
        self.login_admin(api)
        response=api.get('/api/availability/watched',params={'group_name':watchlist_import.GROUP,'offset':2100})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['items']),5)
        self.assertEqual(api.get('/api/availability/watched?limit=10000').status_code,400)
