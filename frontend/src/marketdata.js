import { sourceGuide, sourceTitle } from "./source-guidance.js";
const labels = {
  running: "采集中",
  paused: "已暂停",
  stopped: "已停止",
  complete: "计划已完成",
  expired: "已到期 / 错过快照",
  pending: "待采集",
  done: "已归档",
  blocked: "权限不足",
  error: "失败",
  metadata: "应用元数据",
  estimates: "下载与收入估算",
  ranks: "历史排名",
  reviews: "评论",
  charts: "榜单",
  countries: "国家目录",
  categories: "分类目录",
  stores: "商店目录",
  mine: "账户应用",
  csv: "CSV 数据集",
};

export function marketDataPanel(
  panel,
  esc,
  state = {},
  source = { id: "marketdata", name: "商业市场数据" },
) {
  const end = new Date().toISOString().slice(0, 10);
  const start = new Date(Date.now() - 365 * 86400000)
    .toISOString()
    .slice(0, 10);
  return panel(
    "市场数据采集",
    `
    <div data-source-guide="marketdata"><strong>${esc(sourceTitle(source))}</strong>${sourceGuide(esc, source)}</div>
    <p id="marketdata-state" role="status">${esc(state.message || "未配置账户")}</p>
    <form id="marketdata-account" class="toolbar">
      <label>账户邮箱<input type="email" name="email" autocomplete="off" value="${esc(state.email || "")}" placeholder="可选，与密码一起填写"></label>
      <label>账户密码<input type="password" name="password" autocomplete="new-password"></label>
      <label class="wide">Personal Access Token<input type="password" name="access_token" autocomplete="new-password" placeholder="API 采集必填；不回显"></label>
      <button type="submit">保存并检查 API 授权</button>
      <button type="button" id="marketdata-authorize" class="secondary" ${state.configured ? "" : "disabled"}>检查授权</button>
    </form>
    <p class="sub">账密及 Token 加密保存、不回显。可单独填写 Token；每次保存替换全部 市场数据 凭据。官方 API 已停用账密登录，仅保存账密不会启用采集。</p>
    <p class="sub">采集范围由账户权限及剩余额度决定，遇到限制时保留续采进度。</p>
    <form id="marketdata-plan" class="toolbar">
      <label>历史开始日期<input name="start_date" type="date" required value="${start}"></label>
      <label>历史结束日期<input name="end_date" type="date" required value="${end}"></label>
      <label>试用截止时间（本机时区）<input name="stop_at" type="datetime-local" required></label>
      <label>每日请求上限<input name="daily_limit" type="number" min="1" max="50000" value="1000" required></label>
      <label class="wide">国家代码<input name="countries" placeholder="留空为全部国家；或 us,cn,th"></label>
      <label class="wide">市场数据 分类 ID<input name="categories" placeholder="留空为全部 Apple / Google 分类"></label>
      <label class="wide">市场数据 应用 ID<input name="products" placeholder="可留空，从账户应用和榜单发现；逗号分隔"></label>
      <label><input type="checkbox" name="charts" checked>采集各国免费、付费、畅销榜</label>
      <label><input type="checkbox" name="daily_charts" checked>试用期每日追加榜单</label>
      <label><input type="checkbox" name="expand_products" checked>为发现的应用补采历史</label>
      ${["metadata", "estimates", "ranks", "reviews"].map((k) => `<label><input type="checkbox" name="${k}" checked>${labels[k]}</label>`).join("")}
      <button type="submit">建立采集计划</button>
    </form>
    <p class="sub">默认回溯一年，可调整日期。先归档目录，再采集榜单和应用历史；保存逐日、分国家数据。空国家范围会显著增加请求量。由观星采集服务执行，关闭页面后继续；达到实际 API 配额或截止时间后停止请求。</p>
    <div class="toolbar"><button id="marketdata-refresh" class="secondary">刷新进度与归档</button></div>
    <div id="marketdata-progress" role="status"></div>
    <form id="marketdata-import" class="toolbar"><label class="wide">导入 市场数据 导出的 CSV<input name="dataset" type="file" accept=".csv,text/csv" required></label><button>归档数据集</button></form>
    <p class="sub">可在网页导出后导入 UTF-8 CSV（单文件最多 10 MB），完整保留原始列、行数与校验值。来源估算和官方实测数据分别保存。</p>
    <div id="marketdata-import-result" role="status"></div>
  `,
  );
}

export async function bindMarketData({ $, api, esc, table, toast, root }) {
  const find = (selector) => root.querySelector(selector);
  const anchor = find("#marketdata-account");
  const run = async (button, action) => {
    button.disabled = true;
    try {
      await action();
    } catch (e) {
      toast(e.message);
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  };
  const updateAccount = (data) => {
    if (!anchor.isConnected) return;
    find("#marketdata-state").textContent = data.marketdata.message;
    find("#marketdata-authorize").disabled = !data.marketdata.configured;
  };
  find("#marketdata-account").onsubmit = (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const value = Object.fromEntries(
      ["email", "password", "access_token"].map((k) => [
        k,
        form.elements[k].value,
      ]),
    );
    form.elements.password.value = "";
    form.elements.access_token.value = "";
    run(form.querySelector("button"), async () => {
      try {
        updateAccount(await api("/data-sources/marketdata", value, "PUT"));
      } finally {
        value.password = "";
        value.access_token = "";
      }
    });
  };
  find("#marketdata-authorize").onclick = (event) =>
    run(event.currentTarget, async () =>
      updateAccount(await api("/data-sources/marketdata/authorize", {})),
    );
  const refresh = async () => {
    const data = await api("/data-sources/marketdata/collection");
    if (!anchor.isConnected) return;
    const budget = data.budget;
    find("#marketdata-progress").innerHTML =
      `<p>${data.heartbeat && data.heartbeat > Date.now() / 1000 - 180 ? "采集服务最近在线" : "等待采集服务启动"}</p>${budget ? `<p>今日 UTC 请求：${esc(budget.used)} / 来源上限 ${esc(budget.source_limit)}${budget.next_at > Date.now() / 1000 ? " · 下次请求 " + esc(new Date(budget.next_at * 1000).toLocaleString()) : ""}</p>` : ""}
      ${
        data.runs
          .map(
            (
              r,
            ) => `<p>计划 #${r.id} · ${esc(labels[r.status] || r.status)} · 截止 ${esc(new Date(r.stop_at * 1000).toLocaleString())}${r.last_error ? " · " + esc(r.last_error) : ""}</p>
        <div class="toolbar">${
          ["running", "paused"].includes(r.status)
            ? [
                ["pause", "暂停"],
                ["resume", "继续"],
                ["retry", "重试失败 / 权限任务"],
                ["stop", "停止计划"],
              ]
                .map(
                  ([action, label]) =>
                    `<button class="secondary" data-af-run="${r.id}" data-af-action="${action}">${label}</button>`,
                )
                .join("")
            : ""
        }</div>
        ${table(
          ["数据类型", "状态", "任务数"],
          r.counts.map((c) => [
            esc(labels[c.kind] || c.kind),
            esc(labels[c.status] || c.status),
            esc(c.count),
          ]),
        )}`,
          )
          .join("") || "<p>尚无采集计划。</p>"
      }
      ${
        data.errors.length
          ? table(
              ["计划", "类型", "待处理问题"],
              data.errors.map((e) => [
                esc(e.run_id),
                esc(labels[e.kind] || e.kind),
                esc(e.error),
              ]),
            )
          : ""
      }
      <p>最近 50 份原始归档（全部文件保留在服务器，包含 SHA256）</p>
      ${table(
        ["类型", "记录数", "采集时间", "原始数据"],
        data.artifacts.map((a) => [
          esc(labels[a.kind] || a.kind),
          esc(a.records),
          esc(new Date(a.created_at * 1000).toLocaleString()),
          `<a href="/api/data-sources/marketdata/artifacts/${a.id}" title="${esc(a.sha256)}">下载 GZIP</a>`,
        ]),
      )}`;
    find("#marketdata-progress")
      .querySelectorAll("[data-af-action]")
      .forEach((button) => {
        button.onclick = () =>
          run(button, async () => {
            await api(
              `/data-sources/marketdata/collection/${button.dataset.afRun}/control`,
              { action: button.dataset.afAction },
            );
            await refresh();
          });
      });
  };
  find("#marketdata-refresh").onclick = (event) =>
    run(event.currentTarget, refresh);
  find("#marketdata-plan").onsubmit = (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      value = {};
    for (const key of ["products", "countries", "categories"])
      value[key] = form.elements[key].value
        .toLowerCase()
        .split(/[,，\s]+/)
        .filter(Boolean);
    for (const key of ["charts", "daily_charts", "expand_products"])
      value[key] = form.elements[key].checked;
    value.kinds = ["metadata", "estimates", "ranks", "reviews"].filter(
      (k) => form.elements[k].checked,
    );
    value.start_date = form.elements.start_date.value;
    value.end_date = form.elements.end_date.value;
    const stop = new Date(form.elements.stop_at.value);
    if (!Number.isFinite(stop.getTime())) {
      toast("请填写试用截止时间");
      return;
    }
    value.stop_at = stop.toISOString();
    value.daily_limit = Number(form.elements.daily_limit.value);
    run(form.querySelector("button"), async () => {
      await api("/data-sources/marketdata/collection", value);
      await refresh();
    });
  };
  find("#marketdata-import").onsubmit = (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      file = form.elements.dataset.files[0];
    run(form.querySelector("button"), async () => {
      if (!file || file.size > 10 * 1024 * 1024)
        throw new Error("请选择不超过 10 MB 的 CSV");
      const result = await api("/data-sources/marketdata/datasets", {
        content: await file.text(),
      });
      if (!anchor.isConnected) return;
      find("#marketdata-import-result").textContent =
        `${result.note}，共 ${result.records} 行。`;
      form.reset();
      await refresh();
    });
  };
  await refresh().catch((e) => toast(e.message));
}
