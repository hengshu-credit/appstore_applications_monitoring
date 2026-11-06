import { pagination, bindPagination } from "./pagination.js";
import { filterBar } from "./filter-bar.js";
export function createAccounts({ esc, onReady, toast }) {
  let user = null,
    csrf = "",
    config,
    mode = "login";
  const dialog = document.querySelector("#login");
  dialog.className = "account-gate";
  dialog.addEventListener("cancel", (event) => event.preventDefault());
  const roleNames = { admin: "超级管理员", user: "普通用户", member: "会员" };
  async function request(path, body, method) {
    const response = await fetch("/api/auth" + path, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json();
    if (!response.ok)
      throw Error(
        typeof data.detail === "string" ? data.detail : "请求失败，请检查输入",
      );
    return data;
  }
  function accept(data) {
    user = data.user;
    csrf = data.csrf_token;
  }
  function can(resource, action = "read") {
    if (!user) return false;
    if (resource === "datasources") return user.role === "admin";
    if (resource === "account") return true;

    if (resource === "target")
      return ["charts", "featured", "reviews", "collection"].some((r) =>
        can(r, action),
      );
    if (resource === "chart")
      return can("charts", action) || can("globalcharts", action);
    return (
      user.role === "admin" ||
      user.permissions.includes(`${resource}:${action}`)
    );
  }
  function applyNavigation() {
    const label = document.querySelector("#account-name");
    if (label) label.textContent = user?.username || "我的账户";
    const avatar = document.querySelector("#account-avatar");
    if (avatar) avatar.textContent = (user?.username || "U")[0].toUpperCase();
    document.querySelectorAll("[data-nav]").forEach((a) => {
      const allowed = (a.dataset.pages || a.dataset.nav)
        .split(" ")
        .filter((page) => can(page));
      a.hidden = !allowed.length;
      if (allowed.length) a.href = `#${allowed[0]}`;
    });
    document.querySelectorAll(".nav-section").forEach((section) => {
      section.hidden = !section.querySelector("[data-nav]:not([hidden])");
    });
  }
  function showCode(data, done) {
    dialog.innerHTML = `<section class="auth-card"><p class="auth-eyebrow">衡枢 · 观星</p><h1>请保存账户恢复码</h1>
      <p>账号：<strong>${esc(data.username || "")}</strong></p><p>忘记账号或密码时可使用此码找回。它仅在此处显示，请保存在安全位置，勿转发。</p>
      <textarea readonly aria-label="账户恢复码" rows="3">${esc(data.recovery_code)}</textarea>
      <label class="auth-check"><input type="checkbox" id="saved-code">我已安全保存恢复码</label>
      <button id="download-code" type="button" class="secondary">下载恢复码</button><button id="code-done" disabled>继续</button></section>`;
    if (!dialog.open) dialog.showModal();
    dialog.querySelector("#saved-code").onchange = (e) => {
      dialog.querySelector("#code-done").disabled = !e.target.checked;
    };
    dialog.querySelector("#code-done").onclick = () => {
      dialog.replaceChildren();
      done();
    };
    dialog.querySelector("#download-code").onclick = () => {
      const url = URL.createObjectURL(
        new Blob(
          [
            `观星账户：${data.username}\n恢复码：${data.recovery_code}\n请安全保存，勿转发。`,
          ],
          { type: "text/plain;charset=utf-8" },
        ),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = "观星账户恢复码.txt";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
  }
  function field(
    label,
    name,
    type = "text",
    autocomplete = "",
    required = true,
  ) {
    return `<label>${label}<input name="${name}" type="${type}" ${required ? "required" : ""} autocomplete="${autocomplete}" ${name.includes("password") ? 'maxlength="128"' : 'maxlength="256"'}></label>`;
  }
  function show(modeValue = "login") {
    mode = config?.initialization_required ? "initialize" : modeValue;
    document.querySelector("#account-editor-dialog")?.remove();
    user = null;
    csrf = "";
    document.body.classList.add("account-locked");
    const titles = {
      login: "登录观星平台",
      initialize: "创建超级管理员",
      register: "注册观星账户",
      recover: "找回账号与密码",
    };
    dialog.innerHTML = `<section class="auth-card"><p class="auth-eyebrow">衡枢 · 观星</p><h1>${titles[mode]}</h1>
      <p>${mode === "initialize" ? "首次启动，请创建系统唯一的超级管理员。完成后将关闭初始化入口。" : "星河璀璨，万象浮沉。登录后访问为你开放的观察资源。"}</p>
      ${mode === "recover" ? "<p>恢复码可在登录后的账户管理 → 编辑账户 → 安全设置中重新生成。超管如已全部遗忘，请由服务器运维执行应急重置。</p>" : ""}
      <form id="account-entry">
      ${mode === "recover" ? field("恢复码", "recovery_code", "password", "off") : field("账号", "username", "text", "username")}
      ${field(mode === "recover" ? "新密码（10–128 位）" : mode === "login" ? "密码" : "密码（10–128 位）", "password", "password", mode === "login" ? "current-password" : "new-password")}
      ${mode !== "login" ? field("确认密码", "confirm_password", "password", "new-password") : ""}
      <p class="auth-error" role="alert"></p><button type="submit">${titles[mode]}</button></form>
      ${mode !== "initialize" ? `<nav class="auth-links">${mode !== "login" ? '<button data-mode="login" class="secondary">返回登录</button>' : `${config?.registration_enabled ? '<button data-mode="register" class="secondary">注册账户</button>' : ""}<button data-mode="recover" class="secondary">忘记账号或密码</button>`}</nav>` : ""}</section>`;
    if (!dialog.open) dialog.showModal();
    dialog.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => show(b.dataset.mode);
    });
    dialog.querySelector("form").onsubmit = async (event) => {
      event.preventDefault();
      const form = event.target,
        values = Object.fromEntries(new FormData(form));
      const button = form.querySelector("[type=submit]");
      button.disabled = true;
      try {
        if (mode !== "login" && values.password !== values.confirm_password)
          throw Error("两次输入的密码不一致");
        const result = await request("/" + mode, values);
        form.reset();
        if (mode === "login") {
          accept(result);
          dialog.close();
          document.body.classList.remove("account-locked");
          applyNavigation();
          await onReady();
        } else {
          config = await request("/config");
          showCode(result, () => show("login"));
        }
      } catch (error) {
        form.querySelector("[role=alert]").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
  }
  async function start() {
    document.body.classList.add("account-locked");
    try {
      config = await request("/config");
      try {
        accept(await request("/me"));
      } catch {
        show();
        return false;
      }
      document.body.classList.remove("account-locked");
      applyNavigation();
      return true;
    } catch (error) {
      dialog.innerHTML = `<section class="auth-card"><h1>暂时无法连接账户服务</h1><p role="alert">${esc(error.message)}</p><button id="auth-retry">重试</button></section>`;
      if (!dialog.open) dialog.showModal();
      dialog.querySelector("button").onclick = async () => {
        dialog.close();
        if (await start()) onReady();
      };
      return false;
    }
  }
  async function refresh() {
    accept(await request("/me"));
    applyNavigation();
  }
  function closeMenu(focus = false) {
    const menu = document.querySelector("#account-menu");
    if (menu) menu.hidden = true;
    document
      .querySelector("#account-trigger")
      ?.setAttribute("aria-expanded", "false");
    if (focus) document.querySelector("#account-trigger")?.focus();
  }
  const trigger = document.querySelector("#account-trigger");
  trigger?.addEventListener("click", () => {
    const menu = document.querySelector("#account-menu");
    menu.hidden = !menu.hidden;
    trigger.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) menu.querySelector("[role=menuitem]")?.focus();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".account-dropdown")) closeMenu();
  });
  document.querySelector("#account-menu")?.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeMenu(true);
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const items = [
        ...document.querySelectorAll("#account-menu [role=menuitem]"),
      ];
      const i = items.indexOf(document.activeElement);
      items[
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? items.length - 1
            : (i + (e.key === "ArrowDown" ? 1 : -1) + items.length) %
              items.length
      ]?.focus();
    }
  });
  document.querySelector("#account-settings")?.addEventListener("click", () => {
    closeMenu();
    if (location.hash === "#administration?own=1") onReady();
    else location.hash = "administration?own=1";
  });
  document
    .querySelector("#open-theme")
    ?.addEventListener("click", () => closeMenu());
  async function logout() {
    try {
      await request("/logout", {});
      document.querySelector("#content")?.replaceChildren();
      closeMenu();
      show();
    } catch (error) {
      toast(error.message);
    }
  }
  document.querySelector("#logout-account")?.addEventListener("click", logout);

  function tabs(items, active, prefix) {
    return `<div class="account-tabs" role="tablist">${items.map(([key, label]) => `<button type="button" role="tab" id="${prefix}-tab-${key}" aria-controls="${prefix}-panel-${key}" aria-selected="${key === active}" tabindex="${key === active ? 0 : -1}" data-tab="${key}">${label}</button>`).join("")}</div>`;
  }
  function bindTabs(root, change) {
    const bar = root.querySelector(".account-tabs");
    const choose = (button) => {
      bar.querySelectorAll("[role=tab]").forEach((b) => {
        b.setAttribute("aria-selected", String(b === button));
        b.tabIndex = b === button ? 0 : -1;
      });
      change(button.dataset.tab);
    };
    bar
      .querySelectorAll("[role=tab]")
      .forEach((b) => (b.onclick = () => choose(b)));
    bar.onkeydown = (e) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      const buttons = [...bar.querySelectorAll("[role=tab]")],
        i = buttons.indexOf(document.activeElement);
      const b =
        buttons[
          e.key === "Home"
            ? 0
            : e.key === "End"
              ? buttons.length - 1
              : (i + (e.key === "ArrowRight" ? 1 : -1) + buttons.length) %
                buttons.length
        ];
      choose(b);
      b.focus();
    };
  }
  function permissionFields(resources, selected) {
    return `<div class="table-wrap permission-table"><table><thead><tr><th>页面 / 功能</th><th>访问</th><th>操作</th></tr></thead><tbody>${Object.entries(
      resources,
    )
      .map(
        ([key, label]) =>
          `<tr><th scope="row">${esc(label)}</th>${["read", "write"].map((action) => `<td><input type="checkbox" name="permissions" aria-label="${esc(label)}${action === "read" ? "访问" : "操作"}" value="${key}:${action}" ${selected.includes(`${key}:${action}`) ? "checked" : ""}></td>`).join("")}</tr>`,
      )
      .join("")}</tbody></table></div>`;
  }
  function syncPermissionChecks(root) {
    root.querySelectorAll("[name=permissions]").forEach((input) =>
      input.addEventListener("change", () => {
        const [resource, action] = input.value.split(":");
        if (action === "write" && input.checked)
          root.querySelector(`[value="${resource}:read"]`).checked = true;
        if (action === "read" && !input.checked)
          root.querySelector(`[value="${resource}:write"]`).checked = false;
      }),
    );
  }
  async function management(root, q = "", offset = 0) {
    root.textContent = "正在加载账户…";
    const own =
      new URLSearchParams(location.hash.split("?")[1] || "").get("own") === "1";
    const data = await request(
      "/admin?" + new URLSearchParams({ q, offset, own }),
    );
    const isAdmin = user.role === "admin",
      canManage = isAdmin || user.permissions.includes("administration:write");
    const names = { ...roleNames, ...data.group_names };
    const groupIds = (account) => account.group_ids || [account.role];
    const groupLabel = (account) =>
      account.role === "admin"
        ? names.admin
        : groupIds(account)
            .map(
              (id) =>
                names[id] +
                (id === "member" && account.effective_role === "user"
                  ? "（已到期）"
                  : ""),
            )
            .join("、");
    root.innerHTML = `<section class="panel account-admin">${tabs(
      [
        ["users", "账户管理"],
        ...(isAdmin
          ? [
              ["roles", "角色权限"],
              ["audit", "操作日志"],
            ]
          : []),
      ],
      "users",
      "accounts",
    )}
      <div id="accounts-panel-users" role="tabpanel" aria-labelledby="accounts-tab-users">
      ${filterBar({ id: "user-search", submit: "搜索", values: { q }, fields: [{ name: "q", label: "搜索账号", options: data.users.map((u) => u.username) }], actions: own && can("administration") ? '<a href="#administration">查看全部账户</a>' : "" })}
      ${isAdmin ? '<div class="auth-actions"><button type="button" id="add-account">新增账户</button></div>' : ""}
      <div class="table-wrap"><table><thead><tr><th>账号</th><th>用户组</th><th>状态</th><th>会员到期</th><th>权限来源</th><th>操作</th></tr></thead><tbody>${data.users.map((u) => `<tr><td>${esc(u.username)}${u.id === user.id ? ' <span class="sub">（当前账户）</span>' : ""}</td><td data-account-groups="${u.id}">${esc(groupLabel(u))}</td><td>${u.enabled ? "启用" : "停用"}</td><td>${u.member_until ? new Date(u.member_until * 1000).toLocaleString() : "—"}</td><td>${u.role === "admin" ? "全部权限" : u.override === null ? "用户组授权" : "单独配置"}</td><td>${u.id === user.id || canManage ? `<button data-user="${u.id}" class="secondary">编辑</button>` : "仅查看"}</td></tr>`).join("")}</tbody></table></div>
      ${pagination({ id: "users-pages", offset, limit: 100, total: data.total, prevId: "users-prev", nextId: "users-next" })}</div>
      ${isAdmin ? '<div id="accounts-panel-roles" role="tabpanel" aria-labelledby="accounts-tab-roles" hidden></div><div id="accounts-panel-audit" role="tabpanel" aria-labelledby="accounts-tab-audit" hidden></div>' : ""}</section>`;
    const reload = () => management(root, q, offset);
    root.querySelector("#add-account")?.addEventListener("click", () =>
      edit({
        username: "",
        role: "user",
        override: null,
        enabled: true,
        permissions: data.roles.user,
      }),
    );
    bindPagination(root, "users-pages", (start) => management(root, q, start));
    root.querySelector("#user-search").onsubmit = (e) => {
      e.preventDefault();
      management(root, new FormData(e.target).get("q")).catch((e) =>
        toast(e.message),
      );
    };
    bindTabs(root, (tab) => {
      root.querySelectorAll("[role=tabpanel]").forEach((p) => {
        p.hidden = p.id !== `accounts-panel-${tab}`;
      });
      if (tab === "roles") renderRoles();
      if (tab === "audit") renderAudit().catch((e) => toast(e.message));
    });
    async function renderAudit() {
      const target = root.querySelector("#accounts-panel-audit");
      target.textContent = "正在加载…";
      const rows = await request("/admin/audit");
      const labels = {
        initialize: "初始化超管",
        register: "注册",
        login: "登录",
        profile: "修改账密",
        recover: "恢复账户",
        rotate_recovery_code: "更新恢复码",
        operator_recover_admin: "服务器应急重置",
        update_user: "编辑账户",
        create_user: "新增账户",
        registration_settings: "注册设置",
      };
      for (const id of Object.keys(data.roles)) {
        labels["create_role_" + id] = "新增用户组 · " + names[id];
        labels["role_" + id] = "编辑用户组 · " + names[id];
      }
      target.innerHTML = `<p class="sub">最近 200 条安全操作</p><div class="table-wrap"><table><thead><tr><th>开始时间</th><th>结束时间</th><th>操作人</th><th>动作</th><th>目标</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${new Date(r.created_at * 1000).toLocaleString()}</td><td>${new Date((r.ended_at ?? r.created_at) * 1000).toLocaleString()}</td><td>${esc(r.actor || "服务器运维")}</td><td>${esc(labels[r.action] || r.action)}</td><td>${esc(r.target || "—")}</td></tr>`).join("")}</tbody></table></div>`;
    }
    function renderRoles(selected = "user") {
      const target = root.querySelector("#accounts-panel-roles");
      target.innerHTML = `<div class="auth-actions"><label class="auth-check"><input id="registration-enabled" type="checkbox" ${data.registration_enabled ? "checked" : ""}>允许外部用户注册</label></div><form id="role-editor"><div class="auth-actions"><label>用户组<select name="role">${Object.keys(
        data.roles,
      )
        .map((id) => `<option value="${id}">${esc(names[id])}</option>`)
        .join(
          "",
        )}<option value="">＋ 新增用户组</option></select></label><label>组名称<input name="group_name" maxlength="64" required></label></div><p class="sub">操作权限包含访问权限。选择多个用户组时，合并各组权限。</p><div id="role-permissions"></div><button>保存用户组</button><p role="alert"></p></form>`;
      const form = target.querySelector("form");
      form.elements.role.value = selected;
      const sync = () => {
        form.elements.group_name.value = names[form.elements.role.value] || "";
        form.querySelector("[role=alert]").textContent = "";
        target.querySelector("#role-permissions").innerHTML = permissionFields(
          data.resources,
          data.roles[form.elements.role.value] || [],
        );
        syncPermissionChecks(form);
      };
      sync();
      form.elements.role.onchange = sync;
      target.querySelector("#registration-enabled").onchange = async (e) => {
        try {
          await request(
            "/admin/settings",
            { registration_enabled: e.target.checked },
            "PUT",
          );
          data.registration_enabled = e.target.checked;
          toast("注册设置已保存");
        } catch (error) {
          e.target.checked = !e.target.checked;
          toast(error.message);
        }
      };
      form.onsubmit = async (e) => {
        e.preventDefault();
        const button = form.querySelector("button");
        button.disabled = true;
        try {
          const value = {
            name: form.elements.group_name.value.trim(),
            permissions: new FormData(form).getAll("permissions"),
          };
          const id = form.elements.role.value;
          const saved = await request(
            id ? "/admin/roles/" + id : "/admin/roles",
            value,
            id ? "PUT" : "POST",
          );
          data.roles[saved.role] = saved.permissions;
          names[saved.role] = saved.name;
          data.users.forEach((u) => {
            root.querySelector(`[data-account-groups="${u.id}"]`).textContent =
              groupLabel(u);
          });
          renderRoles(saved.role);
          toast("用户组已保存");
        } catch (error) {
          form.querySelector("[role=alert]").textContent = error.message;
        } finally {
          button.disabled = false;
        }
      };
    }
    function edit(target) {
      document.querySelector("#account-editor-dialog")?.remove();
      const ownAccount = target.id === user.id,
        editablePermissions = !ownAccount && target.role !== "admin";
      const creating = !target.id;
      const delegatedRole =
        !isAdmin &&
        groupIds(target).length === 1 &&
        ["user", "member"].includes(groupIds(target)[0]);
      const memberships = isAdmin
        ? `<fieldset class="membership-options"><legend>用户组（可多选）</legend>${Object.keys(
            data.roles,
          )
            .map(
              (id) =>
                `<label class="auth-check"><input type="checkbox" name="group_ids" value="${id}" ${groupIds(target).includes(id) ? "checked" : ""}>${esc(names[id])}</label>`,
            )
            .join("")}</fieldset>`
        : delegatedRole
          ? `<label>用户组<select name="role">${["user", "member"].map((id) => `<option value="${id}">${esc(names[id])}</option>`).join("")}</select></label>`
          : `<p>用户组：${esc(groupLabel(target))}</p>`;
      const modal = document.createElement("dialog");
      modal.id = "account-editor-dialog";
      modal.className = "account-dialog";
      modal.setAttribute("aria-labelledby", "account-editor-title");
      modal.innerHTML = `<header class="account-dialog-heading"><h2 id="account-editor-title">${creating ? "新增账户" : "编辑账户 · " + esc(target.username)}</h2><button type="button" id="close-account-editor" class="secondary" aria-label="关闭账户窗口">关闭</button></header>
      ${tabs([["details", "基本信息"], ["permissions", "页面权限"], ...(ownAccount ? [["security", "安全设置"]] : [])], "details", "edit-account")}
      <form class="account-editor"><div id="edit-account-panel-details" role="tabpanel" aria-labelledby="edit-account-tab-details">
      ${field("账号", "username", "text", "username")}
      ${ownAccount ? field("当前密码", "current_password", "password", "current-password", false) : `${memberships}<label>会员到期（留空为长期）<input type="datetime-local" name="member_until"></label><label class="auth-check"><input type="checkbox" name="enabled" ${target.enabled ? "checked" : ""}>启用账户</label>`}
      ${field(creating ? "密码（10–128 位）" : "新密码（留空则不修改）", "password", "password", "new-password", creating)}${field("确认新密码", "confirm_password", "password", "new-password", creating)}</div>
      <div id="edit-account-panel-permissions" role="tabpanel" aria-labelledby="edit-account-tab-permissions" hidden>
      <p class="sub">${ownAccount ? "自己的身份与权限由超管配置。" : isAdmin ? "单独配置优先于角色权限。" : "只能分配自己已有的权限，账户管理授权由超管配置。"}</p>
      ${editablePermissions ? `<label class="auth-check"><input type="checkbox" name="inherit" ${target.override === null ? "checked" : ""}>继承所选用户组的合并权限</label>` : ""}
      ${permissionFields(data.resources, target.override || target.permissions)}</div>
      ${ownAccount ? `<div id="edit-account-panel-security" role="tabpanel" aria-labelledby="edit-account-tab-security" hidden><h3>账户恢复码</h3><p>旧恢复码无法再次读取。验证当前密码后可生成新码并下载保存，旧码立即失效。</p>${field("验证当前密码", "recovery_password", "password", "current-password", false)}<button type="button" id="rotate-recovery-code">重新生成恢复码</button>${isAdmin ? '<p class="sub">如果账号、密码及恢复码全部遗失，可由有服务器权限的运维人员执行应急重置，无需旧凭据。</p>' : ""}</div>` : ""}
      <p role="alert"></p><footer class="account-editor-actions"><button type="submit" id="save-account">保存设置</button><button type="button" id="cancel-account-editor" class="secondary">取消</button></footer></form>`;
      document.body.append(modal);
      const form = modal.querySelector("form");
      form.elements.username.value = target.username;
      if (!ownAccount) {
        if (form.elements.role) form.elements.role.value = target.role;
        if (target.member_until) {
          const d = new Date(target.member_until * 1000);
          d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
          form.elements.member_until.value = d.toISOString().slice(0, 16);
        }
      }
      const close = () => {
        modal.close();
        modal.remove();
      };
      modal.querySelector("#close-account-editor").onclick = close;
      modal.querySelector("#cancel-account-editor").onclick = close;
      modal.addEventListener("close", () => modal.remove());
      bindTabs(modal, (tab) => {
        modal.querySelectorAll("[role=tabpanel]").forEach((p) => {
          p.hidden = p.id !== `edit-account-panel-${tab}`;
        });
        modal.querySelector("#save-account").hidden =
          tab === "security" || (ownAccount && tab === "permissions");
      });
      function selectedGroups() {
        if (isAdmin && !ownAccount)
          return new FormData(form).getAll("group_ids");
        return form.elements.role
          ? [form.elements.role.value]
          : groupIds(target);
      }
      const sync = () => {
        const selected = selectedGroups();
        const expiry = form.elements.member_until;
        if (expiry) expiry.disabled = !selected.includes("member");
        const expired =
          expiry?.value && new Date(expiry.value).getTime() <= Date.now();
        const inherited = new Set(
          selected.flatMap(
            (id) => data.roles[id === "member" && expired ? "user" : id] || [],
          ),
        );
        form.querySelectorAll("[name=permissions]").forEach((el) => {
          if (form.elements.inherit?.checked)
            el.checked = inherited.has(el.value);
          el.disabled =
            !editablePermissions ||
            form.elements.inherit?.checked ||
            (!isAdmin &&
              (!user.permissions.includes(el.value) ||
                el.value.startsWith("administration:")));
        });
      };
      form
        .querySelectorAll("[name=group_ids], [name=role], [name=member_until]")
        .forEach((el) => el.addEventListener("change", sync));
      if (form.elements.inherit) form.elements.inherit.onchange = sync;
      sync();
      syncPermissionChecks(form);
      modal
        .querySelector("#rotate-recovery-code")
        ?.addEventListener("click", async (e) => {
          e.target.disabled = true;
          try {
            const result = await request("/recovery-code", {
              current_password: form.elements.recovery_password.value,
            });
            form.elements.recovery_password.value = "";
            close();
            showCode(result, () => dialog.close());
          } catch (error) {
            form.querySelector("[role=alert]").textContent = error.message;
          } finally {
            e.target.disabled = false;
          }
        });
      form.onsubmit = async (e) => {
        e.preventDefault();
        const button = modal.querySelector("#save-account");
        button.disabled = true;
        try {
          const values = new FormData(form);
          if (values.get("password") !== values.get("confirm_password"))
            throw Error("两次输入的密码不一致");
          const payload = {
            username: values.get("username"),
            password: values.get("password"),
          };
          if (ownAccount)
            payload.current_password = values.get("current_password");
          else
            Object.assign(payload, {
              ...(isAdmin
                ? { group_ids: selectedGroups() }
                : form.elements.role
                  ? { role: values.get("role") }
                  : {}),
              member_until: values.get("member_until")
                ? new Date(values.get("member_until")).getTime() / 1000
                : null,
              enabled: values.has("enabled"),
              permissions: values.has("inherit")
                ? null
                : [...form.querySelectorAll("[name=permissions]:checked")].map(
                    (el) => el.value,
                  ),
            });
          if (payload.group_ids && !payload.group_ids.length)
            throw Error("请选择至少一个用户组");
          const result = await request(
            creating ? "/admin/users" : "/admin/users/" + target.id,
            payload,
            creating ? "POST" : "PUT",
          );
          form.reset();
          if (ownAccount) {
            close();
            root.replaceChildren();
            show();
            return;
          }
          toast("账户设置已保存");
          await reload().catch((error) =>
            toast("账户已保存，列表刷新失败：" + error.message),
          );
          close();
          if (result.recovery_code)
            showCode({ ...result, username: payload.username }, () =>
              dialog.close(),
            );
        } catch (error) {
          form.querySelector("[role=alert]").textContent = error.message;
        } finally {
          button.disabled = false;
        }
      };
      modal.showModal();
    }
    root
      .querySelectorAll("[data-user]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            edit(data.users.find((u) => u.id === Number(b.dataset.user)))),
      );
  }
  return {
    start,
    refresh,
    can,
    show,
    logout,
    management,
    get user() {
      return user;
    },
    headers: () => ({ "X-CSRF-Token": csrf }),
  };
}

export function routeResource(name) {
  const part = name.split("/")[0];
  return { app: "apps", keyword: "keywords" }[part] || part;
}
