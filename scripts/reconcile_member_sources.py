"""Idempotently queue member-name searches and matching category charts."""
import hashlib
import json
import time
from pathlib import Path
from appmon import chart_enrichment, collection
from appmon.catalog import listing_category, BY_ID, CATALOG, APPLE_COUNTRIES
from appmon.charts import capabilities
from appmon.locales import language_for
from appmon.config import load
from appmon.db import connect, dump
from appmon.keywords import validate_target


def main():
    cfg=load();db=connect(cfg,initialize=False)
    try:
        global_plan=chart_enrichment.start(db)
        # Also register every supported category/base-source combination exposed
        # by the selector. Derived views reuse their base source, never duplicate it.
        extra=[]
        for store in ('google','apple'):
            bases={s['base_collection'] for s in capabilities(store) if s['category_supported']}
            for country in CATALOG['countries']:
                code=country['code']
                if store=='apple' and code not in APPLE_COUNTRIES:continue
                for category in CATALOG['categories'][store]:
                    if category['legacy']:continue
                    for board in sorted(bases):
                        extra.append((store,code,language_for(store,code),board,category['id']))
        extra_created=0
        for start in range(0,len(extra),1000):
            with db:
                result=db.executemany('''INSERT INTO observation_targets(kind,store,app_id,country,language,collection,category,depth,refresh_hours)
                    VALUES('chart',%s,'',%s,%s,%s,%s,50,24) ON DUPLICATE KEY UPDATE id=observation_targets.id''',extra[start:start+1000])
                extra_created+=result.rowcount
        from appmon.collection_batch import touch
        touch(db)
        rows=db.execute('''SELECT DISTINCT m.store,m.app_id,m.country,m.language,a.category,
            COALESCE(l.payload->>'$.name',a.name) name,
            JSON_OBJECT('category_id',l.payload->'$.category_id','category',l.payload->'$.category','genre_ids',l.payload->'$.genre_ids') metadata
            FROM app_group_members m LEFT JOIN apps a USING(store,app_id)
            LEFT JOIN listings l USING(store,app_id,country,language)''').fetchall()
        tasks={};skipped=0
        for row in rows:
            market={k:row[k] for k in ('store','country','language')}
            category=listing_category(row['store'],json.loads(row['metadata'])) or row['category']
            categories=['']
            if category in BY_ID[row['store']] and not BY_ID[row['store']][category]['legacy']:categories.append(category)
            for cat in categories:
                for board in chart_enrichment.COLLECTIONS:
                    task=market|dict(kind='chart',collection=board,category=cat,depth=50,refresh_hours=24)
                    tasks[dump(task)]=task
            try:word=validate_target(market|dict(keyword=row['name'],depth=200))
            except ValueError:skipped+=1;continue
            task=word|{'kind':'keywords'};tasks[dump(task)]=task
        missing=[]
        for task in tasks.values():
            m=tuple(task[k] for k in ('store','country','language'))
            if task['kind']=='chart':
                old=db.execute("SELECT id FROM observation_targets WHERE kind='chart' AND store=%s AND country=%s AND language=%s AND collection=%s AND category=%s",
                    (*m,task['collection'],task['category'])).fetchone()
            else:
                old=db.execute('SELECT id FROM keyword_targets WHERE store=%s AND country=%s AND language=%s AND keyword=%s',(*m,task['keyword'])).fetchone()
            if not old:missing.append(task)
        receipts=[]
        for start in range(0,len(missing),500):
            batch=missing[start:start+500]
            key=hashlib.sha256(dump(batch).encode()).hexdigest()
            receipts.append(collection.enqueue(db,dict(source='member-source-reconciliation',request_id=key,name='应用分组分类榜与名称关键词补采',tasks=batch)))
        # The shared consumer persists checkpoints; safely finish the small intake
        # batches while leaving actual HTTP to normal rate-limited collectors.
        for _ in range(len(receipts)*30):
            if not receipts:break
            states=db.execute('SELECT status FROM collection_plans WHERE id IN ('+','.join('%s' for _ in receipts)+')',tuple(r['id'] for r in receipts)).fetchall()
            if all(r['status'] in ('complete','error') for r in states):break
            collection.work_plans(db,500)
        result=dict(at=time.time(),global_category_plan=global_plan,supported_category_combinations=len(extra),additional_category_targets_created=extra_created,member_targets=len(rows),missing_names=skipped,
            new_charts=sum(t['kind']=='chart' for t in missing),new_keywords=sum(t['kind']=='keywords' for t in missing),plans=receipts,
            plan_states=db.execute('SELECT id,status,completed,total,error FROM collection_plans WHERE id IN ('+','.join('%s' for _ in receipts)+')',tuple(r['id'] for r in receipts)).fetchall() if receipts else [],
            category_progress=db.execute("SELECT store,COUNT(*) targets,SUM(enabled) enabled,SUM(latest_success_id IS NOT NULL) sampled FROM observation_targets WHERE kind='chart' AND category NOT IN ('','APPLICATION') GROUP BY store").fetchall())
        path=Path(cfg['data_dir'])/'exports'/'member-source-reconciliation.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True,default=str),flush=True)
    finally:db.close()


if __name__=='__main__':main()
