import json
import time
from appmon import charts
from appmon.intelligence import add_observation_target
from appmon.monitor import raw_save
from test_monitor import MySQLTestCase


class ChartDetailTest(MySQLTestCase):
    def seed(self):
        at=time.time()-100
        target=add_observation_target(self.db,dict(kind='chart',store='apple',country='cn',language='zh-cn',collection='top-paid'))['id']
        self.db.execute("INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count) VALUES(%s,%s,'ok','fixture','v1','returned_chart_range',3)",(target,at))
        run=self.db.cursor.lastrowid
        for i in range(1,4):
            self.db.execute('INSERT INTO observation_items VALUES(%s,%s,%s,%s)',(run,str(i),i,json.dumps({'name':f'App {i}','developer':'Chart developer'})))
        self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,target))
        return at

    def test_detail_includes_the_full_observed_range_and_market_scoped_metadata(self):
        at=self.seed()
        self.db.execute('INSERT INTO listings VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            ('apple','2','cn','zh-cn','available',at+1,at+1,0,json.dumps({'rating':4.8,'ratings':123,'price':6,'currency':'CNY','version':'2.0'}),'d'*64))
        self.db.execute('INSERT INTO listings VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            ('apple','1','us','en','available',at,at,0,json.dumps({'price':999}),'e'*64))
        r=charts.detail(self.db,'apple','cn','zh-cn','top-paid',q='App 2')
        self.assertEqual(r['chart_count'],3);self.assertEqual(r['total'],1)
        self.assertEqual(r['items'][0]['position'],2)
        self.assertEqual(r['items'][0]['metadata']['currency'],'CNY')
        self.assertEqual(r['items'][0]['metadata_observed_at'],at+1)
        all_rows=charts.detail(self.db,'apple','cn','zh-cn','top-paid')
        self.assertEqual(all_rows['items'][0]['metadata'],{})
        self.assertEqual(charts.detail(self.db,'apple','cn','zh-cn','top-paid',offset=2)['items'][0]['position'],3)

    def test_historical_detail_excludes_later_app_metadata(self):
        at=self.seed()
        for t,rating in ((at-100,3.5),(at+20,4.9)):
            self.db.execute('INSERT INTO snapshots(store,app_id,country,language,observed_at,digest,raw_path,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                ('apple','1','cn','zh-cn',t,'a'*64,'fixture.gz',json.dumps({'rating':rating})))
        r=charts.detail(self.db,'apple','cn','zh-cn','top-paid',as_of=at+10)
        self.assertEqual(r['items'][0]['metadata']['rating'],3.5)
        self.assertLess(r['items'][0]['metadata_observed_at'],r['latest_success']['observed_at'])
        with self.assertRaises(ValueError):charts.detail(self.db,'apple','cn','zh-cn',limit=500)

    def test_details_reuse_prices_and_category_in_the_saved_store_chart(self):
        at=self.seed()
        raw={'feed':{'entry':[{'id':{'attributes':{'im:id':'1'}},'im:price':{'attributes':{'amount':'6.00','currency':'CNY'}},
            'category':{'attributes':{'im:id':'6014','label':'Games'}}}]}}
        path=raw_save(self.cfg['data_dir'],json.dumps(raw).encode())
        self.db.execute("UPDATE observation_runs SET source='apple-rss-chart',raw_path=%s",(path,))
        result=charts.detail(self.db,'apple','cn','zh-cn','top-paid',cfg=self.cfg)
        row=result['items'][0]
        self.assertEqual(row['metadata']['price'],6)
        self.assertEqual(row['metadata']['category_id'],'6014')
        self.assertEqual(row['metadata_origin'],'chart')
        self.assertEqual(row['metadata_observed_at'],at)
