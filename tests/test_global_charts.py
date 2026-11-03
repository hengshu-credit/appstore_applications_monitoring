import json
import time
from appmon import global_charts
from appmon.intelligence import add_observation_target, claim_observation
from appmon.collection import work_plans
from test_monitor import MySQLTestCase


class GlobalChartsTest(MySQLTestCase):
    def test_country_boards_preserve_each_rank_and_report_missing_and_stale_separately(self):
        now=time.time()
        for country,position,at in [('us',1,now),('jp',7,now-3*86400)]:
            language='en' if country=='us' else 'ja'
            target=add_observation_target(self.db,dict(store='apple',country=country,language=language,kind='chart',collection='top-free'))['id']
            self.db.execute("INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count) VALUES(%s,%s,'ok','fixture','v1','returned_chart_range',10)",(target,at))
            run=self.db.cursor.lastrowid
            self.db.execute('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',(run,'123',position,json.dumps({'app_id':'123','name':'Test'})))
            self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,target))
        result=global_charts.report(self.db,'apple','us,jp,cn',collection='top-free,top-paid')
        self.assertEqual(result['requested_boards'],6);self.assertEqual(result['observed_boards'],2);self.assertEqual(result['fresh_boards'],1)
        self.assertEqual([result['countries'][i]['boards'][0]['items'][0]['position'] for i in (0,1)],[1,7])
        self.assertEqual(result['countries'][2]['boards'][0]['items'],[])
        old=global_charts.report(self.db,'apple','us',collection='top-free',as_of=now-86400)
        self.assertEqual(old['observed_boards'],0)

    def test_global_plan_deduplicates_derived_bases_and_preserves_existing_pauses(self):
        value={'store':'apple','countries':'us,jp','collections':'top-free,trending,new-grossing','depth':100}
        self.assertEqual(global_charts.preview(value)['total'],4)
        target=add_observation_target(self.db,dict(store='apple',country='us',language='en',kind='chart',depth=20,refresh_hours=6))['id']
        self.db.execute('UPDATE observation_targets SET enabled=0 WHERE id=%s',(target,))
        result=global_charts.create_plan(self.db,value);work_plans(self.db,20)
        row=self.db.execute('SELECT enabled,depth,refresh_hours FROM observation_targets WHERE id=%s',(target,)).fetchone()
        self.assertEqual((row['enabled'],row['depth'],row['refresh_hours']),(0,20,6))
        self.assertEqual(self.db.execute('SELECT status FROM collection_plans WHERE id=%s',(result['id'],)).fetchone()['status'],'complete')
        self.assertIsNone(claim_observation(self.db,60,target_id=target,refresh=True))
        with self.assertRaises(ValueError):global_charts.preview(value|{'countries':'zz'})
        with self.assertRaises(ValueError):global_charts.preview(value|{'countries':['us']})
        unsupported=global_charts.preview(value|{'collections':'new-free','category':'6015'})
        self.assertEqual(unsupported['total'],0)
