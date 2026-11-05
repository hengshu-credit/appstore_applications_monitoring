import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  navigationSections,
  pageInfo,
  navigationMarkup,
  updateWorkspace,
  workspacePage,
} from "../src/navigation.js";

test("every existing page has exactly one workspace and detail routes return to it", () => {
  const pages = navigationSections.flatMap((s) =>
    s.modules.flatMap((m) => m.pages),
  );
  assert.equal(pages.length, 21);
  assert.deepEqual([...pages].sort(), Object.keys(pageInfo).sort());
  assert.equal(new Set(pages).size, pages.length);
  for (const [route, page, scope] of [
    ["app/google/com.example", "apps", "market"],
    ["keyword/12", "keywords", "detail"],
    ["target/4", "collection", "detail"],
    ["chart", "globalcharts", "detail"],
  ]) {
    assert.equal(workspacePage(route).page, page);
    assert.equal(workspacePage(route).scope, scope);
  }
});

test("workspace tabs respect individual permissions and hide filters that do not apply", async () => {
  const window = new Window(),
    doc = window.document;
  doc.body.innerHTML = `<nav id="navigation">${navigationMarkup(() => "")}</nav><h1 id="page-title"></h1><p id="workspace-description"></p><nav id="workspace-pages"></nav><div class="store-field"></div><div class="country-field"></div><div class="language-field"></div><p id="workspace-scope"></p>`;
  try {
    updateWorkspace(doc, "groups", () => true);
    assert.equal(doc.querySelector(".store-field").hidden, true);
    assert.equal(doc.querySelector(".country-field").hidden, true);
    assert.equal(doc.querySelector("#workspace-pages").hidden, true);
    updateWorkspace(
      doc,
      "charts",
      (page) => page === "charts" || page === "featured",
    );
    assert.deepEqual(
      [...doc.querySelectorAll("#workspace-pages a")].map((a) => a.hash),
      ["#charts", "#featured"],
    );
    assert.equal(
      doc.querySelector("#navigation .active").dataset.nav,
      "globalcharts",
    );
    assert.equal(doc.querySelector(".country-field").hidden, false);
    updateWorkspace(doc, "developers", () => true);
    assert.equal(doc.querySelector(".store-field").hidden, false);
    assert.equal(doc.querySelector(".country-field").hidden, true);
    updateWorkspace(doc, "globalcharts", () => true);
    assert.equal(doc.querySelector(".store-field").hidden, false);
    assert.equal(doc.querySelector(".country-field").hidden, true);
    assert.match(
      doc.querySelector("#workspace-scope").textContent,
      /榜单内筛选/,
    );
    updateWorkspace(doc, "datasources", (page) => page === "datasources");
    assert.equal(doc.querySelector("#workspace-pages").hidden, true);
    assert.equal(doc.querySelector(".store-field").hidden, true);
  } finally {
    await window.happyDOM.close();
  }
});
