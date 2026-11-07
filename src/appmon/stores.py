import html
import json
import math
import re
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote_plus, urlsplit

import requests

from . import collection_limits


class FetchError(Exception):
    def __init__(self, message, retry_after=0, details=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.details = details or {}


class CollectionDeferred(FetchError):
    """No upstream request was admitted; keep the task pending, not failed."""


@dataclass
class Result:
    outcome: str
    payload: dict | None = None
    raw: bytes = b""


APPLE_MAPPING = {"name": "trackName", "version": "version", "description": "description",
                       "developer": "artistName", "developer_id": "artistId", "category": "primaryGenreName",
                       "price": "price", "currency": "currency", "rating": "averageUserRating",
                       "ratings": "userRatingCount", "updated": "currentVersionReleaseDate",
                       "released": "releaseDate", "bundle_id": "bundleId", "url": "trackViewUrl",
                       "icon": "artworkUrl512", "screenshots": "screenshotUrls",
                       "release_notes": "releaseNotes", "languages": "languageCodesISO2A",
                       "category_id": "primaryGenreId", "genre_ids": "genreIds", "genres": "genres",
                       "size_bytes": "fileSizeBytes", "minimum_os": "minimumOsVersion", "age_rating": "contentAdvisoryRating",
                       "seller": "sellerName", "developer_url": "sellerUrl", "devices": "supportedDevices"}

GOOGLE_MAPPING = {"name": "title", "version": "version", "description": "description",
                       "developer": "developer", "developer_id": "developerId", "category": "genreId",
                       "price": "price", "currency": "currency", "rating": "score", "ratings": "ratings",
                       "installs": "minInstalls", "installs_reported": "realInstalls",
                       "has_ads": "adSupported", "has_iap": "offersIAP",
                       "updated": "updated", "released": "released", "url": "url",
                       "icon": "icon", "screenshots": "screenshots", "video": "video",
                       "release_notes": "recentChanges", "summary": "summary", "category_id": "genreId"}

APPLE_MAPPING.update({
    "app_id":"trackId", "ipad_screenshots":"ipadScreenshotUrls", "appletv_screenshots":"appletvScreenshotUrls",
    "current_version_rating":"averageUserRatingForCurrentVersion", "current_version_ratings":"userRatingCountForCurrentVersion",
    "features":"features", "advisories":"advisories", "is_game_center":"isGameCenterEnabled",
    "formatted_price":"formattedPrice", "seller_url":"sellerUrl",
})
GOOGLE_MAPPING.update({
    "reviews":"reviews", "rating_distribution":"histogram", "content_rating":"contentRating",
    "content_rating_description":"contentRatingDescription", "minimum_os":"androidVersion",
    "developer_email":"developerEmail", "developer_website":"developerWebsite", "developer_address":"developerAddress",
    "developer_legal_name":"developerLegalName", "developer_legal_email":"developerLegalEmail",
    "developer_legal_address":"developerLegalAddress", "developer_legal_phone":"developerLegalPhoneNumber",
    "privacy_url":"privacyPolicy", "iap_range":"IAPRange", "categories":"categories",
    "header_image":"headerImage", "video_image":"videoImage", "preview_video":"previewVideo",
    "sale":"sale", "original_price":"originalPrice", "price_text":"priceText",
    "early_access":"earlyAccessEnabled", "preregister":"preregister", "play_pass":"isAvailableInPlayPass",
})

def detail_payload(app, mapping, app_id):
    payload={key:app.get(value) for key,value in mapping.items()}
    payload["app_id"]=str(app_id)
    # Preserve fields returned by this response without another network request.
    payload["store_fields"]=app
    return payload

def google_detail(data, app_id):
    from appgoblin_play_scraper.constants.element import ElementSpecs
    try:
        identity=data[1][2][77][0]
        if identity != app_id:
            raise ValueError("Detail application identity mismatch")
        app={name:spec.extract_content({"ds:5":data}) for name,spec in ElementSpecs.Detail.items()}
        if not app.get("title"):
            raise ValueError("Missing detail title")
        app["appId"]=app_id
        return app
    except (ValueError,TypeError,IndexError,KeyError) as exc:
        raise FetchError("Google detail identity or schema mismatch") from exc

class Client:
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.session = requests.Session()
        if cfg.get("tls_maximum_version") == "1.2":
            self.session.mount("https://", TLS12Adapter())
        self.session.headers["User-Agent"] = "Mozilla/5.0 (compatible; AppPoolMonitor/0.1)"
        self.progress = None
        self.request_guard = None
        self.rpc_coalescer = cfg.get('_rpc_coalescer')
        self.lookup_coalescer = cfg.get('_lookup_coalescer')

    def report(self,event,message):
        if self.progress:
            self.progress(event,message)

    def get(self, store, url, params=None, headers=None):
        return self.request(store, "GET", url, params=params, headers=headers)

    def request(self, store, method, url, **kwargs):
        ticket=uuid.uuid4().hex
        try:
            deadline = time.monotonic() + max(1, self.cfg.get('lease_seconds',600)-self.cfg['timeout_seconds']*2)
            for attempt in range(2):
                while True:
                    if self.request_guard:
                        self.request_guard()
                    delay = collection_limits.admission(self.db,self.cfg,store,ticket=ticket)
                    if delay is None:
                        raise CollectionDeferred('整体采集已暂停', 60)
                    if delay == 0:
                        break
                    if time.monotonic()+delay >= deadline:
                        raise CollectionDeferred('等待共享限速窗口，稍后重试', delay)
                    self.report('rate_wait', f'等待整体 / 商店限速窗口：{delay:.1f} 秒')
                    time.sleep(min(delay, 2))  # re-read settings and cooldown after every wait
                self.report('request', f'{method} 请求 {urlsplit(url).hostname}；超时上限 {self.cfg["timeout_seconds"]} 秒')
                started = time.monotonic()
                details=dict(stage='http_request',method=method,host=urlsplit(url).hostname,timeout_seconds=self.cfg['timeout_seconds'])
                try:
                    if method == "GET":
                        r = self.session.get(url, timeout=self.cfg["timeout_seconds"], **kwargs)
                    else:
                        r = self.session.request(method, url, timeout=self.cfg["timeout_seconds"], **kwargs)
                except requests.RequestException as e:
                    from .collection_lineage import record_http
                    record_http(self.db,store,url,elapsed=time.monotonic()-started,lane=getattr(self,'lineage_lane',None))
                    # Retrying read-only TLS connection failures uses a new verified connection.
                    # Certificate verification errors remain visible and are not bypassed.
                    readonly = method in ('GET','HEAD') or (method=='POST' and urlsplit(url).hostname=='play.google.com' and '/batchexecute' in urlsplit(url).path)
                    certificate_error = 'CERTIFICATE_VERIFY_FAILED' in str(e)
                    if attempt==0 and readonly and isinstance(e,requests.exceptions.SSLError) and not certificate_error:
                        self.session.close()
                        self.report('transport_retry','TLS 连接中断，重建连接后重试一次')
                        continue
                    if isinstance(e,requests.exceptions.SSLError):
                        details['note']='certificate_verification_failed' if certificate_error else 'tls_connection_error'
                    # Do not persist exception URLs, proxy credentials or bearer tokens.
                    raise FetchError(type(e).__name__,details=details) from e
                break
            from .collection_lineage import record_http
            record_http(self.db,store,url,r.status_code,time.monotonic()-started,lane=getattr(self,'lineage_lane',None))
            self.report('response', f'HTTP {r.status_code}；响应耗时 {time.monotonic()-started:.2f} 秒')
            if r.status_code in (403, 429) or r.status_code >= 500:
                delay = 300
                value = r.headers.get("Retry-After", "")
                try:
                    seconds = float(value)
                    if math.isfinite(seconds):
                        delay = max(delay, seconds)
                except ValueError:
                    try:
                        delay = max(delay, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError):
                        pass
                delay = collection_limits.cooldown(self.db,self.cfg,store,delay)
                raise FetchError(f"HTTP {r.status_code}", delay, details|dict(status_code=r.status_code,retry_after_seconds=delay))
            if r.status_code not in (200, 404):
                raise FetchError(f"HTTP {r.status_code}",details=details|dict(status_code=r.status_code))
            return r
        finally:collection_limits.withdraw(self.db,ticket)

    def fetch(self, job):
        store, app_id = job["store"], job["app_id"]
        if store == "apple":
            result = self.fetch_many([job])[0]
            if isinstance(result, FetchError):
                raise result
            return result
        else:
            from appgoblin_play_scraper.features.app import parse_dom
            r = self.get(store, "https://play.google.com/store/apps/details",
                         {"id": app_id, "gl": job["country"], "hl": job["language"]})
            if r.status_code == 404:
                return Result("missing", raw=r.content)
            if "consent.google" in r.url or "recaptcha" in r.text.lower() or "unusual traffic" in r.text.lower():
                raise FetchError("Google consent or challenge page")
            try:
                app = parse_dom(r.text, app_id, r.url)
                if not app.get("title") or app.get("appId") != app_id:
                    raise ValueError("Missing title")
            except Exception as e:
                raise FetchError("Google parser failed; retain previous state") from e
            mapping = GOOGLE_MAPPING
        payload = detail_payload(app,mapping,app_id)
        return Result("ok", payload, r.content)

    def fetch_many(self, jobs):
        """Bounded store-specific requests; ambiguous omissions retry singly."""
        if not jobs:
            return []
        if jobs[0]['store']=='google':
            return self.fetch_google_many(jobs)
        from .collection_batch import can_batch
        if self.lookup_coalescer and all(not j.get('failures') or can_batch(j) for j in jobs):
            if len(jobs)>200 or len({(j['store'],j['country'],j.get('language','en')) for j in jobs})!=1 or jobs[0]['store']!='apple':
                raise ValueError('A detail batch must contain at most 200 jobs in one market')
            return self.lookup_coalescer.submit(self,jobs[0],[('lookup',j['app_id']) for j in jobs])[0]
        return self.fetch_apple_many(jobs)

    def fetch_apple_many(self,jobs):
        if len(jobs) > 200 or len({(j['store'],j['country'],j.get('language','en')) for j in jobs}) != 1:
            raise ValueError('A detail batch must contain at most 200 jobs in one market')
        if jobs[0]['store'] != 'apple':
            raise ValueError('This store does not support multi-ID detail lookup')
        ids = [j['app_id'] for j in jobs]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate batch IDs')
        language = jobs[0].get('language','en')
        try:
            r = self.get('apple', 'https://itunes.apple.com/lookup',
                         {'id':','.join(ids),'country':jobs[0]['country'],'entity':'software','lang':language,'limit':200},
                         headers={'Accept-Language':language})
        except FetchError as exc:
            if str(exc) not in ('HTTP 413','HTTP 414') or len(jobs)<2:raise
            middle=len(jobs)//2
            return self.fetch_apple_many(jobs[:middle])+self.fetch_apple_many(jobs[middle:])
        if r.status_code != 200:
            raise FetchError('Apple lookup endpoint unavailable')
        try:
            data = r.json()
            rows = data['results']
            if not isinstance(rows,list) or type(data.get('resultCount')) is not int or data['resultCount'] != len(rows):
                raise ValueError('Bad lookup schema')
            found = {}
            for app in rows:
                identifier = str(app.get('trackId'))
                if identifier not in ids or identifier in found or app.get('wrapperType') != 'software' or not app.get('trackName'):
                    raise ValueError('Invalid lookup item')
                found[identifier] = app
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise FetchError('Apple response schema changed') from exc
        return [Result('ok', detail_payload(found[i],APPLE_MAPPING,i), r.content) if i in found
                else Result('missing',raw=r.content) if len(ids)==1
                else FetchError('Apple batch omitted this ID; verify with a single lookup',60) for i in ids]

    def fetch_google_many(self, jobs):
        from .google_rpc import same_market, template, rpc_many
        from urllib.parse import urlencode
        same_market(jobs)
        calls=[]
        for job in jobs:
            payload=template('detail');payload[-1][0][0]=job['app_id']
            calls.append(('Ws7gDc',payload))
        payloads,raw=rpc_many(self,jobs[0],calls)
        results=[]
        for job,data in zip(jobs,payloads):
            if isinstance(data,FetchError):
                results.append(data);continue
            try:
                app=google_detail(data,job['app_id'])
                app['url']='https://play.google.com/store/apps/details?'+urlencode({'id':job['app_id'],'gl':job['country'],'hl':job['language']})
                results.append(Result('ok',detail_payload(app,GOOGLE_MAPPING,job['app_id']),raw))
            except (ValueError,TypeError,IndexError,KeyError,FetchError):
                results.append(FetchError('Google batch detail schema changed; retry separately',60))
        return results

    def discover(self, store, country, kind, term=""):
        if store == "apple":
            if kind == "chart":
                r = self.get(store, f"https://rss.marketingtools.apple.com/api/v2/{country}/apps/{term}/100/apps.json")
                if r.status_code != 200:
                    raise FetchError("Apple chart unavailable")
                values = r.json()["feed"]["results"]
                return [(store, str(x["id"]), x.get("name"), None) for x in values]
            params = {"country": country, "entity": "software", "limit": 200}
            params["id" if kind == "developer" else "term"] = term
            r = self.get(store, "https://itunes.apple.com/" + ("lookup" if kind == "developer" else "search"), params)
            values = r.json()["results"]
            return [(store, str(x["trackId"]), x.get("trackName"), x.get("primaryGenreName"))
                    for x in values if x.get("wrapperType") == "software"]
        params = {"gl": country, "hl": self.cfg["language"]}
        if kind == "search":
            url = "https://play.google.com/store/search"
            params.update(q=term, c="apps")
        elif kind == "developer":
            url = "https://play.google.com/store/apps/" + ("dev" if term.isdigit() else "developer")
            params["id"] = unquote_plus(term)
        else:
            url = "https://play.google.com/store/apps/category/" + term
        r = self.get(store, url, params)
        if r.status_code != 200 or "consent.google" in r.url:
            raise FetchError("Google discovery page unavailable")
        ids = set(re.findall(r"/store/apps/details\?id=([A-Za-z0-9_.]+)", html.unescape(r.text)))
        if not ids:
            raise FetchError("No Google detail links: empty results or parser change")
        return [(store, app_id, None, None) for app_id in sorted(ids)]


class TLS12Adapter(requests.adapters.HTTPAdapter):
    """Compatibility with proxies that reset TLS 1.3; never disable verification."""
    @staticmethod
    def context():
        context = ssl.create_default_context()
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        return context

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self.context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self.context()
        return super().proxy_manager_for(*args, **kwargs)
