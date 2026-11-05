"""Public chart/review protocols adapted from MIT scrapers; see notices."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from urllib.parse import urlsplit

from .keyword_stores import path
from .ingest import valid_id
from .stores import Client, FetchError


@dataclass
class ObservationResult:
    items: list[dict]
    raw: bytes
    source: str
    coverage: str
    parser_version: str = "observations-v1"
    pagination: dict | None = None


def batch_payload(text, rpc):
    body = text[text.find('['):]
    for line in [*body.splitlines(), body]:
        try:
            frames = json.loads(line)
        except ValueError:
            continue
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if isinstance(frame, list) and len(frame) >= 3 and frame[:2] == ["wrb.fr", rpc]:
                if frame[2] is None:
                    raise FetchError("Google RPC returned no payload")
                return json.loads(frame[2])
    raise FetchError("Google RPC envelope missing")


def google_chart(payload):
    rows = path(payload, 0, 1, 0, 28, 0)
    if not isinstance(rows, list):
        raise FetchError("Google chart schema changed")
    result = []
    for row in rows:
        app_id, name = path(row, 0, 0, 0), path(row, 0, 3)
        if not isinstance(app_id, str) or not valid_id("google", app_id) or not isinstance(name, str):
            raise FetchError("Google chart item invalid")
        result.append(dict(id=app_id, app_id=app_id, name=name, icon=path(row, 0, 1, 3, 2),
                           developer=path(row, 0, 14), rating=path(row, 0, 4, 1)))
    return result


def google_reviews(payload):
    if payload == []:
        return [], None
    if not isinstance(payload, list) or not isinstance(payload[0], list):
        raise FetchError("Google reviews schema changed")
    results = []
    for row in payload[0]:
        review_id, rating, seconds = path(row, 0), path(row, 2), path(row, 5, 0)
        if not isinstance(review_id, str) or type(rating) is not int or not 1 <= rating <= 5 or not isinstance(seconds, (int, float)):
            raise FetchError("Google review item invalid")
        results.append(dict(id=review_id, rating=rating, content=path(row, 4) or "",
                            version=path(row, 10), published_at=datetime.fromtimestamp(seconds, timezone.utc).isoformat(),
                            reply=path(row, 7, 1), thumbs_up=path(row, 6)))
    token = path(payload, 1, 1)
    if token is not None and not isinstance(token, str):
        raise FetchError("Google review cursor invalid")
    return results, token


def apple_reviews(data):
    if not isinstance(data.get("feed"), dict):
        raise FetchError("Apple review feed missing")
    if "entry" not in data["feed"] and "id" not in data["feed"]:
        raise FetchError("Apple review feed has no result boundary")
    rows = data["feed"].get("entry", [])
    rows = rows if isinstance(rows, list) else [rows]
    output = []
    for row in rows:
        # Some old feeds prefix the app itself; never treat it as a review.
        if "im:rating" not in row and "im:name" in row:
            continue
        rating = int(row["im:rating"]["label"])
        if not 1 <= rating <= 5:
            raise FetchError("Apple review rating invalid")
        output.append(dict(id=str(row["id"]["label"]), rating=rating, content=row["content"]["label"],
                           title=row["title"]["label"], version=path(row, "im:version", "label"),
                           published_at=path(row, "updated", "label")))
    return output


def chart_result(target, items, raw, source):
    depth=target['depth'];items=items[:depth]
    end='depth_reached' if len(items)>=depth else 'source_limit'
    return ObservationResult(items,raw,source,'returned_chart_range' if end=='depth_reached' else 'chart_source_limit',
        pagination={'requested':depth,'collected':len(items),'pages':1,'end':end})


class ObservationClient(Client):
    def rpc(self, target, rpc, payload):
        return self.request("google", "POST", "https://play.google.com/_/PlayStoreUi/data/batchexecute",
                            params={"rpcids": rpc, "hl": target["language"], "gl": target["country"], "rt": "c"},
                            data={"f.req": json.dumps([[[rpc, json.dumps(payload), None, "generic"]]])})

    def fetch_observation(self, target):
        if target["kind"] == "chart":
            return self.chart(target)
        if target["kind"] == "featured":
            return self.featured(target)
        return self.reviews(target)

    def fetch_observations(self, targets):
        from .google_rpc import same_market, rpc_many, template
        same_market(targets)
        if len({t['kind'] for t in targets})!=1:
            raise ValueError('Observation batch must have one kind')
        if targets[0]['kind']=='reviews':
            return self.review_batches(targets)
        if targets[0]['kind']!='chart':
            raise ValueError('Unsupported batch observation kind')
        calls=[]
        for t in targets:
            payload=template('chart')
            payload[0][1][0][1][1]=t['depth']
            payload[0][2][1:]=[{'top-free':'topselling_free','top-paid':'topselling_paid','top-grossing':'topgrossing',
                'new-free':'topselling_new_free','new-paid':'topselling_new_paid','trending':'movers_shakers'}[t['collection']],t['category'] or 'APPLICATION']
            calls.append(('vyAe2',payload))
        payloads,raw=rpc_many(self,targets[0],calls)
        results=[]
        for t,payload in zip(targets,payloads):
            if isinstance(payload,FetchError):
                results.append(payload);continue
            try:
                results.append(chart_result(t,google_chart(payload),raw,'google-play-chart-rpc'))
            except (FetchError,ValueError,TypeError,KeyError,IndexError):
                results.append(FetchError('Google chart batch item invalid; retry separately',60))
        return results

    def review_batches(self, targets):
        from .google_rpc import rpc_many
        from .pagination import PageWindow
        results=[None]*len(targets)
        states=[PageWindow(t['depth']) for t in targets]
        raw_pages=[[] for _ in targets]
        while any(result is None for result in results):
            active=[i for i,result in enumerate(results) if result is None]
            calls=[]
            for i in active:
                t,state=targets[i],states[i]
                # Prefer the complete remaining depth in one source call.
                calls.append(('UsvDTd',[None,None,[2,2,[min(500,t['depth']-len(state.items)),None,state.cursor],None,[]],[t['app_id'],7]]))
            try:
                payloads,raw=rpc_many(self,targets[0],calls)
            except FetchError as error:
                for i in active:results[i]=error
                break  # Preserve targets that already completed earlier rounds.
            for i,payload in zip(active,payloads):
                if isinstance(payload,FetchError):
                    results[i]=payload;continue
                state,t=states[i],targets[i]
                try:
                    items,token=google_reviews(payload)
                    raw_pages[i].append(raw.decode('utf-8'))
                    done=state.add(items,token)
                    self.report('page',f"评论批次目标 {i+1}：已取得 {len(state.items)}/{t['depth']} 条")
                    if done:
                        results[i]=ObservationResult(state.items,json.dumps(raw_pages[i],ensure_ascii=False).encode(),
                                                     'google-public-reviews','recent_reviews_bounded',pagination=state.summary())
                except FetchError as error:
                    results[i]=error
                except (ValueError,TypeError,KeyError,IndexError):
                    results[i]=FetchError('Google review batch item invalid; retry separately',60)
        return results

    def chart(self, t):
        if t["store"] == "google":
            payload = json.loads(files("appmon").joinpath("google_chart_request.json").read_text(encoding="utf-8"))
            payload[0][1][0][1][1] = t["depth"]
            payload[0][2][1:] = [{"top-free": "topselling_free", "top-paid": "topselling_paid", "top-grossing": "topgrossing",
                                 "new-free":"topselling_new_free","new-paid":"topselling_new_paid","trending":"movers_shakers"}[t["collection"]], t["category"] or "APPLICATION"]
            r = self.rpc(t, "vyAe2", payload)
            items = google_chart(batch_payload(r.text, "vyAe2"))
            return chart_result(t,items,r.content,"google-play-chart-rpc")
        collection = {"top-free": "topfreeapplications", "top-paid": "toppaidapplications", "top-grossing": "topgrossingapplications",
                      "new-free":"newfreeapplications","new-paid":"newpaidapplications"}[t["collection"]]
        genre = f"/genre={t['category']}" if t["category"] else ""
        r = self.get("apple", f"https://itunes.apple.com/{t['country']}/rss/{collection}/limit={min(t['depth'], 200)}{genre}/json")
        if r.status_code != 200:
            raise FetchError("Apple chart unavailable for selected collection/category")
        data = r.json()
        rows = path(data, "feed", "entry")
        if rows is None and isinstance(data.get('feed'),dict) and 'id' in data['feed']:rows=[]
        if isinstance(rows,dict):rows=[rows]
        if not isinstance(rows, list):
            raise FetchError("Apple chart entries missing")
        items = []
        for row in rows:
            link=path(row,'id','label')
            if isinstance(link,str):
                region=urlsplit(link).path.split('/')[1:2]
                if region and len(region[0])==2 and region[0].lower()!=t['country']:
                    raise FetchError('Apple chart returned a different country; no fallback')
            app_id = str(row["id"]["attributes"]["im:id"])
            if not valid_id("apple", app_id):
                raise FetchError("Apple chart ID invalid")
            items.append(dict(id=app_id, app_id=app_id, name=row["im:name"]["label"],
                              developer=path(row, "im:artist", "label"), icon=row["im:image"][-1]["label"],
                              released=path(row,'im:releaseDate','label')))
        return chart_result(t,items,r.content,"apple-rss-chart")

    def reviews(self, t):
        from .pagination import PageWindow
        state=PageWindow(t['depth']);raw=[]
        while not state.end:
            if t['store']=='apple':
                page=state.pages+1
                r=self.get('apple',f"https://itunes.apple.com/{t['country']}/rss/customerreviews/page={page}/id={t['app_id']}/sortby=mostrecent/json")
                if r.status_code!=200:
                    raise FetchError('Apple review feed unavailable')
                data=r.json();rows=apple_reviews(data)
                links=data['feed'].get('link')
                if isinstance(links,dict):links=[links]
                # Prefer source navigation over guessing from the number of rows.
                more=any(link.get('attributes',{}).get('rel')=='next' for link in links) if isinstance(links,list) else bool(rows)
                token=str(page+1) if more else None
            else:
                request=[None,None,[2,2,[min(500,t['depth']-len(state.items)),None,state.cursor],None,[]],[t['app_id'],7]]
                r=self.rpc(t,'UsvDTd',request)
                rows,token=google_reviews(batch_payload(r.text,'UsvDTd'))
            raw.append(r.text)
            state.add(rows,token)
            self.report('page',f"评论已取得 {len(state.items)}/{t['depth']} 条")
            if t['store']=='apple' and not state.end and state.pages==10:
                state.end='source_limit'
        coverage='reviews_source_limit' if state.end=='source_limit' else 'recent_reviews_bounded'
        return ObservationResult(state.items,json.dumps(raw,ensure_ascii=False).encode(),
                                 f"{t['store']}-public-reviews",coverage,pagination=state.summary())

    def featured(self, t):
        from .featured import parse_featured
        r = self.get("apple", f"https://apps.apple.com/{t['country']}/iphone/today")
        if r.status_code != 200:
            raise FetchError("Apple Today unavailable")
        return ObservationResult(parse_featured(r.content.decode("utf-8"))[:t["depth"]], r.content, "apple-today-public", "visible_editorial_cards")
