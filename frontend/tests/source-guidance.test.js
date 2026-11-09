import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  sourceGuide,
  sourceTitle,
  sourceAddress,
} from "../src/source-guidance.js";
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );

test("source instructions distinguish official endpoints from custom credential destinations", () => {
  const official = {
    id: "marketdata",
    name: "商业市场数据",
    origin: "https://api.appfigures.com/v2",
    editable_origin: true,
  };
  assert.match(sourceTitle(official), /Appfigures/);
  assert.match(sourceGuide(esc, official), /Personal Access Token/);
  const custom = { ...official, origin: "https://private.example/v2" };
  assert.match(sourceTitle(custom), /自定义接入/);
  const w = new Window();
  try {
    w.document.body.innerHTML = sourceGuide(esc, custom);
    assert.match(w.document.body.textContent, /凭据由该接入服务的管理者提供/);
    assert.ok(w.document.querySelector('a[href="https://private.example/v2"]'));
    w.document.body.innerHTML =
      sourceAddress(esc, "javascript:alert(1)") +
      sourceAddress(esc, "<img src=x onerror=alert(1)>");
    assert.equal(w.document.querySelector("a,img,script"), null);
    w.document.body.innerHTML = sourceAddress(esc, "https://is1.mzstatic.com / https://play-lh.googleusercontent.com");
    assert.deepEqual([...w.document.querySelectorAll("a")].map((a) => a.href),
      ["https://is1.mzstatic.com/", "https://play-lh.googleusercontent.com/"]);
  } finally {
    w.happyDOM.abort();
  }
});
