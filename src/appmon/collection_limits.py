"""Live, database-coordinated request admission; no future slots are pre-booked."""
import json
import math
import time
from .db import dump

NAME = 'request-limits'


def settings(db, cfg):
    defaults = dict(global_interval_seconds=cfg.get('global_interval_seconds', 5),
                    google_interval_seconds=cfg['google_interval_seconds'],
                    apple_interval_seconds=cfg['apple_interval_seconds'],
                    cooldown_seconds=cfg.get('cooldown_seconds', 900), paused=False)
    row = db.execute('SELECT settings FROM collection_controls WHERE name=%s', (NAME,)).fetchone()
    return defaults | (json.loads(row['settings']) if row else {})


def save(db, cfg, value):
    old = settings(db, cfg)
    if set(value) - set(old):
        raise ValueError('未知控频配置')
    result = old | value
    if type(result['paused']) is not bool:
        raise ValueError('暂停状态须为布尔值')
    for key in set(old) - {'paused'}:
        v = result[key]
        low, high = (60, 86400) if key == 'cooldown_seconds' else (0.1, 3600)
        if type(v) not in (int,float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f'{key} 须在 {low}–{high} 秒之间')
    db.execute('''INSERT INTO collection_controls VALUES(%s,%s,%s) AS incoming
        ON DUPLICATE KEY UPDATE settings=incoming.settings,updated_at=incoming.updated_at''',
        (NAME, dump(result), time.time()))
    return result


def admission(db, cfg, store):
    if store not in ('google','apple'):
        raise ValueError('未知商店')
    with db:
        # Always lock global first, so separate processes cannot admit a burst.
        keys = ('rate:global', 'rate:'+store)
        for key in keys:
            # Acquire an exclusive lock directly; INSERT IGNORE's shared duplicate
            # lock can deadlock when multiple workers later upgrade to FOR UPDATE.
            db.execute('INSERT INTO schedules VALUES(%s,0) ON DUPLICATE KEY UPDATE name=schedules.name', (key,))
        rows = [db.execute('SELECT next_at FROM schedules WHERE name=%s FOR UPDATE',(k,)).fetchone() for k in keys]
        limits = settings(db, cfg)
        if limits['paused']:
            return None
        now = time.time()
        delay = max(0, *(r['next_at']-now for r in rows))
        if delay == 0:
            for key, interval in zip(keys, (limits['global_interval_seconds'],limits[store+'_interval_seconds'])):
                db.execute('UPDATE schedules SET next_at=%s WHERE name=%s',(now+interval,key))
        return delay


def cooldown(db, cfg, store, retry_after):
    delay = max(settings(db,cfg)['cooldown_seconds'], retry_after)
    with db:
        # Shared IP protection: a rejection from either store cools all workers.
        for key in ('rate:global', 'rate:'+store):
            db.execute('''INSERT INTO schedules VALUES(%s,%s) AS incoming
                ON DUPLICATE KEY UPDATE next_at=GREATEST(schedules.next_at,incoming.next_at)''',
                (key,time.time()+delay))
    return delay
