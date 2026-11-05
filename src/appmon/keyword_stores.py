"""Ordered public search observations, not personalized device rankings.

Google mappings adapted from MrAdex77/google-play-scraper (MIT); Apple native
search protocol/markets from facundoolano/app-store-scraper (MIT). See licenses.
Read the source window and follow its cursor when the requested depth needs more.
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
    pagination: dict | None = None


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
            if isinstance(app.get('icon'),str) and app['icon']:output[-1]['icon']=app['icon']
    return output


def google_search_payload(text):
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
    return next(iter(blocks.values()))


def parse_google(text):
    return parse_google_payload(google_search_payload(text))


def parse_google_payload(payload):
    return google_search_page(payload)[0]


def google_search_page(payload):
    sections = path(payload, 0, 1)
    if not isinstance(sections, list):
        raise FetchError("Google search sections unavailable")
    apps, exact, recognized, cursor = [], None, False, None
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
            cursor=path(section,22,1,3,1)
            if cursor is not None and (not isinstance(cursor,str) or not cursor):
                raise FetchError('Google search cursor invalid')
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
    return ordered_apps("google", apps),cursor


def parse_apple(data,country=None):
    if not isinstance(data, dict) or not isinstance(data.get("bubbles"), list):
        raise FetchError("Apple native search schema changed")
    bubbles = data["bubbles"]
    if not bubbles:
        return []
    rows = bubbles[0].get("results")
    if not isinstance(rows, list):
        raise FetchError("Apple native search results missing")
    apps = []
    catalog=path(data,'storePlatformData','native-search-lockup-search') or {}
    storefront=path(catalog,'meta','storefront','cc')
    if country and storefront and str(storefront).lower()!=country.lower():raise FetchError('Apple returned a different storefront')
    metadata=catalog.get('results',{}) if isinstance(catalog,dict) else {}
    for row in rows:
        app_id = row.get("id")
        if not isinstance(app_id, (str, int)) or isinstance(app_id, bool):
            raise FetchError("Apple native search app ID missing")
        detail=metadata.get(str(app_id),{}) if isinstance(metadata,dict) else {}
        if detail.get('id') is not None and str(detail['id'])!=str(app_id):raise FetchError('Apple metadata identity mismatch')
        apps.append({"app_id": str(app_id), "name": row.get("name") or detail.get('name'),
                     'icon':path(detail,'artwork',0,'url')})
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
    def search_many(self, targets):
        from .google_rpc import same_market, template, rpc_many
        same_market(targets)
        calls=[]
        for target in targets:
            payload=template('search');payload[0][2][0]=target['keyword']
            calls.append(('lGYRle',payload))
        payloads,raw=rpc_many(self,targets[0],calls)
        return self.complete_google_searches(targets,payloads,[raw]*len(targets))

    def complete_google_searches(self, targets, payloads, initial_raw):
        from .google_rpc import rpc_many
        from .pagination import PageWindow
        states=[PageWindow(t['depth'],'app_id') for t in targets]
        raw_pages=[[raw.decode('utf-8')] for raw in initial_raw]
        results=[None]*len(targets)
        for i,payload in enumerate(payloads):
            if isinstance(payload,FetchError):
                results[i]=payload;continue
            try:
                states[i].add(*google_search_page(payload))
            except (FetchError,ValueError,TypeError,KeyError,IndexError) as error:
                results[i]=error if isinstance(error,FetchError) else FetchError('Google search schema changed')
        while True:
            active=[i for i,state in enumerate(states) if results[i] is None and not state.end]
            if not active:
                break
            calls=[]
            for i in active:
                state=states[i]
                payload=[[None,[[10,[10,min(100,state.depth-len(state.items))]],True,None,
                    [96,27,4,8,57,30,110,79,11,16,49,1,3,9,12,104,55,56,51,10,34,77]],None,state.cursor]]
                calls.append(('qnKhOb',payload))
            try:
                pages,raw=rpc_many(self,targets[0],calls)
            except FetchError as error:
                for i in active:results[i]=error
                break
            for i,page in zip(active,pages):
                if isinstance(page,FetchError):
                    results[i]=page;continue
                try:
                    if page==[]:
                        rows,cursor=[],None  # Valid empty continuation payload, not a null/error frame.
                    else:
                        rows=path(page,0,0,0);cursor=path(page,0,0,7,1)
                        if not isinstance(rows,list):raise FetchError('Google search continuation schema changed')
                        parsed=[]
                        for row in rows:
                            name=path(row,2)
                            if not isinstance(name,str) or not name:raise FetchError('Google search continuation title missing')
                            parsed.append({'app_id':path(row,12,0),'name':name})
                        rows=ordered_apps('google',parsed)
                    states[i].add(rows,cursor)
                    raw_pages[i].append(raw.decode('utf-8'))
                    self.report('page',f"关键词批次目标 {i+1}：已取得 {len(states[i].items)}/{targets[i]['depth']} 条")
                except (FetchError,ValueError,TypeError,KeyError,IndexError) as error:
                    results[i]=error if isinstance(error,FetchError) else FetchError('Google search continuation schema changed')
        for i,state in enumerate(states):
            if results[i] is None:
                paginated=state.pages>1
                raw=json.dumps(raw_pages[i],ensure_ascii=False).encode() if paginated else initial_raw[i]
                results[i]=SearchResult(state.items,raw,SOURCES['google'],
                    coverage='paginated_search' if paginated else 'first_response',
                    parser_version='serp-v2' if paginated else PARSER_VERSION,pagination=state.summary())
        return results

    def search(self, target):
        store = target["store"]
        if store=='google' and target['depth']>20 and not target.get('failures',0):
            result=self.search_many([target])[0]
            if isinstance(result,FetchError):raise result
            return result
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
            if store=='google':
                result=self.complete_google_searches([target],[google_search_payload(response.text)],[response.content])[0]
                if isinstance(result,FetchError):raise result
                return result
            apps=parse_apple(response.json(),target['country'])
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            raise FetchError(f'{store} search schema changed') from error
        return SearchResult(apps[:target["depth"]], response.content, SOURCES[store])
