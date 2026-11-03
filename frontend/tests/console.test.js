import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const html = (
  await readFile(new URL("../index.html", import.meta.url), "utf8")
).replace(/<script[^>]*><\/script>/g, "");
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
    wrappedModule("icons.js", "icon,storeLogo"),
    wrappedModule(
      "monitoring-charts.js",
      "monitoringChart,bindMonitoringCharts",
    ),
    wrappedModule("app-insights.js", "createAppInsights"),
    wrappedModule("discovery.js", "createDiscoveryPages"),
    wrappedModule("availability.js", "createAvailabilityPage"),
    wrappedModule(
      "market-scope.js",
      "effectiveLanguage,autoCaption,languageChoices,recommendedLanguage,selectedStores,languageName",
    ),
    wrappedModule("app-icons.js", "createAppIconManager"),
    wrappedModule("market-controls.js", "initMarketControls"),
    wrappedModule("task-logs.js", "createTaskLogViewer"),
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
    path.startsWith("/api/keywords?") ? [] : undefined,
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
    await tick();
    assert.match(doc.querySelector("#language-value").textContent, /俄语/);
    doc.querySelector("#store-trigger").click();
    doc.querySelector('#store-menu [data-value="all"]').click();
    window.location.hash = "keywords";
    await tick();
    assert.ok(calls.some((c) => c.path === "/api/keywords?store=all"));
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
    if (data === undefined && path === "/api/research/collection/pipelines")
      data = [];
    if (
      data === undefined &&
      path.split("?")[0] === "/api/research/collection/activity"
    )
      data = { plans: [], heartbeat: null };
    if (data === undefined) throw new Error("Unmocked route " + path);
    return { ok: true, status: 200, json: async () => data };
  };
  window.eval(
    themeSource +
      "\n" +
      selectorSource +
      "\n" +
      researchSource +
      "\n" +
      "\n" +
      newModules +
      "\n" +
      source.replace(/^import .*;$/gm, ""),
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
    window.location.hash = "apps";
    await tick();
    assert.match(
      window.document.querySelector("main").textContent,
      /<img src=x onerror=alert\(1\)>/,
    );
    assert.equal(window.document.querySelector("img[onerror]"), null);
    assert.ok(calls.some((c) => c.path.startsWith("/api/apps?")));
    assert.equal(window.document.querySelectorAll("nav a").length, 17);
    assert.equal(window.document.querySelectorAll("nav a .ui-icon").length, 17);
    assert.ok(window.document.querySelector('a[href="#availability"]'));
  } finally {
    await window.happyDOM.close();
  }
});

test("watchlist configuration moves to the center and sends explicit market and numeric interval", async () => {
  const { window, calls } = await setup((path, options) => {
    if (path.split("?")[0] === "/api/watchlist") return [];
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
      "关键词中心",
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
    window.document.querySelector("#aso-load [name=app_id]").value =
      "com.example.app";
    window.document
      .querySelector("#aso-load")
      .dispatchEvent(
        new window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    const editor = window.document.querySelector("#aso-editor");
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

test("review desk keeps unknown reply state separate and saves internal workflow revision", async () => {
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
    labels: [],
    workflow_status: "open",
    public_reply_status: "unknown",
    owner: "",
    note: "",
    reply_draft: "",
    revision: 0,
    first_seen: 100,
    last_seen: 100,
  };
  let saved;
  const { window } = await setup((path, options) => {
    if (path.startsWith("/api/research/reviews?"))
      return {
        items: [row],
        summary: { total: 1, mean_review_rating: 1 },
        ratings: [{ rating: 1, count: 1 }],
        daily: [],
        next_offset: null,
        scope: "sample",
      };
    if (path === "/api/research/reviews/workflow") {
      saved = JSON.parse(options.body);
      return { revision: 1, published: false };
    }
  });
  try {
    window.location.hash = "reviews";
    await tick();
    assert.match(
      window.document.querySelector("main").textContent,
      /来源未提供回复状态/,
    );
    assert.equal(window.document.querySelector("img[onerror]"), null);
    window.document.querySelector("[data-review-case]").click();
    const form = window.document.querySelector("#review-case-form");
    form.elements.status.value = "closed";
    form.elements.reply_draft.value = "Draft response";
    form.elements.labels.value = "bug,login";
    form.dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
    await tick();
    assert.equal(saved.revision, 0);
    assert.equal(saved.reply_draft, "Draft response");
    assert.deepEqual(saved.labels, ["bug", "login"]);
    assert.match(window.document.querySelector("#toast").textContent, /未发布/);
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
          source: "点点",
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
