import html
import json
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote_plus

import requests

from .db import reserve


class FetchError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Result:
    outcome: str
    payload: dict | None = None
    raw: bytes = b""


class Client:
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.session = requests.Session()
        if cfg.get("tls_maximum_version") == "1.2":
            self.session.mount("https://", TLS12Adapter())
        self.session.headers["User-Agent"] = "Mozilla/5.0 (compatible; AppPoolMonitor/0.1)"

    def get(self, store, url, params=None):
        delay = reserve(self.db, f"rate:{store}", self.cfg[f"{store}_interval_seconds"])
        time.sleep(delay)
        try:
            r = self.session.get(url, params=params, timeout=self.cfg["timeout_seconds"])
        except requests.RequestException as e:
            # Do not persist exception URLs, proxy credentials or bearer tokens.
            raise FetchError(type(e).__name__) from e
        if r.status_code in (403, 429) or r.status_code >= 500:
            delay = 300
            value = r.headers.get("Retry-After", "")
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    delay = max(delay, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError):
                    pass
            reserve(self.db, f"rate:{store}", delay)
            raise FetchError(f"HTTP {r.status_code}", delay)
        if r.status_code not in (200, 404):
            raise FetchError(f"HTTP {r.status_code}")
        return r

    def fetch(self, job):
        store, app_id = job["store"], job["app_id"]
        if store == "apple":
            r = self.get(store, "https://itunes.apple.com/lookup",
                         {"id": app_id, "country": job["country"], "entity": "software"})
            if r.status_code != 200:
                raise FetchError("Apple lookup endpoint unavailable")
            try:
                data = r.json()
                if not isinstance(data.get("results"), list) or data.get("resultCount") != len(data["results"]):
                    raise ValueError("Bad lookup schema")
                if data["resultCount"] == 0:
                    return Result("missing", raw=r.content)
                app = next(x for x in data["results"] if str(x.get("trackId")) == app_id and x.get("wrapperType") == "software")
                if not app.get("trackName"):
                    raise ValueError("Missing title")
            except (ValueError, TypeError, StopIteration, AttributeError) as e:
                raise FetchError("Apple response schema changed") from e
            mapping = {"name": "trackName", "version": "version", "description": "description",
                       "developer": "artistName", "developer_id": "artistId", "category": "primaryGenreName",
                       "price": "price", "currency": "currency", "rating": "averageUserRating",
                       "ratings": "userRatingCount", "updated": "currentVersionReleaseDate",
                       "released": "releaseDate", "bundle_id": "bundleId", "url": "trackViewUrl"}
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
            mapping = {"name": "title", "version": "version", "description": "description",
                       "developer": "developer", "developer_id": "developerId", "category": "genreId",
                       "price": "price", "currency": "currency", "rating": "score", "ratings": "ratings",
                       "installs": "minInstalls", "updated": "updated", "released": "released", "url": "url"}
        payload = {k: app.get(v) for k, v in mapping.items()}
        return Result("ok", payload, r.content)

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
