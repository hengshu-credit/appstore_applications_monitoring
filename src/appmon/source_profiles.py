"""Private deployment bindings for otherwise provider-neutral adapters."""
import json
from pathlib import Path
from urllib.parse import urlsplit


def profile(cfg, name):
    profiles = cfg.get('source_profiles')
    if profiles is None:
        path = Path(cfg['data_dir']) / 'secrets' / 'source-profiles.json'
        profiles = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    value = dict(profiles.get(name, {}))
    origin = value.get('origin', '')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None,443):
        raise ValueError('请先在服务器私有配置中设置数据接入地址')
    hosts = value.get('hosts', [parsed.hostname])
    if not isinstance(hosts, list) or not hosts or any(not isinstance(h,str) or '/' in h or ':' in h for h in hosts) or parsed.hostname not in hosts:
        raise ValueError('数据接入域名配置无效')
    value.update(origin=origin.rstrip('/'), hosts=hosts)
    return value


def bind(client, cfg, name):
    client.source_profile = profile(cfg, name)
    return client
