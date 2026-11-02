# Third-party material

Data provided by [AppGoblin](https://appgoblin.info).

- AppGoblin data: CC BY 4.0. Full notice in `licenses/appgoblin-data.txt`.
- `src/appgoblin_play_scraper/`: unmodified Python package source from
  `appgoblin-dev/appgoblin-play-scraper`, source tree
  `8f41211f2e3213bc97d5e22c31ed75fbba5f2ea3`.
  Copyright 2026 AppGoblin / James O'Claire and copyright 2019 PlanB;
  MIT terms retained in `licenses/appgoblin-play-scraper.txt`.
- Other upstream repositories under the ignored `upstream/` directory are
  local research references. Their individual licensing applies. Material
  from repositories without a clear license is not incorporated in the
  monitoring package or its distributable wheel.
- Store responses are collected from their respective public services;
  the AppGoblin data license does not grant rights to unrelated store assets.
- `src/appmon/keyword_stores.py`: Google search dataset mappings adapted to
  Python from `MrAdex77/google-play-scraper` at
  `1b8f957aaacc026edfa62a05cdc7633b208bd060` (MIT, copyright 2026 MrAdex77).
  Full notice in `licenses/mradex-google-play-scraper.txt`.
- Apple native search protocol and `src/appmon/apple_markets.json` adapted from
  `facundoolano/app-store-scraper` at
  `05d59c110240104901fa47622f64a7c6ed841a3b`. The repository LICENSE is MIT,
  copyright 2017 LambdaClass; retained in `licenses/app-store-scraper.txt`
  (its package.json separately labels the package ISC).
- AsoBeast at `c49f547b75b8899d27aeeda45b6bea7191c0acaa` was consulted for
  tracking concepts (market-scoped history and captured-depth semantics).
  No AsoBeast code is incorporated. Its source remains in the ignored research
  directory and is licensed AGPL-3.0 by its authors.
- The Google chart RPC projection in `src/appmon/google_chart_request.json`
  and chart/review mappings in `observation_stores.py` are adapted from the
  same pinned `MrAdex77/google-play-scraper` MIT source listed above, specifically
  `src/features/list/specs.ts` and `src/features/reviews/specs.ts`.
- Apple chart/review RSS structures are adapted from the same pinned
  `facundoolano/app-store-scraper` MIT source (`lib/list.js`, `lib/reviews.js`).
  HTTPS endpoints are used. Apple Today parsing is an independent implementation
  of the public page's structured editorial cards.
- AsoBeast was also consulted for console feature organization, observation
  provenance and explainable ASO actions. No AGPL source is copied into the console.
- FastAPI, Starlette, Uvicorn and HTTPX are installed as dependencies under their
  respective upstream licenses; dependency packages retain their license files.
- Country names and geographic containment in `src/appmon/store_catalog.json`
  are derived from Unicode CLDR 49 (`cldr-core` and `cldr-localenames-full`),
  Unicode-3.0 license. The pinned package URLs and SHA256 values are included in
  the catalog; license copies are in `licenses/unicode-cldr.txt` and
  `frontend/public/licenses/unicode-cldr.txt`. Chinese display names for HK/MO/TW
  are adapted to the requested country/region selector wording.
- `frontend/public/flags/`: selected unmodified 4:3 SVG assets from
  `flag-icons` 7.6.0 (MIT), copyright Panayiotis Lipiridis. Full license in
  `frontend/public/licenses/flag-icons.txt`.
- `frontend/public/fonts/`: Alimama FangYuanTi VF, copyright Taobao (China)
  Software Co., Ltd. Embedded under the accompanying font license, not under
  this project's code license. Files are mirrored unchanged from the Tianshu
  frontend, including its documented browser-compatibility metadata repair;
  source, instructions, license and selected SemiBold axes are retained there.
- Apple genre identifiers/names come from Apple's public genres service;
  Google category names follow the Google Play Console category documentation
  and the public Watch Face category. Historical aliases support earlier
  AppGoblin snapshots without creating new categories. Source URLs and scope
  are documented in `docs/store-catalog.md`.
