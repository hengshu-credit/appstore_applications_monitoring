import json

from appgoblin_play_scraper.constants.element import ElementSpecs
from appgoblin_play_scraper.constants.regex import Regex
from appgoblin_play_scraper.constants.request import Formats
from appgoblin_play_scraper.utils.request import post


def permissions(
    app_id: str,
    lang: str = "en",
    country: str = "us",
    timeout: int | None = None,
    proxies: dict[str, str] | None = None,
) -> dict[str, list]:
    dom = post(
        Formats.Permissions.build(lang=lang, country=country),
        Formats.Permissions.build_body(app_id),
        {"content-type": "application/x-www-form-urlencoded"},
        timeout=timeout,
        proxies=proxies,
    )

    matches = json.loads(Regex.PERMISSIONS.findall(dom)[0])
    container = json.loads(matches[0][2])

    result = {}

    for permission_items in container:
        if isinstance(permission_items, list):
            if len(permission_items[0]) == 2:
                # rearrange layout to fit ElementSpecs
                permission_items = [["Uncategorized", None, permission_items, None]]

            for permission in permission_items:
                if permission:
                    result[ElementSpecs.PermissionType.extract_content(permission)] = (
                        ElementSpecs.PermissionList.extract_content(permission)
                    )

    return result
