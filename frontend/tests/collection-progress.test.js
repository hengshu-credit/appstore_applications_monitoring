import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { taskProgress } from "../src/collection-progress.js";

const render = (data) => {
  const w = new Window();
  w.document.body.innerHTML = taskProgress(data, {
    num: (n) => n.toLocaleString("en-US"),
    esc: (s) =>
      s
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;"),
  });
  return w.document;
};
test("tiny running and retry populations remain explicit and selectable beside millions of pending tasks", () => {
  const doc = render({
    kind: "details",
    total: 1000000,
    collected: 25,
    summary: { pending: 999970, running: 1, error: 4, scheduled: 25 },
    selected: true,
    activeState: "error",
  });
  assert.equal(
    doc.querySelector('[data-category-state="pending"] b').textContent,
    "999,970",
  );
  assert.equal(
    doc.querySelector('[data-category-state="running"] b').textContent,
    "1",
  );
  assert.equal(
    doc
      .querySelector('[data-category-state="error"]')
      .getAttribute("aria-pressed"),
    "true",
  );
  assert.equal(doc.querySelectorAll(".state-track").length, 5);
  assert.equal(
    doc.querySelector('[role="meter"]').getAttribute("aria-valuenow"),
    "25",
  );
  assert.equal(
    doc.querySelector(".category-coverage strong").textContent,
    "<0.1%",
  );
});
test("empty scope has no misleading completion percentage and one-off tasks expose done", () => {
  const empty = render({ kind: "aso_import", total: 0, collected: 0 });
  assert.equal(
    empty.querySelector(".category-coverage strong").textContent,
    "—",
  );
  assert.equal(empty.querySelector('[data-category-state="scheduled"]'), null);
  assert.ok(empty.querySelector('[data-category-state="done"]'));
  const done = render({
    kind: "aso_import",
    total: 3,
    collected: 2,
    summary: { done: 2, pending: 1 },
  });
  assert.equal(
    done.querySelector('[data-category-state="done"] b').textContent,
    "2",
  );
  assert.equal(
    done.querySelector(".category-coverage small").textContent,
    "2 / 3",
  );
});
