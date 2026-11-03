import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";
import { initMarketControls } from "../src/market-controls.js";
const html = (
  await readFile(new URL("../index.html", import.meta.url), "utf8")
).replace(/<script[^>]*><\/script>/g, "");
test("store and language menus commit values, close on Escape, and retain custom language tags", async () => {
  const win = new Window({
    url: "http://localhost:5173",
    settings: {
      disableCSSFileLoading: true,
      disableJavaScriptFileLoading: true,
    },
  });
  try {
    win.document.write(html);
    const controls = initMarketControls(win),
      doc = win.document;
    let changes = 0;
    doc
      .querySelector("#global-store")
      .addEventListener("change", () => changes++);
    doc.querySelector("#store-trigger").click();
    assert.equal(doc.querySelector("#store-menu").hidden, false);
    doc.querySelector('#store-menu [data-value="apple"]').click();
    assert.equal(doc.querySelector("#global-store").value, "all");
    assert.equal(changes, 1);
    assert.equal(doc.querySelector('#store-menu [data-value="all"]'), null);
    assert.equal(
      doc.querySelectorAll('#store-menu [aria-selected="true"]').length,
      2,
    );
    assert.equal(doc.querySelector("#store-menu").hidden, false);
    doc.querySelector('#store-menu [data-value="google"]').click();
    assert.equal(doc.querySelector("#global-store").value, "apple");
    assert.equal(changes, 2);
    doc.querySelector('#store-menu [data-value="apple"]').click();
    assert.equal(doc.querySelector("#global-store").value, "apple");
    assert.equal(changes, 2, "keep the last selected store");
    assert.match(
      doc.querySelector("#store-value img").src,
      /stores\/apple.svg$/,
    );
    doc.querySelector("#language-trigger").click();
    assert.equal(doc.querySelector("#store-menu").hidden, true);
    const search = doc.querySelector("#language-menu input");
    search.value = "泰语";
    search.dispatchEvent(new win.Event("input"));
    doc.querySelector('#language-menu [data-value="th"]').click();
    assert.equal(doc.querySelector("#global-language").value, "th");
    doc.querySelector("#language-trigger").click();
    search.value = "de-at";
    search.dispatchEvent(new win.Event("input"));
    doc.querySelector('[data-value="de-at"]').click();
    assert.equal(doc.querySelector("#global-language").value, "de-at");
    controls.sync();
    assert.match(doc.querySelector("#language-value").textContent, /de-at/);
    doc.querySelector("#store-trigger").click();
    doc
      .querySelector("#store-menu")
      .dispatchEvent(
        new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    assert.equal(
      doc.querySelector("#store-trigger").getAttribute("aria-expanded"),
      "false",
    );
    assert.equal(doc.activeElement.id, "store-trigger");
  } finally {
    await win.happyDOM.close();
  }
});
