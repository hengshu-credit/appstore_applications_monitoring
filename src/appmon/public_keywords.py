"""Auditable public keyword observations; never search-volume or paid-keyword claims."""
import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

from .catalog import APPLE_COUNTRIES
from .db import dump
from .keyword_discovery import url
from .keyword_stores import parse_google
from .keywords import validate_target
from .locales import PREFERENCES, language_for

# Query seeds only. A seed becomes a candidate ONLY when a source returns it.
# These are retrieval vocabulary, not translated observations or popularity data.
SEEDS = {
    'en': 'loan|personal loan|cash advance|credit|buy now pay later|loan app|online loan|instant loan|mobile banking|credit card',
    'fr': 'prêt|crédit|prêt personnel|prêt en ligne|crédit rapide',
    'ar': 'قرض|تمويل|قرض شخصي|قروض فورية',
    'es': 'préstamos|crédito|préstamos personales|préstamos en línea|préstamos rápidos',
    'pt': 'empréstimo|crédito|empréstimo pessoal|empréstimo online',
    'pt-br': 'empréstimo|crédito|empréstimo pessoal|empréstimo online|empréstimo consignado',
    'nl': 'lening|geld lenen|persoonlijke lening', 'ru': 'займ|кредит|микрозайм|займ онлайн',
    'de': 'Kredit|Darlehen|Privatkredit|Sofortkredit',
    'zh-tw': '貸款|借錢|信用貸款|小額貸款|分期付款',
    'zh-cn': '贷款|借钱|信用贷款|小额贷款|分期付款|网贷',
    'it': 'prestito|finanziamento|prestito personale', 'sw': 'mkopo|mikopo|mkopo wa haraka',
    'fa': 'وام|وام فوری|وام آنلاین', 'sq': 'kredi|kredi personale',
    'sv': 'lån|privatlån|snabblån', 'hr': 'kredit|brzi kredit',
    'ms': 'pinjaman|pinjaman peribadi|pinjaman wang', 'ne': 'ऋण|कर्जा',
    'no': 'lån|forbrukslån', 'el': 'δάνειο|προσωπικό δάνειο',
    'da': 'lån|forbrugslån', 'ko': '대출|신용대출|소액대출',
    'ro': 'credit|împrumut|credit rapid', 'sr': 'кредит|позајмица',
    'ca': 'préstec|crèdit', 'hy': 'վարկ|առցանց վարկ',
    'az': 'kredit|onlayn kredit', 'bn': 'ঋণ|লোন', 'bg': 'кредит|бърз кредит',
    'cs': 'půjčka|úvěr', 'et': 'laen|kiirlaen', 'am': 'ብድር',
    'fi': 'laina|kulutusluotto', 'ka': 'სესხი|ონლაინ სესხი',
    'hu': 'hitel|személyi kölcsön',
    'id': 'pinjaman|pinjaman online|pinjaman tunai|pinjol|cicilan|paylater',
    'he': 'הלוואה|הלוואה מיידית', 'hi': 'लोन|पर्सनल लोन|ऋण|तुरंत लोन',
    'is': 'lán|smálán', 'ja': 'ローン|カードローン|キャッシング|借入',
    'ky': 'насыя|онлайн насыя', 'km': 'កម្ចី|ប្រាក់កម្ចី',
    'kk': 'несие|онлайн несие|микрокредит', 'lo': 'ເງິນກູ້',
    'si': 'ණය|ක්ෂණික ණය', 'lt': 'paskola|greitas kreditas',
    'lv': 'kredīts|aizdevums', 'mk': 'кредит|брз кредит',
    'my': 'ချေးငွေ', 'mn': 'зээл|онлайн зээл',
    'fil': 'utang|pautang|loan app|online loan|cash loan',
    'ur': 'قرض|فوری قرض', 'pl': 'pożyczka|kredyt|chwilówka',
    'sl': 'posojilo|kredit', 'sk': 'pôžička|úver',
    'th': 'สินเชื่อ|เงินกู้|กู้เงิน|ยืมเงิน|สินเชื่อส่วนบุคคล',
    'tr': 'kredi|ihtiyaç kredisi|nakit avans', 'uk': 'кредит|позика|мікрокредит',
    'vi': 'vay tiền|vay online|vay tín chấp|vay nhanh|trả góp',
}
LABELS = {'suggest': '公开搜索联想（非搜索量）', 'apple': 'App Store 金融榜文案（ASO候选）',
          'google': 'Google Play 搜索文案（ASO候选）', 'website': '金融APP官网文案',
          'ad_case': '广告平台公开案例（非投放关键词报告）'}
IDENTITY = ('store', 'country', 'language', 'keyword')


def normalized(text):
    # Presentation controls from store titles are not visible search text.
    text = ''.join(c for c in unicodedata.normalize('NFKC', text) if unicodedata.category(c) != 'Cf')
    return ' '.join(text.split())


def vocabulary(language):
    return list(dict.fromkeys(SEEDS.get(language, SEEDS.get(language.split('-')[0], '')).split('|') + SEEDS['en'].split('|')))


def matches(term, text):
    # Unicode letters/marks: do not match 'loan' inside 'Sloane' or credit in credited.
    term, text = term.casefold(), text.casefold()
    if re.fullmatch(r'[\x00-\x7f]+', term) or ' ' in term:
        return re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text) is not None
    return term in text


def extract_terms(text, language, *, title=False):
    text = normalized(text)
    terms = {v.casefold() for v in vocabulary(language) if v and matches(v, text)}
    if title:
        # Titles are intact ASO/brand candidates, not tokenized word salad.
        if 2 <= len(text) <= 100:
            terms.add(text.casefold())
        for part in re.split(r'[:：|–—]|\s-\s', text):
            part = part.strip(' .,!?')
            if 2 <= len(part) <= 60 and any(matches(v, part) for v in vocabulary(language) if v):
                terms.add(part.casefold())
    return sorted(terms)


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if tag == 'meta':
            a = dict(attrs)
            if a.get('name') == 'description' or a.get('property') in ('og:title', 'og:description'):
                self.parts.append(a.get('content', ''))

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def make_jobs(countries, sources=('suggest', 'apple', 'google'), seeds_per_market=3, pages=()):
    jobs = []
    for country in sorted(set(countries)):
        if country not in PREFERENCES['countries']:
            raise ValueError('Unknown country: ' + country)
        language = language_for('google', country)
        seeds = [x for x in SEEDS.get(language, '').split('|') if x]
        if not seeds:
            raise ValueError('Missing native query seeds: ' + language)
        if 'suggest' in sources:
            for seed in seeds[:seeds_per_market]:
                jobs.append(dict(kind='suggest', country=country, language=language,
                                 url='https://suggestqueries.google.com/complete/search?' + urlencode(
                                     dict(client='firefox', gl=country, hl=language, q=seed)), query=seed))
        if 'google' in sources:
            jobs.append(dict(kind='google', country=country, language=language,
                             url='https://play.google.com/store/search?' + urlencode(dict(c='apps', gl=country, hl=language, q=seeds[0]))))
        if 'apple' in sources and country in APPLE_COUNTRIES:
            jobs.append(dict(kind='apple', country=country, language=language_for('apple', country),
                             url=f'https://itunes.apple.com/{country}/rss/topfreeapplications/limit=100/genre=6015/json'))
    for page in pages:
        if page['country'] not in countries:
            continue
        if page['kind'] not in ('website', 'ad_case'):
            raise ValueError('Page kind must be website or ad_case')
        url(page['url'])
        language_for('google', page['country'], page['language'])
        jobs.append(dict(page))
    return jobs


def parse_observation(job, body):
    kind, country, language = (job[k] for k in ('kind', 'country', 'language'))
    items = []

    def add(term, evidence, source_url=None, app_id=None, role='finance_phrase'):
        term = normalized(term).casefold()
        if not 2 <= len(term) <= 100:
            return
        # Evidence must contain the original contiguous phrase (ignoring case/spacing).
        if term not in normalized(evidence).casefold():
            raise ValueError('Extracted term missing from evidence')
        at = normalized(evidence).casefold().find(term)
        excerpt = normalized(evidence)[max(0, at-40):at+len(term)+80]
        stores = [kind] if kind in ('apple', 'google') else ['google'] + (['apple'] if country in APPLE_COUNTRIES else [])
        for store in stores:
            try:
                target = validate_target(dict(store=store, country=country, language=language, keyword=term))
            except ValueError:
                # A malformed title must not discard the other apps on a page.
                continue
            items.append({k: target[k] for k in IDENTITY} | dict(source_url=source_url or job['url'],
                evidence=excerpt, app_id=app_id, role=role, kind=kind,
                geography='requested_storefront' if kind in ('apple', 'google') else
                          'requested_gl_not_verified' if kind == 'suggest' else 'curated_country_page',
                language_basis='request_configuration_not_detected', search_volume=None))

    if kind == 'suggest':
        payload = json.loads(body)
        if not isinstance(payload, list) or len(payload) < 2 or payload[0] != job['query'] or not isinstance(payload[1], list):
            raise ValueError('Unrecognized suggestion response')
        for term in payload[1]:
            if isinstance(term, str) and any(matches(v, term) for v in vocabulary(language) if v):
                add(term, term, role='search_suggestion')
    elif kind == 'apple':
        feed = json.loads(body)['feed']
        if f'/{country}/rss/' not in feed.get('id', {}).get('label', ''):
            raise ValueError('Apple storefront fallback or missing identity')
        entries = feed.get('entry', [])
        if not isinstance(entries, list):
            raise ValueError('Unrecognized Apple entries')
        for entry in entries:
            if str(entry['category']['attributes']['im:id']) != '6015':
                continue
            link = entry['id']['label']
            app_id = entry['id']['attributes']['im:id']
            title = entry['im:name']['label']
            for term in extract_terms(title, language, title=True):
                add(term, title, link, app_id, 'aso_title')
            description = entry.get('summary', {}).get('label', '')
            for term in extract_terms(description, language):
                add(term, description, link, app_id)
    elif kind == 'google':
        for app in parse_google(body):
            title = app['name']
            # Search can return irrelevant apps. Require an observed finance anchor.
            if not extract_terms(title, language):
                continue
            link = 'https://play.google.com/store/apps/details?' + urlencode(dict(id=app['app_id'], gl=country, hl=language))
            for term in extract_terms(title, language, title=True):
                add(term, title, link, app['app_id'], 'aso_title')
    else:
        parser = VisibleText()
        parser.feed(body)
        visible = normalized(' '.join(parser.parts))
        terms = extract_terms(visible, language)
        # Reviewed page-specific phrases still require exact live-text evidence.
        terms += [t for t in job.get('phrases', []) if matches(t, visible)]
        for term in terms:
            add(term, visible, role='ad_case_phrase' if kind == 'ad_case' else 'website_phrase')
    unique = {}
    for item in items:
        unique[tuple(item[k] for k in IDENTITY) + (item['source_url'],)] = item
    return list(unique.values())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def challenge_page(text, final_url):
    # Legitimate loan application pages can embed reCAPTCHA in a form. Only
    # recognize challenge pages themselves, not a script's library name.
    if 'consent.google' in urlsplit(final_url).hostname:
        return True
    title = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
    title = normalized(title[1]).casefold() if title else ''
    return any(marker in title for marker in ('captcha', 'just a moment', 'access denied', 'verify you are human')) or (
        'google.com' in (urlsplit(final_url).hostname or '') and 'unusual traffic' in text.casefold())


class Fetcher:
    """Limit per-host start rate; retain TLS validation and stop on HTTP blocks."""
    def __init__(self, interval=1.0, timeout=20, proxy=None):
        self.interval, self.timeout = interval, timeout
        self.proxies = {'http': proxy, 'https': proxy} if proxy else None
        self.lock = threading.Lock()
        self.next_request = {}
        self.blocked = {}

    def __call__(self, job):
        host = urlsplit(job['url']).hostname
        with self.lock:
            if host in self.blocked:
                raise ValueError('Host paused after HTTP ' + str(self.blocked[host]))
            start = max(time.monotonic(), self.next_request.get(host, 0))
            self.next_request[host] = start + self.interval
        time.sleep(max(0, start-time.monotonic()))
        with self.lock:
            if host in self.blocked:
                raise ValueError('Host paused after HTTP ' + str(self.blocked[host]))
        with requests.get(job['url'], timeout=(8, self.timeout), proxies=self.proxies,
                          headers={'User-Agent': 'appmon-keyword-research/1.0',
                                   'Accept-Language': job['language']}, stream=True) as response:
            if response.status_code in (403, 429):
                with self.lock:
                    self.blocked[host] = response.status_code
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 8_000_000:
                    raise ValueError('Response exceeds 8 MB')
                chunks.append(chunk)
            raw = b''.join(chunks)
            text = raw.decode('utf-8-sig')
            if challenge_page(text, response.url):
                raise ValueError('Challenge/consent page; no extraction')
            return raw, text, response.url


def collect(jobs, output, *, workers=6, fetcher=None, retry_errors=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'observations').mkdir(exist_ok=True)
    (output / 'raw').mkdir(exist_ok=True)
    plan = {'version': 1, 'jobs': jobs}
    plan_path = output / 'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text(encoding='utf-8')) != plan:
        raise ValueError('Output belongs to a different collection plan; use a new directory')
    atomic_json(plan_path, plan)
    fetcher = fetcher or Fetcher()

    def run(job):
        key = digest(job)
        path = output / 'observations' / (key + '.json')
        if path.exists():
            old = json.loads(path.read_text(encoding='utf-8'))
            if old['status'] == 'ok' or not retry_errors:
                return old
        observation = dict(job=job, observed_at=datetime.now(timezone.utc).isoformat(), items=[])
        try:
            raw, body, final_url = fetcher(job)
            raw_sha = hashlib.sha256(raw).hexdigest()
            raw_path = output / 'raw' / (key + '.bin')
            raw_path.write_bytes(raw)
            observation.update(raw_sha256=raw_sha, raw_file=str(raw_path.relative_to(output)), final_url=final_url)
            observation.update(status='ok', items=parse_observation(job, body))
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            # No credentials, proxy configuration or response dumps in errors.
            detail = str(exc)[:160]
            if isinstance(exc, requests.RequestException):
                detail = 'HTTP ' + str(exc.response.status_code) if exc.response is not None else 'request failed'
            observation.update(status='error', error=type(exc).__name__ + ': ' +
                               detail)
        atomic_json(path, observation)
        return observation

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        for n, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if n % 50 == 0 or n == len(jobs):
                print(json.dumps({'completed': n, 'total': len(jobs), 'status': dict(Counter(r['status'] for r in results))}), flush=True)
    return export(output, results)


def export(output, results):
    records = {}
    coverage = []
    for result in sorted(results, key=lambda r: digest(r['job'])):
        job = result['job']
        coverage.append({k: job[k] for k in ('country', 'language', 'kind', 'url')} |
                        dict(status=result['status'], candidates=len(result['items']), error=result.get('error')))
        for item in result['items']:
            key = tuple(item[k] for k in IDENTITY)
            row = records.setdefault(key, {k: item[k] for k in IDENTITY} | {'sources': []})
            source = {k: v for k, v in item.items() if k not in IDENTITY}
            source.update(observed_at=result['observed_at'], raw_sha256=result['raw_sha256'],
                          raw_file=result['raw_file'], request_url=job['url'])
            row['sources'].append(source)
    target = output / 'keywords.jsonl'
    with target.open('w', encoding='utf-8', newline='\n') as stream:
        for key in sorted(records):
            stream.write(json.dumps(records[key], ensure_ascii=False, sort_keys=True) + '\n')
    # Full read-back identity/evidence checks, not a sampled count.
    checked = set()
    for line in target.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        key = tuple(row[k] for k in IDENTITY)
        if key in checked or validate_target(row)['keyword'] != row['keyword']:
            raise ValueError('Duplicate or invalid exported identity')
        if any(row['keyword'] not in normalized(s['evidence']).casefold() for s in row['sources']):
            raise ValueError('Export lacks verbatim evidence')
        checked.add(key)
    if len(checked) != len(records):
        raise ValueError('Export count mismatch')
    manifest = dict(version=1, terms=len(records), source_records=sum(len(r['sources']) for r in records.values()),
                    countries_requested=len({j['country'] for j in coverage}),
                    countries_with_candidates=len({k[1] for k in records}),
                    requests=len(results), statuses=dict(Counter(r['status'] for r in results)),
                    source_counts=dict(Counter(s['kind'] for r in records.values() for s in r['sources'])),
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest(), coverage=coverage,
                    note='公开来源候选词；不是搜索量、真实投流关键词或实采搜索排名。gl/hl 只是请求参数，不能证明当地用户搜索行为。')
    atomic_json(output / 'manifest.json', manifest)
    return manifest


def reparse(output):
    """Re-extract a completed run from hash-checked raw bodies, without network calls."""
    output = Path(output)
    plan = json.loads((output / 'plan.json').read_text(encoding='utf-8'))
    results = []
    for job in plan['jobs']:
        path = output / 'observations' / (digest(job) + '.json')
        result = json.loads(path.read_text(encoding='utf-8'))
        if result.get('raw_file'):
            raw = (output / result['raw_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != result['raw_sha256']:
                raise ValueError('Raw evidence checksum mismatch')
            try:
                result.update(items=parse_observation(job, raw.decode('utf-8-sig')), status='ok')
                result.pop('error', None)
            except (ValueError, KeyError, TypeError) as exc:
                result.update(items=[], status='error', error=type(exc).__name__ + ': ' + str(exc)[:160])
            atomic_json(path, result)
        results.append(result)
    return export(output, results)


def import_release(db, output):
    """Idempotent observations in existing candidate UI, with source/date attribution."""
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    raw = (output / 'keywords.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['sha256']:
        raise ValueError('Release checksum mismatch')
    groups, identities = {}, set()
    for line in raw.decode('utf-8').splitlines():
        row = json.loads(line)
        target = validate_target(row)
        identity = tuple(target[k] for k in IDENTITY)
        if identity in identities:
            raise ValueError('Duplicate release identity')
        identities.add(identity)
        for source in row['sources']:
            if target['keyword'] not in normalized(source['evidence']).casefold():
                raise ValueError('Missing evidence')
            link = url(source['source_url'])
            period = source['observed_at'][:10]
            label = '公开扩词 / ' + LABELS[source['kind']]
            key = (*identity[:3], link, period, label)
            groups.setdefault(key, set()).add(target['keyword'])
    if len(identities) != manifest['terms']:
        raise ValueError('Release row count mismatch')
    prepared = []
    now = time.time()
    for key, words in groups.items():
        store, country, language, link, period, label = key
        # Same source identity/columns as keyword_discovery.import_candidates,
        # batched across pages so 17,000 apps do not require 17,000 commits.
        source_key = hashlib.sha256(dump([label, link, period]).encode()).hexdigest()
        prepared.extend((store, country, language, word, source_key, label, link,
                         '公开页面词语；原文版权归来源方；未声明开放许可',
                         'appmon public keyword research', None, period, now) for word in sorted(words))
    for start in range(0, len(prepared), 1000):
        with db:
            db.executemany('''INSERT INTO keyword_candidates(store,country,language,keyword,source_key,source,source_url,
                license_name,attribution,corpus_frequency,source_period,imported_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE imported_at=VALUES(imported_at)''', prepared[start:start+1000])
    result = dict(processed_source_records=len(prepared), release_terms=len(identities), groups=len(groups), queued=0)
    atomic_json(output / 'import.json', result)
    return result
