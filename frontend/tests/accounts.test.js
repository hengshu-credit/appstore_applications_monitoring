import { pagination, bindPagination } from "../src/pagination.js";
import { filterBar } from "../src/filter-bar.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import { readFile } from "node:fs/promises";

const source = (
  await readFile(new URL("../src/accounts.js", import.meta.url), "utf8")
)
  .replace(/^import .*;$/gm, "")
  .replace(/^export /gm, "");
const tick = async () => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 5));
};
function setup(responder) {
  const window = new Window({ url: "http://localhost/" });
  window.document.write(
    '<nav><a data-nav="apps"></a><a data-nav="keywords"></a><a data-nav="administration"></a><a data-nav="account"></a></nav><header><div class="account-dropdown"><button id="account-trigger"><span id="account-name"></span><span id="account-avatar"></span></button><div id="account-menu" hidden><button id="account-settings" role="menuitem">用户设置</button><button id="open-theme" role="menuitem">主题设置</button><button id="logout-account" role="menuitem">退出登录</button></div></div></header><main></main><dialog id="login"></dialog>',
  );
  const calls = [];
  window.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ url, body, options });
    const result = responder(url, body, options);
    return {
      ok: result.status === undefined || result.status === 200,
      json: async () => result.data,
    };
  };
  Object.assign(window,{pagination,bindPagination});
  window.filterBar = filterBar;
  window.eval(
    source +
      `\nwindow.accounts=createAccounts({esc:v=>String(v??'').replaceAll('<','&lt;'),toast:()=>{},onReady:()=>{window.ready=true;}});`,
  );
  return { window, calls, doc: window.document, accounts: window.accounts };
}
const user = {
  id: 2,
  username: "user-test",
  role: "user",
  effective_role: "user",
  permissions: ["apps:read"],
};
test("initialization validates confirmation, displays one-time recovery and then logs in", async () => {
  let initialized = false;
  const { window, doc, calls, accounts } = setup((url) => {
    if (url.endsWith("/config"))
      return {
        data: {
          initialization_required: !initialized,
          registration_enabled: true,
        },
      };
    if (url.endsWith("/me")) return { status: 401, data: { detail: "请登录" } };
    if (url.endsWith("/initialize")) {
      initialized = true;
      return {
        data: {
          username: "first-admin",
          recovery_code: "fixture-recovery-code",
        },
      };
    }
    if (url.endsWith("/login"))
      return {
        data: { user: { ...user, role: "admin" }, csrf_token: "csrf-fixture" },
      };
    throw Error(url);
  });
  try {
    assert.equal(await accounts.start(), false);
    assert.match(doc.querySelector("h1").textContent, /创建超级管理员/);
    let form = doc.querySelector("form");
    form.elements.username.value = "first-admin";
    form.elements.password.value = "long-password";
    form.elements.confirm_password.value = "different";
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick();
    assert.match(form.querySelector("[role=alert]").textContent, /不一致/);
    assert.equal(calls.filter((c) => c.url.endsWith("/initialize")).length, 0);
    form.elements.confirm_password.value = "long-password";
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick();
    assert.equal(doc.querySelector("textarea").value, "fixture-recovery-code");
    assert.equal(doc.querySelector("#code-done").disabled, true);
    doc.querySelector("#saved-code").click();
    doc.querySelector("#code-done").click();
    form = doc.querySelector("form");
    form.elements.username.value = "first-admin";
    form.elements.password.value = "long-password";
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick();
    assert.equal(window.ready, true);
    assert.equal(doc.querySelector("dialog").open, false);
    assert.equal(accounts.headers()["X-CSRF-Token"], "csrf-fixture");
  } finally {
    await window.happyDOM.close();
  }
});

test("ordinary users see allowed navigation only and account changes require current credentials", async () => {
  const { window, doc, calls, accounts } = setup((url) => {
    if (url.endsWith("/config"))
      return {
        data: { initialization_required: false, registration_enabled: true },
      };
    if (url.endsWith("/me")) return { data: { user, csrf_token: "csrf-test" } };
    if (url.includes("/admin?"))
      return {
        data: {
          users: [{ ...user, enabled: true, override: null }],
          total: 1,
          resources: { apps: "应用库" },
          roles: { user: ["apps:read"], member: [] },
        },
      };
    if (url.endsWith("/admin/users/2")) return { data: { ok: true } };
    throw Error(url);
  });
  try {
    assert.equal(await accounts.start(), true);
    doc
      .querySelector("nav")
      .insertAdjacentHTML(
        "beforeend",
        '<div class="nav-section"><a data-nav="keywords" data-pages="keywords apps"></a></div><div class="nav-section"><a data-nav="exports" data-pages="exports datasources"></a></div>',
      );
    await accounts.refresh();
    const module = doc.querySelector('[data-pages="keywords apps"]');
    assert.equal(module.hidden, false);
    assert.equal(module.getAttribute("href"), "#apps");
    assert.equal(
      doc
        .querySelector('[data-pages="exports datasources"]')
        .closest(".nav-section").hidden,
      true,
    );
    assert.equal(doc.querySelector("[data-nav=administration]").hidden, true);
    assert.equal(doc.querySelector("[data-nav=keywords]").hidden, true);
    assert.equal(doc.querySelector("[data-nav=apps]").hidden, false);
    assert.equal(accounts.can("apps", "write"), false);
    window.location.hash = "administration?own=1";
    await accounts.management(doc.querySelector("main"));
    doc.querySelector("[data-user]").click();
    const form = doc.querySelector(".account-editor");
    form.elements.username.value = "renamed";
    form.elements.current_password.value = "current-password";
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick();
    const sent = calls.find((c) => c.url.endsWith("/admin/users/2"));
    assert.equal(sent.options.method, "PUT");
    assert.equal(sent.body.current_password, "current-password");
    assert.equal(sent.options.headers["X-CSRF-Token"], "csrf-test");
    assert.equal(accounts.user, null);
    assert.match(doc.querySelector("#login").textContent, /登录观星平台/);
  } finally {
    await window.happyDOM.close();
  }
});

test("admin edits member expiry and explicit permission override without granting admin status", async () => {
  const { window, doc, calls, accounts } = setup((url) => {
    if (url.endsWith("/config"))
      return {
        data: { initialization_required: false, registration_enabled: true },
      };
    if (url.endsWith("/me"))
      return {
        data: {
          user: { ...user, id: 1, role: "admin" },
          csrf_token: "csrf-admin",
        },
      };
    if (url.includes("/admin?"))
      return {
        data: {
          users: [{ ...user, enabled: true, override: null }],
          total: 1,
          resources: { apps: "应用库" },
          roles: { user: ["apps:read"], member: ["apps:read"] },
          registration_enabled: true,
        },
      };
    if (url.endsWith("/admin/users/2")) return { data: { ok: true } };
    throw Error(url);
  });
  try {
    await accounts.start();
    await accounts.management(doc.querySelector("main"));
    doc.querySelector("[data-user]").click();
    const form = doc.querySelector(".account-editor");
    form.querySelector("[name=group_ids][value=user]").click();
    form.querySelector("[name=group_ids][value=member]").click();
    form.elements.member_until.value = "2030-12-01T10:30";
    doc.querySelector("#edit-account-tab-permissions").click();
    form.elements.inherit.click();
    form.querySelector('[value="apps:write"]').click();
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
    await tick();
    const sent = calls.find((c) => c.url.endsWith("/admin/users/2"));
    assert.deepEqual(sent.body.group_ids, ["member"]);
    assert.ok(sent.body.member_until > 0);
    assert.deepEqual(sent.body.permissions, ["apps:read", "apps:write"]);
    assert.equal(doc.querySelector("[name=role] option[value=admin]"), null);
    assert.equal(form.querySelector("[name=group_ids][value=admin]"), null);
  } finally {
    await window.happyDOM.close();
  }
});

test("admin creates and renames a group, previews merged grants and creates an account", async () => {
  const data = {
    users: [],
    total: 0,
    resources: { apps: "应用库", events: "事件中心" },
    roles: { user: ["apps:read"], member: [] },
    group_names: { user: "普通用户", member: "会员" },
  };
  const { window, doc, calls, accounts } = setup((url, body) => {
    if (url.endsWith("/config")) return { data: {} };
    if (url.endsWith("/me"))
      return {
        data: { user: { ...user, role: "admin" }, csrf_token: "csrf-admin" },
      };
    if (url.includes("/admin?")) return { data };
    if (url.endsWith("/roles") || url.endsWith("/roles/custom"))
      return { data: { ...body, role: "custom" } };
    if (url.endsWith("/admin/users"))
      return {
        data: { username: body.username, recovery_code: "one-time-code" },
      };
    throw Error(url);
  });
  const submit = (form) =>
    form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  try {
    await accounts.start();
    await accounts.management(doc.querySelector("main"));
    doc.querySelector("#accounts-tab-roles").click();
    let editor = doc.querySelector("#role-editor");
    editor.elements.role.value = "";
    editor.elements.role.dispatchEvent(new window.Event("change"));
    editor.elements.group_name.value = "运营";
    editor.querySelector('[value="events:write"]').click();
    submit(editor);
    await tick();
    assert.deepEqual(calls.find((c) => c.url.endsWith("/roles")).body, {
      name: "运营",
      permissions: ["events:read", "events:write"],
    });
    editor = doc.querySelector("#role-editor");
    assert.equal(editor.elements.role.value, "custom");
    editor.elements.group_name.value = "内容运营";
    submit(editor);
    await tick();
    assert.equal(
      calls.find((c) => c.url.endsWith("/roles/custom")).options.method,
      "PUT",
    );
    doc.querySelector("#accounts-tab-users").click();
    doc.querySelector("#add-account").click();
    const form = doc.querySelector(".account-editor");
    const group = form.querySelector("[name=group_ids][value=custom]");
    assert.match(group.parentElement.textContent, /内容运营/);
    group.click();
    assert.equal(form.querySelector('[value="events:write"]').checked, true);
    assert.equal(form.querySelector('[value="apps:read"]').checked, true);
    form.elements.username.value = "new-account";
    form.elements.password.value = "new-password-123";
    form.elements.confirm_password.value = "new-password-123";
    submit(form);
    await tick();
    const sent = calls.find((c) => c.url.endsWith("/admin/users"));
    assert.deepEqual(sent.body.group_ids, ["user", "custom"]);
    assert.equal(sent.body.permissions, null);
    assert.equal(sent.options.method, "POST");
    assert.equal(doc.querySelector("#login textarea").value, "one-time-code");
  } finally {
    await window.happyDOM.close();
  }
});
