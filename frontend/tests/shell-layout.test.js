import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { initShellLayout } from "../src/shell-layout.js";

test("sidebar collapse persists across reloads with accessible expand/collapse actions", async () => {
  const win = new Window({ url: "http://localhost" });
  const doc = win.document;
  const markup =
    '<nav id="navigation"><a href="#groups" aria-label="应用分组">应用分组</a></nav><button id="sidebar-toggle" aria-controls="navigation"></button>';
  try {
    doc.body.innerHTML = markup;
    initShellLayout(doc, win);
    let button = doc.querySelector("#sidebar-toggle");
    assert.equal(button.getAttribute("aria-expanded"), "true");
    button.click();
    assert.equal(doc.documentElement.dataset.sidebarCollapsed, "true");
    assert.equal(button.getAttribute("aria-label"), "展开侧边栏");
    assert.equal(win.localStorage.getItem("appmon-sidebar-collapsed"), "true");
    assert.equal(doc.querySelector("nav a").getAttribute("href"), "#groups");
    doc.body.innerHTML = markup;
    initShellLayout(doc, win);
    button = doc.querySelector("#sidebar-toggle");
    assert.equal(button.getAttribute("aria-expanded"), "false");
    button.click();
    assert.equal(button.title, "收起侧边栏");
    assert.equal(doc.documentElement.dataset.sidebarCollapsed, "false");
    assert.equal(win.localStorage.getItem("appmon-sidebar-collapsed"), "false");
  } finally {
    await win.happyDOM.close();
  }
});

test("blocked preference storage does not disable the sidebar control", async () => {
  const win = new Window();
  win.document.body.innerHTML = '<button id="sidebar-toggle"></button>';
  try {
    initShellLayout(win.document, {
      get localStorage() {
        throw new Error("blocked");
      },
    });
    win.document.querySelector("button").click();
    assert.equal(win.document.documentElement.dataset.sidebarCollapsed, "true");
  } finally {
    await win.happyDOM.close();
  }
});
