import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  monitoringChart,
  bindMonitoringCharts,
} from "../src/monitoring-charts.js";

test("interactive plots preserve null gaps and expose values, source and dates to focus/hover", () => {
  const w = new Window();
  const d = w.document;
  d.body.innerHTML = monitoringChart(
    [
      { at: 1, value: 3, note: "source <script>" },
      { at: 2, value: null },
      { at: 5, value: 1 },
    ],
    true,
    String,
  );
  assert.equal(d.querySelectorAll("polyline").length, 2);
  assert.equal(d.querySelectorAll(".plot-point").length, 3);
  assert.match(
    d.querySelector(".plot-point").getAttribute("aria-label"),
    /排名 #3/,
  );
  assert.match(d.querySelectorAll(".plot-point")[1].textContent, /未观测/);
  assert.equal(d.querySelector("script"), null);
  assert.ok(
    Number(d.querySelectorAll(".plot-hit")[1].getAttribute("width")) > 0,
  );
  bindMonitoringCharts(d);
  const point = d.querySelector(".plot-point");
  point.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.ok(point.classList.contains("selected"));
  point.dispatchEvent(
    new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
  );
  assert.ok(!point.classList.contains("selected"));
  w.happyDOM.abort();
});

test("empty and all-missing histories never fabricate zero points", () => {
  assert.match(monitoringChart([]), /暂无/);
  const w = new Window();
  w.document.body.innerHTML = monitoringChart([
    { at: 1, value: null },
    { at: 2, value: NaN },
  ]);
  assert.equal(w.document.querySelectorAll("circle").length, 0);
  assert.equal(w.document.querySelectorAll(".plot-point").length, 2);
  w.happyDOM.abort();
});
