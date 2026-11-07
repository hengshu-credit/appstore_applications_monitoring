import { createAppDetailTable } from "../src/app-detail-table.js";
import { pagination, bindPagination } from "../src/pagination.js";
import { filterBar, bindFilterBar } from "../src/filter-bar.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const source = (
  await readFile(new URL("../src/availability.js", import.meta.url), "utf8")
)
  .replace(/^import .*;$/gm, "")
  .replace(/^export /gm, "");
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

test("availability keeps classified watches in the header market and paginates records", async () => {
  const window = new Window({ url: "http://localhost" }),
    doc = window.document,
    calls = [];
  let market = { store: "all", country: "th", language: "en" };
  doc.body.innerHTML = "<main></main>";
  Object.assign(window, { pagination, bindPagination });
  window.filterBar = filterBar;
  window.bindFilterBar = bindFilterBar;
  window.eval(source + ";window.createPage=createAvailabilityPage;");
  const esc = (v) =>
    String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll('"', "&quot;");
  const detailTable = createAppDetailTable({
    esc,
    stamp: (v) => v || "-",
    num: (v) => v,
  });
  const page = window.createPage({
    appRows: async (items, options) =>
      detailTable(
        { items: items.map((r, i) => ({ ...r, id: i + 1, _source: r })) },
        { ...options, appCell: (r) => esc(r.name) },
      ),
    api: async (path) => {
      calls.push(path);
      if (path.startsWith("/availability/watched?")) {
        const offset = Number(
          new URL("http://local" + path).searchParams.get("offset"),
        );
        return {
          items: [
            {
              store: "google",
              app_id: "com.loan.app",
              name: "<img src=x>",
              country: "th",
              language: "th",
              group_name: "现金贷类APP",
              state: null,
              enabled: 1,
            },
          ],
          groups: ["现金贷类APP"],
          countries: ["th", "vn"],
          total: 2132,
          offset,
          limit: 100,
          has_more: true,
        };
      }
      return {
        scope: "test",
        counts: [],
        unobserved_watched: 0,
        history: [],
        attempts: [],
        pending: [],
        events: [],
        market: { store: "google", language: "en" },
      };
    },
    market: () => market,
    qs: (value) => new URLSearchParams(value).toString(),
    mount: (html) => {
      doc.querySelector("main").innerHTML = html;
      return true;
    },
    panel: (title, body) => `<section><h2>${title}</h2>${body}</section>`,
    metric: () => "",
    table: (headers, rows) =>
      `<table>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`,
    chart: () => "",
    bars: () => "",
    esc,
    stamp: (v) => v || "—",
    num: (v) => v,
    appLink: (store, id, name) => esc(name),
    toast: (e) => {
      throw new Error(e);
    },
    nextRevision: () => 2,
  });
  try {
    await page(1);
    await tick();
    const panel = doc.querySelector("#availability-watched");
    assert.match(panel.textContent, /2132/);
    assert.match(panel.textContent, /现金贷类APP/);
    assert.match(panel.textContent, /未验证/);
    assert.equal(panel.querySelector('img[src="x"]'), null);
    assert.equal(
      panel.querySelector(".country-flag").getAttribute("src"),
      "/flags/th.svg",
    );
    assert.equal(panel.querySelector("[name=country]"), null);
    assert.ok(calls.some((p) => p.includes("country=th")));
    panel.querySelector("#watched-next").click();
    await tick();
    assert.ok(calls.some((p) => p.includes("offset=100")));
    const form = doc.querySelector("#watched-filter");
    form.elements.group_name.value = "现金贷类APP";

    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick();
    const last = new URL("http://local" + calls.at(-1));
    assert.equal(last.searchParams.get("group_name"), "现金贷类APP");
    assert.equal(last.searchParams.get("store"), "all");
    assert.equal(last.searchParams.get("country"), "th");
    assert.equal(last.searchParams.get("offset"), "0");
    market = { store: "apple", country: "vn", language: "vi" };
    await page(2);
    await tick();
    const changed = new URL("http://local" + calls.at(-1));
    assert.equal(changed.searchParams.get("store"), "apple");
    assert.equal(changed.searchParams.get("country"), "vn");
    assert.equal(changed.searchParams.get("offset"), "0");
  } finally {
    await window.happyDOM.close();
  }
});
