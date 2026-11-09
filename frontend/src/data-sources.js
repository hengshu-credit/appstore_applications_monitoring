import {
  secretField,
  bindSourceAccount,
  accountSummary,
} from "./source-account.js";
import { sourceGuide, sourceTitle, sourceAddress } from "./source-guidance.js";
import { filterBar, bindFilterBar } from "./filter-bar.js";
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
    const [data, credentials] = await Promise.all([
      api("/data-sources"),
      api("/data-sources/credentials"),
    ]);
    const accountUpdates = {};
    const state = data.community,
      directory = data.directory || {
        configured: false,
        message: "未配置账户",
      };
    const connection = (id) =>
      (data.connections || []).find((s) => s.id === id) || {
        id,
        name:
          {
            community: "公开元数据",
            directory: "安装指标",
            marketdata: "商业市场数据",
          }[id] || id,
        editable_origin: true,
      };
    const help = (id) =>
      `<div data-source-guide="${id}"><strong>${esc(sourceTitle(connection(id)))}</strong>${sourceGuide(esc, connection(id))}</div>`;
    const sourceTable = () =>
      table(
        [
          "数据源",
          "接入地址 / 仓库",
          "访问方式",
          "数据范围",
          "来源说明与配置入口",
          "凭据",
          "操作",
        ],
        (data.connections || []).map((source) => [
          esc(sourceTitle(source)),
          sourceAddress(esc, source.origin),
          esc(source.access),
          esc(source.fields),
          sourceGuide(esc, source, true),
          ["community", "directory", "marketdata"].includes(source.id)
            ? esc(accountSummary(data[source.id] || source.account))
            : source.account?.configured
              ? "凭据已保存"
              : "无需账户 / 未配置",
          source.editable
            ? `<button type="button" class="link" data-source-edit="${esc(source.id)}">${source.editable_origin ? "配置地址" : "配置"}${source.batch_size ? ` · 每批 ${source.batch_size}` : ""}</button>${["community", "directory", "marketdata"].includes(source.id) ? `<br><button type="button" class="link" data-source-account="${source.id}">配置账户 / 查看授权</button>` : ""}`
            : "内置协议",
        ]),
      );
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
        `${panel("数据源配置", `<p class="sub">官方商店公开接口无需账号。第三方来源先核对接入地址，再在对应区域填写该网站的账户或 Token；这里填写的不是观星平台登录账号。点击“配置地址”只修改接入地址；账户和密码请在对应接入区域填写并保存。第三方网页登录不会自动配置本系统。</p><div id="source-config-list">${sourceTable()}</div><p class="sub">配置保存在后台并实时回显。批量设置在新批次生效；修改接入地址后须重新保存账户凭据。</p>`)}${panel("首轮 24 小时容量", `<div id="initial-capacity">正在核算全库容量…</div>`)}${panel("公开批量数据包", `<div id="bulk-source-imports">正在读取本地数据包…</div>`)}${panel("公开指标接入", `${help("community")}<p id="provider-state" role="status">${esc(accountSummary(state))}${state.checked_at ? " · " + new Date(state.checked_at * 1000).toLocaleString() : ""}</p><form id="provider-account" class="source-account-form"><label>账户邮箱<input type="email" name="email" required autocomplete="off" value="${esc(state.email || "")}" placeholder="${state.configured ? "已保存，更换账户时填写" : "AppGoblin 登录邮箱"}"></label>${secretField(esc, "password", "密码", credentials.community?.password, true)}<div class="source-account-actions"><button type="submit">保存并检查授权</button><button type="button" id="provider-authorize" class="secondary" ${state.configured ? "" : "disabled"}>重新检查授权</button><button type="button" id="provider-datasets" class="secondary">读取免费数据目录</button></div></form><p class="sub">密码加密保存，默认隐藏；点击输入框右侧小眼睛查看。填写后点击保存，页面会分别显示保存结果和授权结果。邮箱验证等步骤需在 AppGoblin 完成，再回此页面重新检查；网页登录状态不会自动同步。</p><div id="provider-result"></div>`)}${panel("安装指标接入", `${help("directory")}<p id="directory-state" role="status">${esc(accountSummary(directory))}</p><form id="directory-account" class="source-account-form"><label>账户邮箱<input name="email" type="email" required autocomplete="off" value="${esc(directory.email || "")}" placeholder="${directory.configured ? "已保存，更换时填写" : "AppBrain 登录邮箱"}"></label>${secretField(esc, "password", "密码", credentials.directory?.password, true)}<div class="source-account-actions"><button type="submit">保存并检查授权</button><button type="button" id="directory-authorize" class="secondary" ${directory.configured ? "" : "disabled"}>重新检查授权</button></div></form><p class="sub">填写 AppBrain 自身的邮箱密码后点击保存；密码可通过右侧小眼睛查看。官网单点登录或验证码不会把浏览器会话同步到本系统；若持续要求验证，当前自动登录方式无法完成授权。</p>`)}${panel(
          "获取应用公开指标",
          `${filterBar({
            id: "source-app-query",
            submit: "获取来源快照",
            resetSubmit: false,
            values: { provider: "auto", store: "google" },
            fields: [
              {
                name: "provider",
                label: "优先来源",
                type: "select",
                options: [
                  ["auto", "自动兜底"],
                  ["community", sourceTitle(connection("community"))],
                  ["directory", sourceTitle(connection("directory"))],
                ],
              },
              {
                name: "store",
                label: "商店",
                type: "store",
                options: [
                  ["google", "Google Play"],
                  ["apple", "App Store"],
                ],
              },
              {
                name: "app_id",
                label: "应用 ID",
                required: true,
                wide: true,
                placeholder: "com.openai.chatgpt 或 6448311069",
                source: "app",
              },
            ],
          })}<p class="sub">优先使用 公开元数据；失败时检查 Android 备用来源。每个来源保留独立口径和缺失字段。</p><div id="source-app-result"></div>`,
        )}${panel(
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
        )}${marketDataPanel(panel, esc, { ...data.marketdata, ...credentials.marketdata }, connection("marketdata"))}`,
        revision,
      )
    )
      return;
    const root = $("#provider-account").closest("main");
    const syncAccounts = (result) => {
      for (const id of ["community", "directory", "marketdata"]) {
        if (!result[id]) continue;
        data[id] = { ...data[id], ...result[id] };
        const row = connection(id);
        row.account = data[id];
      }
      const target = root.querySelector("#source-config-list");
      if (target) target.innerHTML = sourceTable();
    };
    bindMarketData({
      $,
      api,
      esc,
      table,
      toast,
      root,
      onAccountUpdate: syncAccounts,
      onAccountReady: (update) => {
        accountUpdates.marketdata = update;
      },
    });
    const loadCapacity = async () => {
      const capacity = await api("/data-sources/initial-capacity");
      const target = root.querySelector("#initial-capacity");
      if (!target) return;
      target.innerHTML = `<p><strong>全内容 24 小时达标条件尚未满足</strong> · 全库 ${capacity.inventory.reduce((n, r) => n + r.apps, 0).toLocaleString()} 个应用</p><p>单市场详情请求理论下限 <strong>${capacity.detail_requests_lower_bound.toLocaleString()}</strong> 次 · 当前配置至少 <strong>${capacity.detail_hours_lower_bound}</strong> 小时</p><p>磁盘可用 ${capacity.free_gib ?? "—"} GiB · 保护线 ${capacity.minimum_free_gib ?? "—"} GiB · 单份详情载荷样本估算 ${capacity.estimated_payload_gib ?? "待采样"} GiB</p><p class="sub">${esc(capacity.scope_note)} ${esc(capacity.blockers.join("；"))}</p>`;
    };
    loadCapacity().catch((error) => {
      const target = root.querySelector("#initial-capacity");
      if (target) target.textContent = error.message;
    });
    let bulkTimer;
    const loadBulk = async () => {
      if (!root.isConnected) return;
      const data = await api("/data-sources/baseline-imports"),
        target = root.querySelector("#bulk-source-imports");
      if (!target) return;
      const status = {
        queued: "排队中",
        running: "导入中",
        complete: "导入完成",
        complete_with_rejections: "导入结束，有拒绝行",
        paused_disk: "磁盘保护暂停",
        paused_user: "已暂停",
        error: "导入异常，可续传",
      };
      target.innerHTML = `<p class="sub">${esc(data.coverage)} · <a href="https://appgoblin.info" target="_blank" rel="noopener noreferrer">Data provided by AppGoblin</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">${esc(data.license)}</a></p>${table(
        ["数据包", "本地文件", "操作"],
        data.files.map((row) => [
          esc(row.dataset),
          esc(row.file_name),
          `<button class="secondary small" data-bulk-import="${row.dataset}" ${row.available ? "" : "disabled"}>导入 / 续传</button>`,
        ]),
      )}${table(
        ["数据包", "状态", "已处理行", "有效行", "拒绝行", "最近写入", "操作"],
        data.imports.map((row) => [
          esc(row.dataset),
          esc(status[row.status] || row.status),
          row.processed.toLocaleString(),
          row.valid.toLocaleString(),
          row.rejected.toLocaleString(),
          new Date(row.updated_at * 1000).toLocaleString(),
          ["complete", "complete_with_rejections"].includes(row.status)
            ? "—"
            : `<button class="link" data-bulk-control="${row.digest}" data-action="${["queued", "running"].includes(row.status) ? "pause" : "resume"}">${["queued", "running"].includes(row.status) ? "暂停" : "续传"}</button>`,
        ]),
      )}`;
      if (
        data.imports.some((row) => ["queued", "running"].includes(row.status))
      )
        bulkTimer = setTimeout(() => loadBulk().catch(() => {}), 5000);
    };
    $("#bulk-source-imports").onclick = async (event) => {
      const button = event.target.closest(
        "[data-bulk-import], [data-bulk-control]",
      );
      if (!button) return;
      button.disabled = true;
      try {
        if (button.dataset.bulkControl)
          await api(
            `/data-sources/baseline-imports/${button.dataset.bulkControl}/control`,
            { action: button.dataset.action },
          );
        else
          await api("/data-sources/baseline-imports", {
            dataset: button.dataset.bulkImport,
          });
        clearTimeout(bulkTimer);
        await loadBulk();
      } catch (error) {
        toast(error.message);
        if (button.isConnected) button.disabled = false;
      }
    };
    loadBulk().catch((error) => {
      const target = root.querySelector("#bulk-source-imports");
      if (target) target.textContent = error.message;
    });

    $("#source-config-list").onclick = (event) => {
      const account = event.target.closest("[data-source-account]");
      if (account) {
        const prefix =
          account.dataset.sourceAccount === "community"
            ? "provider"
            : account.dataset.sourceAccount;
        const form = root.querySelector(`#${prefix}-account`);
        form.scrollIntoView({ block: "center", behavior: "smooth" });
        form.elements.email.focus({ preventScroll: true });
        return;
      }
      const button = event.target.closest("[data-source-edit]");
      if (!button) return;
      const source = data.connections.find(
        (row) => row.id === button.dataset.sourceEdit,
      );
      const dialog = document.createElement("dialog");
      dialog.className = "collection-editor";
      dialog.innerHTML = `<form id="source-binding-form" class="form-grid"><h2 class="full">${esc(sourceTitle(source))}</h2><div class="full">${sourceGuide(esc, source)}</div>${source.editable_origin ? `<label class="full">HTTPS 接入地址<input name="origin" type="url" required value="${esc(source.origin || "")}"></label><label class="full">允许的域名（逗号分隔）<input name="hosts" required value="${esc((source.hosts || []).join(", "))}"></label>` : source.editable_token ? `<label>广告账户 ID<input name="ad_account_id" required value="${esc(source.ad_account_id || "")}"></label><label>访问令牌<input name="access_token" type="password" autocomplete="new-password" placeholder="${source.account?.configured ? "已保存，留空保留" : "填写访问令牌"}"></label>` : `<label class="full">每次请求最大任务数<input name="batch_size" type="number" min="1" max="${source.batch_max}" required value="${source.batch_size}"></label><p class="sub full">已验证最大 ${source.batch_max}。${esc(source.batch_scope || "同市场应用详情")}。保持国家、语言隔离和完整字段；超大请求会拆分补采。</p>`}<p class="sub full" role="status"></p><button type="submit">保存配置</button><button type="button" class="secondary" data-close-source>关闭</button></form>`;
      root.append(dialog);
      dialog.querySelector("[data-close-source]").onclick = () =>
        dialog.close();
      dialog.onclose = () => dialog.remove();
      dialog.showModal();
      dialog.querySelector("form").onsubmit = async (event) => {
        event.preventDefault();
        const form = event.currentTarget,
          submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        const value = source.editable_origin
          ? {
              origin: form.elements.origin.value.trim(),
              hosts: form.elements.hosts.value
                .split(",")
                .map((v) => v.trim())
                .filter(Boolean),
            }
          : source.editable_token
            ? {
                ad_account_id: form.elements.ad_account_id.value.trim(),
                access_token: form.elements.access_token.value,
              }
            : { batch_size: Number(form.elements.batch_size.value) };
        if (source.editable_token) form.elements.access_token.value = "";
        try {
          const saved = await api(
            `/data-sources/config/${source.id}`,
            value,
            "PUT",
          );
          data.connections = data.connections.map((row) =>
            row.id === saved.id ? saved : row,
          );
          if (root.querySelector("#source-config-list"))
            root.querySelector("#source-config-list").innerHTML = sourceTable();
          for (const block of root.querySelectorAll("[data-source-guide]")) {
            const current = connection(block.dataset.sourceGuide);
            block.innerHTML = `<strong>${esc(sourceTitle(current))}</strong>${sourceGuide(esc, current)}`;
          }
          if (saved.account && accountUpdates[saved.id])
            accountUpdates[saved.id]({ [saved.id]: saved.account });
          dialog.close();
          toast("数据源配置已保存");
          loadCapacity().catch(() => {});
        } catch (error) {
          if (dialog.isConnected)
            form.querySelector('[role="status"]').textContent = error.message;
        } finally {
          if (source.editable_token) value.access_token = "";
          if (submit.isConnected) submit.disabled = false;
        }
      };
    };

    for (const [provider, prefix] of [
      ["community", "provider"],
      ["directory", "directory"],
    ]) {
      accountUpdates[provider] = bindSourceAccount({
        root,
        prefix,
        provider,
        api,
        onUpdate: syncAccounts,
      });
    }
    const run = async (button, work) => {
      button.disabled = true;
      try {
        await work();
      } catch (error) {
        toast(error.message);
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    };
    $("#provider-datasets").onclick = (event) =>
      run(event.currentTarget, async () => {
        const result = await api("/data-sources/community/datasets");
        if (event.target.isConnected)
          $("#provider-result").innerHTML = `<p>${esc(result.note)}</p>${table(
            ["可用文件"],
            result.files.map((f) => [esc(f)]),
          )}`;
      });
    const queryForm = $("#source-app-query");
    queryForm.addEventListener("filter-reset", () =>
      $("#source-app-result").replaceChildren(),
    );
    bindFilterBar(
      queryForm,
      async (values, form) => {
        $("#source-app-result").textContent = "正在获取所选应用的来源快照…";
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
      },
      toast,
    );
  };
}
