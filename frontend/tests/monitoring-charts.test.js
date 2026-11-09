import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  monitoringChart,
  monitoringBars,
  bindMonitoringCharts,
} from "../src/monitoring-charts.js";
const at = Date.UTC(2026, 10, 7, 0, 0) / 1000;
const event = (w, node, type, extra = {}) =>
  node.dispatchEvent(new w.MouseEvent(type, { bubbles: true, ...extra }));
const key = (w, node, value) =>
  node.dispatchEvent(
    new w.KeyboardEvent("keydown", {
      key: value,
      bubbles: true,
      cancelable: true,
    }),
  );

test("chart geometry is initialized for asynchronously mounted axes and singleton points", async () => {
  const w = new Window(),
    d = w.document;
  const dispose = bindMonitoringCharts(d);
  try {
    d.body.insertAdjacentHTML("beforeend", monitoringChart([{ at, value: 0 }]));
    await new Promise((resolve) => setTimeout(resolve, 20));
    const point = d.querySelector(".plot-point");
    assert.equal(point.style.left, "0%");
    assert.equal(point.style.width, "100%");
    assert.equal(point.style.getPropertyValue("--point-x"), "50%");
    assert.equal(point.style.getPropertyValue("--point-y"), "100%");
    assert.equal(d.querySelector(".plot-x-tick").style.left, "50%");
    const ticks = [...d.querySelectorAll(".plot-y-axis span")];
    assert.equal(new Set(ticks.map((e) => e.style.top)).size, ticks.length);
    assert.ok(ticks.every((e) => e.style.top.endsWith("%")));
    d.querySelector("figure").remove();
    d.body.insertAdjacentHTML(
      "beforeend",
      monitoringChart([
        { at, value: 2 },
        { at: at + 86400 * 3, value: 4 },
      ]),
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    const x = [...d.querySelectorAll(".plot-x-tick")].map((e) =>
      parseFloat(e.style.left),
    );
    assert.equal(x[0], 0);
    assert.equal(x.at(-1), 100);
    assert.ok(x.every((value, i) => i === 0 || value > x[i - 1]));
  } finally {
    dispose();
    w.happyDOM.abort();
  }
});

test("trends preserve timestamp spacing, null gaps, exact values and safe source text", () => {
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML = monitoringChart(
      [
        { at: at, value: 3 },
        { at: at + 60, value: 2, note: "source <script>" },
        { at: at + 120, value: null },
        { at: at + 3600, value: 2 },
        { at: at + 7200, value: 1 },
      ],
      true,
    );
    assert.equal(d.querySelectorAll("polyline").length, 2);
    assert.equal(d.querySelectorAll(".plot-point").length, 5);
    assert.equal(d.querySelectorAll(".plot-dot").length, 4);
    assert.equal(d.querySelectorAll(".plot-gap").length, 1);
    assert.equal(d.querySelector("script"), null);
    assert.match(
      d.querySelector(".plot-point").getAttribute("aria-label"),
      /排名 #3/,
    );
    const points = [...d.querySelectorAll(".plot-point")];
    const dispose = bindMonitoringCharts(d);
    assert.ok(
      parseFloat(points[1].style.width) < parseFloat(points[3].style.width),
    );
    assert.equal(points[4].style.getPropertyValue("--point-y"), "0%");
    event(w, points[1], "pointerover");
    assert.equal(d.querySelector(".chart-tooltip").hidden, false);
    assert.match(
      d.querySelector(".chart-tooltip").textContent,
      /source <script>/,
    );
    assert.equal(d.querySelector(".chart-tooltip script"), null);
    event(w, points[2], "pointerover");
    assert.match(d.querySelector(".chart-tooltip").textContent, /未观测/);
    dispose();
  } finally {
    w.happyDOM.abort();
  }
});

test("time axes use date or minute labels, deduplicate single moments and keep a 0-5 rating scale", () => {
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML = monitoringChart(
      [{ at, value: 3.94218 }],
      false,
      undefined,
      { label: "评分", domain: [0, 5], precision: 2 },
    );
    assert.equal(d.querySelectorAll(".plot-x-tick").length, 1);
    assert.doesNotMatch(
      d.querySelector(".plot-x-tick").textContent,
      /\d{2}:\d{2}:\d{2}/,
    );
    assert.match(d.querySelector("figcaption").textContent, /单次观察/);
    assert.deepEqual(
      [...d.querySelectorAll(".plot-y-axis span")].map((e) => e.textContent),
      ["0", "1", "2", "3", "4", "5"],
    );
    const dispose = bindMonitoringCharts(d);
    event(w, d.querySelector(".plot-point"), "pointerover");
    assert.equal(d.querySelector(".chart-tip-value").textContent, "3.94");
    assert.match(
      d.querySelector(".chart-tooltip").textContent,
      /原始值 3.94218/,
    );
    dispose();
    d.body.innerHTML = monitoringChart([
      { at, value: 2 },
      { at: at + 21 * 86400, value: 4 },
    ]);
    const ticks = [...d.querySelectorAll(".plot-x-tick")].map(
      (e) => e.textContent,
    );
    assert.equal(new Set(ticks).size, ticks.length);
    assert.ok(ticks.every((t) => !t.includes(":") && !t.includes(String(at))));
    d.body.innerHTML = monitoringChart([
      { at, value: 2 },
      { at: at + 1, value: 4 },
    ]);
    assert.equal(d.querySelectorAll(".plot-x-tick").length, 1);
  } finally {
    w.happyDOM.abort();
  }
});

test("hover is transient, click pins, other hover cannot replace it and Escape fully dismisses", () => {
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML = monitoringChart([
      { at, value: 2 },
      { at: at + 86400, value: 4 },
    ]);
    const dispose = bindMonitoringCharts(d),
      points = [...d.querySelectorAll(".plot-point")],
      tip = d.querySelector(".chart-tooltip");
    event(w, points[0], "pointerover");
    event(w, points[0], "pointerout", { relatedTarget: d.body });
    assert.equal(tip.hidden, true);
    event(w, points[0], "click");
    assert.equal(points[0].getAttribute("aria-pressed"), "true");
    event(w, points[1], "pointerover");
    assert.equal(d.querySelector(".chart-tip-value").textContent, "2");
    key(w, points[0], "Escape");
    assert.equal(tip.hidden, true);
    assert.equal(points[0].classList.contains("selected"), false);
    event(w, points[1], "click");
    assert.equal(d.querySelector(".chart-tip-value").textContent, "4");
    event(w, d.body, "pointerdown");
    assert.equal(tip.hidden, true);
    event(w, points[0], "click");
    d.dispatchEvent(new w.Event("scroll"));
    assert.equal(tip.hidden, true);
    dispose();
  } finally {
    w.happyDOM.abort();
  }
});

test("keyboard navigation uses one tab stop, supports boundaries and closes without closing a containing dialog", () => {
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML =
      "<dialog open>" +
      monitoringChart([
        { at, value: 1 },
        { at: at + 1, value: null },
        { at: at + 2, value: 3 },
      ]) +
      "</dialog>";
    const dispose = bindMonitoringCharts(d),
      points = [...d.querySelectorAll(".plot-point")],
      dialog = d.querySelector("dialog");
    points[0].focus();
    key(w, points[0], "ArrowRight");
    assert.equal(d.activeElement, points[1]);
    key(w, points[1], "End");
    assert.equal(d.activeElement, points[2]);
    assert.equal(d.querySelectorAll('.plot-point[tabindex="0"]').length, 1);
    key(w, points[2], "Enter");
    assert.equal(points[2].getAttribute("aria-pressed"), "true");
    const tip = d.querySelector(".chart-tooltip");
    assert.equal(tip.parentElement, dialog);
    tip.querySelector("button").focus();
    key(w, tip.querySelector("button"), "Escape");
    assert.equal(tip.hidden, true);
    assert.equal(d.activeElement, points[2]);
    assert.equal(dialog.open, true);
    key(w, points[2], "Home");
    assert.equal(d.activeElement, points[0]);
    event(w, points[0], "click");
    event(w, tip.querySelector("button"), "click");
    assert.equal(tip.hidden, true);
    dispose();
  } finally {
    w.happyDOM.abort();
  }
});

test("missing values and invalid dates do not invent zeros or numeric observations", () => {
  assert.match(monitoringChart([]), /暂无/);
  assert.match(
    monitoringChart([
      { at: null, value: 1 },
      { at: "", value: 2 },
      { at: Infinity, value: 3 },
    ]),
    /暂无/,
  );
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML = monitoringChart([
      { at, value: null },
      { at: at + 1, value: NaN },
    ]);
    assert.equal(
      d.querySelectorAll(".plot-dot,polyline,.plot-y-axis span").length,
      0,
    );
    assert.equal(d.querySelectorAll(".plot-point").length, 2);
    assert.match(d.querySelector(".plot-no-values").textContent, /暂无有效/);
    d.body.innerHTML = monitoringChart([
      { at, value: 0 },
      { at: at + 1, value: 0 },
    ]);
    assert.equal(d.querySelectorAll(".plot-dot").length, 2);
  } finally {
    w.happyDOM.abort();
  }
});

test("bar charts share hover and pin controls and retain zero, missing and category labels", () => {
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML = monitoringBars([
      { rating: 0, count: 0 },
      { label: "Finance <img src=x>", count: "1200" },
      { label: "Unknown", count: null },
    ]);
    const dispose = bindMonitoringCharts(d),
      rows = [...d.querySelectorAll(".barrow")];
    assert.equal(rows[0].querySelector(".bar-label").textContent, "0");
    assert.equal(rows[0].querySelector(".bar-count").textContent, "0");
    assert.equal(rows[2].querySelector(".bar-count").textContent, "未观测");
    assert.equal(d.querySelector("img"), null);
    event(w, rows[1], "pointerover");
    assert.equal(d.querySelector(".chart-tip-value").textContent, "1,200");
    rows[0].focus();
    key(w, rows[0], "ArrowDown");
    assert.equal(d.activeElement, rows[1]);
    key(w, rows[1], " ");
    assert.equal(rows[1].getAttribute("aria-pressed"), "true");
    key(w, rows[1], "Escape");
    assert.equal(d.querySelector(".chart-tooltip").hidden, true);
    dispose();
  } finally {
    w.happyDOM.abort();
  }
});

test("detached charts dismiss their floating tooltip and duplicate bind calls do not add listeners", async () => {
  const w = new Window(),
    d = w.document;
  try {
    d.body.innerHTML = monitoringChart([{ at, value: 1 }]);
    const dispose = bindMonitoringCharts(d);
    assert.equal(bindMonitoringCharts(d), dispose);
    const point = d.querySelector(".plot-point");
    event(w, point, "click");
    assert.equal(point.getAttribute("aria-pressed"), "true");
    d.querySelector(".interactive-plot").remove();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(d.querySelector(".chart-tooltip").hidden, true);
    dispose();
    assert.equal(d.querySelector(".chart-tooltip"), null);
  } finally {
    w.happyDOM.abort();
  }
});

test("very small and very large finite values keep bounded, finite chart geometry", () => {
  for (const values of [
    [0.00001, 0.00002],
    [Number.MIN_VALUE, Number.MIN_VALUE * 2],
    [-Number.MAX_VALUE, Number.MAX_VALUE],
  ]) {
    const markup = monitoringChart(
      values.map((value, i) => ({ at: at + i, value })),
    );
    assert.doesNotMatch(markup, /NaN|Infinity/);
  }
});
