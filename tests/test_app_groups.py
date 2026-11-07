import json
import time
import unittest
from datetime import datetime,timezone
from unittest.mock import Mock
from fastapi.testclient import TestClient
from appmon import app_groups as groups,app_permissions,metric_facts
from appmon.db import add_apps,dump
from appmon.intelligence import watch
from appmon.stores import FetchError
from appmon.web import create_app
from test_monitor import MySQLTestCase


def permission_bytes(value):
    return (')]}\'\n\n'+json.dumps([['wrb.fr','xdSrCf',json.dumps(value)]])).encode()


class GroupRulesTest(unittest.TestCase):
    def test_permissions_parse_rpc_and_distinguish_missing_from_empty(self):
        raw=permission_bytes([[['Camera',None,[[None,'take pictures and videos']],None],['Location',None,[[None,'precise location']],None]]])
        self.assertEqual(app_permissions.parse(raw),{'Camera':['take pictures and videos'],'Location':['precise location']})
        self.assertEqual(app_permissions.parse(permission_bytes([])),{})
        for value in (None,[None],{'data':[]}):
            with self.assertRaises(FetchError):app_permissions.parse(permission_bytes(value))
        with self.assertRaises(FetchError):app_permissions.parse(b'<html>challenge</html>')

    def test_metric_rows_require_explicit_currency_and_estimate_status(self):
        today=datetime.now(timezone.utc).date().isoformat()
        row=dict(store='apple',app_id='123',country='us',date=today,downloads=0,revenue='2.50',currency='USD',is_estimate=True)
        self.assertEqual(len(metric_facts.prepare({'rows':[row]})),2)
        self.assertEqual(len(metric_facts.prepare({'rows':[row|{'is_estimate':' FALSE ','currency':' usd '}]})),2)
        for change in ({'is_estimate':None},{'currency':''},{'downloads':'NaN'},{'date':'2999-01-01'}):
            with self.assertRaises(ValueError):metric_facts.prepare({'rows':[row|change]})


class GroupDashboardTest(MySQLTestCase):
    def test_yesterday_reviews_count_publication_date_in_the_member_market(self):
        from datetime import timedelta
        from appmon.intelligence import add_observation_target
        owner = self.seed()
        midnight = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        target = dict(store='google',app_id='com.loan.example',country='th',language='en',kind='reviews')
        identifier = add_observation_target(self.db,target)['id']
        run = self.db.execute('''INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count)
            VALUES(%s,%s,'ok','test','v1','recent_reviews_bounded',3)''',(identifier,midnight.timestamp()+1)).lastrowid
        self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,identifier))
        for i,hours in enumerate((-25,-1,1)):
            local = (midnight+timedelta(hours=hours)).astimezone(timezone(timedelta(hours=8)))
            self.db.execute('''INSERT INTO reviews(store,app_id,country,language,review_id,rating,content,published_at,first_seen,last_seen,payload)
                VALUES('google','com.loan.example','th','en',%s,5,'review',%s,0,0,'{}')''',(str(i),local.isoformat()))
        data=groups.dashboard(self.db,self.cfg,owner['id'],store='google')['items']
        self.assertEqual({r['country']:r['yesterday_reviews'] for r in data},{'th':1,'vn':None})
        self.db.execute('DELETE FROM reviews')
        self.assertEqual(groups.dashboard(self.db,self.cfg,owner['id'],store='google',country='th')['items'][0]['yesterday_reviews'],0)

    def test_row_period_changes_use_complete_comparable_days_and_group_market_shares(self):
        from datetime import timedelta
        owner = self.seed()
        day = datetime.now(timezone.utc).date()
        for country, values in [('th',(50,100)),('vn',(0,300))]:
            for date, value in zip((day-timedelta(days=1),day),values):
                self.db.execute("INSERT INTO app_metric_facts VALUES('google','com.loan.example',%s,%s,'downloads',0,%s,'',0)",(country,date,value))
        rows=groups.dashboard(self.db,self.cfg,owner['id'],store='google',country='th',days=1)['items']
        fact=rows[0]['metrics'][0]
        self.assertEqual(fact['change_pct'],100)
        self.assertEqual(fact['market_mix'],[{'country':'th','pct':25},{'country':'vn','pct':75}])
        seven=groups.dashboard(self.db,self.cfg,owner['id'],store='google',days=7)['items']
        self.assertTrue(all(r['metrics'][0]['change_pct'] is None for r in seven))

    def test_one_page_returns_identity_top_words_and_individual_chart_positions(self):
        from appmon.keywords import add_targets
        from appmon.intelligence import add_observation_target
        owner = self.seed()
        market = dict(store='google', country='th', language='en')
        now = time.time()
        self.db.execute("UPDATE apps SET category='TOOLS' WHERE store='google' AND app_id='com.loan.example'")
        self.db.execute('''INSERT INTO listings(store,app_id,country,language,state,checked_at,payload)
            VALUES('google','com.loan.example','th','en','available',%s,%s)''',
            (now, dump({'developer':'Loan Studio','icon':'https://example.org/icon.png','category_id':'FINANCE','category':'Finance'})))
        for word, pos, age in [('loan',2,0),('budget',2,0),('cash',5,0),('pay',9,0),('old',1,90000)]:
            target = add_targets(self.db,[market|{'keyword':word}])['ids'][0]
            run = self.db.execute('''INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,requested_depth,returned_count,coverage)
                VALUES(%s,%s,'ok','google','v1',50,20,'first_response')''',(target,now-age)).lastrowid
            self.db.execute("INSERT INTO keyword_ranks VALUES(%s,'com.loan.example',%s,'Loan')",(run,pos))
            self.db.execute('UPDATE keyword_targets SET latest_success_id=%s WHERE id=%s',(run,target))
        for board,category,pos,age in [('top-free','',12,0),('top-free','FINANCE',4,0),('top-free','TOOLS',1,0),('top-paid','',None,0),('top-grossing','',1,90000)]:
            target = add_observation_target(self.db,market|dict(kind='chart',collection=board,category=category))['id']
            run = self.db.execute('''INSERT INTO observation_runs(target_id,observed_at,outcome,source,parser_version,coverage,returned_count)
                VALUES(%s,%s,'ok','google','v1','returned_chart_range',20)''',(target,now-age)).lastrowid
            if pos:
                self.db.execute("INSERT INTO observation_items VALUES(%s,'com.loan.example',%s,'{}')",(run,pos))
            self.db.execute('UPDATE observation_targets SET latest_success_id=%s WHERE id=%s',(run,target))
        rows=groups.dashboard(self.db,self.cfg,owner['id'],store='google')['items']
        th=next(r for r in rows if r['country']=='th');vn=next(r for r in rows if r['country']=='vn')
        self.assertEqual((th['developer'],th['icon']),('Loan Studio','https://example.org/icon.png'))
        self.assertEqual((th['category'],vn['category']),('FINANCE','TOOLS'))
        self.assertEqual((th['keyword_count'],th['keyword_top3_count']),(4,2))
        self.assertEqual([(r['keyword'],r['position']) for r in th['top_keywords']],[('budget',2),('loan',2),('cash',5)])
        boards={(r['collection'],r['category']):(r['sampled'],r['position']) for r in th['chart_positions']}
        self.assertEqual(boards,{('top-free',''):(True,12),('top-free','FINANCE'):(True,4),('top-paid',''):(True,None),('top-grossing',''):(False,None)})
        self.assertEqual((vn['keyword_count'],vn['top_keywords'],vn['chart_positions']),(None,[],[]))

    def test_auto_repair_only_enqueues_missing_jobs_for_enabled_members(self):
        self.seed()
        self.db.execute("UPDATE watchlist SET enabled=0 WHERE country='vn'")
        self.db.execute("DELETE FROM jobs WHERE store='google'")
        self.db.execute("UPDATE jobs SET due=123456,token='owned',lease_until=777")
        self.assertEqual(groups.ensure_verification_jobs(self.db),1)
        self.assertEqual(groups.ensure_verification_jobs(self.db),0)
        self.assertEqual(self.db.execute("SELECT count(*) n FROM jobs WHERE country='vn'").fetchone()['n'],0)
        self.assertEqual(self.db.execute("SELECT due,token,lease_until FROM jobs WHERE store='apple'").fetchone(),{'due':123456,'token':'owned','lease_until':777})

    def test_classification_uses_store_reference_and_scoped_reversible_adjustments(self):
        owner=self.seed();gid=owner['id'];now=time.time()
        common=dict(store='google',app_id='com.loan.example',language='en')
        groups.import_references(self.db,[common|dict(country='th',source_status='off'),common|dict(country='vn',source_status='on')])
        self.db.execute("INSERT INTO listings(store,app_id,country,language,state,checked_at,misses) VALUES('google','com.loan.example','th','en','suspected_missing',%s,1)",(now,))
        self.db.execute('UPDATE watchlist SET enabled=0')
        before=groups.dashboard(self.db,self.cfg,gid)
        th=next(r for r in before['items'] if r['country']=='th' and r['store']=='google')
        vn=next(r for r in before['items'] if r['country']=='vn')
        self.assertEqual((th['classification'],th['classification_source'],th['state']),('region_unavailable','corroborated','suspected_missing'))
        self.assertEqual((vn['classification'],vn['classification_source']),('available','reference'))
        self.assertEqual(before['stats']['classifications'],{'region_unavailable':1,'unverified':1,'available':1})
        second=groups.create(self.db,{'name':'另一组'});groups.add_members(self.db,second['id'],{'members':[common|{'country':'th'}]})
        change=dict(revision=0,status_override='available',permission_overrides={'Camera':True,'SMS':False})
        self.assertEqual(groups.adjust_member(self.db,gid,th['id'],change,'tester')['revision'],1)
        with self.assertRaises(ValueError):groups.adjust_member(self.db,gid,th['id'],change,'tester')
        with self.assertRaises(ValueError):groups.adjust_member(self.db,second['id'],th['id'],change,'tester')
        filtered=groups.dashboard(self.db,self.cfg,gid,classification='available')
        self.assertEqual(filtered['total'],2)
        adjusted=next(r for r in filtered['items'] if r['id']==th['id'])
        self.assertEqual((adjusted['classification_source'],adjusted['updated_by'],adjusted['permission_overrides']),('adjustment','tester',change['permission_overrides']))
        self.assertEqual(groups.member_detail(self.db,gid,th['id'])['classification'],'available')
        self.assertEqual(groups.dashboard(self.db,self.cfg,second['id'])['items'][0]['classification'],'region_unavailable')
        self.assertEqual(self.db.execute('SELECT SUM(enabled) n FROM watchlist').fetchone()['n'],0)
        groups.adjust_member(self.db,gid,th['id'],dict(revision=1,status_override=None,permission_overrides={}),'tester')
        self.assertEqual(groups.member_detail(self.db,gid,th['id'])['classification_source'],'corroborated')
        self.db.execute("UPDATE listings SET state='available'")
        self.assertEqual(groups.member_detail(self.db,gid,th['id'])['classification_source'],'store')
        groups.remove_members(self.db,gid,{'ids':[th['id']]})
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_group_adjustments').fetchone()['n'],0)

    def test_inline_adjustment_api_enforces_write_permission_and_store_support(self):
        owner=self.seed();gid=owner['id'];apple=self.db.execute("SELECT id FROM app_group_members WHERE store='apple'").fetchone()['id']
        url=f'/api/groups/{gid}/members/{apple}/adjustment'
        change=dict(revision=0,status_override='available',permission_overrides={})
        api=TestClient(create_app(self.cfg));self.login_admin(api)
        self.assertEqual(api.put(url,json=change|{'permission_overrides':{'SMS':True}}).status_code,400)
        self.assertEqual(api.put(url,json=change).status_code,200)
        self.assertEqual(api.get(f'/api/groups/{gid}/dashboard?classification=available').json()['total'],1)
        reader=TestClient(create_app(self.cfg));reader.post('/api/auth/register',json={'username':'status-reader','password':'password-123'})
        login=reader.post('/api/auth/login',json={'username':'status-reader','password':'password-123'});reader.headers['X-CSRF-Token']=login.json()['csrf_token']
        self.assertEqual(reader.put(url,json=change|{'revision':1}).status_code,403)

    def seed(self):
        group=groups.create(self.db,dict(name='现金贷观察',description='跨国'))
        groups.add_members(self.db,group['id'],{'members':[
            dict(store='google',app_id='com.loan.example',country='th',language='en'),
            dict(store='google',app_id='com.loan.example',country='vn',language='en'),
            dict(store='apple',app_id='123',country='th',language='en')]})
        return group

    def test_many_to_many_groups_preserve_history_and_existing_monitor_settings(self):
        watch(self.db,dict(store='google',app_id='com.loan.example',country='th',language='en',group_name='原分类',refresh_hours=3))
        self.db.execute('UPDATE watchlist SET enabled=0')
        owner=self.seed();other=groups.create(self.db,dict(name='第二组'))
        value={'members':[dict(store='google',app_id='com.loan.example',country='th',language='en')]}
        self.assertEqual(groups.add_members(self.db,other['id'],value)['added'],1)
        self.assertEqual(groups.add_members(self.db,other['id'],value)['added'],0)
        watched=self.db.execute("SELECT * FROM watchlist WHERE app_id='com.loan.example' AND country='th'").fetchone()
        self.assertEqual((watched['enabled'],watched['group_name'],watched['refresh_hours']),(0,'原分类',3))
        member=self.db.execute('SELECT id FROM app_group_members WHERE group_id=%s',(other['id'],)).fetchone()['id']
        groups.remove_members(self.db,other['id'],{'ids':[member]})
        self.assertEqual(groups.dashboard(self.db,self.cfg,owner['id'])['stats']['targets'],3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM watchlist').fetchone()['n'],3)
        with self.assertRaises(ValueError):groups.add_members(self.db,owner['id'],{'members':[value['members'][0],{'store':'apple','app_id':'bad','country':'th'}]})
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_group_members').fetchone()['n'],3)

    def test_dashboard_country_status_reference_and_events_never_mix_scopes(self):
        owner=self.seed();now=time.time()
        self.db.execute('''INSERT INTO listings(store,app_id,country,language,state,checked_at,last_success,payload)
            VALUES('google','com.loan.example','th','en','available',%s,%s,%s)''',(now,now,dump({'rating':4.6,'ratings':120,'installs':1000})))
        groups.import_references(self.db,[dict(store='apple',app_id='123',country='th',language='en',source_status='off',source_generated_at='import time')])
        for country in ('th','vn','us'):
            self.db.execute("INSERT INTO events(store,app_id,country,language,observed_at,kind,changes) VALUES('google','com.loan.example',%s,'en',%s,'region_unavailable','{}')",(country,now))
        all_data=groups.dashboard(self.db,self.cfg,owner['id'])
        self.assertEqual((all_data['stats']['apps'],all_data['stats']['countries'],all_data['stats']['targets']),(2,2,3))
        self.assertEqual(all_data['stats']['states'],{'available':1,'unverified':2})
        self.assertEqual(all_data['event_counts']['region_unavailable'],2)
        apple=next(r for r in all_data['items'] if r['store']=='apple')
        self.assertEqual((apple['state'],apple['reference_state'],apple['permission_state']),('unverified','off','unsupported'))
        self.assertEqual(groups.dashboard(self.db,self.cfg,owner['id'],country='th')['event_counts']['region_unavailable'],1)
        self.assertEqual(groups.dashboard(self.db,self.cfg,owner['id'],q='no match')['total'],0)

    def test_regional_metric_facts_keep_zero_missing_and_currencies_separate(self):
        owner=self.seed();day=datetime.now(timezone.utc).date().isoformat()
        groups.add_members(self.db,owner['id'],{'members':[dict(store='google',app_id='com.loan.example',country='th',language='th')]})
        data={'rows':[
            dict(store='google',app_id='com.loan.example',country='th',date=day,downloads=0,revenue=10,currency='USD',is_estimate=False),
            dict(store='google',app_id='com.loan.example',country='vn',date=day,downloads=8,revenue=20,currency='VND',is_estimate=True)]}
        groups.import_metrics(self.db,owner['id'],data);groups.import_metrics(self.db,owner['id'],data)
        result=groups.dashboard(self.db,self.cfg,owner['id'])
        self.assertEqual(len(result['money']),4)
        usd=next(r for r in result['money'] if r['metric']=='revenue' and r['currency']=='USD')
        self.assertEqual(float(usd['value']),10);self.assertEqual(usd['markets'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_metric_facts').fetchone()['n'],4)
        apple=next(r for r in result['items'] if r['store']=='apple');self.assertEqual(apple['metrics'],[])
        self.assertTrue(any(r['metric']=='downloads' and float(r['value'])==0 for r in result['money']))
        with self.assertRaises(ValueError):groups.import_metrics(self.db,owner['id'],{'rows':[data['rows'][0]|{'country':'us'}]})
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM app_metric_facts').fetchone()['n'],4)

    def test_permissions_failures_preserve_last_success_and_never_change_listing(self):
        owner=self.seed()
        self.db.execute("UPDATE app_permission_targets SET enabled=0 WHERE country='vn'")
        client=Mock();client.request.return_value=Mock(status_code=200,content=permission_bytes([[['Camera',None,[[None,'take pictures']],None]]]))
        self.assertEqual(app_permissions.work(self.cfg,self.db,1,client)['ok'],1)
        before=self.db.execute("SELECT * FROM app_permission_targets WHERE country='th'").fetchone()
        self.db.execute('UPDATE app_permission_targets SET due=0')
        client.request.side_effect=FetchError('temporary failure')
        self.assertEqual(app_permissions.work(self.cfg,self.db,1,client)['error'],1)
        after=self.db.execute("SELECT * FROM app_permission_targets WHERE country='th'").fetchone()
        self.assertEqual((after['payload'],after['success_at']),(before['payload'],before['success_at']))
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM listings').fetchone()['n'],0)
        member=self.db.execute("SELECT id FROM app_group_members WHERE store='google' AND country='th'").fetchone()['id']
        detail=groups.member_detail(self.db,owner['id'],member)
        self.assertEqual(detail['permission_state'],'error');self.assertEqual(detail['permission']['permission_count'],1)

    def test_bootstrap_and_bulk_collection_are_deduplicated(self):
        watch(self.db,dict(store='google',app_id='com.first.app',country='th',language='en',group_name='已有分类'))
        groups.bootstrap(self.db);groups.bootstrap(self.db)
        owner=groups.list_groups(self.db)[0]
        self.assertEqual(owner['targets'],1)
        result=groups.collect(self.db,owner['id'],{'kinds':['details','permissions','charts','reviews','keywords'],'keywords':['loan']})
        self.assertEqual(result['queued'],{'details':1,'permissions':1,'charts':3,'reviews':1,'keywords':1})
        groups.collect(self.db,owner['id'],{'kinds':['charts','reviews','keywords'],'keywords':['loan']})
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM observation_targets').fetchone()['n'],4)
        self.assertEqual(self.db.execute('SELECT COUNT(*) n FROM keyword_targets').fetchone()['n'],1)

    def test_group_api_permissions_and_revision_conflicts(self):
        api=TestClient(create_app(self.cfg))
        self.assertEqual(api.get('/api/groups').status_code,401)
        self.login_admin(api)
        created=api.post('/api/groups',json={'name':'组合'});self.assertEqual(created.status_code,200,created.text)
        identifier=created.json()['id']
        self.assertEqual(api.put(f'/api/groups/{identifier}',json={'name':'新名称','revision':1}).status_code,200)
        self.assertEqual(api.put(f'/api/groups/{identifier}',json={'name':'过期修改','revision':1}).status_code,400)
        response=api.post(f'/api/groups/{identifier}/members',json={'text':'google,com.example.app,th,en\napple,123,th,en'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(api.get(f'/api/groups/{identifier}/dashboard').json()['total'],2)
        member=TestClient(create_app(self.cfg));member.post('/api/auth/register',json={'username':'group-reader','password':'password-123'})
        login=member.post('/api/auth/login',json={'username':'group-reader','password':'password-123'});member.headers['X-CSRF-Token']=login.json()['csrf_token']
        self.assertEqual(member.post('/api/groups',json={'name':'forbidden'}).status_code,403)

    def test_sampling_distinguishes_zero_hits_from_unknown_and_retains_lost_keywords(self):
        from appmon.keywords import add_targets
        owner=self.seed();now=time.time()
        identifier=add_targets(self.db,[dict(store='google',country='th',language='en',keyword='loan')])['ids'][0]
        ids=[]
        for n in range(2):
            run=self.db.execute('''INSERT INTO keyword_runs(target_id,observed_at,outcome,source,parser_version,requested_depth,returned_count,coverage)
                VALUES(%s,%s,'ok','official','v1',50,50,'first_response')''',(identifier,now-10+n)).lastrowid
            ids.append(run)
            if n==0:self.db.execute("INSERT INTO keyword_ranks VALUES(%s,'com.loan.example',3,'Loan')",(run,))
        self.db.execute('UPDATE keyword_targets SET latest_success_id=%s,latest_attempt_id=%s,previous_success_id=%s WHERE id=%s',(ids[1],ids[1],ids[0],identifier))
        data=groups.dashboard(self.db,self.cfg,owner['id'])
        th=next(r for r in data['items'] if r['store']=='google' and r['country']=='th')
        vn=next(r for r in data['items'] if r['country']=='vn')
        self.assertEqual(th['keyword_count'],0);self.assertIsNone(vn['keyword_count'])
        detail=groups.member_detail(self.db,owner['id'],th['id'])
        self.assertEqual(detail['keywords'][0]['keyword'],'loan')
        self.assertIsNone(detail['keywords'][0]['position']);self.assertTrue(detail['keywords'][0]['fresh'])
        trend=groups.rank_history(self.db,owner['id'],th['id'],'keyword',identifier)
        self.assertEqual([p['value'] for p in trend['points']],[3,None])
        with self.assertRaises(ValueError):groups.rank_history(self.db,owner['id'],vn['id'],'keyword',identifier)

    def test_authorized_estimates_map_to_store_ids_without_guessing_currency(self):
        from appmon.marketdata_source import import_products,save_estimate_facts
        owner=self.seed();today=datetime.now(timezone.utc).date().isoformat()
        with self.db:
            import_products(self.db,[dict(id=567,store='google_play',package_name='com.loan.example',name='Loan')])
            save_estimate_facts(self.db,'567',{today:{'TH':{'downloads':0,'revenue':'9.25'}}})
        facts=self.db.execute('SELECT * FROM app_metric_facts').fetchall()
        self.assertEqual(len(facts),1);self.assertEqual(facts[0]['metric'],'downloads')
        with self.db:save_estimate_facts(self.db,'567',{today:{'TH':{'downloads':0,'revenue':'9.25'}}},'THB')
        data=groups.dashboard(self.db,self.cfg,owner['id'])
        revenue=next(f for f in data['money'] if f['metric']=='revenue')
        self.assertEqual(revenue['currency'],'THB');self.assertTrue(revenue['is_estimate'])

    def test_reference_reports_remain_bound_to_app_market_and_can_schedule_selected_words(self):
        owner=self.seed();member=self.db.execute("SELECT * FROM app_group_members WHERE store='google' AND country='th'").fetchone()
        report=self.db.execute('''INSERT INTO keyword_reports(store,app_id,country,language,digest,filename,source,observed_at,imported_at,raw_path,summary)
            VALUES('google','com.loan.example','th','en',%s,'report.xlsx','导入报告',1,1,'raw',%s)''',
            ('a'*64,dump([{'sheet':'ASO关键词','rows':1,'title':'private metadata'}]))).lastrowid
        self.db.execute("INSERT INTO keyword_report_rows VALUES(%s,'ASO关键词',3,'cash loan',5,'10','20','{}')",(report,))
        rows=groups.reference_report(self.db,owner['id'],member['id'],report)
        self.assertEqual(rows['items'][0]['keyword'],'cash loan');self.assertNotIn('payload',rows['items'][0])
        other=self.db.execute("SELECT id FROM app_group_members WHERE country='vn'").fetchone()['id']
        with self.assertRaises(ValueError):groups.reference_report(self.db,owner['id'],other,report)
        detail=groups.member_detail(self.db,owner['id'],member['id'])
        self.assertNotIn('title',detail['reports'][0]['summary'][0]);self.assertEqual(len(detail['markets']),2)
        result=groups.collect(self.db,owner['id'],{'member_id':member['id'],'kinds':['keywords'],'keywords':['cash loan','cash loan']})
        self.assertEqual(result['queued']['keywords'],1)
        self.assertEqual(self.db.execute('SELECT country FROM keyword_targets').fetchall(),[{'country':'th'}])

    def test_group_monitoring_controls_invalidate_paused_leases_without_erasing_data(self):
        owner=self.seed();now=time.time()
        self.db.execute("UPDATE jobs SET token='old',lease_until=%s,failures=2,last_error='previous failure'",(now+600,))
        self.db.execute("UPDATE app_permission_targets SET token='old',lease_until=%s,payload='{}',success_at=%s,permission_count=0",(now+600,now))
        groups.control_monitoring(self.db,owner['id'],{'action':'pause','country':'th'})
        paused=self.db.execute("SELECT w.enabled,j.token,j.failures FROM watchlist w JOIN jobs j USING(store,app_id,country,language) WHERE w.country='th'").fetchall()
        self.assertTrue(all(not r['enabled'] and r['token'] is None and r['failures']==2 for r in paused))
        self.assertEqual(self.db.execute("SELECT enabled FROM watchlist WHERE country='vn'").fetchone()['enabled'],1)
        permission=self.db.execute("SELECT enabled,payload,success_at FROM app_permission_targets WHERE country='th'").fetchone()
        self.assertEqual(permission['payload'],'{}');self.assertIsNotNone(permission['success_at']);self.assertFalse(permission['enabled'])
        groups.control_monitoring(self.db,owner['id'],{'action':'resume','country':'th'})
        self.assertEqual(self.db.execute("SELECT COUNT(*) n FROM watchlist WHERE enabled=1").fetchone()['n'],3)
        self.assertEqual(self.db.execute("SELECT failures FROM jobs WHERE country='th' LIMIT 1").fetchone()['failures'],2)
