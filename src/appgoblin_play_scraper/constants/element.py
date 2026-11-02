from datetime import datetime
from typing import Callable

from appgoblin_play_scraper.utils import nested_lookup
from appgoblin_play_scraper.utils.data_processors import (
    normalize_video_url,
    unescape_text,
)


class ElementSpec:
    def __init__(
        self,
        ds_num: int | None,
        data_map: list[int],
        post_processor: Callable | None = None,
        fallback_value=None,
    ):
        self.ds_num = ds_num
        self.data_map = data_map
        self.post_processor = post_processor
        self.fallback_value = fallback_value

    def extract_content(self, source: dict):
        result = None
        try:
            if self.ds_num is None:
                result = nested_lookup(source, self.data_map)
            else:
                result = nested_lookup(
                    source["ds:{}".format(self.ds_num)], self.data_map
                )

            if self.post_processor is not None:
                result = self.post_processor(result)
        except Exception:
            result = None

        if result is None and self.fallback_value is not None:
            if isinstance(self.fallback_value, ElementSpec):
                return self.fallback_value.extract_content(source)
            return self.fallback_value

        return result


def extract_categories(s, categories=None) -> list:
    # Init an empty list if first iteration
    if categories is None:
        categories = []
    if s is None or len(s) == 0:
        return categories

    if len(s) >= 4 and type(s[0]) is str:
        categories.append({"name": s[0], "id": s[2]})
    else:
        for sub in s:
            extract_categories(sub, categories)

    return categories


def get_categories(s) -> list:
    categories = extract_categories(nested_lookup(s, [118]))
    if len(categories) == 0:
        # add genre and genreId like GP does when there're no categories available
        categories.append(
            {
                "name": nested_lookup(s, [79, 0, 0, 0]),
                "id": nested_lookup(s, [79, 0, 0, 2]),
            }
        )

    return categories


def normalize_android_version(android_version_text) -> str:
    if not android_version_text:
        return "VARY"

    number = android_version_text.split(" ")[0]
    try:
        float(number)
        return number
    except ValueError:
        return "VARY"


def extract_comments(source) -> list:
    for ds_num in [8, 9]:
        try:
            ds_key = f"ds:{ds_num}"
            if ds_key in source:
                comments_data = source[ds_key][0]
                return [item[4] for item in comments_data][:5]
        except (KeyError, IndexError, TypeError):
            continue
    return []


class ElementSpecs:
    Detail = {
        "title": ElementSpec(5, [1, 2, 0, 0]),
        "description": ElementSpec(
            5,
            [1, 2],
            lambda s: unescape_text(
                nested_lookup(s, [12, 0, 0, 1]) or nested_lookup(s, [72, 0, 1])
            ),
        ),
        "descriptionHTML": ElementSpec(
            5,
            [1, 2],
            lambda s: nested_lookup(s, [12, 0, 0, 1]) or nested_lookup(s, [72, 0, 1]),
        ),
        "summary": ElementSpec(5, [1, 2, 73, 0, 1], unescape_text),
        "installs": ElementSpec(5, [1, 2, 13, 0]),
        "minInstalls": ElementSpec(5, [1, 2, 13, 1]),
        "realInstalls": ElementSpec(5, [1, 2, 13, 2]),
        "score": ElementSpec(5, [1, 2, 51, 0, 1]),
        "scoreText": ElementSpec(5, [1, 2, 51, 0, 0]),
        "ratings": ElementSpec(5, [1, 2, 51, 2, 1]),
        "reviews": ElementSpec(5, [1, 2, 51, 3, 1]),
        "histogram": ElementSpec(
            5,
            [1, 2, 51, 1],
            lambda container: (
                [
                    container[1][1],
                    container[2][1],
                    container[3][1],
                    container[4][1],
                    container[5][1],
                ]
                if container
                else [0, 0, 0, 0, 0]
            ),
            [0, 0, 0, 0, 0],
        ),
        "price": ElementSpec(
            5, [1, 2, 57, 0, 0, 0, 0, 1, 0, 0], lambda price: (price / 1000000) or 0
        ),
        "free": ElementSpec(5, [1, 2, 57, 0, 0, 0, 0, 1, 0, 0], lambda s: s == 0),
        "currency": ElementSpec(5, [1, 2, 57, 0, 0, 0, 0, 1, 0, 1]),
        "sale": ElementSpec(4, [0, 2, 0, 0, 0, 14, 0, 0], bool, False),
        "saleTime": ElementSpec(4, [0, 2, 0, 0, 0, 14, 0, 0]),
        "originalPrice": ElementSpec(
            5,
            [1, 2, 57, 0, 0, 0, 0, 1, 1, 0],
            lambda price: (price / 1000000) if price else None,
        ),
        "saleText": ElementSpec(4, [0, 2, 0, 0, 0, 14, 1]),
        "discountEndDate": ElementSpec(5, [1, 2, 57, 0, 0, 0, 0, 14, 1]),
        "priceText": ElementSpec(
            5, [1, 2, 57, 0, 0, 0, 0, 1, 0, 2], lambda s: s or "Free"
        ),
        "available": ElementSpec(5, [1, 2, 18, 0], bool),
        "offersIAP": ElementSpec(5, [1, 2, 19, 0], bool),
        "inAppProductPrice": ElementSpec(5, [1, 2, 19, 0]),
        "IAPRange": ElementSpec(5, [1, 2, 19, 0]),
        "androidVersion": ElementSpec(
            5,
            [1, 2, 140, 1, 1, 0, 0, 1],
            normalize_android_version,
            fallback_value=ElementSpec(
                5, [1, 2, -1, "141", 1, 1, 0, 0, 1], normalize_android_version
            ),
        ),
        "androidVersionText": ElementSpec(
            5,
            [1, 2, 140, 1, 1, 0, 0, 1],
            lambda s: s or "Varies with device",
            fallback_value=ElementSpec(
                5, [1, 2, -1, "141", 1, 1, 0, 0, 1], lambda s: s or "Varies with device"
            ),
        ),
        "androidMaxVersion": ElementSpec(
            5,
            [1, 2, 140, 1, 1, 0, 1, 1],
            normalize_android_version,
            fallback_value=ElementSpec(
                5, [1, 2, -1, "141", 1, 1, 0, 1, 1], normalize_android_version
            ),
        ),
        "developer": ElementSpec(5, [1, 2, 68, 0]),
        "developerId": ElementSpec(5, [1, 2, 68, 1, 4, 2], lambda s: s.split("id=")[1]),
        "developerEmail": ElementSpec(5, [1, 2, 69, 1, 0]),
        "developerWebsite": ElementSpec(5, [1, 2, 69, 0, 5, 2]),
        "developerAddress": ElementSpec(5, [1, 2, 69, 2, 0]),
        "developerLegalName": ElementSpec(5, [1, 2, 69, 4, 0]),
        "developerLegalEmail": ElementSpec(5, [1, 2, 69, 4, 1, 0]),
        "developerLegalAddress": ElementSpec(
            5,
            [1, 2, 69],
            lambda s: (nested_lookup(s, [4, 2, 0]) or "").replace("\n", ", "),
        ),
        "developerLegalPhoneNumber": ElementSpec(5, [1, 2, 69, 4, 3]),
        "privacyPolicy": ElementSpec(5, [1, 2, 99, 0, 5, 2]),
        "developerInternalID": ElementSpec(
            5, [1, 2, 68, 1, 4, 2], lambda s: s.split("id=")[1]
        ),
        "genre": ElementSpec(5, [1, 2, 79, 0, 0, 0]),
        "genreId": ElementSpec(5, [1, 2, 79, 0, 0, 2]),
        "categories": ElementSpec(5, [1, 2], get_categories, []),
        "icon": ElementSpec(5, [1, 2, 95, 0, 3, 2]),
        "headerImage": ElementSpec(5, [1, 2, 96, 0, 3, 2]),
        "screenshots": ElementSpec(
            5,
            [1, 2, 78, 0],
            lambda container: [item[3][2] for item in container] if container else [],
            [],
        ),
        "video": ElementSpec(5, [1, 2, 100, 0, 0, 3, 2], normalize_video_url),
        "videoImage": ElementSpec(5, [1, 2, 100, 1, 0, 3, 2]),
        "previewVideo": ElementSpec(5, [1, 2, 100, 1, 2, 0, 2]),
        "contentRating": ElementSpec(5, [1, 2, 9, 0]),
        "contentRatingDescription": ElementSpec(5, [1, 2, 9, 2, 1]),
        "adSupported": ElementSpec(5, [1, 2, 48], bool),
        "containsAds": ElementSpec(5, [1, 2, 48], bool, False),
        "released": ElementSpec(5, [1, 2, 10, 0]),
        "lastUpdatedOn": ElementSpec(
            5,
            [1, 2, 145, 0, 0],
            fallback_value=ElementSpec(5, [1, 2, -1, "146", 0, 0]),
        ),
        "updated": ElementSpec(
            5,
            [1, 2, 145, 0, 1, 0],
            lambda ts: ts if ts else None,
            fallback_value=ElementSpec(
                5, [1, 2, -1, "146", 0, 1, 0], lambda ts: ts if ts else None
            ),
        ),
        "version": ElementSpec(
            5,
            [1, 2, 140, 0, 0, 0],
            lambda s: s or "VARY",
            fallback_value=ElementSpec(
                5, [1, 2, -1, "141", 0, 0, 0], lambda s: s or "VARY"
            ),
        ),
        "recentChanges": ElementSpec(
            5,
            [1, 2, 144, 1, 1],
            fallback_value=ElementSpec(5, [1, 2, -1, "145", 1, 1]),
        ),
        "comments": ElementSpec(None, [], extract_comments, []),
        "preregister": ElementSpec(5, [1, 2, 18, 0], lambda s: s == 1),
        "earlyAccessEnabled": ElementSpec(
            5, [1, 2, 18, 2], lambda s: isinstance(s, str)
        ),
        "isAvailableInPlayPass": ElementSpec(5, [1, 2, 62], bool),
    }

    Review = {
        "reviewId": ElementSpec(None, [0]),
        "userName": ElementSpec(None, [1, 0]),
        "userImage": ElementSpec(None, [1, 1, 3, 2]),
        "content": ElementSpec(None, [4]),
        "score": ElementSpec(None, [2]),
        "thumbsUpCount": ElementSpec(None, [6]),
        "reviewCreatedVersion": ElementSpec(None, [10]),
        "at": ElementSpec(None, [5, 0], lambda v: datetime.fromtimestamp(v)),
        "replyContent": ElementSpec(None, [7, 1]),
        "repliedAt": ElementSpec(None, [7, 2, 0], lambda v: datetime.fromtimestamp(v)),
        "appVersion": ElementSpec(None, [10]),
    }

    PermissionType = ElementSpec(None, [0])

    PermissionList = ElementSpec(
        None, [2], lambda container: sorted([item[1] for item in container])
    )

    SearchResultOnTop = {
        "appId": ElementSpec(None, [11, 0, 0]),
        "icon": ElementSpec(None, [2, 95, 0, 3, 2]),
        "screenshots": ElementSpec(
            None,
            [2, 78, 0],
            lambda container: [item[3][2] for item in container],
            [],
        ),
        "title": ElementSpec(None, [2, 0, 0]),
        "score": ElementSpec(None, [2, 51, 0, 1]),
        "genre": ElementSpec(None, [2, 79, 0, 0, 0]),
        "price": ElementSpec(
            None, [2, 57, 0, 0, 0, 0, 1, 0, 0], lambda price: (price / 1000000) or 0
        ),
        "free": ElementSpec(None, [2, 57, 0, 0, 0, 0, 1, 0, 0], lambda s: s == 0),
        "currency": ElementSpec(None, [2, 57, 0, 0, 0, 0, 1, 0, 1]),
        "video": ElementSpec(None, [2, 100, 0, 0, 3, 2], normalize_video_url),
        "videoImage": ElementSpec(None, [2, 100, 1, 0, 3, 2]),
        "description": ElementSpec(None, [2, 72, 0, 1], unescape_text),
        "descriptionHTML": ElementSpec(None, [2, 72, 0, 1]),
        "developer": ElementSpec(None, [2, 68, 0]),
        "installs": ElementSpec(None, [2, 13, 0]),
    }

    SearchResult = {
        "appId": ElementSpec(None, [0, 0, 0]),
        "icon": ElementSpec(None, [0, 1, 3, 2]),
        "screenshots": ElementSpec(
            None, [0, 2], lambda container: [item[3][2] for item in container], []
        ),
        "title": ElementSpec(None, [0, 3]),
        "score": ElementSpec(None, [0, 4, 1]),
        "genre": ElementSpec(None, [0, 5]),
        "price": ElementSpec(
            None, [0, 8, 1, 0, 0], lambda price: (price / 1000000) or 0
        ),
        "free": ElementSpec(None, [0, 8, 1, 0, 0], lambda s: s == 0),
        "currency": ElementSpec(None, [0, 8, 1, 0, 1]),
        "video": ElementSpec(None, [0, 12, 0, 0, 3, 2], normalize_video_url),
        "videoImage": ElementSpec(None, [0, 12, 0, 3, 3, 2]),
        "description": ElementSpec(None, [0, 13, 1], unescape_text),
        "descriptionHTML": ElementSpec(None, [0, 13, 1]),
        "developer": ElementSpec(None, [0, 14]),
        "installs": ElementSpec(None, [0, 15]),
    }
