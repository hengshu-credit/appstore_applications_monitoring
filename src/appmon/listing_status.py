"""Current market availability over the last valid listing and the latest detail attempt."""
from . import locales


# A failed lookup is recorded on its job, even if it never produced a listing.
# Only a subsequent valid detail result clears that failure. Older single misses
# were persisted as suspected_missing and are conclusive under the current rule.
CURRENT_STATE = """CASE WHEN j.failures>0 THEN 'unverified'
    WHEN l.state IN ('suspected_missing','region_unavailable') THEN 'region_unavailable'
    WHEN l.state='available' THEN 'available' ELSE 'unverified' END"""


def state_key(value):
    return {'suspected_missing':'region_unavailable', 'unobserved':'unverified'}.get(value,value)


def listing(db, key):
    row = db.execute(f'''SELECT l.*, {CURRENT_STATE} current_state FROM listings l
        LEFT JOIN jobs j USING(store,app_id,country,language)
        WHERE l.store=%s AND l.app_id=%s AND l.country=%s AND l.language=%s''',key).fetchone()
    if row:
        row['state'] = row.pop('current_state')
    return row


def counts(db, now, store='all', country='', language='', *, include_unchecked_watches=False):
    listing_scope, listing_args = locales.where(store,country,language,alias='l')
    job_scope, job_args = locales.where(store,country,language,alias='j')
    targets = f'''SELECT l.store,l.country,l.language,{CURRENT_STATE} state,
            IF(j.failures>0,NULL,l.checked_at) valid_at,l.last_success,0 unchecked
        FROM listings l LEFT JOIN jobs j USING(store,app_id,country,language) WHERE {listing_scope}
        UNION ALL
        SELECT j.store,j.country,j.language,'unverified',NULL,NULL,0
        FROM jobs j LEFT JOIN listings l USING(store,app_id,country,language)
        WHERE j.failures>0 AND l.app_id IS NULL AND {job_scope}'''
    args = [now-48*3600, *listing_args, *job_args]
    if include_unchecked_watches:
        scope, params = locales.where(store,country,language,alias='w')
        targets += f''' UNION ALL SELECT w.store,w.country,w.language,'unverified',NULL,NULL,1
            FROM watchlist w LEFT JOIN listings l USING(store,app_id,country,language)
            LEFT JOIN jobs j USING(store,app_id,country,language)
            WHERE w.enabled=1 AND l.app_id IS NULL AND COALESCE(j.failures,0)=0 AND {scope}'''
        args.extend(params)
    return db.execute(f'''SELECT store,country,language,state,COUNT(*) count,SUM(unchecked) unchecked,
        SUM(CASE WHEN valid_at>=%s THEN 1 ELSE 0 END) fresh,MAX(last_success) last_success,MIN(last_success) oldest_success
        FROM ({targets}) targets GROUP BY store,country,language,state''',args).fetchall()
