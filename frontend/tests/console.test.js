import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const html = (
  await readFile(new URL("../index.html", import.meta.url), "utf8")
).replace(/<script[^>]*><\/script>/g, "");
const accountSource = (
  await readFile(new URL("../src/accounts.js", import.meta.url), "utf8")
)
  .replace(/^import .*;$/gm, "")
  .replace(/^export /gm, "");
const themeSource = (
  await readFile(new URL("../src/theme.js", import.meta.url), "utf8")
).replace(/^export /gm, "");
const selectorSource = (
  await readFile(new URL("../src/selectors.js", import.meta.url), "utf8")
)
  .replace(/^export /gm, "")
  .replace(/^import .*;$/gm, "");
const catalogFixture = JSON.parse(
  await readFile(
    new URL("../../src/appmon/store_catalog.json", import.meta.url),
    "utf8",
  ),
);
const appleCountries = JSON.parse(
  await readFile(
    new URL("../../src/appmon/apple_countries.json", import.meta.url),
    "utf8",
  ),
).countries;
catalogFixture.locales = JSON.parse(
  await readFile(
    new URL("../../src/appmon/market_locales.json", import.meta.url),
    "utf8",
  ),
);
catalogFixture.countries = catalogFixture.countries.map((c) => ({
  ...c,
  apple_supported: appleCountries.includes(c.code),
}));
catalogFixture.charts = Object.fromEntries(
  ["google", "apple"].map((store) => [
    store,
    [
      ["top-free", "免费榜"],
      ["top-paid", "付费榜"],
      ["top-grossing", "畅销榜"],
      ["trending", "人气飙升榜"],
      ["new-free", "最新免费榜"],
      ["new-grossing", "最新畅销榜"],
      ["new-paid", "最新付费榜"],
    ].map(([id, name]) => ({
      id,
      name,
      mode:
        id === "new-grossing" || (store === "apple" && id === "trending")
          ? "derived"
          : "direct",
    })),
  ]),
);
const researchSource = (
  await readFile(new URL("../src/research.js", import.meta.url), "utf8")
)
  .replace(/^export /gm, "")
  .replace(/^import .*;$/gm, "");
const source = (
  await readFile(new URL("../src/main.js", import.meta.url), "utf8")
).replace(/import ['"]\.\/style\.css['"];?/, "");
async function wrappedModule(name, exports) {
  const code = (
    await readFile(new URL("../src/" + name, import.meta.url), "utf8")
  )
    .replace(/^import .*;$/gm, "")
    .replace(/^export /gm, "");
  return `const {${exports}} = (()=>{${code};return {${exports}};})();`;
}
const newModules = (
  await Promise.all([
    wrappedModule(
      "filter-bar.js",
      "filterBar,filterFields,bindFilterBar,initFilterBars,setFilterCatalog",
    ),
    wrappedModule("pagination.js", "pagination,bindPagination"),
    wrappedModule("icons.js", "icon,storeLogo"),
    wrappedModule("shell-layout.js", "initShellLayout"),
    wrappedModule(
      "navigation.js",
      "navigationMarkup,setWorkspaceHint,updateWorkspace",
    ),
    wrappedModule(
      "monitoring-charts.js",
      "monitoringChart,monitoringBars,bindMonitoringCharts",
    ),
    wrappedModule("app-insights.js", "createAppInsights"),
    wrappedModule("discovery.js", "createDiscoveryPages"),
    wrappedModule("global-chart-hover.js", "bindGlobalChartHover"),
    wrappedModule("marketdata.js", "marketDataPanel,bindMarketData"),
    wrappedModule("data-sources.js", "createDataSourcePage"),
    wrappedModule("global-charts.js", "createGlobalCharts"),
    wrappedModule("chart-detail.js", "createChartDetail"),
    wrappedModule("availability.js", "createAvailabilityPage"),
    wrappedModule("app-detail-table.js", "createAppDetailTable"),
    wrappedModule("app-detail-rows.js", "createAppDetailRows"),
    wrappedModule("app-groups.js", "createGroupWorkspace"),
    wrappedModule(
      "market-scope.js",
      "effectiveLanguage,autoCaption,languageChoices,recommendedLanguage,selectedStores,languageName,toggleStore",
    ),
    wrappedModule("app-icons.js", "createAppIconManager"),
    wrappedModule("market-controls.js", "initMarketControls"),
    wrappedModule("task-logs.js", "createTaskLogViewer"),
    wrappedModule("collection-lineage.js", "createCollectionLineage"),
    wrappedModule("collection-progress.js", "taskProgress,taskLabels,taskMark"),
    wrappedModule("collection-center.js", "createCollectionCenter"),
  ])
).join("\n");
const emptyCoverage = {
  stats: { tracked: 0, fresh: 0 },
  apps: [],
  keywords: [],
  app_keywords: [],
  note: "测试词库",
};
const emptyKeywordOverview = () => ({
  items: [],
  observed_at: 100,
  scope: "all_stores_countries_languages",
  note: "全系统统计，同一语言内去重。",
  totals: {
    library_words: 0,
    monitored_words: 0,
    enabled_words: 0,
    paused_words: 0,
    fresh_words: 0,
    tasks: 0,
    fresh_tasks: 0,
    languages: 0,
    monitoring_languages: 0,
    countries: 0,
    monitoring_countries: 0,
  },
});
const overview = {
  counts: {
    watchlist: 1,
    keyword_targets: 2,
    observation_targets: 3,
    unread_alerts: 0,
  },
  freshness: [],
  recent_events: [],
  failures: {},
  worker: null,
};
const tick = async () => {
  for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 5));
};

test("country follows automatic language and all-store views send explicit scope", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/keywords?")
      ? [
          {
            id: 901,
            store: "google",
            country: "kz",
            language: "kk",
            keyword: "Google 跟踪词",
          },
          {
            id: 902,
            store: "apple",
            country: "kz",
            language: "ru",
            keyword: "Apple 跟踪词",
          },
          {
            id: 903,
            store: "apple",
            country: "us",
            language: "en",
            keyword: "其他市场",
          },
        ]
      : undefined,
  );
  try {
    const doc = window.document;
    doc.querySelector("#global-country").value = "kz";
    doc
      .querySelector("#global-country")
      .dispatchEvent(new window.Event("change"));
    await tick();
    assert.equal(doc.querySelector("#global-language").value, "auto");
    assert.match(doc.querySelector("#language-value").textContent, /哈萨克/);
    doc.querySelector("#store-trigger").click();
    doc.querySelector('#store-menu [data-value="apple"]').click();
    doc.querySelector('#store-menu [data-value="google"]').click();
    await tick();
    assert.match(doc.querySelector("#language-value").textContent, /俄语/);
    doc.querySelector('#store-menu [data-value="google"]').click();
    window.location.hash = "keywords";
    await tick();
    assert.ok(calls.some((c) => c.path === "/api/keywords?store=all"));
    assert.equal(doc.querySelectorAll("[data-keyword]").length, 2);
    assert.ok(doc.querySelector('[data-keyword="901"]'));
    assert.ok(doc.querySelector('[data-keyword="902"]'));
    assert.equal(doc.querySelectorAll("#store-value img").length, 2);
    assert.match(
      doc.querySelector("#language-value").textContent,
      /哈萨克.*俄语/,
    );
    doc.querySelector("#language-trigger").click();
    doc.querySelector('#language-menu [data-value="en"]').click();
    doc.querySelector("#global-country").value = "th";
    doc
      .querySelector("#global-country")
      .dispatchEvent(new window.Event("change"));
    await tick();
    assert.equal(doc.querySelector("#global-language").value, "en");
    assert.equal(
      JSON.parse(window.sessionStorage.getItem("appmon-market")).language_mode,
      "manual",
    );
  } finally {
    await window.happyDOM.close();
  }
});

const emptyApp = {
  app: { store: "apple", app_id: "123", name: "Example", category: "6015" },
  listing: null,
  snapshots: [],
  events: [],
  keywords: { coverage: [] },
  job: null,
  chart_rankings: [],
  collection_request: null,
};
async function setup(responder) {
  const window = new Window({
    url: "http://localhost:5173",
    settings: {
      disableCSSFileLoading: true,
      disableJavaScriptFileLoading: true,
    },
  });
  window.document.write(html);
  window.TextEncoder = TextEncoder;
  const calls = [];
  window.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === "/api/auth/config")
      return {
        ok: true,
        status: 200,
        json: async () => ({
          initialization_required: false,
          registration_enabled: true,
        }),
      };
    if (path === "/api/auth/me")
      return {
        ok: true,
        status: 200,
        json: async () => ({
          user: {
            id: 1,
            username: "admin",
            role: "admin",
            effective_role: "admin",
            permissions: [],
          },
          csrf_token: "test-csrf",
        }),
      };
    let data =
      path === "/api/catalog"
        ? catalogFixture
        : path.split("?")[0] === "/api/overview"
          ? overview
          : responder?.(path, options);
    if (
      data === undefined &&
      path.startsWith("/api/research/collection/tasks?")
    )
      data = {
        items: [],
        total: 0,
        summary: { running: 0, pending: 0, error: 0, paused: 0 },
        has_more: false,
      };
    if (
      data === undefined &&
      path.split("?")[0] === "/api/research/collection/pipelines"
    )
      data = [];
    if (data === undefined && path === "/api/research/collection/limits")
      data = {
        global_interval_seconds: 5,
        google_interval_seconds: 2.5,
        apple_interval_seconds: 3.5,
        cooldown_seconds: 900,
        paused: false,
      };
    if (
      data === undefined &&
      path.split("?")[0] === "/api/research/collection/activity"
    )
      data = { plans: [], heartbeat: null };
    if (data === undefined && path.startsWith("/api/discovery/report-summary?"))
      data = {
        counts: { reports: 1, records: 1, unique_keywords: 1 },
        shared_between_apps: 0,
        shared_between_types: 0,
        note: "测试汇总",
      };
    if (data === undefined && path === "/api/discovery/keywords/overview")
      data = emptyKeywordOverview();
    if (data === undefined) throw new Error("Unmocked route " + path);
    return { ok: true, status: 200, json: async () => data };
  };
  window.eval(
    accountSource +
      "\n" +
      themeSource +
      "\n" +
      selectorSource +
      "\n" +
      researchSource +
      "\n" +
      "\n" +
      newModules +
      "\n" +
      source.replace(/^import\s[^;]*;$/gm, ""),
  );
  await tick();
  return { window, calls };
}

test("navigation queries the API and escapes upstream application content", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/apps?")
      ? {
          items: [
            {
              store: "apple",
              app_id: "123",
              name: "<img src=x onerror=alert(1)>",
              category: "finance",
              state: null,
              payload: null,
            },
          ],
          next: null,
        }
      : undefined,
  );
  try {
    const hint = window.document.querySelector("#workspace-description");
    assert.equal(hint.tagName, "BLOCKQUOTE");
    assert.equal(hint.textContent, "等待后台首次心跳");
    window.location.hash = "apps";
    await tick();
    assert.match(
      window.document.querySelector("main").textContent,
      /<img src=x onerror=alert\(1\)>/,
    );
    assert.equal(window.document.querySelector("img[onerror]"), null);
    assert.equal(hint.textContent, "勾选应用可批量加入监控分组。");
    assert.equal(hint.title, hint.textContent);
    assert.equal(window.document.querySelector("main .lead"), null);
    assert.ok(calls.some((c) => c.path.startsWith("/api/apps?")));
    assert.equal(window.document.querySelectorAll("#navigation a").length, 12);
    assert.equal(
      window.document.querySelectorAll("#navigation a .ui-icon").length,
      12,
    );
    assert.ok(window.document.querySelector('[data-nav="groups"]'));
    assert.ok(window.document.querySelector('[data-pages~="datasources"]'));
    assert.equal(window.document.querySelector('[data-nav="account"]'), null);
    assert.equal(
      window.document.querySelectorAll('#account-menu [role="menuitem"] svg')
        .length,
      3,
    );
    window.document.querySelector("#account-trigger").click();
    assert.equal(window.document.querySelector("#account-menu").hidden, false);
    window.document
      .querySelector("#account-menu")
      .dispatchEvent(
        new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    assert.equal(window.document.querySelector("#account-menu").hidden, true);
    assert.equal(window.document.activeElement.id, "account-trigger");
    assert.ok(window.document.querySelector('a[href="#availability"]'));
  } finally {
    await window.happyDOM.close();
  }
});

test("watchlist configuration moves to the center and sends explicit market and numeric interval", async () => {
  const { window, calls } = await setup((path, options) => {
    if (path.split("?")[0] === "/api/availability/watched")
      return { items: [], groups: [], total: 0, offset: 0, limit: 50 };
    if (path === "/api/research/plans/preview") {
      const v = JSON.parse(options.body);
      return {
        total: 1,
        actions: [
          {
            kind: "details",
            value: { app_id: v.app_ids[0], refresh_hours: v.refresh_hours },
          },
        ],
      };
    }
    if (path === "/api/research/plans") return { id: 1 };
  });
  try {
    window.location.hash = "watchlist";
    await tick();
    assert.equal(window.document.querySelector("#watch-form"), null);
    assert.ok(
      window.document.querySelector("#detail-task-filter[data-filter-bar]"),
    );
    window.document.querySelector('a[href="#collection?new=details"]').click();
    await tick();
    const form = window.document.querySelector("#plan-form");
    form.elements.app_ids.value = "com.example.app";
    form.elements.refresh_hours.value = "6";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    window.document.querySelector("#commit-plan").click();
    await tick();
    const call = calls.find(
      (c) => c.path === "/api/research/plans" && c.options.method === "POST",
    );
    assert.ok(call);
    const body = JSON.parse(call.options.body);
    assert.equal(body.refresh_hours, 6);
    assert.equal(body.country, "us");
    assert.equal(body.store, "google");
    assert.deepEqual(body.app_ids, ["com.example.app"]);
  } finally {
    await window.happyDOM.close();
  }
});

test("detail monitoring uses bounded pages, fuzzy Enter filtering and reset", async () => {
  const { window, calls } = await setup((path) => {
    if (!path.startsWith("/api/availability/watched?")) return;
    const p = new URL(path, "http://local").searchParams;
    return {
      items: [],
      groups: ["现金贷类APP"],
      total: 2132,
      offset: Number(p.get("offset")),
      limit: Number(p.get("limit")),
    };
  });
  try {
    window.location.hash = "watchlist";
    await tick();
    window.document.querySelector("#detail-task-pages-next").click();
    await tick();
    assert.ok(calls.at(-1).path.includes("offset=50"));
    const form = window.document.querySelector("#detail-task-filter");
    form.elements.q.value = "loan";
    form.elements.q.dispatchEvent(
      new window.KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        cancelable: true,
      }),
    );
    await tick();
    assert.ok(calls.at(-1).path.includes("q=loan"));
    assert.ok(calls.at(-1).path.includes("offset=0"));
    window.document
      .querySelector("#detail-task-filter [data-filter-reset-button]")
      .click();
    await tick();
    assert.equal(
      new URL(calls.at(-1).path, "http://local").searchParams.get("q"),
      null,
    );
    assert.equal(
      window.document.querySelector("#detail-task-filter").elements.q.value,
      "",
    );
    assert.equal(
      calls.some((c) => c.path.split("?")[0] === "/api/watchlist"),
      false,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("failed API produces visible error and retry recovers", async () => {
  let broken = true;
  const { window } = await setup((path) => {
    if (path.split("?")[0] === "/api/discovery/keywords/coverage") {
      if (broken) throw new Error("offline");
      return emptyCoverage;
    }
  });
  try {
    window.location.hash = "keywords";
    await tick();
    assert.match(window.document.querySelector("main").textContent, /offline/);
    broken = false;
    window.document.querySelector("#retry-load").click();
    await tick();
    assert.ok(
      window.document.querySelector('a[href="#collection?new=keywords"]'),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("keyword country overview shows global inventory and monitoring across tabs and store filters", async () => {
  const stats = {
    ...emptyKeywordOverview(),
    totals: {
      library_words: 14000,
      monitored_words: 7000,
      enabled_words: 5000,
      paused_words: 2000,
      fresh_words: 3000,
      tasks: 18000,
      languages: 2,
      monitoring_languages: 1,
      countries: 2,
      monitoring_countries: 1,
    },
    items: [
      {
        country: "us",
        country_name: "美国",
        language: "en",
        language_name: "英语",
        library_words: 10000,
        monitored_words: 7000,
        enabled_words: 5000,
        paused_words: 2000,
        fresh_words: 3000,
        countries: 2,
        tasks: 18000,
      },
      {
        country: "th",
        country_name: "泰国",
        language: "th",
        language_name: "泰语 <sample>",
        library_words: 4000,
        monitored_words: 0,
        enabled_words: 0,
        paused_words: 0,
        fresh_words: 0,
        countries: 0,
        tasks: 0,
      },
    ],
  };
  const { window, calls } = await setup((path) => {
    if (path === "/api/discovery/keywords/overview") return stats;
    if (path.startsWith("/api/keywords?"))
      return [
        {
          id: 88,
          store: "google",
          country: "us",
          language: "en",
          keyword: "loan",
          depth: 50,
          refresh_hours: 24,
        },
      ];
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/keywords/candidates?"))
      return { items: [], total: 0, source_records: 0 };
    if (path === "/api/discovery/keywords/sources")
      return { note: "fixture", frequency_languages: ["en"] };
  });
  try {
    window.location.hash = "keywords";
    await tick();
    const panel = () =>
      window.document.querySelector(".keyword-coverage-overview");
    assert.match(panel().textContent, /全系统.*全部商店与国家/);
    const rows = [...panel().querySelectorAll("tbody tr")];
    assert.equal(rows.length, 2);
    assert.deepEqual(
      [...panel().querySelectorAll(".metric .value")].map(
        (el) => el.textContent,
      ),
      ["7,000", "5,000", "3,000", "1"],
    );
    assert.deepEqual(
      [...rows[0].querySelectorAll("td")].map((td) => td.textContent.trim()),
      [
        "美国 US",
        "英语 en",
        "10,000",
        "7,000",
        "5,000",
        "2,000",
        "3,000",
        "18,000",
      ],
    );
    assert.match(rows[1].textContent, /泰语 <sample>/);
    assert.equal(panel().querySelector("sample"), null);
    const filter = window.document.querySelector("#keyword-overview-filter");
    filter.value = "th";
    filter.dispatchEvent(new window.Event("input"));
    assert.equal(rows[0].hidden, true);
    assert.equal(rows[1].hidden, false);
    assert.match(
      window.document.querySelector("#keyword-overview-count").textContent,
      /1 \/ 2/,
    );
    filter.value = "";
    filter.dispatchEvent(new window.Event("input"));
    assert.ok(rows.every((row) => !row.hidden));
    window.document.querySelector('[data-keyword-tab="candidates"]').click();
    await tick();
    assert.match(panel().textContent, /泰语/);
    const before = calls.filter((c) =>
      c.path.startsWith("/api/discovery/keywords/candidates?"),
    ).length;
    window.document.querySelector("#global-store").value = "all";
    window.document
      .querySelector("#global-store")
      .dispatchEvent(new window.Event("change"));
    await tick();
    assert.ok(panel(), window.document.querySelector("main").textContent);
    assert.match(panel().textContent, /已纳入监控/);
    assert.equal(
      calls.filter((c) =>
        c.path.startsWith("/api/discovery/keywords/candidates?"),
      ).length,
      before,
    );
    assert.equal(window.document.querySelector("[data-keyword-tab]"), null);
    assert.ok(window.document.querySelector('[data-keyword="88"]'));
    assert.ok(
      calls
        .filter((c) => c.path.includes("/discovery/keywords/overview"))
        .every((c) => c.path === "/api/discovery/keywords/overview"),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("keyword country overview failures are retryable and late responses cannot replace another page", async () => {
  let phase = "error",
    finish;
  const { window } = await setup((path) => {
    if (path === "/api/discovery/keywords/overview") {
      if (phase === "error") throw Error("offline");
      if (phase === "pending")
        return new Promise((resolve) => (finish = resolve));
      return emptyKeywordOverview();
    }
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/apps?")) return { items: [], next: null };
  });
  try {
    window.location.hash = "keywords";
    await tick();
    assert.match(
      window.document.querySelector(".keyword-coverage-overview").textContent,
      /暂时无法加载/,
    );
    assert.equal(
      window.document.querySelector(".keyword-coverage-metrics"),
      null,
    );
    assert.ok(window.document.querySelector("#coverage-app-form"));
    phase = "ok";
    window.document.querySelector("#keyword-overview-retry").click();
    await tick();
    assert.ok(window.document.querySelector(".keyword-coverage-metrics"));
    phase = "pending";
    window.document.querySelector("#refresh-page").click();
    await tick();
    window.location.hash = "apps";
    await tick();
    finish(emptyKeywordOverview());
    await tick();
    assert.equal(
      window.document.querySelector(".keyword-coverage-overview"),
      null,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("app country changes keep app identity while store changes leave cross-store IDs behind", async () => {
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/apps/apple/123?")) return emptyApp;
    if (path === "/api/research/markets/apple/123")
      return { items: [], scope: "observed" };
    if (path.startsWith("/api/apps?")) return { items: [], next: null };
  });
  try {
    window.location.hash = "app/apple/123";
    await tick();
    assert.equal(window.document.querySelector("#global-store").value, "apple");
    window.document.querySelector("#country-trigger").click();
    await tick();
    const country = window.document.querySelector(
      '#country-picker [data-country-code="kz"]',
    );
    assert.equal(country.disabled, false);
    country.click();
    await tick();
    assert.ok(
      calls.some(
        (c) =>
          c.path.startsWith("/api/apps/apple/123?") &&
          c.path.includes("country=kz"),
      ),
    );
    assert.match(
      window.document.querySelector("#country-value").textContent,
      /哈萨克斯坦/,
    );
    const store = window.document.querySelector("#global-store");
    store.value = "google";
    store.dispatchEvent(new window.Event("change"));
    await tick();
    assert.equal(window.location.hash, "#apps");
    assert.equal(
      window.document.querySelector("#global-store").value,
      "google",
    );
    assert.equal(
      calls.some((c) => c.path.startsWith("/api/apps/google/123")),
      false,
    );
    assert.equal(window.document.querySelector(".errorbox"), null);
  } finally {
    await window.happyDOM.close();
  }
});

test("app collect button submits current storefront and shows asynchronous progress", async () => {
  let request;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/apps/apple/123?")) return emptyApp;
    if (path === "/api/research/markets/apple/123")
      return { items: [], scope: "observed" };
    if (path === "/api/apps/apple/123/collect") {
      request = JSON.parse(options.body);
      return {
        id: 7,
        state: "running",
        finished: 0,
        total: 1,
        errors: 0,
        tasks: [],
        scope: "public data",
      };
    }
  });
  try {
    window.document.querySelector("#global-country").value = "kz";
    window.location.hash = "app/apple/123";
    await tick();
    window.document.querySelector("#collect-app-now").click();
    await tick();
    assert.equal(request.country, "kz");
    assert.equal(request.language, "ru");
    assert.equal(
      window.document.querySelector("#collect-app-now").disabled,
      true,
    );
    assert.match(
      window.document.querySelector("#app-collection-progress").textContent,
      /0\/1/,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("chart center exposes seven views and requests the selected collection", async () => {
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/charts/view?")) {
      const id = new URL(path, "http://localhost").searchParams.get(
        "collection",
      );
      return {
        definition: catalogFixture.charts.google.find((d) => d.id === id),
        items: [],
        target: null,
        latest_success: null,
      };
    }
  });
  try {
    window.location.hash = "charts";
    await tick();
    const select = window.document.querySelector(
      '#chart-view-form [name="collection"]',
    );
    assert.equal(select.options.length, 7);
    select.value = "new-grossing";
    select.dispatchEvent(new window.Event("change"));
    await tick();
    assert.ok(
      calls.some(
        (c) =>
          c.path.includes("/charts/view?") &&
          c.path.includes("collection=new-grossing"),
      ),
    );
    assert.match(window.document.querySelector("main").textContent, /本站派生/);
  } finally {
    await window.happyDOM.close();
  }
});

test("keyword-only plan sends multilingual words and configured depth", async () => {
  let preview;
  const { window } = await setup((path, options) => {
    if (path === "/api/research/plans/preview") {
      preview = JSON.parse(options.body);
      return {
        total: 2,
        actions: preview.keywords.map((keyword) => ({
          kind: "keywords",
          value: {
            keyword,
            depth: preview.keyword_depth,
            refresh_hours: preview.refresh_hours,
          },
        })),
      };
    }
  });
  try {
    window.location.hash = "collection?new=keywords";
    await tick();
    const form = window.document.querySelector("#plan-form");
    assert.equal(form.elements.collect_keywords.checked, true);
    form.elements.keywords.value = "เงินกู้\nvay tiền";
    form.elements.keyword_depth.value = "25";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.deepEqual(preview.keywords, ["เงินกู้", "vay tiền"]);
    assert.equal(preview.keyword_depth, 25);
    assert.equal(preview.details, false);
    assert.deepEqual(preview.app_ids, []);
    assert.ok(window.document.querySelector("#commit-plan"));
  } finally {
    await window.happyDOM.close();
  }
});

test("category cards drill into state and batch interval preserves scope", async () => {
  const queries = [],
    bodies = [];
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/research/collection/tasks?")) {
      queries.push(new URLSearchParams(path.split("?")[1]));
      return {
        items: [],
        total: 120000,
        summary: { running: 2, pending: 119995, error: 3, paused: 0 },
        categories: [
          {
            kind: "keywords",
            total: 120000,
            summary: { running: 2, pending: 119995, error: 3 },
          },
        ],
        has_more: true,
      };
    }
    if (path === "/api/research/collection/tasks/batch") {
      bodies.push(JSON.parse(options.body));
      return { updated: 120000 };
    }
  });
  try {
    window.confirm = () => true;
    window.location.hash = "collection";
    await tick();
    assert.equal(
      window.document.querySelectorAll(".collection-category").length,
      11,
    );
    for (const kind of ["aso_popularity", "aso_import", "aso_report"])
      assert.ok(window.document.querySelector(`[data-category="${kind}"]`));
    assert.equal(
      window.document.querySelectorAll(".collection-legend").length,
      1,
    );
    const keywordCard = window.document
      .querySelector('[data-category="keywords"]')
      .closest(".collection-category");
    assert.doesNotMatch(
      keywordCard.textContent,
      /定时采集|等待采集|失败待重试/,
    );
    assert.equal(
      keywordCard.querySelectorAll(".category-counts button").length,
      5,
    );
    assert.match(
      keywordCard
        .querySelector('[data-category-state="error"]')
        .getAttribute("aria-label"),
      /失败待重试.*3/,
    );
    assert.equal(
      keywordCard.querySelector(".state-track").getAttribute("aria-hidden"),
      "true",
    );
    assert.equal(keywordCard.querySelectorAll(".state-track").length, 5);
    assert.equal(
      keywordCard.querySelector(
        '[data-category-state="error"] .state-track > span',
      ).style.width,
      "0.0025%",
    );
    window.document
      .querySelector('[data-category="keywords"][data-category-state="error"]')
      .click();
    await tick();
    assert.equal(queries.at(-1).get("kind"), "keywords");
    assert.equal(queries.at(-1).get("state"), "error");
    assert.equal(queries.at(-1).get("page"), "1");
    window.document.querySelector("#select-filtered-tasks").click();
    window.document.querySelector('[data-batch="interval"]').click();
    const form = window.document.querySelector("#batch-interval-form");
    form.elements.refresh_hours.value = "168";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(bodies[0].scope, "filtered");
    assert.equal(bodies[0].refresh_hours, 168);
    assert.equal(bodies[0].filters.kind, "keywords");
    assert.equal(bodies[0].filters.state, "error");
    window.document.querySelector("#retry-failed-tasks").click();
    await tick();
    assert.equal(bodies[1].action, "retry");
    assert.equal(bodies[1].scope, "filtered");
    assert.equal(
      window.document.querySelector('[name="daily_request_limit"]').value,
      "0",
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("cross-page selection sends scope and filters without enumerating pages", async () => {
  const bodies = [];
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/research/collection/tasks?"))
      return {
        items: [],
        total: 175,
        summary: { running: 0, pending: 175, error: 0, paused: 0 },
        has_more: true,
      };
    if (path === "/api/research/collection/tasks/batch") {
      bodies.push(JSON.parse(options.body));
      return { updated: 175 };
    }
  });
  try {
    window.confirm = () => true;
    window.location.hash = "collection";
    await tick();
    window.document.querySelector("#select-filtered-tasks").click();
    assert.match(
      window.document.querySelector("#selected-count").textContent,
      /175/,
    );
    assert.equal(
      window.document.querySelector('[data-batch="refresh"]').disabled,
      true,
    );
    window.document.querySelector('[data-batch="pause"]').click();
    await tick();
    assert.equal(bodies[0].scope, "filtered");
    assert.equal(bodies[0].action, "pause");
    assert.equal("tasks" in bodies[0], false);
    window.document.querySelector("#select-every-task").click();
    window.document.querySelector('[data-batch="resume"]').click();
    await tick();
    assert.deepEqual(bodies[1], {
      scope: "all",
      action: "resume",
      filters: {},
    });
  } finally {
    await window.happyDOM.close();
  }
});

test("pending task controls block editing and cannot replace a newly selected page", async () => {
  let release;
  const { window } = await setup((path) => {
    if (path.startsWith("/api/research/collection/tasks?"))
      return {
        total: 1,
        summary: { running: 0, pending: 1, error: 0, paused: 0 },
        has_more: false,
        items: [
          {
            resource: "keywords",
            id: 1,
            kind: "keywords",
            label: "loan",
            store: "google",
            country: "th",
            language: "th",
            depth: 50,
            refresh_hours: 24,
            enabled: 1,
            task_state: "pending",
          },
        ],
      };
    if (path === "/api/research/collection/tasks/batch")
      return new Promise((resolve) => {
        release = resolve;
      });
    if (path.split("?")[0] === "/api/discovery/keywords/coverage")
      return emptyCoverage;
  });
  try {
    window.location.hash = "collection";
    await tick();
    window.document
      .querySelector('[data-task-action="0"][data-action="pause"]')
      .click();
    await tick();
    assert.equal(
      window.document.querySelector('[data-edit-task="0"]').disabled,
      true,
    );
    window.location.hash = "keywords";
    await tick();
    release({ updated: 1 });
    await tick();
    assert.equal(
      window.document.querySelector("#page-title").textContent,
      "关键词研究",
    );
    assert.equal(window.document.querySelector("#task-filters"), null);
  } finally {
    await window.happyDOM.close();
  }
});

test("ranking screen distinguishes failed latest attempt from preserved successful data", async () => {
  const { window } = await setup((path) =>
    path.split("?")[0] === "/api/discovery/keywords/coverage"
      ? {
          ...emptyCoverage,
          stats: { tracked: 1, fresh: 0 },
          keywords: [
            {
              id: 1,
              keyword: "chat",
              depth: 50,
              last_error: "HTTP 429",
              fresh: false,
            },
          ],
        }
      : path === "/api/keywords/1"
        ? {
            target: {
              keyword: "chat",
              store: "apple",
              country: "us",
              language: "en",
            },
            latest_success: {
              observed_at: 100,
              source: "apple-native-search",
              coverage: "first_response",
              returned_count: 1,
            },
            latest_attempt: { outcome: "error", error: "HTTP 429" },
            stale: true,
            ranks: [
              { position: 1, app_id: "123", name: "Example", change: null },
            ],
          }
        : undefined,
  );
  try {
    window.document.querySelector("#global-store").value = "apple";
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector("[data-keyword]").click();
    await tick();
    assert.match(window.document.querySelector("main").textContent, /HTTP 429/);
    assert.match(window.document.querySelector("main").textContent, /Example/);
    assert.match(
      window.document.querySelector("main").textContent,
      /数据待更新/,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("ASO workbench edits a versioned local draft and never submits to a store", async () => {
  const counters = [
    { field: "name", label: "应用名称", limit: 30, unit: "characters" },
    { field: "summary", label: "简短描述", limit: 80, unit: "characters" },
    { field: "description", label: "描述", limit: 4000, unit: "characters" },
  ];
  const checked = { counters, findings: [], coverage: [], valid: true };
  let saved;
  const { window, calls } = await setup((path, options) => {
    if (path.startsWith("/api/research/aso?"))
      return {
        metadata: {
          name: "Original",
          summary: "Summary",
          description: "Description",
        },
        checks: checked,
        keywords: [],
        drafts: [],
        unavailable_fields: [],
        snapshot: { id: 1 },
        last_success: 100,
      };
    if (path === "/api/research/aso/check") return checked;
    if (path.startsWith("/api/research/aso/keywords?")) {
      const q = new URL(path, "https://example.org").searchParams;
      assert.equal(q.get("app_id"), "com.example.app");
      return {
        total: 101,
        limit: 100,
        note: "历史自然搜索词",
        items: [
          {
            keyword: q.get("offset") === "100" ? "next word" : "report word",
            best_report_position: 1,
            latest_report_at: 100,
          },
        ],
      };
    }
    if (path === "/api/research/aso/drafts") {
      saved = JSON.parse(options.body);
      return { id: 1, revision: 1, published: false };
    }
    if (path === "/api/research/aso/drafts/1")
      return {
        id: 1,
        revision: 1,
        name: saved.name,
        metadata: saved.metadata,
        note: saved.note,
        versions: [
          {
            revision: 1,
            saved_at: 200,
            note: saved.note,
            metadata: saved.metadata,
          },
        ],
      };
  });
  try {
    window.location.hash = "aso";
    await tick();
    assert.ok(window.document.querySelector("#aso-load[data-filter-bar]"));
    window.document.querySelector("#aso-load [name=app_id]").value =
      "com.example.app";
    window.document
      .querySelector("#aso-load")
      .dispatchEvent(
        new window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    const editor = window.document.querySelector("#aso-editor");
    const words = window.document.querySelector("#aso-words");
    words.value = "manual word\nreport word";
    window.document.querySelector("#aso-library-add").click();
    await tick();
    assert.equal(words.value, "manual word\nreport word");
    window.document.querySelector("#aso-library-next").click();
    await tick();
    window.document.querySelector("#aso-library-add").click();
    await tick();
    assert.equal(words.value, "manual word\nreport word\nnext word");
    assert.equal(editor.elements.name.value, "Original");
    editor.elements.name.value = "Edited title";
    editor.dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(
      window.document.querySelector("#preview-title").textContent,
      "Edited title",
    );
    editor.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(saved.metadata.name, "Edited title");
    assert.equal(saved.app_id, "com.example.app");
    assert.ok(
      window.document.querySelector('#aso-draft-list [data-draft-id="1"]'),
    );
    assert.match(window.document.querySelector("#toast").textContent, /未发布/);
    assert.ok(calls.every((c) => c.path.startsWith("/api/")));
  } finally {
    await window.happyDOM.close();
  }
});

test("review records provide original content and history without handling controls", async () => {
  const row = {
    store: "google",
    app_id: "com.example.app",
    country: "us",
    language: "en",
    review_id: "r1",
    rating: 1,
    content: "<img src=x onerror=alert(1)>",
    title: "Problem",
    version: "1",
    first_seen: 100,
    last_seen: 200,
    public_reply: "Observed reply",
    owner: "must-not-show",
    workflow_status: "open",
    labels: ["must-not-show"],
    reply_draft: "must-not-show",
  };
  const { window, calls } = await setup((path, options) => {
    if (path.startsWith("/api/research/reviews?"))
      return {
        items: [row],
        summary: { total: 51, apps: 1, mean_review_rating: 1 },
        ratings: [{ rating: 1, count: 51 }],
        daily: [],
        next_offset: path.includes("offset=50") ? null : 50,
        scope: "sample",
      };
    if (path === "/api/research/reviews/history")
      return {
        items: [
          {
            observed_at: 100,
            payload: {
              rating: 1,
              version: "1",
              content: "<script>original</script>",
              reply: "Historical public reply",
            },
          },
        ],
      };
  });
  try {
    window.location.hash = "reviews";
    await tick();
    const doc = window.document;
    assert.equal(doc.querySelector("#page-title").textContent, "评论记录");
    assert.ok(!doc.querySelector("main").textContent.includes("must-not-show"));
    assert.equal(doc.querySelector("[name=status]"), null);
    assert.equal(doc.querySelector("[name=label]"), null);
    assert.equal(doc.querySelector("img[onerror]"), null);
    doc.querySelector("[data-review-detail]").click();
    assert.equal(doc.querySelector("#review-case-form"), null);
    assert.equal(doc.querySelector("[name=reply_draft]"), null);
    assert.match(
      doc.querySelector("#research-dialog").textContent,
      /Observed reply/,
    );
    doc.querySelector("#review-revisions").click();
    await tick();
    assert.match(doc.querySelector("#review-history").textContent, /original/);
    assert.equal(doc.querySelector("#review-history script"), null);
    assert.deepEqual(
      JSON.parse(
        calls.find((c) => c.path === "/api/research/reviews/history").options
          .body,
      ),
      {
        store: "google",
        app_id: "com.example.app",
        country: "us",
        language: "en",
        review_id: "r1",
      },
    );
    doc.querySelector("#research-dialog").close();
    doc.querySelector("#reviews-next").click();
    await tick();
    assert.ok(
      calls.some(
        (c) => c.path.includes("/reviews?") && c.path.includes("offset=50"),
      ),
    );
    assert.equal(doc.querySelector("#reviews-next").disabled, true);
    doc.querySelector("#reviews-prev").click();
    await tick();
    doc.querySelector("[data-review-app]").click();
    await tick();
    assert.equal(
      doc.querySelector("#review-record-filter").elements.app_id.value,
      "com.example.app",
    );
    assert.equal(doc.querySelector("#reviews-prev").disabled, true);
    doc.querySelector("#configure-reviews").click();
    assert.equal(
      window.location.hash,
      "#collection?new=reviews&app=com.example.app",
    );
    assert.equal(
      calls.filter(
        (c) =>
          c.options?.method &&
          c.options.method !== "GET" &&
          c.path.startsWith("/api/research/reviews") &&
          c.path !== "/api/research/reviews/history",
      ).length,
      0,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("collection plan requires a fresh preview and creates only reviewed actions", async () => {
  const quality = {
    summary: { listed: 0, stale: 0, failed: 0, paused: 0 },
    plans: [],
    targets: [],
    sources: [],
    heartbeat: null,
  };
  let created;
  const { window, calls } = await setup((path, options) => {
    if (path === "/api/research/collection") return quality;
    if (path === "/api/research/plans/preview") {
      const s = JSON.parse(options.body);
      return {
        total: 2,
        scope: "preview",
        actions: [
          {
            kind: "details",
            value: { app_id: s.app_ids[0], refresh_hours: s.refresh_hours },
          },
          {
            kind: "reviews",
            value: { app_id: s.app_ids[0], refresh_hours: s.refresh_hours },
          },
        ],
      };
    }
    if (path === "/api/research/plans") {
      created = JSON.parse(options.body);
      return { id: 1, status: "queued" };
    }
  });
  try {
    window.location.hash = "collection";
    await tick();
    window.document.querySelector("#new-collection").click();
    await tick();
    const form = window.document.querySelector("#plan-form");
    form.elements.collect_reviews.checked = true;
    form.elements.app_ids.value = "com.example.app";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.ok(window.document.querySelector("#commit-plan"));
    assert.equal(created, undefined);
    form.elements.refresh_hours.value = "6";
    form.elements.refresh_hours.dispatchEvent(
      new window.Event("input", { bubbles: true }),
    );
    assert.equal(window.document.querySelector("#commit-plan"), null);
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    window.document.querySelector("#commit-plan").click();
    await tick();
    assert.equal(created.refresh_hours, 6);
    assert.deepEqual(created.app_ids, ["com.example.app"]);
    assert.equal(
      calls.filter((c) => c.path === "/api/research/plans").length,
      1,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("app selection creates a same-market competitor set with no assumed ownership", async () => {
  let saved;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/apps?"))
      return {
        items: ["com.a.app", "com.b.app"].map((app_id) => ({
          store: "google",
          app_id,
          name: app_id,
          payload: null,
        })),
        next: null,
      };
    if (
      path.split("?")[0] === "/api/research/comparisons" &&
      options.method === "POST"
    ) {
      saved = JSON.parse(options.body);
      return { id: 1, revision: 1 };
    }
    if (path.split("?")[0] === "/api/research/comparisons") return [];
  });
  try {
    window.location.hash = "apps";
    await tick();
    const all = window.document.querySelector("#pool-select-all");
    all.checked = true;
    all.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(
      window.document.querySelector("#bulk-compare").disabled,
      false,
    );
    window.document.querySelector("#bulk-compare").click();
    const form = window.document.querySelector("#bulk-set");
    form.elements.name.value = "Finance";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(saved.members.length, 2);
    assert.ok(
      saved.members.every(
        (m) =>
          m.store === "google" && m.country === "us" && m.role === "competitor",
      ),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("candidate selection creates only selected tracking targets and retains market context", async () => {
  let submitted;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/keywords/candidates?"))
      return {
        total: 2,
        items: [
          {
            id: 11,
            keyword: "<script>bad</script>",
            source: "manual",
            license_name: "user",
          },
          { id: 12, keyword: "chat", source: "manual", license_name: "user" },
        ],
      };
    if (path === "/api/discovery/keywords/sources")
      return { frequency_languages: ["en"], note: "候选词不是热搜" };
    if (path === "/api/discovery/keywords/track") {
      submitted = JSON.parse(options.body);
      return { targets: 1, existing: 0 };
    }
  });
  try {
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector('[data-keyword-tab="candidates"]').click();
    await tick();
    assert.equal(window.document.querySelectorAll("#content script").length, 0);
    window.document.querySelector('[data-pick="id"][value="12"]').checked =
      true;
    window.document.querySelector("#candidate-track").click();
    await tick();
    assert.deepEqual(submitted.ids, [12]);
  } finally {
    await window.happyDOM.close();
  }
});

test("empty ASO market exposes populated markets and switches exact scope, with independent query", async () => {
  const catalog = {
    total_terms: 3,
    items: [
      {
        store: "apple",
        country: "cn",
        language: "zh-cn",
        terms: 3,
        reports: 1,
        apps: 1,
      },
    ],
  };
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/keywords/candidates?"))
      return { total: 0, items: [] };
    if (path === "/api/discovery/keywords/sources")
      return { frequency_languages: ["en"], note: "候选库" };
    if (path.startsWith("/api/discovery/keywords/aso-library?")) {
      const p = new URL(path, "https://example.org").searchParams;
      const selected =
        p.get("store") === "apple" &&
        p.get("country") === "cn" &&
        p.get("language") === "zh-cn";
      return {
        catalog,
        market_terms: selected ? 3 : 0,
        total: selected && !p.get("q") ? 3 : 0,
        counts: { reports: selected ? 1 : 0, apps: selected ? 1 : 0 },
        note: "自然搜索词",
        items:
          selected && !p.get("q")
            ? [{ keyword: "报告词", candidate_id: 22, apps: 1, reports: 1 }]
            : [],
      };
    }
  });
  try {
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector('[data-keyword-tab="candidates"]').click();
    await tick();
    const search = window.document.querySelector("#candidate-search");
    search.elements.q.value = "previous candidate filter";
    search.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    window.document.querySelector('[data-keyword-tab="aso-library"]').click();
    await tick();
    assert.match(
      window.document.querySelector("#content").textContent,
      /当前筛选.*没有ASO报告词/,
    );
    assert.equal(
      window.document.querySelector("#aso-library-search [name=q]").value,
      "",
    );
    assert.equal(
      window.document.querySelector("#candidate-track").disabled,
      true,
    );
    window.document.querySelector('[data-aso-market="0"]').click();
    await tick();
    for (const [key, value] of Object.entries({
      store: "apple",
      country: "cn",
      language: "zh-cn",
    }))
      assert.equal(
        window.document.querySelector(`#global-${key}`).value,
        value,
      );
    assert.match(
      window.document.querySelector("#content").textContent,
      /报告词/,
    );
    const form = window.document.querySelector("#aso-library-search");
    form.elements.q.value = "missing";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.match(
      window.document.querySelector("#content").textContent,
      /没有匹配筛选词/,
    );
    window.document.querySelector("#aso-clear-query").click();
    await tick();
    assert.match(
      window.document.querySelector("#content").textContent,
      /报告词/,
    );
    assert.ok(
      calls.some(
        (c) =>
          c.path.includes("aso-library?") && c.path.includes("language=zh-cn"),
      ),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("ASO library browses natural words and tracks selected candidate IDs", async () => {
  let submitted;
  const { window, calls } = await setup((path, options) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/keywords/aso-library?"))
      return {
        total: 101,
        counts: { reports: 2, apps: 2 },
        note: "自然搜索词",
        items: [
          {
            keyword: "<script>natural</script>",
            candidate_id: 22,
            best_report_position: null,
            apps: 2,
            reports: 2,
            latest_report_at: 100,
          },
        ],
      };
    if (path === "/api/discovery/keywords/track") {
      submitted = JSON.parse(options.body);
      return { targets: 1, existing: 0 };
    }
  });
  try {
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector('[data-keyword-tab="aso-library"]').click();
    await tick();
    assert.equal(window.document.querySelectorAll("#content script").length, 0);
    window.document.querySelector("#candidate-all").click();
    window.document.querySelector("#candidate-track").click();
    await tick();
    assert.deepEqual(submitted.ids, [22]);
    window.document.querySelector("#candidate-next").click();
    await tick();
    assert.ok(
      calls.some(
        (c) => c.path.includes("aso-library?") && c.path.includes("offset=100"),
      ),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("external report rows preserve missing rank and source metrics when promoted", async () => {
  let submitted;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/reports?"))
      return [
        {
          id: 7,
          app_id: "123",
          filename: "report.xlsx",
          source: "外部报告",
          observed_at: 1,
          summary: [{ sheet: "ASO关键词", rows: 1 }],
        },
      ];
    if (path.startsWith("/api/discovery/reports/7?"))
      return {
        total: 1,
        items: [
          {
            source_row: 3,
            keyword: "term",
            position: null,
            search_index: 0,
            popularity: 5,
            payload: {
              headers: ["关键词", "排名"],
              values: ["term", "未覆盖"],
            },
          },
        ],
      };
    if (path === "/api/discovery/reports/promote") {
      submitted = JSON.parse(options.body);
      return { processed: 1 };
    }
  });
  try {
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector('[data-keyword-tab="reports"]').click();
    await tick();
    assert.match(
      window.document.querySelector("#content").textContent,
      /第三方报告/,
    );
    assert.match(
      window.document.querySelector("#content").textContent,
      /未覆盖/,
    );
    window.document.querySelector('[data-pick="source_row"]').checked = true;
    window.document.querySelector("#report-promote").click();
    await tick();
    assert.deepEqual(submitted, {
      report_id: 7,
      sheet: "ASO关键词",
      source_rows: [3],
    });
  } finally {
    await window.happyDOM.close();
  }
});

test("global rankings show countries and multiple chart kinds together and preview collection scope", async () => {
  let plan;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/charts/global?"))
      return {
        selected_codes: ["us", "jp"],
        total_countries: 2,
        page_size: 6,
        has_more: false,
        fresh_boards: 6,
        requested_boards: 6,
        observed_boards: 6,
        note: "各国独立排名",
        available_countries: [
          { code: "us", name: "美国", english: "United States" },
          { code: "jp", name: "日本", english: "Japan" },
        ],
        countries: [
          {
            country: "us",
            name: "美国",
            language: "en",
            boards: ["top-free", "top-paid", "top-grossing"].map((id) => ({
              collection: id,
              definition: catalogFixture.charts.google.find((d) => d.id === id),
              items: [
                { app_id: "com.example.app", name: "US App", position: 1 },
              ],
              fresh: true,
              observed_at: 10,
              returned_count: 100,
            })),
          },
          {
            country: "jp",
            name: "日本",
            language: "ja",
            boards: [
              {
                collection: "top-free",
                definition: catalogFixture.charts.google[0],
                items: [
                  { app_id: "com.example.app", name: "JP App", position: 7 },
                ],
                fresh: true,
                observed_at: 10,
                returned_count: 100,
              },
            ],
          },
        ],
      };
    if (path === "/api/charts/global/preview") {
      plan = JSON.parse(options.body);
      return { countries: 2, total: 6, unsupported: [], note: "预览" };
    }
  });
  try {
    window.location.hash = "globalcharts";
    await tick();
    assert.equal(window.document.querySelectorAll(".global-country").length, 2);
    assert.equal(window.document.querySelectorAll(".global-chart").length, 4);
    assert.ok(
      window.document.querySelector(
        '[data-app="com.example.app"][data-country="jp"][data-language="ja"]',
      ),
    );
    const layoutForm = window.document.querySelector("#global-chart-form");
    const grid = window.document.querySelector(".global-board-grid");
    assert.equal(grid.dataset.layout, "rows");
    assert.deepEqual(
      [...layoutForm.elements.layout.options].map((o) => [
        o.value,
        o.textContent,
      ]),
      [
        ["rows", "横向排列"],
        ["columns", "纵向排列"],
      ],
    );
    const countryNodes = [...grid.querySelectorAll(".global-country")];
    const originalLinks = [...grid.querySelectorAll(".chart-title-link")].map(
      (el) => el.href,
    );
    layoutForm.elements.depth.value = "20";
    layoutForm.elements.layout.value = "columns";
    layoutForm.elements.layout.dispatchEvent(
      new window.Event("change", { bubbles: true }),
    );
    assert.equal(grid.dataset.layout, "columns");
    assert.equal(layoutForm.elements.depth.value, "20");
    assert.deepEqual(
      [...grid.querySelectorAll(".global-country")],
      countryNodes,
    );
    assert.deepEqual(
      [...grid.querySelectorAll(".chart-title-link")].map((el) => el.href),
      originalLinks,
    );
    assert.equal(
      JSON.parse(window.localStorage.getItem("appmon-global-chart-filters"))
        .google.layout,
      "columns",
    );
    layoutForm.elements.layout.value = "rows";
    layoutForm.elements.layout.dispatchEvent(
      new window.Event("change", { bubbles: true }),
    );
    assert.equal(grid.dataset.layout, "rows");
    assert.equal(window.document.querySelectorAll(".global-chart").length, 4);
    window.document.querySelector("#global-configure").click();
    await tick();
    assert.equal(plan.countries, "us,jp");
    assert.equal(plan.collections, "top-free,top-paid,top-grossing");
    assert.ok(window.document.querySelector("#global-plan-confirm"));
    const doc = window.document;
    doc.querySelector("#global-countries-trigger").click();
    await tick();
    doc.querySelector('[data-country-action="clear"]').click();
    const search = doc.querySelector("#country-picker input");
    search.value = "日本";
    search.dispatchEvent(new window.Event("input"));
    doc.querySelector('[data-country-code="jp"]').click();
    doc.querySelector('[data-country-action="apply"]').click();
    assert.equal(
      doc.querySelector("#global-plan-confirm"),
      null,
      "changing the scope invalidates a reviewed plan",
    );
    assert.equal(
      doc.querySelector("#global-country").value,
      "us",
      "global board selection does not alter header market",
    );
    doc.querySelector("#global-configure").click();
    await tick();
    assert.equal(plan.countries, "jp");
  } finally {
    await window.happyDOM.close();
  }
});

test("horizontal chart app cells preserve source rank slots and links when switching orientation", async () => {
  const { window } = await setup((path) => {
    if (!path.startsWith("/api/charts/global?")) return;
    return {
      ...emptyGlobalBoard(),
      selected_codes: ["jp"],
      countries: [
        {
          country: "jp",
          name: "日本",
          language: "ja",
          boards: ["top-free", "top-paid"].map((collection) => ({
            collection,
            definition: catalogFixture.charts.google.find(
              (d) => d.id === collection,
            ),
            items: [
              { position: 3, app_id: "com.example.third", name: "Third <app>" },
              { position: 1, app_id: "com.example.first", name: "First app" },
            ],
            fresh: true,
            observed_at: 10,
            returned_count: 3,
          })),
        },
      ],
    };
  });
  try {
    window.location.hash = "globalcharts";
    await tick();
    const doc = window.document;
    const country = doc.querySelector(".global-country");
    assert.equal(country.style.getPropertyValue("--chart-slots"), "3");
    const lists = [...country.querySelectorAll(".global-chart-apps")];
    assert.equal(lists.length, 2);
    for (const list of lists) {
      const cells = [...list.querySelectorAll(".global-ranked-app")];
      assert.deepEqual(
        cells.map((c) => c.getAttribute("value")),
        ["1", "3"],
      );
      assert.deepEqual(
        cells.map((c) => c.style.getPropertyValue("--rank-position")),
        ["1", "3"],
      );
      assert.deepEqual(
        cells.map((c) => c.querySelector("[data-app]").dataset.app),
        ["com.example.first", "com.example.third"],
      );
      assert.ok(
        cells.every((c) =>
          c.querySelector("[data-country='jp'][data-language='ja']"),
        ),
      );
    }
    assert.equal(country.querySelector("table"), null);
    const select = doc.querySelector('[name="layout"]');
    select.value = "columns";
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(country.closest("[data-layout]").dataset.layout, "columns");
    assert.deepEqual(
      [...country.querySelectorAll(".global-chart-apps")],
      lists,
    );
    assert.equal(
      lists[0].querySelectorAll(".global-position")[1].textContent,
      "3",
    );
    select.value = "rows";
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(country.closest("[data-layout]").dataset.layout, "rows");
    assert.equal(country.querySelectorAll(".global-ranked-app").length, 4);
  } finally {
    await window.happyDOM.close();
  }
});

test("coverage pagination reaches later terms and resets after a market change", async () => {
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return {
        ...emptyCoverage,
        pages: {
          apps: { total: 0, offset: 0, limit: 100, has_more: false },
          keywords: {
            total: 250,
            offset:
              new URL(path, "http://localhost").searchParams.get(
                "keyword_offset",
              ) === "100"
                ? 100
                : 0,
            limit: 100,
            has_more: true,
          },
        },
      };
  });
  try {
    window.location.hash = "keywords";
    await tick();
    window.document
      .querySelector('[data-pager="coverage-keywords"] [data-page="2"]')
      .click();
    await tick();
    assert.ok(calls.some((c) => c.path.includes("keyword_offset=100")));
    const country = window.document.querySelector("#global-country");
    country.value = "jp";
    country.dispatchEvent(new window.Event("change"));
    await tick();
    const last = calls
      .filter((c) => c.path.startsWith("/api/discovery/keywords/coverage?"))
      .at(-1).path;
    assert.ok(last.includes("country=jp"));
    assert.ok(last.includes("keyword_offset=0"));
  } finally {
    await window.happyDOM.close();
  }
});

test("ASO popularity submission follows its queue receipt before displaying saved data", async () => {
  let finished = false;
  let polls = 0;
  const { window, calls } = await setup((path, options) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/keywords/signals?"))
      return { runs: [], items: [], selected: null, note: "来源快照" };
    if (path === "/api/discovery/keywords/apple-popularity")
      return options.method === "POST"
        ? { id: 81, resource: "sources", status: "pending" }
        : {
            configured: true,
            genres: ["FINANCE"],
            default_start: "2025-01-05",
            note: "已授权",
          };
    if (path === "/api/discovery/keywords/tasks/81") {
      if (++polls === 1) return { id: 81, status: "error", failures: 1 };
      finished = true;
      return { id: 81, status: "done", result: { id: 37, returned: 40 } };
    }
  });
  try {
    window.document.querySelector("#global-store").value = "apple";
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector('[data-keyword-tab="hot"]').click();
    await tick();
    window.document.querySelector("#apple-popularity-form").requestSubmit();
    await tick();
    assert.equal(finished, false);
    assert.ok(
      calls.some(
        (c) =>
          c.path === "/api/discovery/keywords/apple-popularity" &&
          c.options.method === "POST",
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 1100));
    await tick();
    assert.equal(finished, false);
    assert.equal(polls, 1);
    await new Promise((resolve) => setTimeout(resolve, 3100));
    await tick();
    assert.equal(finished, true);
    assert.ok(
      calls.some(
        (c) =>
          c.path.startsWith("/api/discovery/keywords/signals?") &&
          c.path.includes("run_id=37"),
      ),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("official popularity does not send a network query when credentials are unconfigured", async () => {
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/discovery/keywords/coverage?"))
      return emptyCoverage;
    if (path.startsWith("/api/discovery/keywords/signals?"))
      return { runs: [], items: [], selected: null, note: "来源快照" };
    if (path === "/api/discovery/keywords/apple-popularity")
      return {
        configured: false,
        genres: ["FINANCE"],
        default_start: "2025-01-05",
        note: "授权未配置",
      };
  });
  try {
    window.document.querySelector("#global-store").value = "apple";
    window.location.hash = "keywords";
    await tick();
    window.document.querySelector('[data-keyword-tab="hot"]').click();
    await tick();
    assert.equal(
      window.document.querySelector(
        '#apple-popularity-form button[type="submit"]',
      ).disabled,
      true,
    );
    assert.ok(
      window.document.querySelector("#apple-popularity-form[data-filter-bar]"),
    );
    window.document
      .querySelector("#apple-popularity-form [data-filter-reset-button]")
      .click();
    assert.equal(
      window.document.querySelector(
        '#apple-popularity-form button[type="submit"]',
      ).disabled,
      true,
    );

    assert.match(
      window.document.querySelector("#content").textContent,
      /待配置/,
    );
    assert.equal(
      calls.filter(
        (c) =>
          c.path === "/api/discovery/keywords/apple-popularity" &&
          c.options.method === "POST",
      ).length,
      0,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("global chart links retain storefront, category and time and open a richer paged chart", async () => {
  let detailQuery;
  const longName = "很长的应用名称用于验证两行截断与完整悬停文本 <unsafe>";
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/charts/global?"))
      return {
        selected_codes: ["jp"],
        total_countries: 1,
        page_size: 6,
        has_more: false,
        fresh_boards: 1,
        requested_boards: 1,
        observed_boards: 1,
        note: "按国家",
        available_countries: [{ code: "jp", name: "日本", english: "Japan" }],
        countries: [
          {
            country: "jp",
            name: "日本",
            language: "ja",
            boards: [
              {
                collection: "top-paid",
                definition: catalogFixture.charts.google[1],
                items: [
                  { app_id: "com.example.app", name: longName, position: 1 },
                ],
                fresh: true,
                observed_at: 10,
                returned_count: 100,
              },
            ],
          },
        ],
      };
    if (path.startsWith("/api/charts/detail?")) {
      detailQuery = new URL(path, "http://localhost").searchParams;
      return {
        definition: catalogFixture.charts.google[1],
        items: [
          {
            app_id: "com.example.app",
            name: longName,
            position: 1,
            developer: "Developer",
            metadata: {
              rating: 4.7,
              ratings: 123,
              price: 6,
              currency: "JPY",
              version: "1.2",
            },
            metadata_observed_at: 100,
          },
        ],
        total: 100,
        chart_count: 100,
        offset: Number(detailQuery.get("offset") || 0),
        limit: 50,
        has_more:
          !detailQuery.get("offset") || detailQuery.get("offset") === "0",
        base_returned_count: 100,
        latest_success: { observed_at: 10, source: "fixture" },
        metadata_note: "资料独立观察",
      };
    }
  });
  try {
    window.location.hash = "globalcharts";
    await tick();
    window.document.querySelector("#global-category").value = "FINANCE";
    window.document.querySelector('#global-chart-form [name="as_of"]').value =
      "2026-10-01";
    window.document
      .querySelector("#global-chart-form")
      .dispatchEvent(
        new window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    const title = window.document.querySelector(".global-chart .appname");
    assert.equal(title.title, longName);
    assert.equal(window.document.querySelector("#content unsafe"), null);
    const link = window.document.querySelector(".chart-title-link");
    assert.ok(link.href.includes("country=jp"));
    assert.ok(link.href.includes("collection=top-paid"));
    link.click();
    await tick();
    assert.equal(window.location.hash.split("?")[0], "#chart");
    assert.equal(detailQuery.get("country"), "jp");
    assert.equal(detailQuery.get("language"), "ja");
    assert.equal(detailQuery.get("category"), "FINANCE");
    assert.equal(
      Number(detailQuery.get("as_of")),
      Date.parse("2026-10-01T23:59:59") / 1000,
    );
    assert.match(
      window.document.querySelector("#content").textContent,
      /Developer/,
    );
    assert.match(window.document.querySelector("#content").textContent, /JPY/);
    window.document.querySelector("#chart-detail-next").click();
    await tick();
    assert.ok(
      calls.some(
        (c) =>
          c.path.includes("/charts/detail?") && c.path.includes("offset=50"),
      ),
    );
    window.document.querySelector("#global-country").value = "us";
    window.document
      .querySelector("#global-country")
      .dispatchEvent(new window.Event("change"));
    await tick();
    assert.equal(detailQuery.get("country"), "us");
    assert.equal(detailQuery.get("offset"), "0");
  } finally {
    await window.happyDOM.close();
  }
});

test("multi-store global boards preserve store links and count countries once", async () => {
  const { window, calls } = await setup((path) => {
    if (!path.startsWith("/api/charts/global?")) return;
    return {
      selected_codes: ["us"],
      total_countries: 1,
      page_country_count: 1,
      page_size: 6,
      has_more: false,
      fresh_boards: 2,
      requested_boards: 2,
      observed_boards: 2,
      available_countries: [
        { code: "us", name: "美国", english: "United States" },
      ],
      note: "各商店独立排名",
      countries: ["google", "apple"].map((store) => ({
        store,
        country: "us",
        name: "美国",
        language: "en",
        boards: [
          {
            collection: "top-free",
            definition: catalogFixture.charts[store][0],
            fresh: true,
            items: [
              {
                app_id: store === "apple" ? "123" : "com.example.app",
                name: store + " App",
                position: store === "apple" ? 3 : 1,
              },
            ],
            returned_count: 100,
            observed_at: 10,
          },
        ],
      })),
    };
  });
  try {
    window.document.querySelector("#global-store").value = "all";
    window.location.hash = "globalcharts";
    await tick();
    assert.ok(calls.some((c) => c.path.includes("charts/global?store=all")));
    const titles = [
      ...window.document.querySelectorAll(".global-country h2"),
    ].map((e) => e.textContent);
    assert.match(titles[0], /Google Play/);
    assert.match(titles[1], /App Store/);
    const links = [
      ...window.document.querySelectorAll(".chart-title-link"),
    ].map((e) => e.getAttribute("href"));
    assert.match(links[0], /store=google/);
    assert.match(links[1], /store=apple/);
    assert.match(
      window.document.querySelector("#content").textContent,
      /共 1 国/,
    );
    assert.ok(
      window.document.querySelector('[data-store="apple"][data-app="123"]'),
    );
  } finally {
    await window.happyDOM.close();
  }
});

const emptyGlobalBoard = () => ({
  selected_codes: ["us"],
  total_countries: 1,
  page_size: 6,
  has_more: false,
  fresh_boards: 0,
  requested_boards: 3,
  observed_boards: 0,
  countries: [],
  note: "暂无观察",
  available_countries: [{ code: "us", name: "美国", english: "United States" }],
});

test("compact global chart headings keep observation details on hover and failures visible", async () => {
  const states = [
    { fresh: true },
    { fresh: false, last_error: "<unsafe>source failed</unsafe>" },
    { fresh: false },
    { fresh: false, observed_at: null },
    { fresh: false, quality: "insufficient_history", reason: "need history" },
    { fresh: false, unsupported: true },
  ];
  const { window } = await setup((path) => {
    if (!path.startsWith("/api/charts/global?")) return;
    return {
      ...emptyGlobalBoard(),
      countries: [
        {
          country: "us",
          name: "美国",
          language: "en",
          boards: states.map((state, i) => ({
            collection: catalogFixture.charts.google[i].id,
            definition: catalogFixture.charts.google[i],
            items: [],
            observed_at: 10,
            returned_count: 100,
            ...state,
          })),
        },
      ],
    };
  });
  try {
    window.location.hash = "globalcharts";
    await tick();
    const doc = window.document;
    const headings = [...doc.querySelectorAll(".global-chart-summary")];
    assert.equal(headings.length, 6);
    assert.ok(
      headings.every(
        (h) => !/更新时间|返回 100 条|有效观察/.test(h.textContent),
      ),
    );
    assert.match(
      headings[0].querySelector("a").title,
      /更新时间：.*返回 100 条/,
    );
    assert.match(
      headings[0].querySelector("a").getAttribute("aria-label"),
      /更新时间/,
    );
    assert.deepEqual(
      headings.map(
        (h) => h.querySelector(".global-chart-status")?.textContent || "",
      ),
      ["", "采集异常", "待更新", "未采集", "历史不足", "不支持"],
    );
    assert.match(headings[1].title, /<unsafe>source failed<\/unsafe>/);
    assert.equal(doc.querySelector("unsafe"), null);
    assert.equal(
      doc.querySelector(".global-chart-meta,.chart-more-link"),
      null,
    );
    assert.ok(headings[5].querySelector('[tabindex="0"][title]'));
  } finally {
    await window.happyDOM.close();
  }
});

test("horizontal Top 10 preferences upgrade once to Top 20 and later explicit choices persist", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/charts/global?") ? emptyGlobalBoard() : undefined,
  );
  const depth = () =>
    new URL(
      calls.filter((c) => c.path.startsWith("/api/charts/global?")).at(-1).path,
      "http://localhost",
    ).searchParams.get("depth");
  try {
    window.localStorage.setItem(
      "appmon-global-chart-filters",
      JSON.stringify({
        google: { layout: "rows", depth: "10" },
        apple: { layout: "columns", depth: "10" },
      }),
    );
    window.location.hash = "globalcharts";
    await tick();
    assert.equal(depth(), "20");
    const form = window.document.querySelector("#global-chart-form");
    form.elements.depth.value = "10";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(depth(), "10");
    assert.equal(
      JSON.parse(window.localStorage.getItem("appmon-global-chart-filters"))
        .google.display_version,
      2,
    );
    const changeStore = async (value) => {
      window.document.querySelector("#global-store").value = value;
      window.document
        .querySelector("#global-store")
        .dispatchEvent(new window.Event("change"));
      await tick();
    };
    await changeStore("apple");
    assert.equal(depth(), "10");
    await changeStore("google");
    assert.equal(depth(), "10");
  } finally {
    await window.happyDOM.close();
  }
});

test("global row and column layouts migrate old preferences and persist per store through refresh and reset", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/charts/global?") ? emptyGlobalBoard() : undefined,
  );
  try {
    window.localStorage.setItem(
      "appmon-global-chart-filters",
      JSON.stringify({
        google: { layout: "compare" },
        apple: { layout: "countries" },
      }),
    );
    window.location.hash = "globalcharts";
    await tick();
    const layout = () =>
      window.document.querySelector(".global-board-grid").dataset.layout;
    assert.equal(layout(), "columns");
    const count = () =>
      calls.filter((c) => c.path.startsWith("/api/charts/global?")).length;
    const before = count();
    const select = window.document.querySelector('[name="layout"]');
    select.value = "rows";
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
    select.value = "columns";
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
    await tick();
    assert.equal(
      count(),
      before,
      "orientation changes reuse the displayed observations",
    );
    window.document.querySelector("#refresh-page").click();
    await tick();
    assert.equal(layout(), "columns");
    const switchStore = async (store) => {
      window.document.querySelector("#global-store").value = store;
      window.document
        .querySelector("#global-store")
        .dispatchEvent(new window.Event("change"));
      await tick();
    };
    await switchStore("apple");
    assert.equal(layout(), "rows");
    await switchStore("google");
    assert.equal(layout(), "columns");
    window.document
      .querySelector("#global-chart-form [data-filter-reset-button]")
      .click();
    await tick();
    assert.equal(layout(), "rows");
    assert.equal(
      JSON.parse(window.localStorage.getItem("appmon-global-chart-filters"))
        .google.layout,
      "rows",
    );
  } finally {
    await window.happyDOM.close();
  }
});
test("global filters recover invalid saved values and reject future dates without poisoning refresh", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/charts/global?") ? emptyGlobalBoard() : undefined,
  );
  try {
    window.localStorage.setItem(
      "appmon-global-chart-filters",
      JSON.stringify({
        google: {
          countries: "zz,us,us",
          collections: "retired,top-free,top-free",
          category: "bogus",
          as_of: "2999-01-01",
          depth: "999",
          layout: "invalid",
          store: "apple",
        },
      }),
    );
    window.location.hash = "globalcharts";
    await tick();
    const query = new URL(
      calls.find((c) => c.path.startsWith("/api/charts/global?")).path,
      "http://localhost",
    ).searchParams;
    assert.equal(query.get("store"), "google");
    assert.equal(query.get("countries"), "us");
    assert.equal(query.get("collections"), "top-free");
    assert.equal(query.get("depth"), "20");
    assert.equal(query.get("category"), null);
    assert.equal(query.get("as_of"), null);
    const form = window.document.querySelector("#global-chart-form");
    form.elements.as_of.value = "2999-01-01";
    const before = calls.filter((c) =>
      c.path.startsWith("/api/charts/global?"),
    ).length;
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(
      calls.filter((c) => c.path.startsWith("/api/charts/global?")).length,
      before,
    );
    assert.match(
      window.document.querySelector("#toast").textContent,
      /不能晚于今天/,
    );
    window.document.querySelector("#refresh-page").click();
    await tick();
    assert.ok(window.document.querySelector("#global-countries-trigger"));
    assert.equal(
      window.document.querySelector('#global-chart-form [name="as_of"]').value,
      "",
    );
  } finally {
    await window.happyDOM.close();
  }
});
test("a delayed collection preview cannot restore an obsolete scope", async () => {
  let finishPreview;
  const { window } = await setup((path) => {
    if (path.startsWith("/api/charts/global?")) return emptyGlobalBoard();
    if (path === "/api/charts/global/preview")
      return new Promise((resolve) => (finishPreview = resolve));
  });
  try {
    window.location.hash = "globalcharts";
    await tick();
    window.document.querySelector("#global-configure").click();
    await tick();
    const collection = window.document.querySelector(
      '[name="collection"][value="top-paid"]',
    );
    collection.checked = false;
    collection.dispatchEvent(new window.Event("change", { bubbles: true }));
    finishPreview({ countries: 1, total: 3, unsupported: [], note: "旧预览" });
    await tick();
    assert.equal(window.document.querySelector("#global-plan-confirm"), null);
    window.document.querySelector("#global-configure").click();
    await tick();
    finishPreview({ countries: 1, total: 2, unsupported: [], note: "新预览" });
    await tick();
    assert.match(
      window.document.querySelector("#global-plan-preview").textContent,
      /新预览/,
    );
    assert.ok(window.document.querySelector("#global-plan-confirm"));
  } finally {
    await window.happyDOM.close();
  }
});
test("global countries are independent of an unsupported header Apple market", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/charts/global?") ? emptyGlobalBoard() : undefined,
  );
  try {
    window.document.querySelector("#global-store").value = "apple";
    window.document.querySelector("#global-country").value = "aq";
    window.location.hash = "globalcharts";
    await tick();
    assert.ok(
      calls.some((c) => c.path.includes("/api/charts/global?store=apple")),
    );
    assert.ok(window.document.querySelector("#global-countries-trigger"));
  } finally {
    await window.happyDOM.close();
  }
});

test("admin source configuration submits secrets in the request body, clears password and shows authorization", async () => {
  const result = {
    community: {
      configured: false,
      status: "not_configured",
      message: "未配置",
    },
    catalog: [],
  };
  const { window, calls } = await setup((path) => {
    if (path === "/api/data-sources") return result;
    if (path === "/api/data-sources/marketdata/collection")
      return { runs: [], artifacts: [], errors: [], budget: null };
    if (path === "/api/data-sources/directory")
      return {
        directory: {
          configured: true,
          status: "authorized",
          message: "安装指标授权有效",
        },
      };
    if (path === "/api/data-sources/community")
      return {
        community: {
          configured: true,
          status: "authorized",
          message: "授权有效",
        },
      };
  });
  try {
    window.location.hash = "datasources";
    await tick();
    const sourceQuery = window.document.querySelector("#source-app-query");
    assert.equal(sourceQuery.dataset.filterBar, "");
    assert.ok(sourceQuery.querySelector('[data-filter-type="store"]'));
    sourceQuery.elements.app_id.value = "com.example.app";
    sourceQuery.querySelector("[data-filter-reset-button]").click();
    assert.equal(sourceQuery.elements.app_id.value, "");
    assert.equal(
      calls.some((c) => c.path === "/api/data-sources/app-metrics"),
      false,
    );
    const form = window.document.querySelector("#provider-account");
    form.elements.email.value = "fixture@example.com";
    form.elements.password.value = "fixture-secret";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    assert.equal(form.elements.password.value, "");
    await tick();
    const call = calls.find((c) => c.path === "/api/data-sources/community");
    assert.equal(call.options.method, "PUT");
    assert.deepEqual(JSON.parse(call.options.body), {
      email: "fixture@example.com",
      password: "fixture-secret",
    });
    assert.match(
      window.document.querySelector("#provider-state").textContent,
      /授权有效/,
    );
    assert.equal(
      window.document.querySelector("#provider-authorize").disabled,
      false,
    );
    assert.ok(calls.every((c) => !c.path.includes("fixture-secret")));
    const directory = window.document.querySelector("#directory-account");
    directory.elements.email.value = "directory@example.com";
    directory.elements.password.value = "directory-secret";
    directory.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    assert.equal(directory.elements.password.value, "");
    await tick();
    assert.match(
      window.document.querySelector("#directory-state").textContent,
      /安装指标授权有效/,
    );
    assert.ok(
      calls.some(
        (c) =>
          c.path === "/api/data-sources/directory" &&
          JSON.parse(c.options.body).email === "directory@example.com",
      ),
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("store lookup reuses shared query controls and reset clears without fetching", async () => {
  const { window, calls } = await setup((path) =>
    path.startsWith("/api/apps?") ? { items: [], next: null } : undefined,
  );
  try {
    window.location.hash = "apps";
    await tick();
    const form = window.document.querySelector(
      "#store-search-form[data-filter-bar]",
    );
    assert.ok(form);
    assert.equal(form.elements.query.required, true);
    form.elements.query.value = "chat";
    form.querySelector("[data-filter-reset-button]").click();
    assert.equal(form.elements.query.value, "");
    assert.equal(
      calls.some((c) => c.path === "/api/discovery/search"),
      false,
    );
  } finally {
    await window.happyDOM.close();
  }
});
test("collection view tabs reuse loaded registry while explicit query refreshes", async () => {
  const { window, calls } = await setup();
  try {
    window.location.hash = "collection";
    await tick();
    const loaded = calls.filter((c) =>
      c.path.startsWith("/api/research/collection/"),
    ).length;
    window.document
      .querySelector('#workspace-pages a[href="#collection?view=pipelines"]')
      .click();
    await tick();
    window.document
      .querySelector('#workspace-pages a[href="#collection"]')
      .click();
    await tick();
    assert.equal(
      calls.filter((c) => c.path.startsWith("/api/research/collection/"))
        .length,
      loaded,
    );
    window.document
      .querySelector("#task-filters")
      .dispatchEvent(
        new window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    assert.ok(
      calls.filter((c) => c.path.startsWith("/api/research/collection/"))
        .length > loaded,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("event dates request a complete server-filtered page and clear the date", async () => {
  const day = Date.UTC(2026, 10, 5) / 1000;
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/research/events?"))
      return {
        items: [],
        daily: [{ day, kind: "first_verified", count: 350 }],
        summary: [{ kind: "first_verified", count: 350 }],
        annotations: [],
        groups: ["贷款组"],
        total: 350,
        limit: 50,
        offset: 0,
      };
  });
  try {
    window.location.hash = "events";
    await tick();
    assert.ok(
      window.document.querySelector(
        '#event-filter [name=group_name] option[value="贷款组"]',
      ),
    );
    window.document
      .querySelector(`.calendar-cell[data-event-day="${day}"]`)
      .click();
    await tick();
    assert.ok(calls.at(-1).path.includes("day=2026-11-05"));
    assert.equal(
      window.document
        .querySelector(".calendar-cell")
        .getAttribute("aria-pressed"),
      "true",
    );
    window.document.querySelector('[data-event-day=""]').click();
    await tick();
    assert.equal(
      new URL(calls.at(-1).path, "http://local").searchParams.get("day"),
      null,
    );
  } finally {
    await window.happyDOM.close();
  }
});

test("shared app details retain exact markets, zero metrics and own-category rank semantics", async () => {
  const item = {
    store: "google",
    app_id: "com.loan.example",
    country: "th",
    language: "en",
    name: "Loan",
  };
  let requested;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/apps?")) return { items: [item], next: null };
    if (path === "/api/apps/detail-rows") {
      requested = JSON.parse(options.body);
      return {
        items: [
          {
            ...item,
            developer_id: "6942553452222258407",
            category: "FINANCE",
            rating: 0,
            price: 0,
            chart_positions: [
              {
                category: "FINANCE",
                collection: "top-free",
                sampled: true,
                position: 4,
              },
              {
                category: "",
                collection: "top-paid",
                sampled: true,
                position: null,
              },
            ],
          },
        ],
      };
    }
  });
  try {
    window.location.hash = "apps";
    await tick();
    assert.deepEqual(requested.targets, [
      {
        store: "google",
        app_id: "com.loan.example",
        country: "th",
        language: "en",
      },
    ]);
    const grid = window.document.querySelector(".group-member-table");
    const text = grid.textContent;
    for (const label of [
      "通用榜单",
      "分类榜单",
      "权限采集",
      "下载量",
      "收入",
      "关键词覆盖",
    ])
      assert.ok(text.includes(label));
    assert.equal(
      grid.querySelector('td[data-col="developer_id"]').textContent,
      "6942553452222258407",
    );
    assert.equal(grid.querySelector('td[data-col="chart1"]').textContent, "");
    assert.equal(grid.querySelector('td[data-col="chart3"]').textContent, "4");
    assert.equal(
      grid.querySelector('td[data-col="rating"]').textContent,
      "0.00",
    );
    assert.equal(grid.querySelector("[data-app]").dataset.country, "th");
    assert.ok(!window.document.querySelector("[data-adjust-row]"));
  } finally {
    await window.happyDOM.close();
  }
});

test("multi-store tracking selects independent official categories and carries scope into collection", async () => {
  const { window, calls } = await setup((path) => {
    if (path.startsWith("/api/charts/view?")) {
      const params = new URL(path, "http://localhost").searchParams;
      return {
        definition: catalogFixture.charts[params.get("store")][0],
        items: [],
        target: null,
        latest_success: null,
      };
    }
  });
  try {
    const doc = window.document;
    doc.querySelector("#global-store").value = "all";
    window.location.hash = "charts";
    await tick();
    doc
      .querySelector('[data-category-input="tracking-google-category"]')
      .click();
    await tick();
    assert.equal(
      doc.querySelector("#category-picker-title").textContent,
      "Google Play 分类",
    );
    doc.querySelector('[data-select-category="FINANCE"]').click();
    await tick();
    doc
      .querySelector('[data-category-input="tracking-apple-category"]')
      .click();
    await tick();
    assert.equal(
      doc.querySelector("#category-picker-title").textContent,
      "App Store 分类",
    );
    doc.querySelector('[data-select-category="6015"]').click();
    await tick();
    const queries = calls
      .filter((c) => c.path.startsWith("/api/charts/view?"))
      .slice(-2)
      .map((c) => new URL(c.path, "http://localhost").searchParams);
    assert.deepEqual(
      queries.map((q) => [q.get("store"), q.get("category")]),
      [
        ["google", "FINANCE"],
        ["apple", "6015"],
      ],
    );
    doc.querySelector('[data-chart-store="google"]').click();
    await tick();
    assert.equal(doc.querySelector("#discovery-category").value, "FINANCE");
    const configure = doc.querySelector(
      '#chart-view-form a[href*="new=chart"]',
    );
    assert.match(configure.getAttribute("href"), /category=FINANCE/);
    window.location.hash = configure.getAttribute("href");
    await tick();
    assert.equal(doc.querySelector("#plan-category").value, "FINANCE");
  } finally {
    await window.happyDOM.close();
  }
});
