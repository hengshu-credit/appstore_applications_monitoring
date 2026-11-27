import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { Window } from "happy-dom";

const source = (await readFile(new URL("../src/media-preview.js", import.meta.url), "utf8"))
  .replace('import "./media-preview.css";', "");

test("preview uses the original, falls back to the thumbnail, and releases video when switching", async () => {
  const win = new Window();
  try {
    const doc = win.document;
    win.eval(source);
    doc.body.innerHTML = `<section data-media-group>
      <button data-media-preview="image" data-source="https://example.org/full" aria-label="图片"><img src="https://example.org/thumb" alt="图片"></button>
      <button data-media-preview="video" data-source="https://example.org/clip" aria-label="视频"></button>
    </section>
    <button data-media-preview="image" aria-label="另一组"><img src="https://example.org/other"></button>`;
    doc.querySelector("button").click();
    const viewer = doc.querySelector("dialog");
    assert.ok(viewer.open);
    const image = viewer.querySelector("img");
    assert.equal(image.src, "https://example.org/full");
    image.dispatchEvent(new win.Event("error"));
    assert.equal(image.src, "https://example.org/thumb");
    viewer.querySelector("[data-next]").click();
    const video = viewer.querySelector("video");
    assert.equal(video.src, "https://example.org/clip");
    assert.ok(video.controls);
    assert.ok(viewer.querySelector("[data-size]").hidden);
    viewer.querySelector("[data-next]").click();
    assert.equal(video.hasAttribute("src"), false);
    assert.equal(viewer.querySelector("img").src, "https://example.org/full");
    viewer.querySelector("[data-close]").click();
    await win.happyDOM.waitUntilComplete();
    assert.equal(doc.querySelector("dialog"), null);
  } finally {
    await win.happyDOM.close();
  }
});
