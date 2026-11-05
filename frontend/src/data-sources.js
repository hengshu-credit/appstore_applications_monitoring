import { marketDataPanel, bindMarketData } from "./marketdata.js";

export function createDataSourcePage({
  $,
  api,
  esc,
  panel,
  table,
  mount,
  toast,
}) {
  return async (revision) => {
    const data = await api("/data-sources"),
      state = data.community,
      brain = data.directory || { configured: false, message: "未配置账户" };
    const metricsLabels = {
      total_installs: "来源累计安装（全球）",
      installs: "累计安装档位下限",
      installs_reported: "商店累计安装观察",
      monthly_active_users_estimate: "月活跃用户估算",
      monthly_revenue_estimate: "月收入估算",
      weekly_install_change: "近周安装变化",
      monthly_install_change: "近月安装变化",
      monthly_downloads_estimate: "近30天下载估算",
    };
    if (
      !mount(
        `<p class="lead">管理数据接入授权、采集计划与文件导入。</p>${panel("公开指标接入", `<p id="provider-state" role="status">${esc(state.message)}${state.checked_at ? " · " + new Date(state.checked_at * 1000).toLocaleString() : ""}</p><form id="provider-account" class="toolbar"><label>账户邮箱<input type="email" name="email" required autocomplete="off" placeholder="${state.configured ? "已保存，更换账户时填写" : "公开元数据 登录邮箱"}"></label><label>密码<input type="password" name="password" required autocomplete="new-password"></label><button type="submit">保存并自动授权</button><button type="button" id="provider-authorize" class="secondary" ${state.configured ? "" : "disabled"}>检查 / 续期授权</button><button type="button" id="provider-datasets" class="secondary" ${state.configured ? "" : "disabled"}>读取免费数据目录</button></form><p class="sub">账密与会话加密保存在服务器，密码不回显。普通密码登录可自动授权；邮箱验证、验证码或二次验证需在 公开元数据 完成。授权不自动开通付费服务。</p><div id="provider-result"></div>`)}${panel("安装指标接入", `<p id="brain-state" role="status">${esc(brain.message)}</p><form id="brain-account" class="toolbar"><label>账户邮箱<input name="email" type="email" required autocomplete="off" placeholder="${brain.configured ? "已保存，更换时填写" : "安装指标 登录邮箱"}"></label><label>密码<input name="password" type="password" required autocomplete="new-password"></label><button type="submit">保存并自动授权</button><button type="button" id="brain-authorize" class="secondary" ${brain.configured ? "" : "disabled"}>检查 / 续期授权</button></form><p class="sub">使用 安装指标 自身的邮箱密码；第三方单点登录和验证码需在来源网站完成。免费账户仅获取实际开放的数据。</p>`)}${panel("获取应用公开指标", `<form id="source-app-query" class="toolbar"><label>优先来源<select name="provider"><option value="auto">自动兜底</option><option value="community">公开元数据</option><option value="directory">安装指标</option></select></label><label>商店<select name="store"><option value="google">Google Play</option><option value="apple">App Store</option></select></label><label class="wide">应用 ID<input name="app_id" required placeholder="com.openai.chatgpt 或 6448311069"></label><button>获取来源快照</button></form><p class="sub">优先使用 公开元数据；失败时检查 Android 备用来源。每个来源保留独立口径和缺失字段。</p><div id="source-app-result"></div>`)}${panel(
          "可用来源与兜底范围",
          table(
            ["来源", "访问方式", "可提供", "限制"],
            data.catalog.map((s) => [
              esc(s.name),
              esc(s.access),
              esc(s.capabilities),
              esc(s.limits),
            ]),
          ),
        )}${marketDataPanel(panel, esc, data.marketdata)}`,
        revision,
      )
    )
      return;
    const root = $("#provider-account").closest("main");
    bindMarketData({ $, api, esc, table, toast, root });
    const update = (result) => {
      if (!root.querySelector("#provider-state")) return;
      root.querySelector("#provider-state").textContent =
        result.community.message;
      root.querySelector("#provider-authorize").disabled =
        !result.community.configured;
      root.querySelector("#provider-datasets").disabled =
        !result.community.configured;
    };
    const run = async (button, work) => {
      button.disabled = true;
      try {
        await work();
      } catch (e) {
        toast(e.message);
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    };
    $("#provider-account").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget,
        button = form.querySelector('[type="submit"]');
      const value = {
        email: form.elements.email.value,
        password: form.elements.password.value,
      };
      form.elements.password.value = "";
      run(button, async () => {
        try {
          const result = await api("/data-sources/community", value, "PUT");
          if (form.isConnected) update(result);
        } finally {
          value.password = "";
        }
      });
    };
    $("#provider-authorize").onclick = (event) =>
      run(event.currentTarget, async () => {
        const result = await api("/data-sources/community/authorize", {});
        if (event.target.isConnected) update(result);
      });
    $("#provider-datasets").onclick = (event) =>
      run(event.currentTarget, async () => {
        const result = await api("/data-sources/community/datasets");
        if (event.target.isConnected)
          $("#provider-result").innerHTML = `<p>${esc(result.note)}</p>${table(
            ["可用文件"],
            result.files.map((f) => [esc(f)]),
          )}`;
      });
    const updateBrain = (result) => {
      $("#brain-state").textContent = result.directory.message;
      $("#brain-authorize").disabled = !result.directory.configured;
    };
    $("#brain-account").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget,
        value = {
          email: form.elements.email.value,
          password: form.elements.password.value,
        };
      form.elements.password.value = "";
      run(form.querySelector('[type="submit"]'), async () => {
        try {
          const result = await api("/data-sources/directory", value, "PUT");
          if (form.isConnected) updateBrain(result);
        } finally {
          value.password = "";
        }
      });
    };
    $("#brain-authorize").onclick = (event) =>
      run(event.currentTarget, async () => {
        const result = await api("/data-sources/directory/authorize", {});
        if (event.target.isConnected) updateBrain(result);
      });
    $("#source-app-query").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      $("#source-app-result").textContent = "正在获取所选应用的来源快照…";
      run(form.querySelector("button"), async () => {
        const result = await api("/data-sources/app-metrics", {
          provider: form.elements.provider.value,
          store: form.elements.store.value,
          app_id: form.elements.app_id.value,
        }).catch((error) => {
          if (form.isConnected)
            $("#source-app-result").textContent =
              "本次未获取到数据，请检查来源授权和网络状态。";
          throw error;
        });
        if (!form.isConnected) return;
        $("#source-app-result").innerHTML =
          `<p>${esc(result.app_id)} · ${esc(result.source)} · ${result.scope === "worldwide" ? "全球口径" : esc(result.scope)} · ${new Date(result.observed_at * 1000).toLocaleString()}</p>${table(
            ["指标", "来源显示值"],
            Object.entries(result.values_display).map(([k, v]) => [
              esc(metricsLabels[k] || k),
              esc(v),
            ]),
          )}<p>${esc(result.note)}</p>`;
      });
    };
  };
}
