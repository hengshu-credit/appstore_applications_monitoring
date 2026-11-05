import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const source = (
  await readFile(new URL("../src/app-groups.js", import.meta.url), "utf8")
).replace(/^export /gm, "");
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
const member = {
  id: 11,
  group_id: 1,
  store: "google",
  app_id: "com.example.loan",
  name: "<img src=x onerror=alert(1)>",
  country: "th",
  language: "en",
  state: "unverified",
  reference_state: "off",
  reference_time: "import time",
  permission_state: "pending",
  permission_count: null,
  permission_success: null,
  metrics: [],
  rating: null,
  ratings: null,
  installs: null,
  keyword_count: null,
  chart_count: null,
};
function dashboard() {
  return {
    group: { id: 1, name: "现金贷组合", description: "跨国观察", revision: 1 },
    stats: {
      apps: 2,
      targets: 3,
      countries: 2,
      states: { unverified: 3 },
      new_members: 3,
      fresh: 0,
      errors: 0,
      permission_states: { pending: 2, unsupported: 1 },
      ratings_known: 0,
      keyword_apps: 0,
      chart_apps: 0,
    },
    event_counts: {},
    events: [],
    countries: [
      {
        country: "th",
        apps: 1,
        targets: 1,
        states: { unverified: 1 },
        reference: { off: 1 },
      },
      {
        country: "vn",
        apps: 1,
        targets: 2,
        states: { unverified: 2 },
        reference: { on: 2 },
      },
    ],
    money: [],
    items: [member],
    total: 3,
    offset: 0,
    limit: 50,
    has_more: false,
    collection: { paused: false, cooldown_seconds: 0 },
    observed_at: 123,
    scope: "scoped",
  };
}
function setup(handler = () => undefined, write = true) {
  const window = new Window({ url: "http://localhost/#groups?id=1" }),
    doc = window.document,
    calls = [],
    errors = [];
  doc.body.innerHTML = "<main></main>";
  window.eval(source + ";window.createGroupWorkspace=createGroupWorkspace;");
  const esc = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll('"', "&quot;");
  const workspace = window.createGroupWorkspace({
    api: async (path, body, method) => {
      calls.push({ path, body: body ? structuredClone(body) : body, method });
      const custom = handler(path, body, method);
      if (custom !== undefined) return await custom;
      if (path === "/groups")
        return {
          items: [
            { id: 1, name: "现金贷组合", apps: 2, targets: 3, countries: 2 },
          ],
        };
      if (path.includes("/dashboard?")) return dashboard();
      if (path.includes("/members/11"))
        return {
          member,
          listing: null,
          permission: null,
          permission_state: "pending",
          permission_history: [],
          keywords: [],
          charts: [],
          snapshots: [],
          events: [],
          reference: { state: "off", observed_text: "import time" },
          metrics: [],
          reviews: [],
        };
      throw new Error(path);
    },
    esc,
    qs: (v) => new URLSearchParams(v).toString(),
    panel: (title, body) => `<section><h2>${title}</h2>${body}</section>`,
    table: (heads, rows) =>
      `<table>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`,
    metric: (title, v, n) =>
      `<article>${title}<strong>${v ?? "—"}</strong><small>${n}</small></article>`,
    chart: () => "<svg></svg>",
    stamp: (v) => (v ? "time" : "尚未采集"),
    num: (v) => (v == null ? "—" : Number(v).toLocaleString()),
    mount: (html) => {
      doc.querySelector("main").innerHTML = html;
      return true;
    },
    toast: (e) => errors.push(e),
    can: (_, action) => action !== "write" || write,
    countryName: (c) => ({ th: "泰国", vn: "越南" })[c] || c,
  });
  return { window, doc, calls, errors, workspace };
}

test("group dashboard drills through countries and keeps unknown observations separate from imported references", async () => {
  const c = setup();
  try {
    await c.workspace.render(1);
    assert.match(c.doc.querySelector(".group-countries").textContent, /泰国/);
    c.doc.querySelector('[data-group-country="th"]').click();
    await tick();
    assert.ok(c.calls.some((r) => r.path.includes("country=th")));
    assert.match(
      c.doc.querySelector("#group-tab-content").textContent,
      /未验证/,
    );
    assert.match(
      c.doc.querySelector("#group-tab-content").textContent,
      /参考不在架/,
    );
    assert.equal(c.doc.querySelector("img"), null);
    const before = c.calls.length;
    c.doc.querySelector('[data-group-tab="permissions"]').click();
    await tick();
    assert.equal(
      c.calls.length,
      before,
      "switching views should reuse the same scoped response",
    );
    assert.match(
      c.doc.querySelector("#group-tab-content").textContent,
      /待采集/,
    );
    c.doc.querySelector('[data-group-member="11"]').click();
    await tick();
    assert.match(c.doc.querySelector("#group-dialog").textContent, /未验证/);
    assert.ok(
      !c.doc.querySelector("#group-dialog").textContent.includes("NaN"),
    );
    c.doc.querySelector('[data-member-tab="metrics"]').click();
    assert.match(
      c.doc.querySelector("#group-member-content").textContent,
      /尚无下载/,
    );
    assert.deepEqual(c.errors, []);
  } finally {
    await c.window.happyDOM.close();
  }
});

test("group overview separates unchecked targets and clears hidden list filters", async () => {
  const c = setup();
  try {
    await c.workspace.render(1);
    const cards = [...c.doc.querySelectorAll(".group-kpis article")];
    assert.equal(
      cards
        .find((e) => e.textContent.startsWith("尚未验证"))
        .querySelector("strong").textContent,
      "3",
    );
    assert.equal(
      cards
        .find((e) => e.textContent.startsWith("缺失待确认"))
        .querySelector("strong").textContent,
      "0",
    );
    assert.equal(c.doc.querySelector('[name="state"]'), null);
    c.doc.querySelector('[data-group-country="th"]').click();
    await tick();
    const form = c.doc.querySelector("#group-filter");
    form.elements.q.value = "no match";
    form.elements.state.value = "available";
    form.dispatchEvent(new c.window.Event("submit", { cancelable: true }));
    await tick();
    c.doc.querySelector('[data-group-tab="overview"]').click();
    await tick();
    const query = new URL("http://local" + c.calls.at(-1).path).searchParams;
    assert.equal(query.get("q"), "");
    assert.equal(query.get("state"), "");
    assert.equal(query.get("country"), "th");
    assert.equal(c.doc.querySelectorAll(".group-country").length, 1);
    assert.match(c.doc.querySelector(".group-country").textContent, /泰国/);
    assert.equal(c.doc.querySelector('[name="country"]').options.length, 3);
    assert.deepEqual(c.errors, []);
  } finally {
    await c.window.happyDOM.close();
  }
});

test("country charts show observed status proportions and keep imported references out of totals", async () => {
  const c = setup((path) => {
    if (!path.includes("/dashboard?")) return;
    const data = dashboard();
    data.countries = [
      {
        country: "th",
        apps: 8,
        targets: 10,
        states: {
          available: 4,
          unverified: 3,
          suspected_missing: 2,
          region_unavailable: 1,
        },
        reference: { on: 99, off: 20 },
      },
    ];
    return data;
  });
  try {
    await c.workspace.render(1);
    const card = c.doc.querySelector(".group-country");
    assert.equal(card.querySelector(".country-total b").textContent, "8");
    assert.equal(card.querySelector(".country-targets").textContent, "10 目标");
    assert.deepEqual(
      [...card.querySelectorAll(".country-status b")].map((e) => e.textContent),
      ["4", "3", "2", "1"],
    );
    const bars = [
      ...card.querySelectorAll("svg rect:not(.country-state-empty)"),
    ];
    assert.deepEqual(
      bars.map((e) => Number(e.getAttribute("width"))),
      [400, 300, 200, 100],
    );
    assert.deepEqual(
      bars.map((e) => Number(e.getAttribute("x"))),
      [0, 400, 700, 900],
    );
    assert.match(card.getAttribute("aria-label"), /已验证在架 4/);
    assert.ok(!card.textContent.includes("99"));
  } finally {
    await c.window.happyDOM.close();
  }
});

test("bulk selected apps retain store country and language identities", async () => {
  const c = setup((path, body) =>
    path === "/groups/1/members" ? { added: body.members.length } : undefined,
  );
  try {
    const members = [
      { store: "google", app_id: "com.a.app", country: "th", language: "th" },
      { store: "apple", app_id: "123", country: "vn", language: "vi" },
    ];
    await c.workspace.addSelected(members);
    const form = c.doc.querySelector("#group-selected");
    form.elements.group_id.value = "1";
    form.dispatchEvent(new c.window.Event("submit", { cancelable: true }));
    await tick();
    assert.deepEqual(
      c.calls.find((r) => r.path === "/groups/1/members").body.members,
      members,
    );
    assert.equal(c.window.location.hash, "#groups?id=1");
  } finally {
    await c.window.happyDOM.close();
  }
});

test("permission categories and ranking history labels are rendered as text", async () => {
  const c = setup((path) => {
    if (path.includes("/rank-history?"))
      return { label: "<img src=x>", points: [] };
    if (path.includes("/members/11"))
      return {
        member,
        listing: null,
        permission: {
          payload: { "<img src=x>": ["<script>bad</script>"] },
          permission_count: 1,
        },
        permission_state: "ok",
        permission_history: [],
        keywords: [
          {
            target_id: 7,
            keyword: "<img src=x>",
            position: 2,
            observed_at: 1,
            fresh: true,
          },
        ],
        charts: [],
        snapshots: [],
        events: [],
        reference: null,
        metrics: [],
        reviews: [],
      };
  });
  try {
    await c.workspace.render(1);
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    c.doc.querySelector('[data-group-member="11"]').click();
    await tick();
    c.doc.querySelector('[data-member-tab="permissions"]').click();
    assert.equal(c.doc.querySelector("#group-dialog img"), null);
    c.doc.querySelector('[data-member-tab="rankings"]').click();
    c.doc.querySelector('[data-rank-target="7"]').click();
    await tick();
    assert.equal(c.doc.querySelector("#group-dialog img"), null);
    assert.match(
      c.doc.querySelector("#group-rank-history").textContent,
      /<img/,
    );
  } finally {
    await c.window.happyDOM.close();
  }
});

test("read-only groups hide mutations and late results cannot replace another page", async () => {
  const c = setup(() => undefined, false);
  try {
    await c.workspace.render(1);
    assert.equal(c.doc.querySelector("#group-create"), null);
    assert.equal(c.doc.querySelector("#group-collect-button"), null);
  } finally {
    await c.window.happyDOM.close();
  }
  let resolve;
  const delayed = setup((path) =>
    path.includes("/dashboard?")
      ? new Promise((r) => {
          resolve = r;
        })
      : undefined,
  );
  try {
    const pending = delayed.workspace.render(1);
    await tick();
    delayed.doc.querySelector("main").innerHTML = "<p>new page</p>";
    resolve(dashboard());
    await pending;
    assert.equal(delayed.doc.querySelector("main").textContent, "new page");
  } finally {
    await delayed.window.happyDOM.close();
  }
});

test("member reference reports paginate and schedule words in the exact member market", async () => {
  const c = setup((path, body) => {
    if (path.includes("/members/11/report?"))
      return {
        items: [
          {
            source_row: 3,
            keyword: "cash loan",
            position: 5,
            popularity: "10",
            search_index: "20",
          },
        ],
        total: 101,
        limit: 100,
        offset: Number(
          new URL("http://local" + path).searchParams.get("offset"),
        ),
      };
    if (path === "/groups/1/collect")
      return { queued: { keywords: 1 }, note: "已安排" };
    if (path.includes("/members/11"))
      return {
        member,
        listing: null,
        permission: null,
        permission_state: "pending",
        permission_history: [],
        keywords: [],
        charts: [],
        snapshots: [],
        events: [],
        reference: null,
        metrics: [],
        reviews: [],
        reports: [
          {
            id: 9,
            observed_at: 1,
            summary: [{ sheet: "ASA竞价词", rows: 101 }],
          },
        ],
      };
  });
  try {
    await c.workspace.render(1);
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    c.doc.querySelector('[data-group-member="11"]').click();
    await tick();
    c.doc.querySelector('[data-member-tab="rankings"]').click();
    c.doc.querySelector('[data-reference-report="9"]').click();
    await tick();
    assert.ok(
      c.calls.some(
        (r) =>
          r.path.includes("/report?") &&
          decodeURIComponent(r.path).includes("ASA竞价词"),
      ),
    );
    c.doc.querySelector("#group-report-next").click();
    await tick();
    assert.ok(
      c.calls.some(
        (r) => r.path.includes("/report?") && r.path.includes("offset=100"),
      ),
    );
    const check = c.doc.querySelector("[data-report-word]");
    check.checked = true;
    check.dispatchEvent(new c.window.Event("change"));
    c.doc.querySelector("#group-report-track").click();
    await tick();
    assert.deepEqual(c.calls.find((r) => r.path === "/groups/1/collect").body, {
      member_id: 11,
      kinds: ["keywords"],
      keywords: ["cash loan"],
    });
  } finally {
    await c.window.happyDOM.close();
  }
});

test("group monitoring controls show scope before submitting a shared-target change", async () => {
  const c = setup((path) =>
    path === "/groups/1/monitoring/control" ? { note: "已恢复" } : undefined,
  );
  try {
    await c.workspace.render(1);
    c.doc.querySelector("#group-control-button").click();
    assert.ok(!c.calls.some((v) => v.path.endsWith("/monitoring/control")));
    assert.match(c.doc.querySelector("#group-dialog").textContent, /其他分组/);
    const form = c.doc.querySelector("#group-control");
    form.elements.action.value = "resume";
    form.dispatchEvent(new c.window.Event("submit", { cancelable: true }));
    await tick();
    assert.deepEqual(
      c.calls.find((v) => v.path.endsWith("/monitoring/control")).body,
      { store: "all", country: "", action: "resume" },
    );
  } finally {
    await c.window.happyDOM.close();
  }
});
