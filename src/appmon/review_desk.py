"""Internal review triage; developer replies are never published from this module."""
import hashlib
import json
import time
from .db import dump
from .intelligence import market, text

DIGEST_SQL = "SHA2(CONCAT(r.rating,CHAR(0),r.content,CHAR(0),COALESCE(r.title,''),CHAR(0),COALESCE(r.version,'')),256)"
STATUS_SQL = f"CASE WHEN w.status='closed' AND w.review_digest<>{DIGEST_SQL} THEN 'reopened' ELSE COALESCE(w.status,'open') END"
JOIN = ' FROM reviews r LEFT JOIN review_workflow w USING(store,app_id,country,language,review_id) LEFT JOIN apps a ON a.store=r.store AND a.app_id=r.app_id '


def digest(review):
    return hashlib.sha256('\0'.join(str(review.get(k) or '') for k in ('rating','content','title','version')).encode()).hexdigest()


def desk(db, filters):
    m = market(filters, require_app=False)
    clauses = ['r.store=%s', 'r.country=%s', 'r.language=%s']
    args = [m['store'], m['country'], m['language']]
    for field in ('app_id', 'version', 'rating'):
        if filters.get(field) not in (None, ''):
            clauses.append(f'r.{field}=%s')
            args.append(filters[field])
    if filters.get('status'):
        if filters['status'] not in ('open','closed','reopened'):
            raise ValueError('未知处理状态')
        clauses.append(STATUS_SQL + '=%s')
        args.append(filters['status'])
    if filters.get('q'):
        clauses.append('(r.content LIKE %s OR r.title LIKE %s)')
        args.extend(['%' + str(filters['q'])[:255] + '%'] * 2)
    if filters.get('label'):
        clauses.append('JSON_CONTAINS(w.labels,JSON_QUOTE(%s))')
        args.append(str(filters['label'])[:60])
    if filters.get('reply_status'):
        reply=filters['reply_status']
        if reply not in ('unknown','not_observed','replied'):raise ValueError('未知回复状态')
        exists="JSON_CONTAINS_PATH(r.payload,'one','$.reply')"
        content="COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.reply')),'null'),'')"
        clauses.append(exists+'=0' if reply=='unknown' else exists+"=1 AND "+content+("=''" if reply=='not_observed' else "<>''"))
    if filters.get('since') is not None:
        clauses.append('r.first_seen>=%s')
        args.append(filters['since'])
    where = ' WHERE ' + ' AND '.join(clauses)
    offset = filters.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('分页偏移无效')
    summary = db.execute('SELECT COUNT(*) total,AVG(r.rating) mean_review_rating,MIN(r.first_seen) oldest_observation,MAX(r.last_seen) latest_observation' + JOIN + where, args).fetchone()
    rating_counts = db.execute('SELECT r.rating,count(*) count' + JOIN + where + ' GROUP BY r.rating ORDER BY r.rating', args).fetchall()
    daily = db.execute('SELECT FLOOR(r.first_seen/86400)*86400 day,count(*) count' + JOIN + where + ' GROUP BY day ORDER BY day DESC LIMIT 90', args).fetchall()
    rows = db.execute('''SELECT r.*,a.name app_name,COALESCE(w.owner,'') owner,COALESCE(w.labels,'[]') labels,
        COALESCE(w.note,'') note,COALESCE(w.reply_draft,'') reply_draft,COALESCE(w.revision,0) revision,
        ''' + STATUS_SQL + ' workflow_status' + JOIN + where +
        ' ORDER BY r.last_seen DESC,r.app_id,r.review_id LIMIT 51 OFFSET %s', (*args, offset)).fetchall()
    for row in rows:
        payload = json.loads(row['payload'])
        row['labels'] = json.loads(row['labels'])
        row['public_reply'] = payload.get('reply')
        row['public_reply_status'] = ('replied' if payload.get('reply') else 'not_observed') if 'reply' in payload else 'unknown'
        row['source'] = f"{row['store']}-public-reviews"
    return {'items': rows[:50], 'summary': summary, 'ratings': rating_counts, 'daily': list(reversed(daily)),
            'next_offset': offset + 50 if len(rows)>50 else None,
            'scope': '当前筛选内已采集评论；趋势按首次观察日期。评论平均星级不是商店累计评分。'}


def save_case(db, value):
    m = market(value)
    review_id = text(value.get('review_id'),255)
    key = (m['store'],m['app_id'],m['country'],m['language'],review_id)
    status = value.get('status','open')
    if status not in ('open','closed','reopened'):
        raise ValueError('未知处理状态')
    owner = value.get('owner','')
    labels = value.get('labels',[])
    if not isinstance(owner,str) or len(owner)>120 or not isinstance(labels,list) or len(labels)>20:
        raise ValueError('负责人或标签无效')
    labels = list(dict.fromkeys(text(label,60) for label in labels))
    note, reply = value.get('note',''),value.get('reply_draft','')
    if any(not isinstance(v,str) or len(v)>4000 for v in (note,reply)):
        raise ValueError('备注和回复草稿各限4000字符')
    with db:
        review = db.execute('SELECT * FROM reviews WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND review_id=%s FOR UPDATE',key).fetchone()
        if not review:
            raise ValueError('评论不存在')
        previous = db.execute('SELECT revision FROM review_workflow WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND review_id=%s FOR UPDATE',key).fetchone()
        current = previous['revision'] if previous else 0
        if type(value.get('revision')) is not int or value['revision']!=current:
            raise ValueError('处理记录已变化，请刷新后重试')
        revision = current+1
        db.execute('''INSERT INTO review_workflow VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS incoming
            ON DUPLICATE KEY UPDATE status=incoming.status,owner=incoming.owner,labels=incoming.labels,
            note=incoming.note,reply_draft=incoming.reply_draft,review_digest=incoming.review_digest,
            revision=incoming.revision,updated_at=incoming.updated_at''',
                   (*key,status,owner,dump(labels),note,reply,digest(review),revision,time.time()))
    return {'revision':revision,'published':False,'status':status}


def review_history(db, value):
    m=market(value)
    review_id=text(value.get('review_id'),255)
    rows=db.execute('''SELECT o.payload,s.observed_at,s.source FROM observation_items o
        JOIN observation_runs s ON s.id=o.run_id JOIN observation_targets t ON t.id=s.target_id
        WHERE t.kind='reviews' AND t.store=%s AND t.app_id=%s AND t.country=%s AND t.language=%s
        AND o.item_id=%s ORDER BY s.id DESC LIMIT 100''',
        (m['store'],m['app_id'],m['country'],m['language'],review_id)).fetchall()
    for row in rows:
        row['payload']=json.loads(row['payload'])
    return {'items':rows}
