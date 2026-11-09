import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  secretField,
  bindSourceAccount,
  accountSummary,
} from "../src/source-account.js";

const esc = (s) =>
  String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;");
function fixture(api, onUpdate) {
  const window = new Window();
  const root = window.document.body;
  root.innerHTML = `<p id="source-state" role="status"></p><form id="source-account"><input name="email" value="test@example.com">${secretField(esc, "password", "密码", 'a"<&secret', true)}<button type="submit">保存并检查授权</button><button type="button" id="source-authorize" disabled>重新检查授权</button></form>`;
  bindSourceAccount({
    root,
    prefix: "source",
    provider: "directory",
    api,
    onUpdate,
  });
  return {
    window,
    root,
    form: root.querySelector("form"),
    status: root.querySelector('[role="status"]'),
    retry: root.querySelector("#source-authorize"),
  };
}

test("saved password is masked and the inline eye toggles without submitting", async () => {
  const f = fixture(() => {
    throw new Error("unexpected request");
  });
  try {
    const input = f.form.elements.password,
      eye = f.form.querySelector(".source-secret-toggle");
    assert.equal(input.value, 'a"<&secret');
    assert.equal(input.type, "password");
    eye.click();
    assert.equal(input.type, "text");
    assert.equal(eye.getAttribute("aria-label"), "隐藏密码");
    assert.equal(eye.getAttribute("aria-pressed"), "true");
    eye.click();
    assert.equal(input.type, "password");
    assert.equal(eye.getAttribute("aria-label"), "显示密码");
  } finally {
    await f.window.happyDOM.close();
  }
});

test("save and authorization report separate progress, update summaries, and retain input on failure", async () => {
  let finishSave, finishAuth;
  const calls = [],
    updates = [];
  const f = fixture(
    (path, value, method) => {
      calls.push({ path, value, method });
      return new Promise((resolve) => {
        if (method === "PUT") finishSave = resolve;
        else finishAuth = resolve;
      });
    },
    (data) => updates.push(data),
  );
  try {
    f.form.elements.password.dispatchEvent(
      new f.window.Event("input", { bubbles: true }),
    );
    assert.match(f.status.textContent, /未保存/);
    const pending = f.form.onsubmit({ preventDefault() {} });
    assert.match(f.status.textContent, /正在保存/);
    assert.equal(f.form.elements.password.readOnly, true);
    assert.equal(f.retry.disabled, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/data-sources/directory?authorize_now=false");
    assert.equal(calls[0].value.password, 'a"<&secret');
    finishSave({ directory: { configured: true, status: "saved" } });
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(f.status.textContent, /凭据已保存.*正在连接/);
    assert.equal(calls[1].path, "/data-sources/directory/authorize");
    finishAuth({ directory: { configured: true, status: "action_required" } });
    await pending;
    assert.match(f.status.textContent, /凭据已保存.*授权未完成/);
    assert.match(f.status.textContent, /不会自动同步/);
    assert.equal(updates.length, 2);
    assert.equal(f.form.elements.password.value, 'a"<&secret');
    assert.equal(f.retry.disabled, false);
    f.form.elements.email.dispatchEvent(
      new f.window.Event("input", { bubbles: true }),
    );
    assert.equal(
      f.retry.disabled,
      true,
      "changed credentials must be saved before checking",
    );
  } finally {
    await f.window.happyDOM.close();
  }
});

test("save errors and authorization transport errors stay inline and permit recovery", async () => {
  for (const failSave of [true, false]) {
    const f = fixture(async (path, value, method) => {
      if (method === "PUT" && !failSave)
        return { directory: { configured: true, status: "saved" } };
      throw new Error("连接中断");
    });
    try {
      await f.form.onsubmit({ preventDefault() {} });
      assert.match(
        f.status.textContent,
        failSave
          ? /保存未确认成功.*连接中断/
          : /凭据已保存.*授权检查未完成.*连接中断/,
      );
      assert.equal(f.form.elements.password.value, 'a"<&secret');
      assert.equal(f.form.querySelector('[type="submit"]').disabled, false);
      assert.equal(f.form.elements.password.readOnly, false);
      assert.equal(f.retry.disabled, failSave);
    } finally {
      await f.window.happyDOM.close();
    }
  }
});

test("saved credentials and successful authorization have distinct labels", () => {
  assert.match(accountSummary({ configured: false }), /未保存/);
  assert.equal(
    accountSummary({ configured: true, status: "authorized" }),
    "凭据已保存 · 授权有效",
  );
  assert.match(
    accountSummary({ configured: true, status: "api_token_required" }),
    /API 未授权.*Token/,
  );
});
