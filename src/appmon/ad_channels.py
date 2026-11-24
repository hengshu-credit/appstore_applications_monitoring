"""Media channel identities and access to the documented ad libraries."""
from urllib.parse import parse_qs, urlsplit
import html
import re


SOCIAL = {
    'ad_meta': 'Meta 广告资料库',
    'ad_tiktok': 'TikTok 商业内容库',
    'ad_reddit': 'Reddit Ads 账户',
}
PRIMARY = {
    'Google': ('ad_google','ad_creatives'), 'Meta': ('ad_meta','ad_creatives'),
    'TikTok': ('ad_tiktok','ad_creatives'), 'Reddit': ('ad_reddit',),
    'Instagram': ('ad_meta',), 'Yandex': ('ad_creatives',), 'App Store': ('ad_apple',),
}
EU = frozenset('at be bg hr cy cz dk ee fi fr de gr hu ie it lv lt lu mt nl pl pt ro sk si es se'.split())
MARKETS = {'ad_meta': EU | {'gb'}, 'ad_tiktok': EU | {'is','li','no','gb','ch'}}
ALIASES = {
    'Google': ('google','google ads','google adwords','adwords','admob','google admob','youtube','google.com','doubleclick.net','谷歌'),
    'Meta': ('meta','meta ads','facebook','facebook ads','facebook.com','facebook audience network','meta audience network','脸书'),
    'Instagram': ('instagram','instagram ads','instagram.com','ins','ig'),
    'TikTok': ('tiktok','tiktok ads','tiktok for business','tiktok.com'),
    'Reddit': ('reddit','reddit ads','reddit.com'),
    'Yandex': ('yandex','yandex ads','yandex direct','yandex.direct','yandex.ru','yandex.com','яндекс'),
    'App Store': ('appstore','app store','apple ads','apple search ads','app store ads','asa'),
}
NAMES = {alias: name for name, aliases in ALIASES.items() for alias in aliases}


def canonical(name):
    return NAMES.get(name.strip().casefold(), name.strip()) if name else None


def store_destination(value, m):
    """Return only an exact download URL for the requested store and app."""
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        u = urlsplit(value)
        if u.scheme not in ('http','https') or u.username is not None or u.port not in (None,80,443) or '\\' in value:
            return None
        if m['store'] == 'google' and u.hostname == 'play.google.com' and u.path.rstrip('/') == '/store/apps/details':
            return value if parse_qs(u.query).get('id') == [m['app_id']] else None
        if m['store'] == 'apple' and u.hostname in ('apps.apple.com','itunes.apple.com'):
            match = re.fullmatch(r'/(?:[a-zA-Z]{2}/)?app/(?:[^/]+/)?id([0-9]+)/?', u.path)
            if match and match[1] == m['app_id']:
                return value
    except ValueError:
        pass
    return None


STORE_LINK = re.compile(r'(?<![\w.@/-])(?:https?://)?(?:play\.google\.com|apps\.apple\.com|itunes\.apple\.com)/[^\s<>"\'\\]+')


def store_link_in(texts, m):
    for text in texts:
        for match in STORE_LINK.finditer(html.unescape(text)):
            url = match[0].rstrip('.,);]')
            if not url.startswith(('http://','https://')):
                url = 'https://' + url
            if store_destination(url, m):
                return url
    return None
