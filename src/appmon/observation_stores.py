"""Public chart/review protocols adapted from MIT scrapers; see notices."""
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files

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

    def chart(self, t):
        if t["store"] == "google":
            payload = json.loads(files("appmon").joinpath("google_chart_request.json").read_text(encoding="utf-8"))
            payload[0][1][0][1][1] = t["depth"]
            payload[0][2][1:] = [{"top-free": "topselling_free", "top-paid": "topselling_paid", "top-grossing": "topgrossing"}[t["collection"]], t["category"] or "APPLICATION"]
            r = self.rpc(t, "vyAe2", payload)
            items = google_chart(batch_payload(r.text, "vyAe2"))
            return ObservationResult(items[:t["depth"]], r.content, "google-play-chart-rpc", "returned_chart_range")
        collection = {"top-free": "topfreeapplications", "top-paid": "toppaidapplications", "top-grossing": "topgrossingapplications"}[t["collection"]]
        genre = f"/genre={t['category']}" if t["category"] else ""
        r = self.get("apple", f"https://itunes.apple.com/{t['country']}/rss/{collection}/limit={min(t['depth'], 200)}{genre}/json")
        if r.status_code != 200:
            raise FetchError("Apple chart unavailable for selected collection/category")
        data = r.json()
        rows = path(data, "feed", "entry")
        if not isinstance(rows, list):
            raise FetchError("Apple chart entries missing")
        items = []
        for row in rows:
            app_id = str(row["id"]["attributes"]["im:id"])
            if not valid_id("apple", app_id):
                raise FetchError("Apple chart ID invalid")
            items.append(dict(id=app_id, app_id=app_id, name=row["im:name"]["label"],
                              developer=path(row, "im:artist", "label"), icon=row["im:image"][-1]["label"]))
        return ObservationResult(items[:t["depth"]], r.content, "apple-rss-chart", "returned_chart_range")

    def reviews(self, t):
        items, raw, seen, tokens = [], [], set(), set()
        token = None
        max_pages = min(10, math.ceil(t["depth"] / 50))
        for page in range(1, max_pages + 1):
            if t["store"] == "apple":
                r = self.get("apple", f"https://itunes.apple.com/{t['country']}/rss/customerreviews/page={page}/id={t['app_id']}/sortby=mostrecent/json")
                if r.status_code != 200:
                    raise FetchError("Apple review feed unavailable")
                rows = apple_reviews(r.json())
                more = len(rows) >= 50
            else:
                request = [None, None, [2, 2, [min(50, t["depth"] - len(items)), None, token], None, []], [t["app_id"], 7]]
                r = self.rpc(t, "UsvDTd", request)
                rows, token = google_reviews(batch_payload(r.text, "UsvDTd"))
                more = bool(token)
                if token and token in tokens:
                    raise FetchError("Google review pagination cycle")
                tokens.add(token)
            raw.append(r.text)
            for row in rows:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    items.append(row)
            if not more or len(items) >= t["depth"]:
                break
        return ObservationResult(items[:t["depth"]], json.dumps(raw, ensure_ascii=False).encode(),
                                 f"{t['store']}-public-reviews", "recent_reviews_bounded")

    def featured(self, t):
        from .featured import parse_featured
        r = self.get("apple", f"https://apps.apple.com/{t['country']}/iphone/today")
        if r.status_code != 200:
            raise FetchError("Apple Today unavailable")
        return ObservationResult(parse_featured(r.content.decode("utf-8"))[:t["depth"]], r.content, "apple-today-public", "visible_editorial_cards")
