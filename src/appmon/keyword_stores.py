"""Ordered public search observations, not personalized device rankings.

Google mappings adapted from MrAdex77/google-play-scraper (MIT); Apple native
search protocol/markets from facundoolano/app-store-scraper (MIT). See licenses.
Only the first response is collected; requested depth is an upper bound.
"""
import json
import re
from dataclasses import dataclass
from importlib.resources import files

from .ingest import valid_id
from .stores import Client, FetchError

PARSER_VERSION = "serp-v1"
SOURCES = {"google": "google-play-public-search", "apple": "apple-native-search"}
APPLE_MARKETS = json.loads(files("appmon").joinpath("apple_markets.json").read_text(encoding="utf-8"))


@dataclass
class SearchResult:
    apps: list[dict]
    raw: bytes
    source: str
    coverage: str = "first_response"
    parser_version: str = PARSER_VERSION


def path(value, *keys):
    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, IndexError, TypeError):
        return None


def ordered_apps(store, apps):
    """Validate before persisting; retain first occurrence and never sort by ID."""
    output, seen = [], set()
    for app in apps:
        app_id = app.get("app_id")
        if not isinstance(app_id, str) or not valid_id(store, app_id):
            raise FetchError("Search response contains an invalid app ID")
        if app_id not in seen:
            seen.add(app_id)
            output.append({"app_id": app_id, "name": app.get("name")})
    return output


def parse_google(text):
    # Resolve the RPC's dataset key when Google moves it; do not scan arbitrary
    # detail links, recommendation carousels or advertisements as search ranks.
    routes = re.findall(r"'(ds:\d+)'\s*:\s*\{\s*id:\s*'lGYRle'", text)
    keys = set(routes or ["ds:4"])
    blocks = {}
    for block in re.findall(r"AF_initDataCallback[\s\S]*?</script", text):
        key = re.search(r"key:\s*'(ds:\d+)'", block)
        if not key or key[1] not in keys:
            continue
        data = re.search(r"data:([\s\S]*?),\s*sideChannel:", block)
        if not data or key[1] in blocks:
            raise FetchError("Google search dataset malformed or duplicated")
        blocks[key[1]] = json.loads(data[1])
    if len(blocks) != 1:
        raise FetchError("Google search dataset unavailable")
    sections = path(next(iter(blocks.values())), 0, 1)
    if not isinstance(sections, list):
        raise FetchError("Google search sections unavailable")
    apps, exact, recognized = [], None, False
    for section in sections:
        card = path(section, 23)
        if card is not None:
            # Current and older exact-match cards use different ID anchors.
            app_id = path(card, 16, 3, "12", 0, 0) or path(card, 16, 11, 0, 0)
            title = path(card, 16, 2, 0, 0)
            if not app_id or not isinstance(title, str):
                raise FetchError("Google exact-match card changed")
            if exact is not None:
                raise FetchError("Multiple Google exact-match cards")
            exact = {"app_id": app_id, "name": title}
        rows = path(section, 22, 0)
        if rows is not None and not apps:
            if not isinstance(rows, list):
                raise FetchError("Google search rows changed")
            recognized = True
            for row in rows:
                title = path(row, 0, 3)
                if not isinstance(title, str) or not title:
                    raise FetchError("Google search title missing")
                apps.append({"app_id": path(row, 0, 0, 0), "name": title})
    if not recognized and exact is None:
        # An unknown layout cannot prove a successful empty result.
        raise FetchError("Google search layout unrecognized; retain previous ranks")
    if exact and not any(a["app_id"] == exact["app_id"] for a in apps):
        apps.insert(0, exact)
    return ordered_apps("google", apps)


def parse_apple(data):
    if not isinstance(data, dict) or not isinstance(data.get("bubbles"), list):
        raise FetchError("Apple native search schema changed")
    bubbles = data["bubbles"]
    if not bubbles:
        return []
    rows = bubbles[0].get("results")
    if not isinstance(rows, list):
        raise FetchError("Apple native search results missing")
    apps = []
    for row in rows:
        app_id = row.get("id")
        if not isinstance(app_id, (str, int)) or isinstance(app_id, bool):
            raise FetchError("Apple native search app ID missing")
        apps.append({"app_id": str(app_id), "name": row.get("name")})
    return ordered_apps("apple", apps)


def parse_apple_web(text, country):
    match=re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>',text,re.S)
    if not match:raise FetchError('Apple web search dataset unavailable')
    data=json.loads(match[1])
    pages=[p for p in data.get('data',[]) if p.get('intent',{}).get('$kind')=='SearchResultsPageIntent']
    if len(pages)!=1 or pages[0]['intent'].get('storefront')!=country:
        raise FetchError('Apple returned a different storefront; no fallback')
    shelves=pages[0]['data'].get('shelves')
    if not isinstance(shelves,list):raise FetchError('Apple web search shelves changed')
    results=[]
    for shelf in shelves:
        if shelf.get('id')!='SearchResults.shelfId':continue
        for item in shelf.get('items',[]):
            if item.get('$kind')!='AppSearchResult':continue
            lockup=item.get('lockup',{})
            results.append({'app_id':str(lockup.get('adamId','')),'name':lockup.get('title')})
    if not any(s.get('id')=='SearchResults.shelfId' for s in shelves):
        raise FetchError('Apple web search results section missing')
    return ordered_apps('apple',results)


class KeywordClient(Client):
    def search(self, target):
        store = target["store"]
        if store == "apple":
            storefront = APPLE_MARKETS.get(target["country"])
            if not storefront:
                from .catalog import APPLE_COUNTRIES
                if target['country'] not in APPLE_COUNTRIES:raise FetchError('Unsupported Apple storefront')
                response=self.get(store,f"https://apps.apple.com/{target['country']}/iphone/search",
                                  {'term':target['keyword'],'l':target['language']})
                if response.status_code!=200:raise FetchError(f'Apple web search HTTP {response.status_code}')
                try:apps=parse_apple_web(response.text,target['country'])
                except (ValueError,TypeError,KeyError,AttributeError) as exc:raise FetchError('Apple web search schema changed') from exc
                return SearchResult(apps[:target['depth']],response.content,'apple-web-search',parser_version='apple-web-v1')
            response = self.get(store, "https://search.itunes.apple.com/WebObjects/MZStore.woa/wa/search",
                                {"clientApplication": "Software", "media": "software", "term": target["keyword"]},
                                headers={"X-Apple-Store-Front": f"{storefront},24 t:native",
                                         "Accept-Language": target["language"]})
        else:
            response = self.get(store, "https://play.google.com/store/search",
                                {"q": target["keyword"], "c": "apps", "gl": target["country"],
                                 "hl": target["language"], "price": "0"})
        if response.status_code != 200:
            raise FetchError(f"Search HTTP {response.status_code}")
        if "consent.google" in response.url or any(
                marker in response.text.lower() for marker in ("recaptcha", "unusual traffic")):
            raise FetchError("Search consent or challenge page")
        try:
            apps = parse_apple(response.json()) if store == "apple" else parse_google(response.text)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise FetchError(f"{store} search schema changed") from e
        return SearchResult(apps[:target["depth"]], response.content, SOURCES[store])
