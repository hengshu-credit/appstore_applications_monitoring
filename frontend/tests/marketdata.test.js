import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { marketDataPanel, bindMarketData } from "../src/marketdata.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
function setup(respond = () => undefined) {
  const window = new Window({ url: "http://localhost" });
  const doc = window.document;
  const esc = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll('"', "&quot;");
  doc.body.innerHTML = `<main>${marketDataPanel((name, body) => `<section><h2>${name}</h2>${body}</section>`, esc)}</main>`;
  const calls = [],
    errors = [];
  const api = async (path, value, method) => {
    calls.push({ path, value: value ? structuredClone(value) : value, method });
    return (
      (await respond(path, value)) ?? {
        runs: [],
        artifacts: [],
        errors: [],
        budget: null,
      }
    );
  };
  const bind = bindMarketData({
    $: (s) => doc.querySelector(s),
    api,
    esc,
    table: (h, rows) =>
      `<table>${rows.map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`,
    toast: (e) => errors.push(e),
    root: doc.querySelector("main"),
  });
  const submit = (id) =>
    doc
      .querySelector(id)
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
  return { window, doc, calls, errors, bind, submit };
}

test("市场数据 accepts token-only configuration and immediately clears secrets", async () => {
  const ctx = setup((path) =>
    path === "/data-sources/marketdata"
      ? { marketdata: { message: "授权有效", configured: true } }
      : undefined,
  );
  try {
    await ctx.bind;
    const form = ctx.doc.querySelector("#figures-account");
    form.elements.access_token.value = "fixture-private-token";
    ctx.submit("#figures-account");
    assert.equal(form.elements.access_token.value, "");
    await tick();
    assert.equal(
      ctx.calls.find((c) => c.path === "/data-sources/marketdata").value
        .access_token,
      "fixture-private-token",
    );
    assert.equal(
      ctx.doc.querySelector("#figures-state").textContent,
      "授权有效",
    );
    assert.ok(
      ctx.calls.every((c) => !c.path.includes("fixture-private-token")),
    );
  } finally {
    await ctx.window.happyDOM.close();
  }
});

test("市场数据 sends explicit scope and UTC deadline", async () => {
  const ctx = setup();
  try {
    await ctx.bind;
    const form = ctx.doc.querySelector("#figures-plan");
    form.elements.countries.value = "US, th";
    form.elements.products.value = "123,456";
    form.elements.stop_at.value = "2030-01-02T03:04";
    form.elements.ranks.checked = false;
    ctx.submit("#figures-plan");
    await tick();
    const request = ctx.calls.find((c) => c.value?.stop_at);
    assert.deepEqual(request.value.countries, ["us", "th"]);
    assert.deepEqual(request.value.products, ["123", "456"]);
    assert.match(request.value.stop_at, /Z$/);
    assert.ok(!request.value.kinds.includes("ranks"));
    assert.equal(request.value.daily_limit, 1000);
  } finally {
    await ctx.window.happyDOM.close();
  }
});

test("a stale 市场数据 response cannot modify a replacement page", async () => {
  let resolve;
  const ctx = setup(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  try {
    ctx.doc.querySelector("main").innerHTML = "<p>replacement page</p>";
    resolve({ runs: [], artifacts: [], errors: [], budget: null });
    await ctx.bind;
    assert.equal(ctx.doc.querySelector("main").textContent, "replacement page");
    assert.deepEqual(ctx.errors, []);
  } finally {
    await ctx.window.happyDOM.close();
  }
});
