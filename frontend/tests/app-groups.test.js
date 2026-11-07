import { pagination, bindPagination } from "../src/pagination.js";
import { filterBar, bindFilterBar } from "../src/filter-bar.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const source = (
  await readFile(new URL("../src/app-groups.js", import.meta.url), "utf8")
)
  .replace(/^import .*;$/gm, "")
  .replace(/^export /gm, "");
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
function setup(handler = () => undefined, write = true, overrides = {}) {
  const window = new Window({ url: "http://localhost/#groups?id=1" }),
    doc = window.document,
    calls = [],
    errors = [];
  doc.body.innerHTML = "<main></main>";
  Object.assign(window, { pagination, bindPagination });
  window.filterBar = filterBar;
  window.bindFilterBar = bindFilterBar;
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
      `<article>${title}<strong>${v ?? "-"}</strong><small>${n}</small></article>`,
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
    ...overrides,
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
    assert.equal(c.doc.querySelector('[data-col="source"]'), null);
    assert.equal(c.doc.querySelector('[data-col="observed"]'), null);
    assert.equal(c.doc.querySelector('[data-col="reference"]'), null);
    assert.equal(c.doc.querySelector('img[src="x"]'), null);
    assert.deepEqual(
      [...c.doc.querySelectorAll("[data-group-tab]")].map((e) => e.textContent),
      ["分组总览", "应用明细"],
    );
    assert.equal(c.doc.querySelectorAll("[data-member-row]").length, 1);
    const before = c.calls.length;
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    assert.equal(
      c.calls.length,
      before,
      "all metrics reuse the same scoped response",
    );
    assert.match(
      c.doc.querySelector("#group-tab-content").textContent,
      /待采集/,
    );
    c.doc.querySelector('[data-group-member="11"]').click();
    await tick();
    assert.match(
      c.doc.querySelector("#group-dialog").textContent,
      /无商店记录/,
    );
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
        .find((e) => e.textContent.startsWith("暂无依据"))
        .querySelector("strong").textContent,
      "3",
    );
    assert.equal(
      cards
        .find((e) => e.textContent.startsWith("商店未检出"))
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
    assert.equal(
      card.querySelector(".country-flag").getAttribute("src"),
      "/flags/th.svg",
    );
    assert.match(card.querySelector(".country-name").textContent, /泰国/);
    const children = [...card.children];
    assert.ok(
      children.indexOf(card.querySelector(".country-total")) <
        children.indexOf(card.querySelector(".country-status-bar")),
    );
    assert.ok(
      children.indexOf(card.querySelector(".country-status-bar")) <
        children.indexOf(card.querySelector(".country-status-counts")),
    );
    assert.equal(card.querySelector(".country-total b").textContent, "8");
    assert.equal(card.querySelector(".country-targets"), null);
    assert.deepEqual(
      [...card.querySelectorAll(".country-status b")].map((e) => e.textContent),
      ["4", "3", "2", "1"],
    );
    card.dispatchEvent(new c.window.Event("pointerenter"));
    const hint = c.doc.querySelector("#group-country-hint");
    assert.equal(hint.hidden, false);
    assert.match(hint.textContent, /10 个观察目标/);
    assert.deepEqual(
      [...hint.querySelectorAll(".country-hint-row b")].map(
        (e) => e.textContent,
      ),
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
    assert.match(card.getAttribute("aria-label"), /在架 4/);
    assert.ok(!card.textContent.includes("99"));
    assert.deepEqual(
      [...hint.querySelectorAll(".country-hint-row small")].map(
        (e) => e.textContent,
      ),
      ["40.0%", "30.0%", "20.0%", "10.0%"],
    );
    card.dispatchEvent(new c.window.Event("pointerleave"));
    assert.equal(hint.hidden, true);
    card.focus();
    assert.equal(hint.hidden, false);
    assert.equal(card.getAttribute("aria-describedby"), hint.id);
    card.dispatchEvent(
      new c.window.KeyboardEvent("keydown", { key: "Escape" }),
    );
    assert.equal(hint.hidden, true);
    assert.equal(card.hasAttribute("aria-describedby"), false);
    card.blur();
    card.focus();
    assert.equal(hint.hidden, false);
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    assert.equal(hint.isConnected, false);
  } finally {
    await c.window.happyDOM.close();
  }
});

test("large country counts stay compact while hints retain exact values", async () => {
  const c = setup((path) => {
    if (!path.includes("/dashboard?")) return;
    const data = dashboard();
    data.countries = [
      {
        country: "th",
        apps: 123456789,
        targets: 123456789,
        states: { available: 123456789 },
        reference: {},
      },
    ];
    return data;
  });
  try {
    await c.workspace.render(1);
    const card = c.doc.querySelector(".group-country");
    assert.ok(card.querySelector(".country-total b").textContent.length < 8);
    assert.equal(card.querySelector(".country-total b").title, "123,456,789");
    assert.ok(card.querySelector(".country-status b").textContent.length < 8);
    card.dispatchEvent(new c.window.Event("pointerenter"));
    assert.match(
      c.doc.querySelector("#group-country-hint").textContent,
      /123,456,789/,
    );
    assert.match(
      c.doc.querySelector("#group-country-hint").textContent,
      /100.0%/,
    );
  } finally {
    await c.window.happyDOM.close();
  }
});

test("unified rows show permissions rankings money and ratings without mixing missing values or currencies", async () => {
  const c = setup((path) => {
    if (!path.includes("/dashboard?")) return;
    const data = dashboard();
    data.items = [
      {
        ...member,
        permission_state: "ok",
        permission_count: 0,
        permission_groups: ["Camera", "SMS", "<img src=x>"],
        permission_error: "<script>problem</script>",
        developer: "Developer <test>",
        developer_id: "6942553452222258407",
        top_keywords: [
          { keyword: "loan <test>", position: 3 },
          { keyword: "finance", position: 8 },
        ],
        yesterday_reviews: 0,
        price: 0,
        currency: "USD",
        languages: ["en", "th"],
        version: "2.0",
        has_iap: false,
        keyword_top3_count: 1,
        chart_positions: [
          { collection: "top-free", category: "", sampled: true, position: 12 },
          {
            collection: "top-paid",
            category: "",
            sampled: true,
            position: null,
          },
        ],
        keyword_count: 5,
        keyword_best: 3,
        chart_count: 0,
        chart_best: null,
        rating: 0,
        ratings: 123,
        installs: 1000,
        metrics: [
          { metric: "downloads", value: "0", days: 3, is_estimate: false },
          {
            metric: "revenue",
            value: "123.4500",
            days: 3,
            is_estimate: false,
            currency: "USD",
          },
          {
            metric: "revenue",
            value: "9",
            days: 1,
            is_estimate: true,
            currency: "EUR",
          },
        ],
      },
    ];
    return data;
  });
  try {
    await c.workspace.render(1);
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    const row = c.doc.querySelector('[data-member-row="11"]');
    const cell = (key) => row.querySelector(`[data-col="${key}"]`);
    const grid = c.doc.querySelector(".group-member-table");
    assert.equal(
      row.querySelectorAll("td").length,
      grid.querySelectorAll('th[scope="col"]').length,
    );
    assert.equal(c.doc.querySelector("[data-column-view]"), null);
    assert.equal(
      cell("app").querySelector(".member-developer").textContent,
      "Developer <test>",
    );
    assert.match(cell("country").textContent, /泰国.*en/);
    assert.deepEqual(
      [...grid.querySelectorAll('th[scope="colgroup"]')].map(
        (el) => el.textContent,
      ),
      [
        "应用信息",
        "状态监控",
        "商店资料",
        "通用榜单",
        "分类榜单",
        "权限采集",
        "下载量",
        "收入",
        "评分",
        "关键词",
      ],
    );
    assert.equal(cell("permission_state").textContent, "已采集");
    assert.equal(cell("permission_count").textContent, "0");
    assert.equal(
      row.querySelector('[data-permission="Camera"]').textContent,
      "✓",
    );
    assert.equal(
      row.querySelector('[data-permission="Location"]').textContent,
      "×",
    );
    assert.equal(cell("keywords").textContent, "5");
    assert.equal(cell("top3_count").textContent, "1");
    assert.match(
      cell("keywords").querySelector("span").title,
      /loan <test> #3.*finance #8/,
    );
    assert.equal(
      cell("top3_count").querySelector("span").title,
      "loan <test> #3",
    );
    assert.equal(cell("developer_id").textContent, "6942553452222258407");
    assert.equal(cell("price").textContent, "免费");
    assert.equal(cell("iap").textContent, "无");
    assert.equal(cell("yesterday_reviews").textContent, "0");
    assert.equal(cell("languages").textContent, "2");
    assert.equal(cell("chart0").textContent, "12");
    assert.equal(cell("chart1").textContent, "");
    assert.equal(cell("chart2").textContent, "-");
    assert.equal(cell("rating").textContent, "0.00");
    assert.equal(cell("ratings").textContent, "123");
    assert.equal(cell("installs").textContent, "1,000+");
    const valueFor = (label) => {
      const head = [...grid.querySelectorAll("th")].find(
        (th) => th.textContent === label,
      );
      return cell(head.dataset.col);
    };
    assert.equal(valueFor("下载量 · 实际").textContent, "0");
    assert.match(valueFor("下载量 · 实际").querySelector("span").title, /3 天/);
    assert.equal(valueFor("收入 · 实际 USD").textContent, "123.45");
    assert.match(
      valueFor("收入 · 实际 USD").querySelector("span").title,
      /3 天/,
    );
    assert.equal(valueFor("收入 · 估算 EUR").textContent, "9");
    assert.match(
      valueFor("收入 · 估算 EUR").querySelector("span").title,
      /1 天/,
    );
    assert.equal(row.querySelector("br,details"), null);
    assert.equal(row.querySelector('img[src="x"]'), null);
    assert.equal(row.querySelector("script"), null);
    c.doc.querySelector("#group-select-page").click();
    assert.equal(row.querySelector("[data-group-check]").checked, true);
    assert.equal(c.doc.querySelector("#group-remove").disabled, false);
    assert.match(
      c.doc.querySelector("#group-selection-count").textContent,
      /已选 1 条/,
    );
    c.doc.querySelector("#group-import-metrics").click();
    assert.ok(c.doc.querySelector("#group-metrics"));
    assert.deepEqual(c.errors, []);
  } finally {
    await c.window.happyDOM.close();
  }
});

test("chart columns use general and own-category ranks without unrelated categories", async () => {
  let reverse = false;
  const ranks = [
    { collection: "top-free", category: "SOCIAL", sampled: true, position: 3 },
    { collection: "top-free", category: "GAME", sampled: true, position: 9 },
    {
      collection: "top-grossing",
      category: "SOCIAL",
      sampled: false,
      position: null,
    },
    { collection: "new-free", category: "", sampled: true, position: 20 },
    {
      collection: "top-grossing",
      category: "GAME",
      sampled: true,
      position: 8,
    },
    {
      collection: "top-paid",
      category: "SOCIAL",
      sampled: true,
      position: null,
    },
    { collection: "top-paid", category: "GAME", sampled: true, position: 7 },
  ];
  const c = setup(
    (path) => {
      if (!path.includes("/dashboard?")) return;
      const data = dashboard();
      data.items = [
        {
          ...member,
          category: "SOCIAL",
          chart_positions: reverse ? [...ranks].reverse() : ranks,
        },
        {
          ...member,
          id: 12,
          store: "apple",
          category: "6005",
          chart_positions: [
            {
              collection: "top-free",
              category: "6005",
              sampled: true,
              position: 11,
            },
          ],
        },
        { ...member, id: 13, category: null },
      ];
      if (reverse) data.items.reverse();
      return data;
    },
    true,
    {
      categoryName: (_store, id) =>
        ({ SOCIAL: "社交", GAME: "游戏", 6005: "社交" })[id] || id,
      chartName: (_store, id) =>
        ({
          "top-free": "免费榜",
          "top-paid": "付费榜",
          "top-grossing": "畅销榜",
          "new-free": "最新免费榜",
        })[id] || id,
    },
  );
  try {
    const inspect = async () => {
      await c.workspace.render(1);
      c.doc.querySelector('[data-group-tab="matrix"]').click();
      await tick();
      const grid = c.doc.querySelector(".group-member-table");
      assert.deepEqual(
        [...grid.querySelectorAll('th[data-col^="chart"]')].map(
          (th) => th.textContent,
        ),
        [
          "免费榜",
          "付费榜",
          "畅销榜",
          "最新免费榜",
          "免费榜",
          "付费榜",
          "畅销榜",
        ],
      );
      const values = (id) =>
        [
          ...grid.querySelectorAll(
            `[data-member-row="${id}"] td[data-col^="chart"]`,
          ),
        ].map((td) => td.textContent);
      assert.deepEqual(values(11), ["-", "-", "-", "20", "3", "", "-"]);
      assert.equal(values(12)[4], "11");
      assert.equal(values(12)[5], "-");
      assert.ok(!grid.textContent.includes("游戏 ·"));
      assert.equal(
        grid.querySelector('[data-member-row="11"] [data-col="chart4"] b')
          .title,
        "社交",
      );
      assert.equal(
        grid.querySelector('th[data-col="category"]').textContent,
        "类别",
      );
      assert.equal(
        grid.querySelector('[data-member-row="11"] [data-col="category"]')
          .textContent,
        "社交",
      );
      assert.equal(
        grid.querySelector('[data-member-row="13"] [data-col="category"]')
          .textContent,
        "-",
      );
    };
    await inspect();
    reverse = true;
    await inspect();
    assert.deepEqual(c.errors, []);
  } finally {
    await c.window.happyDOM.close();
  }
});

test("inline corrections keep the market row and permission unknowns distinct and can return to automatic", async () => {
  let override = null,
    permissions = {},
    revision = 0;
  const c = setup((path, body) => {
    if (path.endsWith("/adjustment")) {
      override = body.status_override;
      permissions = body.permission_overrides;
      revision++;
      return { revision };
    }
    if (path.includes("/dashboard?")) {
      const data = dashboard();
      data.items = [
        {
          ...member,
          classification: override || "region_unavailable",
          classification_source: override ? "adjustment" : "reference",
          status_override: override,
          permission_overrides: permissions,
          adjustment_revision: revision,
        },
      ];
      return data;
    }
  });
  try {
    await c.workspace.render(1);
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    const row = () => c.doc.querySelector('[data-member-row="11"]');
    assert.equal(row().querySelector('[data-col="source"]'), null);
    assert.equal(
      row().querySelector('[data-permission="Camera"]').textContent,
      "-",
    );
    assert.equal(c.doc.querySelector("[data-column-view]"), null);
    assert.equal(row().querySelectorAll("td[hidden]").length, 0);
    row().querySelector("[data-adjust-row]").click();
    row().querySelector("[data-edit-status]").value = "available";
    row().querySelector('[data-edit-permission="Camera"]').value = "true";
    row().querySelector('[data-edit-permission="SMS"]').value = "false";
    row().querySelector("[data-save-row]").click();
    await tick();
    assert.deepEqual(
      c.calls.find((r) => r.path.endsWith("/adjustment")),
      {
        path: "/groups/1/members/11/adjustment",
        method: "PUT",
        body: {
          revision: 0,
          status_override: "available",
          permission_overrides: { Camera: true, SMS: false },
        },
      },
    );
    assert.equal(
      row().querySelector('[data-permission="Camera"]').textContent,
      "✓",
    );
    assert.equal(
      row().querySelector('[data-permission="SMS"]').textContent,
      "×",
    );
    assert.equal(
      row().querySelector('[data-permission="Location"]').textContent,
      "-",
    );
    row().querySelector("[data-adjust-row]").click();
    row().querySelector("[data-edit-status]").value = "";
    row().querySelector('[data-edit-permission="Camera"]').value = "";
    row().querySelector('[data-edit-permission="SMS"]').value = "";
    row().querySelector("[data-save-row]").click();
    await tick();
    assert.equal(row().querySelector('[data-col="source"]'), null);
    assert.equal(
      row().querySelector('[data-permission="Camera"]').textContent,
      "-",
    );
    assert.deepEqual(c.errors, ["本行调整已保存", "本行调整已保存"]);
  } finally {
    await c.window.happyDOM.close();
  }
});

test("crosshair follows the leaf header and row, supports pinning, and leaves controls independent", async () => {
  const c = setup((path) => {
    if (!path.includes("/dashboard?")) return;
    const data = dashboard();
    data.items.push({ ...member, id: 12, country: "vn" });
    return data;
  });
  try {
    await c.workspace.render(1);
    c.doc.querySelector('[data-group-tab="matrix"]').click();
    await tick();
    const grid = c.doc.querySelector(".group-member-table");
    const cell = grid.querySelector('td[data-col="rating"]');
    const [band, leaf] = cell
      .getAttribute("headers")
      .split(" ")
      .map((id) => c.doc.getElementById(id));
    cell.dispatchEvent(new c.window.Event("pointerover", { bubbles: true }));
    assert.ok(cell.classList.contains("crosshair-cell"));
    assert.ok(cell.parentElement.classList.contains("crosshair-row"));
    assert.ok(
      band.classList.contains("crosshair-band") &&
        leaf.classList.contains("crosshair-col"),
    );
    assert.equal(grid.querySelectorAll("td.crosshair-col").length, 2);
    cell.click();
    grid.dispatchEvent(new c.window.Event("pointerleave"));
    assert.ok(
      cell.classList.contains("crosshair-pin") &&
        cell.classList.contains("crosshair-cell"),
    );
    grid.parentElement.dispatchEvent(
      new c.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    assert.equal(grid.querySelector(".crosshair-cell,.crosshair-pin"), null);
    const checkbox = grid.querySelector("[data-group-check]");
    checkbox.click();
    assert.ok(checkbox.checked);
    assert.equal(grid.querySelector(".crosshair-pin"), null);
    const button = grid.querySelector("[data-group-member]");
    button.focus();
    assert.ok(button.closest("td").classList.contains("crosshair-cell"));
    button.blur();
    assert.equal(grid.querySelector(".crosshair-col"), null);
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
