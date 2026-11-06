import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const source = (
  await readFile(
    new URL("../src/collection-lineage.js", import.meta.url),
    "utf8",
  )
)
  .replace(/^import .*;$/gm, "")
  .replace(/^export /gm, "");
const graph = {
  nodes: [
    {
      id: "queue",
      label: "关键词任务",
      stage: 1,
      kind: "keywords",
      value: 18,
      unit: "目标",
      states: { paused: 3 },
      eligible: 15,
    },
    {
      id: "dispatch",
      label: "批次",
      stage: 2,
      kind: "keywords",
      value: 15,
      unit: "待领取",
      batch_limit: 20,
    },
    {
      id: "source",
      label: "Google <img src=x>",
      stage: 3,
      value: 100,
      unit: "记录",
      requests: 1,
      http_errors: 0,
      endpoint: "play.google.com",
      records: [],
    },
    {
      id: "sink",
      label: "排名表",
      stage: 4,
      value: 100,
      unit: "排名",
      tables: "keyword_ranks",
    },
  ],
  edges: [
    {
      id: "a",
      source: "queue",
      target: "dispatch",
      kind: "keywords",
      label: "",
    },
    {
      id: "b",
      source: "dispatch",
      target: "source",
      kind: "keywords",
      label: "18目标",
    },
    {
      id: "c",
      source: "source",
      target: "sink",
      kind: "keywords",
      label: "100排名",
    },
  ],
  workers: [],
  notes: ["真实统计"],
  paused: false,
  at: 1,
};

test("lineage nodes trace upstream/downstream, escape labels, search and zoom", async () => {
  const win = new Window();
  win.document.body.innerHTML = "<main></main>";
  win.eval(source + ";window.factory=createCollectionLineage");
  const escape = (v) =>
    String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll('"', "&quot;");
  const view = win.factory({
    api: async () => graph,
    esc: escape,
    num: String,
    stamp: String,
    market: () => ({ store: "all" }),
    currentRevision: () => 1,
    toast: () => {},
  });
  const host = win.document.querySelector("main");
  try {
    await view.render(host, 1);
    assert.equal(host.querySelectorAll("[data-lineage-node]").length, 4);
    assert.equal(host.querySelector("img"), null);
    assert.equal(host.querySelector('[data-edge-label="a"]'),null);
    assert.equal(host.querySelectorAll("[data-edge-label]").length,2);
    assert.equal(host.querySelector(".lineage-toolbar [data-zoom]"), null);
    assert.ok(host.querySelector('.lineage-canvas-tools [data-zoom="fit"]'));
    assert.equal(
      host.querySelector("marker").getAttribute("markerUnits"),
      "userSpaceOnUse",
    );
    assert.match(
      host.querySelector('[data-lineage-edge="a"]').getAttribute("d"),
      /M 584 91/,
    );
    host.querySelector("[data-lineage-flow]").click();
    assert.equal(
      host.querySelector(".lineage-world").classList.contains("is-flowing"),
      false,
    );
    host.querySelector('[data-lineage-node="source"]').click();
    assert.match(
      host.querySelector(".lineage-inspector").textContent,
      /play.google.com/,
    );
    assert.equal(host.querySelectorAll("path.is-active").length, 3);
    const direction = host.querySelector("[data-lineage-direction]");
    direction.value = "up";
    direction.dispatchEvent(new win.Event("change"));
    assert.ok(
      host
        .querySelector('[data-lineage-node="sink"]')
        .classList.contains("is-dim"),
    );
    host.querySelector('[data-related="dispatch"]').click();
    assert.equal(
      host
        .querySelector('[data-lineage-node="dispatch"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    const search = host.querySelector("[data-lineage-search]");
    search.value = "排名";
    search.dispatchEvent(new win.Event("input"));
    assert.ok(
      host
        .querySelector('[data-lineage-node="sink"]')
        .classList.contains("is-match"),
    );
    host.querySelector('[data-zoom="in"]').click();
    assert.equal(host.querySelector("[data-zoom-label]").textContent, "110%");
    host.querySelector("[data-lineage-clear]").click();
    assert.equal(host.querySelectorAll('[aria-pressed="true"]').length, 0);
  } finally {
    await win.happyDOM.close();
  }
});
