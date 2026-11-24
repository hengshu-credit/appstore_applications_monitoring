import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { mountSnapshotComparison, versionNote } from "../src/snapshot-comparison.js";

const esc = (v) => String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const record = (observed_at, version, fields) => ({ observed_at, payload: { version, ...fields } });

test("published notes are taken from the selected version and attributed when recovered", () => {
  const older = record(10, "1", { store_fields: { recentChanges: "原版说明" } });
  const sameVersion = record(20, "2", { store_fields: { releaseNotes: "开发者提交的更新" } });
  const selected = record(21, "2", {});
  const data = { snapshots: [selected, sameVersion, older] };
  assert.deepEqual(versionNote(selected, data), { text: "开发者提交的更新", supplemental: true, at: 20 });
  assert.deepEqual(versionNote(older, data), { text: "原版说明" });
  assert.equal(versionNote(record(12, "3", { description: "商店介绍" }), data), null);
  assert.equal(versionNote(record(12, undefined, {}), data), null);
});

test("two independent snapshot selections classify changes, preserve falsy values and ignore key ordering", async () => {
  const win = new Window(), host = win.document.createElement("div");
  try {
    win.document.body.append(host);
    const data = { snapshots: [
      record(30, "2", { price: 0, has_ads: false, description: "<img src=x>", detail: { b: 2, a: 1 } }),
      record(25, "2", { price: 5 }),
      record(10, "1", { price: 10, summary: "removed", detail: { a: 1, b: 2 } }),
    ] };
    mountSnapshotComparison(host, data, { esc, stamp: String, snapshotMedia: () => "" });
    const base = host.querySelector('[name="base"]'), target = host.querySelector('[name="target"]');
    assert.equal(base.value, "2");
    assert.equal(target.value, "0");
    const rows = () => Array.from(host.querySelectorAll(".snapshot-diff-row"));
    const row = (key) => rows().find((r) => r.querySelector("header strong").textContent === key);
    assert.equal(row("价格").querySelector(".added-value").textContent, "0");
    assert.equal(row("含广告").querySelector(".added-value").textContent, "false");
    assert.match(row("简短描述").querySelector("header").textContent, /移除/);
    assert.equal(row("detail"), undefined);
    assert.equal(row("应用描述").querySelector("img"), null);
    base.value = "0";
    base.dispatchEvent(new win.Event("change"));
    assert.equal(rows().length, 0);
    assert.match(host.textContent, /已选择同一快照/);
    const toggle = host.querySelector("input");
    toggle.checked = false; toggle.dispatchEvent(new win.Event("change"));
    assert.match(row("detail").querySelector("header").textContent, /一致/);
    target.value = "1"; target.dispatchEvent(new win.Event("change"));
    assert.equal(base.value, "0");
    assert.equal(row("价格").querySelector(".added-value").textContent, "5");
    for (const n of [0, 1]) {
      mountSnapshotComparison(host, { snapshots: data.snapshots.slice(0, n) }, { esc, stamp: String, snapshotMedia: () => "" });
      assert.equal(host.querySelectorAll(".snapshot-preview").length, n);
      assert.equal(rows().length, 0);
    }
  } finally { await win.happyDOM.close(); }
});
