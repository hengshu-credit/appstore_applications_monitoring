import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

test("source configuration prefills saved values and persists batch changes without exposing credentials", async () => {
  const window = new Window();
  window.document.body.innerHTML = "<main></main>";
  const source = (
    await readFile(new URL("../src/data-sources.js", import.meta.url), "utf8")
  )
    .replace(/^import .*;$/gm, "")
    .replace(/^export /gm, "");
  const factory = new Function(
    "document",
    "filterBar",
    "bindFilterBar",
    "marketDataPanel",
    "bindMarketData",
    source + ";return createDataSourcePage;",
  )(
    window.document,
    () => '<form id="source-app-query"></form>',
    () => {},
    () => "",
    () => {},
  );
  const row = {
    id: "apple_lookup",
    name: "Apple 应用详情",
    origin: "https://itunes.apple.com/lookup",
    access: "公开",
    fields: "详情",
    editable: true,
    batch_max: 200,
    batch_size: 200,
    account: { configured: false },
  };
  const calls = [],
    esc = (value) =>
      String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll('"', "&quot;");
  const api = async (path, value, method) => {
    if (path.endsWith("/control")) {
      calls.push({ path, value, method: "POST" });
      return { saved: true };
    }
    if (method === "PUT") {
      calls.push({ path, value, method });
      return { ...row, ...value };
    }
    if (path === "/data-sources")
      return {
        community: {
          configured: true,
          email: "saved@example.org",
          message: "已保存",
        },
        directory: { configured: false },
        connections: [row],
        catalog: [],
      };
    if (path === "/data-sources/baseline-imports")
      return {
        files: [],
        imports: [
          {
            digest: "a".repeat(64),
            dataset: "catalog",
            status: "paused_user",
            processed: 2,
            valid: 2,
            rejected: 0,
            updated_at: 1,
          },
        ],
        coverage: "历史字段",
        license: "CC BY 4.0",
      };
    if (path === "/data-sources/initial-capacity")
      return {
        inventory: [],
        detail_requests_lower_bound: 0,
        detail_hours_lower_bound: 0,
        scope_note: "下限",
        blockers: [],
      };
    throw new Error(path);
  };
  const render = factory({
    $: (s) => window.document.querySelector(s),
    api,
    esc,
    panel: (title, html) => `<section><h2>${title}</h2>${html}</section>`,
    table: (headers, rows) =>
      `<table>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</table>`,
    mount: (html) => {
      window.document.querySelector("main").innerHTML = html;
      return true;
    },
    toast: () => {},
  });
  try {
    await render(1);
    await window.document
      .querySelector("#bulk-source-imports")
      .onclick({
        target: window.document.querySelector("[data-bulk-control]"),
      });
    assert.equal(calls[0].value.action, "resume");
    assert.equal(
      calls[0].path,
      `/data-sources/baseline-imports/${"a".repeat(64)}/control`,
    );
    assert.equal(
      window.document.querySelector('#provider-account [name="email"]').value,
      "saved@example.org",
    );
    assert.equal(
      window.document.querySelector('#provider-account [name="password"]')
        .value,
      "",
    );
    window.document.querySelector('[data-source-edit="apple_lookup"]').click();
    const form = window.document.querySelector("#source-binding-form");
    assert.equal(form.elements.batch_size.value, "200");
    form.elements.batch_size.value = "100";
    await form.onsubmit({ preventDefault() {}, currentTarget: form });
    assert.deepEqual(
      calls.filter((call) => call.method === "PUT"),
      [
        {
          path: "/data-sources/config/apple_lookup",
          value: { batch_size: 100 },
          method: "PUT",
        },
      ],
    );
    assert.match(
      window.document.querySelector('[data-source-edit="apple_lookup"]')
        .textContent,
      /100/,
    );
  } finally {
    await window.happyDOM.close();
  }
});
