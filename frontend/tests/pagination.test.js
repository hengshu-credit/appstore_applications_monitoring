import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { pagination, bindPagination } from "../src/pagination.js";
test("pager jumps directly, changes page size and rejects invalid destinations", async () => {
  const win = new Window(),
    doc = win.document,
    calls = [];
  try {
    doc.body.innerHTML = pagination({
      id: "rows",
      total: 194,
      limit: 20,
      offset: 0,
      sizes: [20, 50, 100],
    });
    bindPagination(doc, "rows", (o, s) => calls.push([o, s]));
    const el = doc.querySelector("[data-pager]");
    assert.ok(el.querySelector('[data-page="10"]'));
    el.querySelector("input").value = "8";
    el.querySelector("[data-page-jump]").click();
    await Promise.resolve();
    assert.deepEqual(calls.at(-1), [140, 20]);
    el.querySelector("input").value = "99";
    el.querySelector("[data-page-jump]").click();
    await Promise.resolve();
    assert.equal(calls.length, 1);
    el.querySelector("select").value = "50";
    el.querySelector("select").dispatchEvent(
      new win.Event("change", { bubbles: true }),
    );
    await Promise.resolve();
    assert.deepEqual(calls.at(-1), [0, 50]);
  } finally {
    await win.happyDOM.close();
  }
});
