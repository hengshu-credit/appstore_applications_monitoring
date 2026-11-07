"""Private deployment bindings for otherwise provider-neutral adapters."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def profile(cfg, name, db=None):
    if db is not None:
        row=db.execute('SELECT settings FROM data_source_configs WHERE name=%s',(name,)).fetchone()
        if row and json.loads(row['settings']).get('origin'):return validate(json.loads(row['settings']))
    profiles = cfg.get('source_profiles')
    if profiles is None:
        path = Path(cfg['data_dir']) / 'secrets' / 'source-profiles.json'
        profiles = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return validate(profiles.get(name, {}))


def validate(value):
    value = dict(value)
    origin = value.get('origin', '')
    if not isinstance(origin,str):raise ValueError('接入地址须为 HTTPS URL')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None,443):
        raise ValueError('请在数据源配置中设置有效的 HTTPS 接入地址')
    hosts = value.get('hosts', [parsed.hostname])
    if not isinstance(hosts, list) or not hosts or any(not isinstance(h,str) or len(h)>253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',h) for h in hosts) or parsed.hostname not in hosts:
        raise ValueError('数据接入域名配置无效')
    value.update(origin=origin.rstrip('/'), hosts=hosts)
    return value


def bind(client, cfg, name, db=None):
    client.source_profile = profile(cfg, name, db)
    return client
