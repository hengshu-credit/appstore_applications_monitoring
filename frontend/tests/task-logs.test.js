import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { createTaskLogViewer } from "../src/task-logs.js";
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const row = (id, message) => ({
  id,
  message,
  level: "info",
  created_at: 100 + id,
  execution_id: "abc123",
});
test("log viewer increments cursor, escapes messages, paginates history and stops polling on close", async () => {
  const win = new Window({ url: "http://localhost" }),
    timers = new Map();
  let timerId = 0;
  const paths = [];
  win.setTimeout = (fn) => {
    timers.set(++timerId, fn);
    return timerId;
  };
  win.clearTimeout = (id) => timers.delete(id);
  const api = async (path) => {
    paths.push(path);
    const u = new URL(path, "http://localhost");
    if (u.searchParams.has("before_id"))
      return {
        items: [row(1, "older")],
        next_cursor: 1,
        oldest_cursor: 1,
        has_older: false,
        task: { status: "scheduled" },
        server_time: 100,
      };
    if (u.searchParams.has("after_id"))
      return {
        items: [row(3, "duplicate"), row(4, "running progress")],
        next_cursor: 4,
        oldest_cursor: 3,
        has_more: false,
        task: { status: "running" },
        server_time: 110,
      };
    return {
      items: [row(2, "<img src=x onerror=bad()>"), row(3, "request sent")],
      next_cursor: 3,
      oldest_cursor: 2,
      has_older: true,
      task: { status: "running" },
      server_time: 100,
    };
  };
  const viewer = createTaskLogViewer({ api, esc, stamp: String }, win);
  try {
    await viewer.open("keywords", 7, "Loan");
    assert.equal(win.document.querySelectorAll(".log-line").length, 2);
    assert.equal(win.document.querySelector(".log-message img"), null);
    const poll = [...timers.values()].at(-1);
    await poll();
    assert.match(paths.at(-1), /after_id=3/);
    assert.equal(win.document.querySelectorAll(".log-line").length, 3);
    win.document.querySelector("#log-older").click();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(win.document.querySelector("#log-auto").checked, false);
    assert.match(paths.at(-1), /before_id=2/);
    assert.equal(win.document.querySelectorAll(".log-line").length, 1);
    const requestCount = paths.length;
    viewer.close();
    await poll();
    assert.equal(paths.length, requestCount);
    assert.equal(win.document.querySelector("#task-logs"), null);
  } finally {
    viewer.close();
    await win.happyDOM.close();
  }
});
