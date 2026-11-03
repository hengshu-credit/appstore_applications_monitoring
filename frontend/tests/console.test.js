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
    wrappedModule("market-controls.js", "initMarketControls"),
    wrappedModule("task-logs.js", "createTaskLogViewer"),
    wrappedModule("collection-center.js", "createCollectionCenter"),
  ])
).join("\n");
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
        : path === "/api/overview"
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
    if (data === undefined && path === "/api/research/collection/activity")
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
    assert.equal(window.document.querySelectorAll("nav a").length, 16);
    assert.equal(window.document.querySelectorAll("nav a .ui-icon").length, 16);
  } finally {
    await window.happyDOM.close();
  }
});

test("watchlist configuration moves to the center and sends explicit market and numeric interval", async () => {
  const { window, calls } = await setup((path, options) => {
    if (path === "/api/watchlist") return [];
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
    if (path === "/api/keywords") {
      if (broken) throw new Error("offline");
      return [];
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
    if (path === "/api/keywords") return [];
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
    path === "/api/keywords"
      ? [
          {
            id: 1,
            keyword: "chat",
            store: "apple",
            country: "us",
            language: "en",
            depth: 50,
            refresh_hours: 24,
            enabled: 1,
          },
        ]
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
    if (path === "/api/research/comparisons" && options.method === "POST") {
      saved = JSON.parse(options.body);
      return { id: 1, revision: 1 };
    }
    if (path === "/api/research/comparisons") return [];
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
