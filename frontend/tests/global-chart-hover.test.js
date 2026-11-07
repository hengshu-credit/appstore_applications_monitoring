import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { bindGlobalChartHover } from "../src/global-chart-hover.js";

test("hover and keyboard link exact store IDs, cache summaries, ignore late responses and keep selection on Escape", async () => {
  const win = new Window();
  const doc = win.document;
  doc.body.innerHTML =
    '<div id="root"><table><tbody>' +
    [
      ["apple", "123"],
      ["apple", "123"],
      ["apple", "456"],
      ["google", "123"],
    ]
      .map(
        ([store, id]) =>
          `<tr><td><div class="appcell"><button data-store="${store}" data-app="${id}">Same name</button></div></td></tr>`,
      )
      .join("") +
    "</tbody></table></div>";
  let resolve,
    calls = 0;
  const scope = { countries: "us,jp,cn", collections: "top-free", depth: "10" };
  bindGlobalChartHover({
    root: doc.querySelector("#root"),
    win,
    scope,
    esc: (s) => String(s),
    delay: 0,
    api: async (path) => {
      calls++;
      assert.match(path, /countries=us%2Cjp%2Ccn/);
      return await new Promise((r) => (resolve = r));
    },
  });
  const tick = () => new Promise((r) => setTimeout(r, 15));
  const buttons = doc.querySelectorAll("[data-app]");
  const data = { depth: 10, charts: [], note: "all selected countries" };
  try {
    buttons[0].dispatchEvent(
      new win.MouseEvent("mouseover", { bubbles: true }),
    );
    await tick();
    assert.equal(doc.querySelectorAll(".same-ranked-app").length, 2);
    buttons[0].dispatchEvent(
      new win.KeyboardEvent("keydown", { bubbles: true, key: "Escape" }),
    );
    resolve(data);
    await tick();
    assert.equal(doc.querySelector("[role=tooltip]").hidden, true);
    assert.equal(doc.querySelectorAll(".same-ranked-app").length, 2);
    buttons[1].focus();
    await tick();
    assert.equal(calls, 1);
    assert.match(
      doc.querySelector("[role=tooltip]").textContent,
      /all selected countries/,
    );
    assert.equal(
      buttons[1].getAttribute("aria-describedby"),
      "global-app-summary",
    );
    buttons[2].focus();
    await tick();
    assert.equal(doc.querySelectorAll(".same-ranked-app").length, 1);
    assert.equal(calls, 2);
    buttons[2].blur();
    resolve(data);
    await tick();
    assert.equal(doc.querySelector("[role=tooltip]").hidden, true);
  } finally {
    await win.happyDOM.close();
  }
});

test("ranked app cells highlight matching store IDs and hide the tooltip on scroll while retaining selection", async () => {
  const win = new Window();
  const doc = win.document;
  doc.body.innerHTML =
    '<div id="root"><ol>' +
    [
      ["google", "one"],
      ["google", "one"],
      ["apple", "one"],
    ]
      .map(
        ([store, id]) =>
          `<li class="global-ranked-app"><div class="appcell"><button data-store="${store}" data-app="${id}">${store}</button></div></li>`,
      )
      .join("") +
    "</ol></div>";
  bindGlobalChartHover({
    root: doc.querySelector("#root"),
    win,
    esc: String,
    scope: {},
    delay: 0,
    api: async () => ({ depth: 10, charts: [], note: "fixture" }),
  });
  try {
    doc.querySelector("[data-app]").focus();
    assert.equal(doc.querySelectorAll("li.same-ranked-app").length, 2);
    assert.equal(
      doc
        .querySelector('[data-store="apple"]')
        .closest("li")
        .classList.contains("same-ranked-app"),
      false,
    );
    doc.querySelector("ol").dispatchEvent(new win.Event("scroll"));
    assert.equal(doc.querySelectorAll(".same-ranked-app").length, 2);
    assert.equal(doc.querySelector('[role="tooltip"]').hidden, true);
  } finally {
    await win.happyDOM.close();
  }
});
