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
    if (resource === "account") return true;
    if (resource === "administration") return user.role === "admin";
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
    document.querySelectorAll("[data-nav]").forEach((a) => {
      a.hidden = !can(a.dataset.nav);
    });
  }
  function showCode(data, done) {
    dialog.innerHTML = `<section class="auth-card"><p class="auth-eyebrow">衡枢 · 观星</p><h1>请保存账户恢复码</h1>
      <p>账号：<strong>${esc(data.username || "")}</strong></p><p>忘记账号或密码时可使用此码找回。它仅在此处显示，请保存在安全位置，勿转发。</p>
      <textarea readonly aria-label="账户恢复码" rows="3">${esc(data.recovery_code)}</textarea>
      <label class="auth-check"><input type="checkbox" id="saved-code">我已安全保存恢复码</label>
      <button id="code-done" disabled>继续</button></section>`;
    if (!dialog.open) dialog.showModal();
    dialog.querySelector("#saved-code").onchange = (e) => {
      dialog.querySelector("#code-done").disabled = !e.target.checked;
    };
    dialog.querySelector("#code-done").onclick = done;
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
  async function account(root) {
    root.innerHTML = `<section class="panel account-panel"><h2>我的账户</h2><p>${esc(user.username)} · ${roleNames[user.effective_role]}${user.member_until ? " · 会员到期 " + new Date(user.member_until * 1000).toLocaleString() : ""}</p>
      <p>修改账号或密码后需要重新登录；忘记凭据可在登录页使用恢复码。</p><form id="profile-form">
      ${field("新账号", "username", "text", "username")}${field("当前密码", "current_password", "password", "current-password")}
      ${field("新密码（留空则不修改）", "password", "password", "new-password", false)}
      ${field("确认新密码", "confirm_password", "password", "new-password", false)}<button>保存并重新登录</button><p role="alert"></p></form>
      <button id="logout-account" class="secondary">退出登录</button></section>`;
    root.querySelector("[name=username]").value = user.username;
    root.querySelector("form").onsubmit = async (event) => {
      event.preventDefault();
      const form = event.target,
        value = Object.fromEntries(new FormData(form));
      const button = form.querySelector("button");
      button.disabled = true;
      try {
        if (value.password !== value.confirm_password)
          throw Error("两次输入的密码不一致");
        await request("/profile", value, "PUT");
        form.reset();
        root.replaceChildren();
        show();
      } catch (error) {
        form.querySelector("[role=alert]").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
    root.querySelector("#logout-account").onclick = async () => {
      try {
        await request("/logout", {});
        root.replaceChildren();
        show();
      } catch (error) {
        toast(error.message);
      }
    };
  }
  function permissionFields(resources, selected) {
    return `<div class="permission-grid">${Object.entries(resources)
      .map(
        ([key, label]) =>
          `<fieldset><legend>${esc(label)}</legend>${["read", "write"].map((action) => `<label class="auth-check"><input type="checkbox" name="permissions" value="${key}:${action}" ${selected.includes(`${key}:${action}`) ? "checked" : ""}>${action === "read" ? "查看" : "操作"}</label>`).join("")}</fieldset>`,
      )
      .join("")}</div>`;
  }
  async function management(root, q = "", offset = 0) {
    const data = await request(
      "/admin?q=" + encodeURIComponent(q) + "&offset=" + offset,
    );
    root.innerHTML = `<section class="panel account-admin"><h2>账户与权限</h2><p>注册用户默认为普通用户。会员到期后自动使用普通用户权限；单独配置的用户权限持续有效。操作权限自动包含查看权限。</p>
      <label class="auth-check"><input id="registration-enabled" type="checkbox" ${data.registration_enabled ? "checked" : ""}>允许外部用户注册</label>
      <h3>默认角色权限</h3><div class="auth-actions"><button data-role="user" class="secondary">配置普通用户</button><button data-role="member" class="secondary">配置会员</button><button id="show-auth-audit" class="secondary">安全操作记录</button></div>
      <form id="user-search" class="auth-actions"><input name="q" aria-label="搜索账号" placeholder="搜索账号" value="${esc(q)}"><button>搜索</button></form>
      <div class="table-wrap"><table><thead><tr><th>账号</th><th>类型</th><th>状态</th><th>会员到期</th><th>权限来源</th><th>管理</th></tr></thead><tbody>${data.users.map((u) => `<tr><td>${esc(u.username)}</td><td>${roleNames[u.effective_role]}</td><td>${u.enabled ? "启用" : "停用"}</td><td>${u.member_until ? new Date(u.member_until * 1000).toLocaleString() : "—"}</td><td>${u.override === null ? "角色默认" : "单独配置"}</td><td>${u.role !== "admin" ? `<button data-user="${u.id}" class="secondary">编辑</button>` : "个人账户中修改"}</td></tr>`).join("")}</tbody></table></div>
      <div class="auth-actions"><button id="users-prev" ${offset ? "" : "disabled"} class="secondary">上一页</button><span>共 ${data.total} 个账户</span><button id="users-next" ${offset + 100 < data.total ? "" : "disabled"} class="secondary">下一页</button></div><div id="account-editor"></div></section>`;
    const reload = () => management(root, q, offset);
    root.querySelector("#users-prev").onclick = () =>
      management(root, q, Math.max(0, offset - 100)).catch((e) =>
        toast(e.message),
      );
    root.querySelector("#users-next").onclick = () =>
      management(root, q, offset + 100).catch((e) => toast(e.message));
    root.querySelector("#user-search").onsubmit = (e) => {
      e.preventDefault();
      management(root, new FormData(e.target).get("q")).catch((error) =>
        toast(error.message),
      );
    };
    root.querySelector("#registration-enabled").onchange = async (e) => {
      try {
        await request(
          "/admin/settings",
          { registration_enabled: e.target.checked },
          "PUT",
        );
        toast("注册设置已保存");
      } catch (error) {
        e.target.checked = !e.target.checked;
        toast(error.message);
      }
    };
    root.querySelector("#show-auth-audit").onclick = async () => {
      try {
        const rows = await request("/admin/audit");
        root.querySelector("#account-editor").innerHTML =
          `<h3>最近 200 条安全操作</h3><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>目标</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${new Date(r.created_at * 1000).toLocaleString()}</td><td>${esc(r.actor || "—")}</td><td>${esc(r.action)}</td><td>${esc(r.target || "—")}</td></tr>`).join("")}</tbody></table></div>`;
      } catch (error) {
        toast(error.message);
      }
    };
    function edit(role, target) {
      const editor = root.querySelector("#account-editor");
      editor.innerHTML = `<form class="account-editor"><h3>${target ? "编辑账户 · " + esc(target.username) : "配置" + roleNames[role]}</h3>
        ${
          target
            ? `${field("账号", "username", "text", "off")}<label>账户类型<select name="role"><option value="user">普通用户</option><option value="member">会员</option></select></label>
        <label>会员到期（留空为长期）<input type="datetime-local" name="member_until"></label><label class="auth-check"><input type="checkbox" name="enabled" ${target.enabled ? "checked" : ""}>启用账户</label>
        ${field("重置密码（留空则不修改）", "password", "password", "new-password", false)}
        <label class="auth-check"><input type="checkbox" name="inherit" ${target.override === null ? "checked" : ""}>使用角色默认权限（取消后可单独配置）</label>`
            : ""
        }
        <div class="permission-fields">${permissionFields(data.resources, target ? target.override || target.permissions : data.roles[role])}</div><button>保存设置</button><p role="alert"></p></form>`;
      const form = editor.querySelector("form");
      if (target) {
        form.elements.username.value = target.username;
        form.elements.role.value = target.role;
        if (target.member_until) {
          const d = new Date(target.member_until * 1000);
          d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
          form.elements.member_until.value = d.toISOString().slice(0, 16);
        }
        const sync = () =>
          form.querySelectorAll("[name=permissions]").forEach((el) => {
            el.disabled = form.elements.inherit.checked;
          });
        form.elements.inherit.onchange = sync;
        sync();
      }
      form.onsubmit = async (event) => {
        event.preventDefault();
        const values = new FormData(form),
          button = form.querySelector("button");
        button.disabled = true;
        try {
          const payload = { permissions: values.getAll("permissions") };
          if (target)
            Object.assign(payload, {
              username: values.get("username"),
              role: values.get("role"),
              enabled: values.has("enabled"),
              member_until: values.get("member_until")
                ? new Date(values.get("member_until")).getTime() / 1000
                : null,
              password: values.get("password"),
              permissions: values.has("inherit") ? null : payload.permissions,
            });
          const result = await request(
            target ? "/admin/users/" + target.id : "/admin/roles/" + role,
            payload,
            "PUT",
          );
          if (result.recovery_code)
            showCode({ ...result, username: payload.username }, () =>
              dialog.close(),
            );
          toast("设置已保存");
          await reload();
        } catch (error) {
          form.querySelector("[role=alert]").textContent = error.message;
        } finally {
          button.disabled = false;
        }
      };
      editor.scrollIntoView({ block: "nearest" });
    }
    root.querySelectorAll("[data-role]").forEach((b) => {
      b.onclick = () => edit(b.dataset.role);
    });
    root.querySelectorAll("[data-user]").forEach((b) => {
      b.onclick = () =>
        edit(
          null,
          data.users.find((u) => u.id === Number(b.dataset.user)),
        );
    });
  }
  return {
    start,
    refresh,
    can,
    show,
    account,
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
