"""Build a local, auditable vocabulary release from authorized report exports.

The JSONL releases are private data, not package assets. No search jobs are created.
"""
import base64
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import keyword_reports as reports
from .intelligence import market
from .keywords import validate_target

IDENTITY = ('store', 'country', 'language', 'keyword')


def prepare_reports(folder, bindings):
    """Validate every file/binding before importing the first report."""
    paths = sorted(Path(folder).glob('*数据报告*.xlsx'))
    if not paths:
        raise ValueError('目录中未发现应用关键词报告xlsx')
    prepared, seen, duplicates = [], {}, []
    for path in paths:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        matches = [b for b in bindings if b.get('prefix') and path.name.startswith(b['prefix'])]
        if len(matches) != 1:
            raise ValueError('报告必须有且仅有一个已核实应用绑定：' + path.name)
        m = market(matches[0])
        if digest in seen:
            previous = seen[digest]
            if any(m[k] != previous['market'][k] for k in ('store','app_id','country','language')):
                raise ValueError('同一报告文件存在冲突绑定：' + path.name)
            duplicates.append({'filename': path.name, 'duplicate_of': previous['filename'], 'sha256': digest})
            continue
        parsed = reports.parse(raw)
        for key in ('store', 'country'):
            if parsed['inferred'].get(key) and parsed['inferred'][key] != m[key]:
                raise ValueError('报告市场与绑定不符：' + path.name)
        if parsed['inferred']['observed_at'] is None:
            raise ValueError('报告缺少关键词日期：' + path.name)
        if parsed['inferred']['observed_at'] > time.time() + 86400:
            raise ValueError('报告日期在未来：' + path.name)
        valid = 0
        for row in parsed['items']:
            try:
                validate_target(m | {'keyword': row['keyword']})
                valid += 1
            except ValueError:
                pass
        entry = dict(filename=path.name, sha256=digest, market=m, raw=raw,
                     records=len(parsed['items']), valid_records=valid,
                     rejected_records=len(parsed['items'])-valid,
                     observed_at=parsed['inferred']['observed_at'])
        seen[digest] = entry
        prepared.append(entry)
    return prepared, duplicates


def write_grouped(path, rows, source):
    """Stream the DB's ordered identities; retain all source observations."""
    count, evidence_count, markets = 0, 0, Counter()
    current, record = None, None
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        def flush():
            if record is not None:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
        for row in rows:
            key = tuple(row[k] for k in IDENTITY)
            if key != current:
                flush()
                record = {k: row[k] for k in IDENTITY} | {'sources': []}
                current = key
                count += 1
                markets[key[:3]] += 1
            record['sources'].append(source(row))
            evidence_count += 1
        flush()
    # Independent read-back checks of every exported identity and source count.
    identities, checked_sources = set(), 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            key = tuple(row[k] for k in IDENTITY)
            if key in identities or validate_target(row)['keyword'] != row['keyword']:
                raise ValueError('导出包含重复或非规范化关键词')
            identities.add(key)
            checked_sources += len(row['sources'])
    if len(identities) != count or checked_sources != evidence_count:
        raise ValueError('词库导出行数不一致')
    return {'file': path.name, 'terms': count, 'source_records': evidence_count,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'markets': [dict(zip(IDENTITY[:3], k), terms=v) for k, v in sorted(markets.items())]}


def build(cfg, db, folder, bindings, output):
    output = Path(output)
    if output.exists():
        raise ValueError('版本输出目录已存在，请使用新目录以保留旧版本')
    prepared, duplicates = prepare_reports(folder, bindings)
    manifest_reports = []
    for entry in prepared:
        result = reports.import_report(cfg, db, entry['market'] | {
            'filename': entry['filename'], 'content_base64': base64.b64encode(entry['raw']).decode()})
        if (result['rows'], result['rejected_rows']) != (entry['valid_records'], entry['rejected_records']):
            raise ValueError('报告解析与入库计数不一致')
        stored = db.execute('SELECT sheet,source_row FROM keyword_report_rows WHERE report_id=%s ORDER BY sheet,source_row',
                            (result['id'],)).fetchall()
        rejected = db.execute('SELECT count(*) n FROM keyword_report_rejections WHERE report_id=%s', (result['id'],)).fetchone()['n']
        if len(stored) != entry['valid_records'] or rejected != entry['rejected_records']:
            raise ValueError('报告数据库记录不完整')
        for sheet in reports.SHEETS:
            numbers = [r['source_row'] for r in stored if r['sheet'] == sheet]
            for start in range(0, len(numbers), 2000):
                reports.promote(db, {'report_id': result['id'], 'sheet': sheet, 'source_rows': numbers[start:start+2000]})
        manifest_reports.append({k: v for k, v in entry.items() if k != 'raw'} | {'report_id': result['id']})
    output.mkdir(parents=True)
    # One consistent snapshot across both files, even while collection workers run.
    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    with db:
        keywords = write_grouped(output / 'keyword-library.jsonl', db.iter_rows(
            'SELECT * FROM keyword_candidates ORDER BY store,country,language,keyword,source_key'),
            lambda r: {k: r[k] for k in ('source','source_key','source_url','license_name','attribution','source_period','corpus_frequency')})
        aso = write_grouped(output / 'aso-library.jsonl', db.iter_rows('''SELECT r.store,r.country,r.language,k.keyword,
            r.app_id,r.digest,r.filename,r.observed_at,k.source_row,k.position,k.search_index,k.popularity,k.payload
            FROM keyword_report_rows k JOIN keyword_reports r ON r.id=k.report_id
            WHERE k.sheet='ASO关键词' ORDER BY r.store,r.country,r.language,k.keyword,r.digest,r.app_id,k.source_row'''),
            lambda r: {k: r[k] for k in ('app_id','digest','filename','observed_at','source_row','position','search_index','popularity')}
            | {'sheet': 'ASO关键词', 'original': json.loads(r['payload'])})
        expected_candidates = db.execute('SELECT count(*) n FROM keyword_candidates').fetchone()['n']
        expected_aso = db.execute("SELECT count(*) n FROM keyword_report_rows WHERE sheet='ASO关键词'").fetchone()['n']
        if (keywords['source_records'], aso['source_records']) != (expected_candidates, expected_aso):
            raise ValueError('数据库与导出记录数不一致')
    manifest = {'version': output.name, 'built_at': datetime.now(timezone.utc).isoformat(),
                'reports': manifest_reports, 'duplicate_files': duplicates,
                'keyword_library': keywords, 'aso_library': aso,
                'scope': '关键词库包含现有全部候选来源；ASO词库包含全部已导入报告自然搜索词。'
                         '身份为商店/国家/追踪语言/规范化关键词，保留大小写。语言是导入配置，报告指标为第三方历史截面。',
                'new_tracking_jobs': 0, 'validation': 'full JSONL read-back, unique identities, source counts and SHA256'}
    # The manifest is the completion marker; failed builds never publish one.
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
