import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { bindGlobalChartHover } from "../src/global-chart-hover.js";
const tick = () => new Promise((r) => setTimeout(r, 15));
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const stats = { depth: 10, charts: [], note: "Selected countries only" };
function setup(
  api = async (path, body) =>
    path.startsWith("/apps/")
      ? {
          items: [
            {
              ...body.targets[0],
              developer: "<script>Studio</script>",
              version: "1.2",
              keyword_count: 0,
            },
          ],
        }
      : stats,
) {
  const win = new Window(),
    doc = win.document;
  doc.body.innerHTML =
    '<div id="root"><ol>' +
    [
      ["apple", "123", "us"],
      ["apple", "123", "jp"],
      ["apple", "456", "us"],
      ["google", "123", "us"],
    ]
      .map(
        ([store, id, country]) =>
          `<li class="global-ranked-app"><div class="appcell"><span class="appicon-wrap"><img class="appicon"><button data-icon-refresh>refresh</button></span><div><button data-store="${store}" data-app="${id}" data-country="${country}" data-language="en">Same name</button></div></div></li>`,
      )
      .join("") +
    '</ol></div><div id="blank">blank</div>';
  const calls = [];
  const controller = bindGlobalChartHover({
    root: doc.querySelector("#root"),
    win,
    esc,
    scope: { countries: "us,jp", collections: "top-free", depth: "10" },
    delay: 0,
    api: async (path, body) => {
      calls.push({ path, body });
      return api(path, body);
    },
  });
  const cells = [...doc.querySelectorAll(".appcell")],
    icons = [...doc.querySelectorAll(".global-app-select")],
    tip = doc.querySelector("#global-app-summary");
  const fire = (el, type, relatedTarget) =>
    el.dispatchEvent(
      new win.MouseEvent(type, { bubbles: true, relatedTarget }),
    );
  return {
    win,
    doc,
    cells,
    icons,
    tip,
    calls,
    controller,
    fire,
    selected: () => doc.querySelectorAll(".same-ranked-app").length,
  };
}

test("hover highlights the same store ID across countries, clears on leaving and does not open or fetch details", async () => {
  const c = setup();
  try {
    c.fire(c.icons[0], "mouseover");
    assert.equal(c.selected(), 2);
    assert.equal(c.tip.hidden, true);
    c.fire(c.icons[0], "mouseout", c.cells[0].querySelector("[data-app]"));
    assert.equal(c.selected(), 2);
    c.fire(c.cells[0], "mouseout", c.doc.querySelector("#blank"));
    assert.equal(c.selected(), 0);
    c.fire(c.icons[3], "mouseover");
    assert.equal(c.selected(), 1);
    await tick();
    assert.equal(c.calls.length, 0);
    c.icons[0].focus();
    assert.equal(c.selected(), 2);
    c.icons[0].blur();
    assert.equal(c.selected(), 0);
  } finally {
    c.controller.destroy();
    await c.win.happyDOM.close();
  }
});

test("clicking an icon pins the app and its information panel ignores pointer, focus, click and scroll inside", async () => {
  const c = setup();
  try {
    let navigations = 0;
    c.doc.addEventListener("click", (e) => {
      if (e.target.closest("[data-app]")) navigations++;
    });
    c.fire(c.icons[0], "click");
    await tick();
    assert.equal(c.selected(), 2);
    assert.equal(c.tip.hidden, false);
    assert.equal(c.icons[0].getAttribute("aria-pressed"), "true");
    assert.equal(navigations, 0);
    assert.equal(c.doc.querySelectorAll(".ranked-app-pinned").length, 2);
    assert.match(c.tip.textContent, /1.2|Selected countries/);
    assert.equal(c.tip.querySelector("script"), null);
    assert.match(c.tip.textContent, /<script>Studio<\/script>/);
    c.fire(c.cells[0], "mouseout", c.doc.body);
    c.fire(c.icons[2], "mouseover");
    assert.equal(c.selected(), 2);
    assert.equal(c.calls.length, 2);
    c.tip.insertAdjacentHTML("beforeend", '<input aria-label="panel action">');
    const input = c.tip.querySelector("input");
    input.focus();
    c.fire(input, "click");
    c.fire(input, "mouseover");
    c.tip.dispatchEvent(new c.win.Event("scroll"));
    assert.equal(c.selected(), 2);
    assert.equal(c.tip.hidden, false);
    c.doc.querySelector("ol").dispatchEvent(new c.win.Event("scroll"));
    assert.equal(c.selected(), 2);
    assert.equal(c.tip.hidden, false);
    c.fire(c.icons[0], "click");
    await tick();
    assert.equal(c.selected(), 2);
    assert.equal(c.calls.length, 2);
    c.fire(c.doc.querySelector("#blank"), "click");
    assert.equal(c.selected(), 0);
    assert.equal(c.tip.hidden, true);
  } finally {
    c.controller.destroy();
    await c.win.happyDOM.close();
  }
});

test("Escape and close exit pinned selection without focus reselecting, while refresh and names retain their actions", async () => {
  const c = setup();
  try {
    c.fire(c.icons[0], "click");
    await tick();
    const close = c.tip.querySelector("[data-close-app-summary]");
    close.focus();
    close.dispatchEvent(
      new c.win.KeyboardEvent("keydown", {
        bubbles: true,
        key: "Escape",
        cancelable: true,
      }),
    );
    assert.equal(c.selected(), 0);
    assert.equal(c.tip.hidden, true);
    assert.equal(c.doc.activeElement, c.icons[0]);
    c.fire(c.icons[1], "click");
    await tick();
    c.fire(c.tip.querySelector("[data-close-app-summary]"), "click");
    assert.equal(c.selected(), 0);
    c.fire(c.icons[0], "click");
    await tick();
    let refreshed = 0,
      navigations = 0;
    c.doc.addEventListener("click", (e) => {
      if (e.target.closest("[data-icon-refresh]")) refreshed++;
      if (e.target.closest("[data-app]")) navigations++;
    });
    c.fire(c.cells[2].querySelector("[data-icon-refresh]"), "click");
    assert.equal(refreshed, 1);
    assert.equal(c.selected(), 2);
    c.fire(c.cells[0].querySelector("[data-app]"), "click");
    assert.equal(navigations, 1);
    c.doc.dispatchEvent(
      new c.win.KeyboardEvent("keydown", { bubbles: true, key: "Escape" }),
    );
    assert.equal(c.selected(), 0);
  } finally {
    c.controller.destroy();
    await c.win.happyDOM.close();
  }
});

test("late responses cannot reopen a dismissed selection; cache preserves summary scope and separate market profiles", async () => {
  const pending = [];
  const c = setup(
    (path, body) =>
      new Promise((resolve) => pending.push({ path, body, resolve })),
  );
  try {
    c.fire(c.icons[0], "click");
    await tick();
    assert.equal(pending.length, 2);
    assert.match(pending[0].path, /countries=us%2Cjp/);
    assert.equal(pending[1].body.targets[0].country, "us");
    c.fire(c.doc.querySelector("#blank"), "click");
    pending[0].resolve(stats);
    pending[1].resolve({ items: [{ version: "US" }] });
    await tick();
    assert.equal(c.tip.hidden, true);
    assert.equal(c.selected(), 0);
    c.fire(c.icons[1], "click");
    await tick();
    assert.equal(pending.length, 3);
    assert.equal(pending[2].body.targets[0].country, "jp");
    pending[2].resolve({ items: [{ version: "JP" }] });
    await tick();
    assert.match(c.tip.textContent, /JP/);
    assert.doesNotMatch(c.tip.textContent, /US/);
    c.fire(c.icons[2], "click");
    await tick();
    assert.equal(pending.length, 5);
    c.controller.clear();
    pending[3].resolve(stats);
    pending[4].resolve({ items: [] });
    await tick();
    assert.equal(c.tip.hidden, true);
    assert.equal(c.selected(), 0);
  } finally {
    c.controller.destroy();
    await c.win.happyDOM.close();
  }
});

test("failed detail requests remain closable and retry on click; removed views clean up document listeners", async () => {
  let failure = true;
  const c = setup(async () => {
    if (failure) throw Error("offline");
    return stats;
  });
  try {
    c.fire(c.icons[0], "click");
    await tick();
    assert.match(c.tip.textContent, /暂不可用/);
    assert.equal(c.selected(), 2);
    failure = false;
    c.fire(c.icons[0], "click");
    await tick();
    assert.equal(c.calls.length, 4);
    assert.match(c.tip.textContent, /Selected countries/);
    c.doc.querySelector("#root").remove();
    await tick();
    assert.equal(c.doc.querySelector("#global-app-summary"), null);
    c.doc.dispatchEvent(
      new c.win.KeyboardEvent("keydown", { bubbles: true, key: "Escape" }),
    );
  } finally {
    c.controller.destroy();
    await c.win.happyDOM.close();
  }
});
