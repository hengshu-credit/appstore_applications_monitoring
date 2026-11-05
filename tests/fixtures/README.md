# Google batch regression fixture

`google_batch.json` contains public response datasets used by the existing Play
parsers. Sources are the `app/translate.html`, `search/panda.html`, and
`list/topfree-game.txt` fixtures from `MrAdex77/google-play-scraper` revision
`1b8f957aaacc026edfa62a05cdc7633b208bd060` (MIT; see
`licenses/mradex-google-play-scraper.txt` and `THIRD_PARTY_NOTICES.md`).

The detail dataset is retained in full. Search and chart datasets omit unused
recommendations and metadata; their complete parsed lists were compared for
equality before and after projection, including ordering and every field used
by this application. These fixtures are checked in so CI does not depend on
the ignored local `upstream/` research checkout or live network requests.
