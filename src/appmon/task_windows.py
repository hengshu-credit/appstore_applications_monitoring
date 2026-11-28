"""Expiration of waiting collection rounds; callers lock the queue row first."""
import math
import time

from .collection_batch import PAGE_DUE, touch
from .task_logs import emit, INSERT_LOG

TABLES={'watchlist':'jobs','keywords':'keyword_targets','targets':'observation_targets',
        'permissions':'app_permission_targets','icons':'app_icons','media':'app_media_assets'}
KEYS=('store','app_id','country','language')


def validate(value):
    # None disables expiration; zero follows the target's own refresh interval.
    if value is not None and (type(value) is not int or not 0<=value<=525600):
        raise ValueError('有效期须为 0–525600 的整数分钟，或留空关闭')
    return value


def rows_for(db,resource,ids,*,skip=False):
    lock=' FOR UPDATE SKIP LOCKED' if skip else ' FOR UPDATE'
    if resource=='watchlist':
        return db.execute("""SELECT j.*,w.id watch_id,w.refresh_hours,w.enabled FROM watchlist w
            JOIN jobs j USING(store,app_id,country,language) WHERE w.id IN %s ORDER BY w.id"""+lock,(tuple(ids),)).fetchall()
    return db.execute(f'SELECT * FROM {TABLES[resource]} WHERE id IN %s ORDER BY id'+lock,(tuple(ids),)).fetchall()


def identifier(resource,row):
    return row['watch_id'] if resource=='watchlist' else row['id']


def set_due(db,resource,row,due):
    if resource=='watchlist':
        db.execute('UPDATE jobs SET due=%s WHERE store=%s AND app_id=%s AND country=%s AND language=%s',
                   (due,*(row[k] for k in KEYS)))
    else:
        db.execute(f'UPDATE {TABLES[resource]} SET due=%s WHERE id=%s',(due,row['id']))
    row['due']=due


def period(row):
    return row['refresh_hours']*3600


def next_cycle(row,start):
    if row.get('kind')=='chart' and row['refresh_hours']==24:
        from .charts import daily_slot
        return daily_slot(row['store'],row['country'],row['collection'],start)+86400
    return start+period(row)


def promoted_slot(due):
    # Negative slots have already been promoted. Map ordinary due times
    # monotonically into the recovery tier, retaining their original order.
    return due if due < 0 else -0.75 + 0.5 * due / (due + 2147483648)


DISABLED=-1
DISABLED_CHECK=253402300799


def deadline(start,following,minutes):
    if minutes==DISABLED:return DISABLED_CHECK
    return min(start+minutes*60,following) if minutes else following


def configure(db,resource,row,minutes,now):
    key=(resource,identifier(resource,row))
    policy=db.execute('SELECT * FROM collection_task_windows WHERE resource=%s AND target_id=%s FOR UPDATE',key).fetchone()
    if minutes is None:minutes=DISABLED
    if policy and minutes==policy['validity_minutes']:return
    if minutes==DISABLED and policy and PAGE_DUE<row['due']<0:set_due(db,resource,row,policy['round_at'])
    start=max(now,row['due'])
    following=next_cycle(row,start)
    if policy and policy['validity_minutes']!=DISABLED:
        start,following=policy['round_at'],policy['next_round']
    db.execute("""INSERT INTO collection_task_windows(resource,target_id,validity_minutes,round_at,next_round,slot_due,tracked_due,check_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE round_at=VALUES(round_at),next_round=VALUES(next_round),validity_minutes=VALUES(validity_minutes),check_at=VALUES(check_at)""",
        (*key,minutes,start,following,row['due'],row['due'],deadline(start,following,minutes)))


def configure_selection(db,minutes,now):
    minutes=DISABLED if minutes is None else minutes
    count=0
    for resource,table in TABLES.items():
        queue='watchlist w JOIN jobs q USING(store,app_id,country,language)' if resource=='watchlist' else table+' q'
        owner='w' if resource=='watchlist' else 'q'
        selection=f" JOIN collection_selection s ON s.resource=%s AND s.id={owner}.id"
        allowed=" WHERE q.request_mode='periodic'" if resource=='icons' else ''
        if minutes==DISABLED:
            join=queue+selection+' JOIN collection_task_windows p ON p.resource=s.resource AND p.target_id=s.id'
            db.execute('UPDATE '+join+' SET q.due=p.round_at WHERE q.due>%s AND q.due<0',(resource,PAGE_DUE))
        # Match charts.daily_slot for daily chart targets, including rows
        # that entered the page queue before their regular market slot.
        extra=",q.kind,q.store,q.country,q.collection" if resource=='targets' else ""
        following='starts+interval_seconds'
        if resource=='targets':
            offset="MOD(CONV(LEFT(SHA2(CONCAT(store,'/',country,'/',collection),256),8),16,10),86400)"
            following=f"IF(kind='chart' AND interval_seconds=86400,(FLOOR(starts/86400)+1)*86400+{offset},starts+interval_seconds)"
        db.execute(f"""INSERT INTO collection_task_windows(resource,target_id,validity_minutes,round_at,next_round,slot_due,tracked_due,check_at)
            SELECT resource,id,%s,starts,following,due,due,CASE WHEN %s=-1 THEN {DISABLED_CHECK} WHEN %s=0 THEN following ELSE LEAST(starts+%s*60,following) END
            FROM (SELECT selected.*,{following} following FROM (SELECT s.resource,s.id,q.due,GREATEST(q.due,%s) starts,
                {owner}.refresh_hours*3600 interval_seconds{extra} FROM {queue}{selection}{allowed}) selected) initial_windows
            ON DUPLICATE KEY UPDATE
            check_at=CASE WHEN VALUES(validity_minutes)=-1 THEN VALUES(check_at)
                    WHEN collection_task_windows.validity_minutes=VALUES(validity_minutes) THEN collection_task_windows.check_at ELSE %s END,
                round_at=IF(collection_task_windows.validity_minutes=-1,VALUES(round_at),collection_task_windows.round_at),
                next_round=IF(collection_task_windows.validity_minutes=-1,VALUES(next_round),collection_task_windows.next_round),
            validity_minutes=VALUES(validity_minutes)""",(minutes,minutes,minutes,minutes,now,resource,now))
        count+=db.execute('SELECT COUNT(*) n FROM '+queue+selection+allowed,(resource,)).fetchone()['n']
    touch(db)
    return {'updated':count,'action':'validity'}


def expire_receipt(db,resource,target_id,source,request_id,now):
    if source is not None and request_id is not None:
        db.execute("""INSERT IGNORE INTO collection_expired_receipts
            (resource,target_id,request_source,request_id,expired_at) VALUES(%s,%s,%s,%s,%s)""",
            (resource,target_id,source,request_id,now))


def is_expired(db,resource,target_id,source,request_id):
    return bool(db.execute("""SELECT 1 FROM collection_expired_receipts WHERE resource=%s AND target_id=%s
        AND request_source=%s AND request_id=%s""",(resource,target_id,source,request_id)).fetchone())


def latest(db,resource,target_id,at,source=None,request_id=None,*,fallback=None):
    """Caller owns the slot lock. A delayed producer cannot supersede newer work."""
    key=(resource,target_id)
    old=db.execute('SELECT * FROM collection_latest_submissions WHERE resource=%s AND target_id=%s FOR UPDATE',key).fetchone()
    if not old:old=fallback
    if old:
        if request_id is not None and (source,request_id)==(old['request_source'],old['request_id']):return False
        previous_at=old['submitted_at']
        if old['pending'] and old['request_source'] in ('plan-action','collection-plan','user-ad-plan'):
            plan_id=old['request_id'].partition(':')[0]
            if plan_id.isdecimal():
                parent=db.execute("SELECT submitted_at FROM collection_latest_submissions WHERE resource='plans' AND target_id=%s",(int(plan_id),)).fetchone()
                if parent:previous_at=max(previous_at,parent['submitted_at'])
        if at<previous_at:
            expire_receipt(db,*key,source,request_id,time.time())
            return False
        if old['pending']:
            expire_receipt(db,*key,old['request_source'],old['request_id'],at)
            emit(db,resource,target_id,'expired','旧请求已过期，新提交沿用同一队列位置')
    db.execute("""INSERT INTO collection_latest_submissions(resource,target_id,submitted_at,request_source,request_id,pending)
        VALUES(%s,%s,%s,%s,%s,1) ON DUPLICATE KEY UPDATE submitted_at=VALUES(submitted_at),
        request_source=VALUES(request_source),request_id=VALUES(request_id),pending=1""",(*key,at,source,request_id))
    return True


def end_pending(db,resource,ids,*,expired_at=None):
    if not ids:return
    where='resource=%s AND target_id IN %s AND pending=1'
    if expired_at is not None:
        db.execute("""INSERT IGNORE INTO collection_expired_receipts
            SELECT resource,target_id,request_source,request_id,%s FROM collection_latest_submissions WHERE """
            +where+' AND request_source IS NOT NULL AND request_id IS NOT NULL',(expired_at,resource,tuple(ids)))
    db.execute('UPDATE collection_latest_submissions SET pending=0 WHERE '+where,(resource,tuple(ids)))


def page_submitted(db,resource,ids):
    """Record a page submission in the same transaction as the queue upsert."""
    ids=sorted(set(ids))
    for i in range(0,len(ids),500):
        rows=rows_for(db,resource,ids[i:i+500])
        now=time.time()
        waiting=[r for r in rows if r.get('enabled',True) and not r['token']
                 and not r['failures'] and r['due']<=now]
        keys=[identifier(resource,r) for r in waiting]
        if not keys:continue
        end_pending(db,resource,keys,expired_at=now)
        db.executemany("""INSERT INTO collection_latest_submissions
            (resource,target_id,submitted_at,request_source,request_id,pending)
            VALUES(%s,%s,%s,NULL,NULL,1) ON DUPLICATE KEY UPDATE submitted_at=VALUES(submitted_at),
            request_source=NULL,request_id=NULL,pending=1""",[(resource,k,now) for k in keys])
        configured={p['target_id'] for p in db.execute("""SELECT target_id FROM collection_task_windows
            WHERE resource=%s AND target_id IN %s""",(resource,tuple(keys))).fetchall()}
        for row in waiting:
            if identifier(resource,row) in configured:submitted(db,resource,row,now,new_due=row['due'])


def admit_plan(db,resource,row,at,receipt):
    target_id=identifier(resource,row)
    now=time.time()
    policy=db.execute('SELECT * FROM collection_task_windows WHERE resource=%s AND target_id=%s FOR UPDATE',
                      (resource,target_id)).fetchone()
    if policy and policy['validity_minutes']!=DISABLED:
        if policy['check_at']<=now:
            apply_windows(db,resource,[row],[policy],now,claiming=False)
        if policy['last_expired_at'] is not None and at < policy['last_expired_at']:
            expire_receipt(db,resource,target_id,*receipt,now)
            return False
    return latest(db,resource,target_id,at,*receipt)


def submitted(db,resource,row,at,*,new_due=None,interval_changed=False):
    """Replace the pending logical request while keeping the physical queue slot."""
    key=(resource,identifier(resource,row))
    policy=db.execute('SELECT * FROM collection_task_windows WHERE resource=%s AND target_id=%s FOR UPDATE',key).fetchone()
    if not policy or policy['validity_minutes']==DISABLED or row['token']:return
    if at<policy['round_at'] and policy['tracked_due']<=at:return
    following=policy['next_round']
    if row['due']>at:following=row['due']
    elif policy['tracked_due']>at:following=policy['tracked_due']
    if interval_changed:following=next_cycle(row,at)
    if following<=at:
        following+=(math.floor((at-following)/period(row))+1)*period(row)
    queued=at if new_due is None else new_due
    db.execute("""UPDATE collection_task_windows SET round_at=%s,next_round=%s,tracked_due=%s,slot_due=%s,check_at=%s
        WHERE resource=%s AND target_id=%s""",
        (at,following,queued,min(row['due'],queued),deadline(at,following,policy['validity_minutes']),*key))



def roll_window(resource,row,w,now,claiming):
    """Calculate one transition on locked rows. Apply the resulting writes together."""
    due=row['due']
    if not row.get('enabled',True) or row.get('token') or row['lease_until']>now or row['failures'] or (
            resource=='icons' and (not row['requested'] or row['request_mode']!='periodic')):
        if not claiming:
            delay=60 if row.get('enabled',True) else min(period(row),3600)
            w['check_at']=max(now+delay,row['lease_until'])
        return True

    start,following,slot=w['round_at'],w['next_round'],w['slot_due']
    if due!=w['tracked_due']:
        if due>0:
            start=slot=due
            following=next_cycle(row,start)
        elif due<=PAGE_DUE:
            start=max(start,-due if due<PAGE_DUE else now)
            following=min(following,next_cycle(row,start)) if following>start else next_cycle(row,start)
            slot=due
        else:
            start,following,slot=now,next_cycle(row,now),due
    expires=deadline(start,following,w['validity_minutes'])
    expired=now>=expires
    if expired:
        interval=period(row)
        # Jump directly to the most recent round after an extended outage.
        cycles=max(0,math.floor((now-following)/interval))
        start=following+cycles*interval
        following=start+interval
        expires_current=deadline(start,following,w['validity_minutes'])
        if now>=expires_current:
            expires=expires_current
            start,following=following,following+interval
        elif start<=now:
            expires=start
        slot=promoted_slot(slot)
        due=start if start>now else slot
        w['last_expired_at']=expires
    elif due>0 and slot<0 and start<=now:
        due=slot
    if claiming and not expired:
        # The new round consumes its boost on its first lease.
        if PAGE_DUE<due<0:due=start
        slot=due
    row['due']=due
    check=start if start>now and slot<0 else deadline(start,following,w['validity_minutes'])
    w.update(round_at=start,next_round=following,slot_due=slot,tracked_due=due,check_at=check)
    return not expired


def apply_windows(db,resource,rows,policies,now,*,claiming):
    by_id={w['target_id']:w for w in policies}
    changes=[]
    due_changes=[]
    expired=[]
    events=[]
    accepted=[]
    for row in rows:
        target_id=identifier(resource,row)
        w=by_id.get(target_id)
        if w is None or w['validity_minutes']==DISABLED:
            accepted.append(row)
            continue
        before=w.copy()
        due=row['due']
        if roll_window(resource,row,w,now,claiming):
            accepted.append(row)
        else:
            expired.append(target_id)
            emit(db,resource,target_id,'expired','积压轮次已过期，下轮沿用原排队位置并优先采集',events=events)
        if w!=before:
            changes.append(tuple(w[k] for k in ('resource','target_id','validity_minutes','round_at','next_round',
                                                'slot_due','tracked_due','check_at','last_expired_at')))
        if row['due']!=due:due_changes.append((target_id,row['due']))
    # Both the queue and the policies are locked by the caller. Batch all
    # updates in this transaction, including receipts and the revision signal.
    for offset in range(0,len(due_changes),500):
        batch=due_changes[offset:offset+500]
        jobs=resource=='watchlist'
        table='jobs j JOIN watchlist w USING(store,app_id,country,language)' if jobs else TABLES[resource]
        key='w.id' if jobs else 'id'
        column='j.due' if jobs else 'due'
        cases=' '.join('WHEN %s THEN %s' for _ in batch)
        args=[v for item in batch for v in item]
        db.execute(f'UPDATE {table} SET {column}=CASE {key} {cases} END WHERE {key} IN %s',
                   (*args,tuple(i for i,_ in batch)))
    if changes:
        db.executemany("""INSERT INTO collection_task_windows(resource,target_id,validity_minutes,round_at,next_round,
            slot_due,tracked_due,check_at,last_expired_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE round_at=VALUES(round_at),next_round=VALUES(next_round),slot_due=VALUES(slot_due),
            tracked_due=VALUES(tracked_due),check_at=VALUES(check_at),last_expired_at=VALUES(last_expired_at)""",changes)
    if expired:
        end_pending(db,resource,expired,expired_at=now)
        db.executemany(INSERT_LOG,events)
    if expired or due_changes:touch(db)
    return accepted


def save_defaults(db,resource,rows,now):
    values=[]
    for row in rows:
        start=max(now,row['due'])
        following=next_cycle(row,start)
        values.append((resource,identifier(resource,row),start,following,row['due'],row['due'],following))
    if values:
        db.executemany("""INSERT IGNORE INTO collection_task_windows(resource,target_id,validity_minutes,
            round_at,next_round,slot_due,tracked_due,check_at) VALUES(%s,%s,0,%s,%s,%s,%s,%s)""",values)


def before_claim(db,resource,rows,now):
    if not rows:return rows
    ids=tuple(identifier(resource,r) for r in rows)
    policies=db.execute("""SELECT * FROM collection_task_windows WHERE resource=%s AND target_id IN %s
        ORDER BY target_id FOR UPDATE""",(resource,ids)).fetchall()
    if len(policies)<len(rows):
        configured={p['target_id'] for p in policies}
        missing=[r for r in rows if identifier(resource,r) not in configured and
                 (resource!='icons' or r['request_mode']=='periodic')]
        save_defaults(db,resource,missing,now)
    accepted=apply_windows(db,resource,rows,policies,now,claiming=True)
    end_pending(db,resource,[identifier(resource,r) for r in accepted])
    return accepted


def enroll_defaults(db,now,limit=500):
    """Walk each configured queue by primary key. Preserve explicit policies and queue slots."""
    timer='windows:enroll'
    if db.execute('SELECT 1 FROM schedules WHERE name=%s AND next_at>%s',(timer,now)).fetchone():return
    with db:
        db.execute('INSERT IGNORE INTO schedules VALUES(%s,0)',(timer,))
        tick=db.execute('SELECT next_at FROM schedules WHERE name=%s FOR UPDATE SKIP LOCKED',(timer,)).fetchone()
        if not tick or tick['next_at']>now:return
        for resource,table in TABLES.items():
            owner='watchlist' if resource=='watchlist' else table
            key='windows:cursor:'+resource
            cursor=db.execute('SELECT next_at FROM schedules WHERE name=%s',(key,)).fetchone()
            after=int(cursor['next_at']) if cursor else 0
            ids=db.execute(f'SELECT id FROM {owner} WHERE id>%s ORDER BY id LIMIT %s',(after,limit)).fetchall()
            if not ids:continue
            last=ids[-1]['id']
            queue='watchlist t JOIN jobs q USING(store,app_id,country,language)' if resource=='watchlist' else table+' t'
            due='q.due' if resource=='watchlist' else 't.due'
            chart=',t.kind,t.store,t.country,t.collection' if resource=='targets' else ''
            periodic=" AND t.request_mode='periodic'" if resource=='icons' else ''
            key_column='t.id watch_id' if resource=='watchlist' else 't.id'
            rows=db.execute(f"""SELECT {key_column},t.refresh_hours,{due} due{chart} FROM {queue}
                LEFT JOIN collection_task_windows p ON p.resource=%s AND p.target_id=t.id
                WHERE t.id>%s AND t.id<=%s AND p.target_id IS NULL{periodic}""",(resource,after,last)).fetchall()
            save_defaults(db,resource,rows,now)
            db.execute('INSERT INTO schedules VALUES(%s,%s) ON DUPLICATE KEY UPDATE next_at=VALUES(next_at)',(key,last))
        db.execute('UPDATE schedules SET next_at=%s WHERE name=%s',(now+5,timer))


def advance(db,now=None,limit=200):
    now=time.time() if now is None else now
    refs=db.execute("""SELECT resource,target_id FROM collection_task_windows WHERE check_at<=%s
        ORDER BY check_at,resource,target_id LIMIT %s""",(now,limit)).fetchall()
    for resource in sorted({p['resource'] for p in refs}):
        ids=[p['target_id'] for p in refs if p['resource']==resource]
        with db:
            rows=rows_for(db,resource,ids,skip=True)
            locked={identifier(resource,r) for r in rows}
            missing=tuple(set(ids)-locked)
            if missing:
                # Deleted targets have no next cycle. Leave locked live rows to
                # their owner, and move them out of the front of this scan.
                table='watchlist' if resource=='watchlist' else TABLES[resource]
                db.execute(f"""DELETE FROM collection_task_windows WHERE resource=%s AND target_id IN %s
                    AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id=collection_task_windows.target_id)""",
                    (resource,missing))
                db.execute("""UPDATE collection_task_windows SET check_at=%s WHERE resource=%s
                    AND target_id IN %s AND check_at<=%s""",(now+5,resource,missing,now))
            if not rows:continue
            policies=db.execute("""SELECT * FROM collection_task_windows WHERE resource=%s AND target_id IN %s
                AND check_at<=%s ORDER BY target_id FOR UPDATE""",
                (resource,tuple(identifier(resource,r) for r in rows),now)).fetchall()
            apply_windows(db,resource,rows,policies,now,claiming=False)
