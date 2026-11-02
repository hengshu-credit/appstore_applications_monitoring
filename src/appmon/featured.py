"""Capture structured Apple Today editorial cards, not arbitrary page links."""
import hashlib
import json
from html.parser import HTMLParser

from .keyword_stores import path
from .stores import FetchError


class ServerData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "serialized-server-data":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_featured(html):
    parser = ServerData()
    parser.feed(html)
    try:
        shelves = path(json.loads("".join(parser.parts)), "data", 0, "data", "shelves")
    except ValueError as exc:
        raise FetchError("Apple Today server data missing") from exc
    if not isinstance(shelves, list):
        raise FetchError("Apple Today shelves changed")
    output = []
    for shelf in shelves:
        if shelf.get("contentType") != "todayCard" or shelf.get("isHidden"):
            continue
        for card in shelf.get("items", []):
            if card.get("$kind") != "TodayCard":
                continue
            apps = []
            lockups = (path(card, "media", "lockups") or []) + [path(card, "overlay", "lockup")]
            for lockup in lockups:
                if isinstance(lockup, dict) and str(lockup.get("adamId", "")).isdigit():
                    apps.append({"app_id": str(lockup["adamId"]), "name": lockup.get("title")})
            url = path(card, "clickAction", "pageUrl")
            title = card.get("title") or card.get("shortTitle") or card.get("heading")
            identity = json.dumps([url, title, apps], sort_keys=True, ensure_ascii=False)
            output.append(dict(id=hashlib.sha256(identity.encode()).hexdigest(), name=title,
                               heading=card.get("heading"), description=card.get("inlineDescription"),
                               url=url, apps=apps, media_kind=path(card, "media", "kind")))
    if not output:
        raise FetchError("No recognized Today cards; cannot assert an empty editorial page")
    return list({item["id"]: item for item in output}.values())
