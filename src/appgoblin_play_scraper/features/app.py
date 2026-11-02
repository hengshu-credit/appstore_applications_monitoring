import json

from appgoblin_play_scraper.constants.element import ElementSpecs
from appgoblin_play_scraper.constants.regex import Regex
from appgoblin_play_scraper.constants.request import Formats
from appgoblin_play_scraper.utils.request import get

TV_PERMISSIONS = {
    "read TV channel/program information",
    "write TV channel/program information",
}


def _has_tv_permission(value) -> bool:
    if isinstance(value, str):
        return value in TV_PERMISSIONS
    if isinstance(value, dict):
        return any(_has_tv_permission(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_tv_permission(item) for item in value)
    return False


def app(
    app_id: str,
    lang: str = "en",
    country: str = "us",
    timeout: int | None = None,
    proxies: dict[str, str] | None = None,
) -> dict:
    url = Formats.Detail.build(app_id=app_id, lang=lang, country=country)
    dom = get(url, timeout=timeout, proxies=proxies)
    return parse_dom(dom=dom, app_id=app_id, url=url)


def parse_dom(dom: str, app_id: str, url: str) -> dict:
    matches = Regex.SCRIPT.findall(dom)

    dataset = {}

    for match in matches:
        key_match = Regex.KEY.findall(match)
        value_match = Regex.VALUE.findall(match)

        if key_match and value_match:
            key = key_match[0]
            value = json.loads(value_match[0])

            dataset[key] = value

    result = {}

    for k, spec in ElementSpecs.Detail.items():
        result[k] = spec.extract_content(dataset)

    if _has_tv_permission(dataset):
        result["tv_supported"] = True

    result["appId"] = app_id
    result["url"] = url

    return result
