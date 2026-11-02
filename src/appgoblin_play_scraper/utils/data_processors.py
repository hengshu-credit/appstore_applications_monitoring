from html import unescape
from urllib.parse import parse_qs, urlparse


def unescape_text(s):
    return unescape(s.replace("<br>", "\r\n"))


def normalize_video_url(url):
    if not url:
        return url

    if url.startswith("https://play.google.com/video/"):
        return url

    parsed_url = urlparse(url)
    youtube_id = None

    if parsed_url.netloc in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        if parsed_url.path == "/watch":
            youtube_id = parse_qs(parsed_url.query).get("v", [None])[0]
        elif parsed_url.path.startswith("/embed/"):
            youtube_id = parsed_url.path.removeprefix("/embed/").split("/", 1)[0]
    elif parsed_url.netloc == "youtu.be":
        youtube_id = parsed_url.path.lstrip("/").split("/", 1)[0] or None

    if not youtube_id:
        return url

    return (
        "https://play.google.com/video/lava/web/player/"
        f"yt:movie:{youtube_id}?autoplay=1&embed=play"
    )
