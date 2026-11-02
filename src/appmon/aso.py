"""Versioned local metadata workbench. No writes to developer accounts."""
import json
import re
import time
import unicodedata
from .db import dump
from .intelligence import market, text
from .keywords import app_coverage

RULES_VERSION = 'store-metadata-2026-11-03'
RULES = {
    'apple': [('name', '应用名称', 30, 'characters'), ('subtitle', '副标题', 30, 'characters'),
              ('keywords', '后台关键词（用户提供）', 100, 'utf8_bytes'), ('description', '描述', 4000, 'characters')],
    'google': [('name', '应用名称', 30, 'characters'), ('summary', '简短描述', 80, 'characters'),
               ('description', '完整描述', 4000, 'characters')],
}
SOURCES = ['https://developer.apple.com/help/app-store-connect/reference/app-information/app-information/',
           'https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/',
           'https://support.google.com/googleplay/android-developer/answer/9859152?hl=en']


def normalize_metadata(store, metadata):
    if store not in RULES or not isinstance(metadata, dict):
        raise ValueError('请选择商店并提供文案字段')
    allowed = {r[0] for r in RULES[store]}
    if set(metadata) - allowed:
        raise ValueError('包含该商店不支持的文案字段')
    fields = {}
    for key in allowed:
        value = metadata.get(key, '')
        if not isinstance(value, str) or len(value) > 12000 or '\x00' in value:
            raise ValueError('文案必须是有效文本，单字段最多12000字符')
        fields[key] = value
    return fields


def check_metadata(store, metadata, keywords=()):
    fields = normalize_metadata(store, metadata)
    counters, findings = [], []
    for key, label, limit, unit in RULES[store]:
        count = len(fields[key].encode('utf-8')) if unit == 'utf8_bytes' else len(fields[key])
        counters.append(dict(field=key, label=label, count=count, limit=limit, unit=unit, over=count > limit))
        if count > limit:
            findings.append(dict(level='error', field=key, message=f'{label}超出长度限制', evidence=f'{count}/{limit} {unit}'))
        if key in ('name', 'description') and not fields[key].strip():
            findings.append(dict(level='warning', field=key, message=f'{label}尚未填写', evidence='空字段'))
    if store == 'apple':
        terms = [v.strip() for v in re.split('[,，]', fields['keywords']) if v.strip()]
        seen = set()
        for word in terms:
            key = unicodedata.normalize('NFKC', word).casefold()
            if key in seen:
                findings.append(dict(level='warning', field='keywords', message='关键词重复', evidence=word))
            seen.add(key)
    folded = {k: unicodedata.normalize('NFKC', v).casefold() for k, v in fields.items()}
    coverage = []
    for word in list(dict.fromkeys(keywords))[:500]:
        key = unicodedata.normalize('NFKC', word).casefold()
        coverage.append({'keyword': word, 'fields': [k for k, v in folded.items() if key and key in v]})
    return {'fields': fields, 'counters': counters, 'findings': findings, 'coverage': coverage,
            'valid': not any(r['level'] == 'error' for r in findings), 'rules_version': RULES_VERSION,
            'sources': SOURCES, 'scope': '文本包含检查，不代表商店索引、搜索流量或排名保证；草稿尚未发布。'}


def workspace(db, value):
    m = market(value)
    key = (m['store'], m['app_id'], m['country'], m['language'])
    listing = db.execute('SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s', key).fetchone()
    payload = json.loads(listing['payload']) if listing and listing['payload'] else {}
    snapshot = db.execute('SELECT id,observed_at FROM snapshots WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY id DESC LIMIT 1', key).fetchone()
    fields = {r[0]: str(payload.get(r[0]) or '') for r in RULES[m['store']]}
    # Apple keywords/subtitle are not returned by our public lookup source.
    unknown = [k for k in fields if k not in payload or payload[k] is None]
    words = app_coverage(db, *key)['coverage']
    rows = db.execute('SELECT id,name,revision,baseline_snapshot_id,created_at,updated_at FROM aso_drafts WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY updated_at DESC LIMIT 100', key).fetchall()
    return {'market': m, 'metadata': fields, 'unavailable_fields': unknown, 'snapshot': snapshot,
            'creative': {'icon': payload.get('icon'), 'screenshots': payload.get('screenshots') or []},
            'last_success': listing['last_success'] if listing else None, 'drafts': rows,
            'keywords': [{'keyword': w['target']['keyword'], 'position': w['position'], 'change': w['change'],
                          'stale': w['stale'], 'observed_at': w['latest_success']['observed_at'] if w['latest_success'] else None} for w in words],
            'checks': check_metadata(m['store'], fields, [w['target']['keyword'] for w in words])}


def save_draft(db, value):
    m = market(value)
    fields = normalize_metadata(m['store'], value.get('metadata'))
    name = text(value.get('name', '文案草稿'))
    note = value.get('note', '')
    if not isinstance(note, str) or len(note) > 4000:
        raise ValueError('备注最多4000字符')
    now = time.time()
    identifier = value.get('id')
    key = (m['store'], m['app_id'], m['country'], m['language'])
    with db:
        if identifier is not None:
            if type(identifier) is not int or type(value.get('revision')) is not int:
                raise ValueError('更新草稿必须携带ID和版本')
            row = db.execute('SELECT * FROM aso_drafts WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
            if not row or tuple(row[k] for k in ('store', 'app_id', 'country', 'language')) != key:
                raise ValueError('草稿不属于当前应用市场')
            if row['revision'] != value['revision']:
                raise ValueError('草稿已被更新，请重新加载后再保存')
            revision = row['revision'] + 1
            db.execute('UPDATE aso_drafts SET name=%s,revision=%s,metadata=%s,note=%s,updated_at=%s WHERE id=%s',
                       (name, revision, dump(fields), note, now, identifier))
        else:
            baseline = db.execute('SELECT id FROM snapshots WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY id DESC LIMIT 1', key).fetchone()
            db.execute('''INSERT INTO aso_drafts(store,app_id,country,language,name,metadata,baseline_snapshot_id,note,created_at,updated_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (*key, name, dump(fields), baseline['id'] if baseline else None, note, now, now))
            identifier, revision = db.cursor.lastrowid, 1
        checks = check_metadata(m['store'], fields)
        db.execute('INSERT INTO aso_draft_versions VALUES(%s,%s,%s,%s,%s,%s)', (identifier, revision, dump(fields), note, dump(checks), now))
    return {'id': identifier, 'revision': revision, 'checks': checks, 'published': False}


def draft_detail(db, identifier):
    row = db.execute('SELECT * FROM aso_drafts WHERE id=%s', (identifier,)).fetchone()
    if not row:
        raise ValueError('草稿不存在')
    row['metadata'] = json.loads(row['metadata'])
    versions = db.execute('SELECT * FROM aso_draft_versions WHERE draft_id=%s ORDER BY revision DESC', (identifier,)).fetchall()
    for version in versions:
        version['metadata'] = json.loads(version['metadata'])
        version['checks'] = json.loads(version['checks'])
    return row | {'versions': versions, 'published': False}
