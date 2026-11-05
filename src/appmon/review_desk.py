"""Read-only archive of collected store reviews and their observed revisions."""
import json
from .intelligence import market, text

JOIN = ' FROM reviews r LEFT JOIN apps a ON a.store=r.store AND a.app_id=r.app_id '


def desk(db, filters):
    m = market(filters, require_app=False)
    clauses = ['r.store=%s', 'r.country=%s', 'r.language=%s']
    args = [m['store'], m['country'], m['language']]
    for field in ('app_id', 'version', 'rating'):
        if filters.get(field) not in (None, ''):
            clauses.append(f'r.{field}=%s')
            args.append(filters[field])
    if filters.get('q'):
        clauses.append('(r.content LIKE %s OR r.title LIKE %s)')
        args.extend(['%' + str(filters['q'])[:255] + '%'] * 2)
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
    summary = db.execute('SELECT COUNT(*) total,COUNT(DISTINCT r.app_id) apps,AVG(r.rating) mean_review_rating,MIN(r.first_seen) oldest_observation,MAX(r.last_seen) latest_observation' + JOIN + where, args).fetchone()
    rating_counts = db.execute('SELECT r.rating,count(*) count' + JOIN + where + ' GROUP BY r.rating ORDER BY r.rating', args).fetchall()
    daily = db.execute('SELECT FLOOR(r.first_seen/86400)*86400 day,count(*) count' + JOIN + where + ' GROUP BY day ORDER BY day DESC LIMIT 90', args).fetchall()
    rows = db.execute('SELECT r.*,a.name app_name' + JOIN + where +
        ' ORDER BY r.last_seen DESC,r.app_id,r.review_id LIMIT 51 OFFSET %s', (*args, offset)).fetchall()
    for row in rows:
        payload = json.loads(row['payload'])
        row['public_reply'] = payload.get('reply')
        row['public_reply_status'] = ('replied' if payload.get('reply') else 'not_observed') if 'reply' in payload else 'unknown'
        row['source'] = f"{row['store']}-public-reviews"
    return {'items': rows[:50], 'summary': summary, 'ratings': rating_counts, 'daily': list(reversed(daily)),
            'next_offset': offset + 50 if len(rows)>50 else None,
            'scope': '当前筛选内已采集评论；趋势按首次观察日期。评论平均星级不是商店累计评分。'}


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
    return {'items':list(rows)}
