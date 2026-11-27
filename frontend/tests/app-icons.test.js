import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { createAppIconManager } from "../src/app-icons.js";

test("display uses the store image and only a user click forces a collection", async () => {
  const win = new Window({ settings: { enableImageFileLoading: false } });
  win.IntersectionObserver = undefined;
  win.document.body.innerHTML = `<span data-icon-store="apple" data-icon-app="123">
    <img hidden><span class="appicon-placeholder"></span><button data-icon-refresh></button></span>`;
  const requests = [];
  const remote = "https://is1.mzstatic.com/known.png";
  const manager = createAppIconManager({
    win,
    getMarket: () => ({ country: "us", language: "en" }),
    api: async (path, body) => {
      assert.equal(path, "/icons/resolve");
      requests.push(body);
      return { items: [{ pending: false, url: body.force ? "/api/icons/12?v=7" : remote }] };
    },
  });
  try {
    await win.happyDOM.waitUntilComplete();
    const image = win.document.querySelector("img");
    assert.equal(image.getAttribute("src"), remote);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].force, undefined);
    win.document.querySelector("button").click();
    await win.happyDOM.waitUntilComplete();
    assert.equal(requests[1].force, true);
    assert.equal(image.getAttribute("src"), "/api/icons/12?v=7");
  } finally {
    manager.destroy();
    await win.happyDOM.close();
  }
});
