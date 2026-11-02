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
const source = (
  await readFile(new URL("../src/main.js", import.meta.url), "utf8")
).replace(/import ['"]\.\/style\.css['"];?/, "");
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
  const calls = [];
  window.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    let data = path === "/api/overview" ? overview : responder?.(path, options);
    if (data === undefined) throw new Error("Unmocked route " + path);
    return { ok: true, status: 200, json: async () => data };
  };
  window.eval(themeSource + "\n" + source.replace(/^import .*;$/gm, ""));
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
    assert.equal(window.document.querySelectorAll("nav a").length, 12);
  } finally {
    await window.happyDOM.close();
  }
});

test("watchlist form sends explicit market and numeric interval to Python API", async () => {
  const { window, calls } = await setup((path) =>
    path === "/api/watchlist" ? [] : undefined,
  );
  try {
    window.location.hash = "watchlist";
    await tick();
    window.document.querySelector("[name=app_id]").value = "com.example.app";
    window.document.querySelector("[name=refresh_hours]").value = "6";
    window.document
      .querySelector("#watch-form")
      .dispatchEvent(
        new window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    const call = calls.find(
      (c) => c.path === "/api/watchlist" && c.options.method === "POST",
    );
    assert.ok(call);
    const body = JSON.parse(call.options.body);
    assert.equal(body.refresh_hours, 6);
    assert.equal(body.country, "us");
    assert.equal(body.store, "google");
    assert.equal(body.app_id, "com.example.app");
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
    assert.ok(window.document.querySelector("#keyword-form"));
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
