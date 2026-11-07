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

test("mobile navigation closes on selection, outside click and Escape without changing desktop preference", async () => {
  const win = new Window({ url: "http://localhost" });
  const doc = win.document;
  const media = new win.EventTarget();
  media.matches = true;
  win.matchMedia = () => media;
  doc.body.innerHTML = `<aside class="app-sidebar"><button id="mobile-nav-toggle" aria-controls="navigation"></button><nav id="navigation"><a href="#groups" aria-current="page">应用分组</a><a href="#administration" hidden>账户管理</a></nav><button id="sidebar-toggle"></button></aside><main><button id="outside">页面操作</button></main>`;
  win.localStorage.setItem("appmon-sidebar-collapsed", "true");
  try {
    initShellLayout(doc, win);
    const toggle = doc.querySelector("#mobile-nav-toggle");
    const navigation = doc.querySelector("#navigation");
    assert.equal(navigation.hidden, true);
    toggle.click();
    assert.equal(navigation.hidden, false);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.equal(doc.querySelector('[href="#administration"]').hidden, true);
    doc.querySelector('[href="#groups"]').click();
    assert.equal(navigation.hidden, true);
    assert.equal(doc.activeElement, toggle);
    toggle.click();
    doc.querySelector("#outside").click();
    assert.equal(navigation.hidden, true);
    toggle.dispatchEvent(
      new win.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    assert.equal(doc.activeElement, doc.querySelector('[href="#groups"]'));
    doc.activeElement.dispatchEvent(
      new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    assert.equal(navigation.hidden, true);
    assert.equal(doc.activeElement, toggle);
    toggle.click();
    media.matches = false;
    media.dispatchEvent(new win.Event("change"));
    assert.equal(navigation.hidden, false);
    assert.equal(doc.documentElement.dataset.mobileNavOpen, "false");
    assert.equal(doc.documentElement.dataset.sidebarCollapsed, "true");
    media.matches = true;
    media.dispatchEvent(new win.Event("change"));
    assert.equal(navigation.hidden, true);
    assert.equal(win.localStorage.getItem("appmon-sidebar-collapsed"), "true");
  } finally {
    await win.happyDOM.close();
  }
});
