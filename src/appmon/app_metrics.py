"""Market-scoped monitoring evidence. Absence never proves an Apple penalty."""
import json
import time
from .intelligence import market


def disappearance(rows, now, refresh_hours):
    """Require a ranked baseline and two comparable, spaced successful absences."""
    if len(rows) < 3:
        return False
    a, b, c = rows[-3:]
    signature = lambda r: (r['source'], r['parser_version'], r['coverage'], r.get('requested_depth'))
    return (all(r['outcome'] == 'ok' for r in (a, b, c))
            and signature(a) == signature(b) == signature(c)
            and a['position'] is not None and b['position'] is None and c['position'] is None
            and min(b['returned_count'] or 0, c['returned_count'] or 0) >= a['position']
            and b['observed_at'] - a['observed_at'] >= 3600
            and c['observed_at'] - b['observed_at'] >= 3600
            and now - c['observed_at'] < refresh_hours * 3600)


def report(db, value):
    m = market(value)
    days = value.get('days', 30)
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('趋势区间必须为1–365天')
    now = time.time(); since = now - days * 86400
    key = tuple(m[k] for k in ('store', 'app_id', 'country', 'language'))
    mk = (m['store'], m['country'], m['language'])
    listing = db.execute('SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s', key).fetchone()
    healthy = bool(listing and listing['state'] == 'available' and listing['last_success'] and now-listing['last_success'] < 48*3600)
    series = []; losses = {'keyword': [], 'chart': []}; totals = {'tracked': 0, 'fresh': 0, 'covered': 0, 'top3': 0, 'top10': 0}
    truncated = False
    daily_keywords = {}
    for kind, targets_table, runs_table, items_table, id_field in (
        ('keyword', 'keyword_targets', 'keyword_runs', 'keyword_ranks', 'app_id'),
        ('chart', 'observation_targets', 'observation_runs', 'observation_items', 'item_id'),
    ):
        extra = " AND kind='chart'" if kind == 'chart' else ''
        targets = db.execute(f'SELECT * FROM {targets_table} WHERE store=%s AND country=%s AND language=%s AND enabled=1'+extra+' ORDER BY id LIMIT 201', mk).fetchall()
        truncated |= len(targets) > 200
        for t in targets[:200]:
            rows = db.execute(f'''SELECT r.*,i.position FROM {runs_table} r LEFT JOIN {items_table} i
                ON i.run_id=r.id AND i.{id_field}=%s WHERE r.target_id=%s AND r.observed_at>=%s
                ORDER BY r.id DESC LIMIT 301''', (m['app_id'], t['id'], since)).fetchall()
            truncated |= len(rows) > 300
            rows = list(reversed(rows[:300]))
            latest = rows[-1] if rows else None
            fresh = bool(latest and latest['outcome'] == 'ok' and not t['failures'] and now-latest['observed_at'] < t['refresh_hours']*3600)
            if kind == 'keyword':
                totals['tracked'] += 1
                totals['fresh'] += int(fresh)
                if fresh and latest['position'] is not None:
                    totals['covered'] += 1
                    totals['top3'] += int(latest['position'] <= 3)
                    totals['top10'] += int(latest['position'] <= 10)
                for row in rows:
                    day = int(row['observed_at'] // 86400) * 86400
                    daily_keywords.setdefault(day, {})[t['id']] = row
            label = t['keyword'] if kind == 'keyword' else t['collection']+' / '+(t['category'] or '总榜')
            if rows and (kind == 'chart' or any(r['position'] is not None for r in rows)):
                series.append({'kind': kind, 'target_id': t['id'], 'label': label, 'fresh': fresh,
                               'points': [{'at': r['observed_at'], 'value': r['position'] if r['outcome']=='ok' else None,
                                           'note': f"{r['source']} · {r['outcome']} · 返回 {r['returned_count']} 条 · {r['coverage']}"} for r in rows]})
            if healthy and fresh and disappearance(rows, now, t['refresh_hours']):
                losses[kind].append({'target_id': t['id'], 'label': label, 'baseline_position': rows[-3]['position'],
                                     'run_ids': [r['id'] for r in rows[-3:]], 'observed_at': rows[-1]['observed_at']})
    snapshots = db.execute('SELECT observed_at,payload FROM snapshots WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND observed_at>=%s ORDER BY id DESC LIMIT 300', (*key, since)).fetchall()
    snapshots = [r | {'payload': json.loads(r['payload'])} for r in reversed(snapshots)]
    reviews = db.execute('SELECT rating,count(*) count FROM reviews WHERE store=%s AND app_id=%s AND country=%s AND language=%s GROUP BY rating ORDER BY rating', key).fetchall()
    review_daily = db.execute('''SELECT FLOOR(first_seen/86400)*86400 at,count(*) value FROM reviews
        WHERE store=%s AND app_id=%s AND country=%s AND language=%s AND first_seen>=%s GROUP BY at ORDER BY at''', (*key, since)).fetchall()
    availability = db.execute('''SELECT observed_at at,outcome FROM requests WHERE store=%s AND app_id=%s
        AND country=%s AND language=%s AND observed_at>=%s ORDER BY id DESC LIMIT 300''', (*key, since)).fetchall()
    coverage_daily = []
    for day, samples in sorted(daily_keywords.items()):
        valid = [r for r in samples.values() if r['outcome'] == 'ok']
        coverage_daily.append({'at': day, 'value': sum(r['position'] is not None for r in valid) if valid else None,
                               'note': f'当日最后尝试成功 {len(valid)} / 已尝试 {len(samples)} 词；词库和样本数可能变化，不填补未采集日期'})
    signals = []
    for kind, threshold, title in (('keyword', 3, '疑似批量掉词（清词待核查）'), ('chart', 1, '持续出榜（清榜待核查）')):
        if len(losses[kind]) >= threshold:
            signals.append({'kind': kind, 'title': title, 'evidence': losses[kind], 'status': 'suspected'})
    return {'market': m, 'days': days, 'observed_at': now, 'coverage': totals, 'series': series,
            'snapshots': snapshots, 'review_distribution': reviews, 'review_daily': review_daily,
            'coverage_daily': coverage_daily,
            'availability': [{'at': r['at'], 'value': {'ok': 1, 'missing': 0}.get(r['outcome']),
                              'note': {'ok': '当前市场可访问', 'missing': '当前市场单次未查到，尚不能判定下架'}.get(r['outcome'], '请求失败，保留上次有效状态')} for r in reversed(availability)],
            'signals': signals, 'truncated': truncated,
            'downloads': {'available': False, 'reason': '日下载量需接入开发者授权报表或有来源的第三方估算；Google 安装下限不能换算日下载量。'},
            'scope': '当前市场启用目标最多各200个，每个目标区间内最近300次尝试。覆盖数仅计最新成功且未过期的命中词；未采集不计为0。连续两次同口径缺失且返回深度足够才提示异常，不代表官方处罚。'}
