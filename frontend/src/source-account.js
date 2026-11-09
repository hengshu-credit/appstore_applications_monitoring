const eye = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="eye-slash" d="m3 3 18 18"/></svg>`;

export function secretField(esc, name, label, value = "", required = false) {
  return `<label>${esc(label)}<span class="source-secret"><input name="${name}" type="password" autocomplete="new-password" value="${esc(value)}" ${required ? "required" : ""}><button type="button" class="source-secret-toggle" aria-label="显示${esc(label)}" aria-pressed="false" title="显示${esc(label)}" data-secret-label="${esc(label)}">${eye}</button></span></label>`;
}

export function bindSecretFields(root) {
  for (const button of root.querySelectorAll(".source-secret-toggle")) {
    button.onclick = () => {
      const input = button.previousElementSibling;
      const visible = input.type === "password";
      input.type = visible ? "text" : "password";
      button.setAttribute("aria-pressed", String(visible));
      button.title = button
        .getAttribute("aria-label")
        .replace(/^(显示|隐藏)/, visible ? "隐藏" : "显示");
      button.setAttribute("aria-label", button.title);
    };
  }
}

export function accountSummary(state = {}) {
  if (!state.configured) return "未保存凭据 · 填写后点击保存";
  const messages = {
    saved: "等待授权检查",
    authorized: "授权有效",
    authorizing: "正在授权，请稍后检查",
    invalid_credentials: "授权失败，请检查邮箱和密码后重新保存",
    action_required:
      "授权未完成：需在来源网站完成邮箱、验证码或二次验证；完成后回到这里重新检查授权。网页登录状态不会自动同步，若仍需验证，当前接入方式无法自动完成登录",
    api_token_required:
      "API 未授权，请填写 Personal Access Token；仅邮箱密码不能启用 API 采集",
    endpoint_changed: "地址已变更，请确认地址并重新保存凭据后授权",
    key_unavailable: "凭据无法读取，请恢复服务器原密钥后重试",
  };
  const retry =
    state.retry_after > Date.now() / 1000 && state.status === "rate_limited"
      ? `；可在 ${new Date(state.retry_after * 1000).toLocaleString()} 后重试`
      : "";
  return `凭据已保存 · ${messages[state.status] || state.message || "等待授权检查"}${retry}`;
}

export function bindSourceAccount({
  root,
  prefix,
  provider,
  api,
  onUpdate = () => {},
}) {
  const form = root.querySelector(`#${prefix}-account`);
  const status = root.querySelector(`#${prefix}-state`);
  const authorize = root.querySelector(`#${prefix}-authorize`);
  const submit = form.querySelector('[type="submit"]');
  const fields = [...form.querySelectorAll("input")];
  let busy = false,
    dirty = false,
    configured = !authorize.disabled;
  bindSecretFields(form);
  const refreshButtons = () => {
    submit.disabled = busy;
    authorize.disabled = busy || dirty || !configured;
  };
  const update = (data) => {
    if (!form.isConnected) return;
    configured = data[provider].configured;
    status.textContent = accountSummary(data[provider]);
    onUpdate(data);
    refreshButtons();
  };
  form.oninput = () => {
    dirty = true;
    status.textContent = "有未保存的修改 · 点击“保存并检查授权”后生效";
    refreshButtons();
  };
  const run = async (save) => {
    if (busy) return;
    busy = true;
    let saved = false;
    const label = submit.textContent;
    fields.forEach((input) => {
      input.readOnly = true;
    });
    form.setAttribute("aria-busy", "true");
    refreshButtons();
    try {
      if (save) {
        status.textContent = "正在保存凭据…";
        submit.textContent = "正在保存…";
        const values = Object.fromEntries(
          fields.map((input) => [
            input.name,
            input.name === "email" ? input.value.trim() : input.value,
          ]),
        );
        const data = await api(
          `/data-sources/${provider}?authorize_now=false`,
          values,
          "PUT",
        );
        saved = true;
        dirty = false;
        update(data);
      }
      status.textContent = "凭据已保存 · 正在连接来源检查授权，请稍候…";
      submit.textContent = "正在检查授权…";
      update(await api(`/data-sources/${provider}/authorize`, {}));
    } catch (error) {
      if (form.isConnected)
        status.textContent = `${save && !saved ? "保存未确认成功，填写内容已保留，请重试" : "凭据已保存，本次授权检查未完成，可点击重新检查授权"}：${error.message}`;
    } finally {
      busy = false;
      if (form.isConnected) {
        fields.forEach((input) => {
          input.readOnly = false;
        });
        form.setAttribute("aria-busy", "false");
        submit.textContent = label;
        refreshButtons();
      }
    }
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    return run(true);
  };
  authorize.onclick = () => run(false);
  return update;
}
