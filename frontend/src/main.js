import { pagination, bindPagination } from "./pagination.js";
import {
  filterBar,
  filterFields,
  bindFilterBar,
  initFilterBars,
  setFilterCatalog,
} from "./filter-bar.js";
("use strict");
import { createAccounts, routeResource } from "./accounts.js";
import "./style.css";
import "./theme.css";
import "./fonts.css";
import "./selectors.css";
import "./research.css";
import { createResearchPages } from "./research.js";
import { createDiscoveryPages } from "./discovery.js";
import "./discovery.css";
import { createGlobalCharts } from "./global-charts.js";
import { createDataSourcePage } from "./data-sources.js";
import { createChartDetail } from "./chart-detail.js";
import "./global-charts.css";
import { createSelectors } from "./selectors.js";
import { effectiveLanguage } from "./market-scope.js";
import { createAppIconManager } from "./app-icons.js";
import { icon, storeLogo } from "./icons.js";
import { createTaskLogViewer } from "./task-logs.js";
import { initMarketControls } from "./market-controls.js";
import "./workspace.css";
import "./filter-bar.css";
import "./pagination.css";
import "./monitoring.css";
import "./experience.css";
import "./accounts.css";
import {
  monitoringChart,
  monitoringBars,
  bindMonitoringCharts,
} from "./monitoring-charts.js";
import { createAppInsights } from "./app-insights.js";
import { createAvailabilityPage } from "./availability.js";
import { createGroupWorkspace } from "./app-groups.js";
import { createAppDetailRows } from "./app-detail-rows.js";
import "./app-groups.css";
import { initTheme } from "./theme.js";
import {
  navigationMarkup,
  setWorkspaceHint,
  updateWorkspace,
} from "./navigation.js";
import "./navigation.css";
import { initShellLayout } from "./shell-layout.js";
import "./shell-layout.css";
import "./table-surfaces.css";
initTheme();
initShellLayout();
const $ = (s, root = document) => root.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const stamp = (v) =>
  v
    ? new Date(Number(v) * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚未采集";
const num = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN"));
const safeURL = (v) => {
  try {
    const u = new URL(v);
    return ["https:", "http:"].includes(u.protocol) ? esc(u.href) : "";
  } catch {
    return "";
  }
};
let localePreferences;
const market = (store = $("#global-store").value) => ({
  store,
  country: $("#global-country").value.trim().toLowerCase(),
  language: effectiveLanguage(
    localePreferences,
    store,
    $("#global-country").value,
    $("#global-language").value || "auto",
  ),
});
try {
  const saved = JSON.parse(sessionStorage.getItem("appmon-market") || "null");
  if (
    saved &&
    ["google", "apple", "all"].includes(saved.store) &&
    /^[a-z]{2}$/.test(saved.country) &&
    /^[a-z-]{2,16}$/.test(saved.language)
  ) {
    $("#global-store").value = saved.store;
    $("#global-country").value = saved.country;
    $("#global-language").value =
      saved.language_mode === "manual" ? saved.language : "auto";
  }
} catch {}
const marketControls = initMarketControls();
function saveMarket() {
  marketControls.sync();
  selectors.syncCountry();
  try {
    sessionStorage.setItem(
      "appmon-market",
      JSON.stringify({
        ...market(),
        language: $("#global-language").value,
        language_mode:
          $("#global-language").value === "auto" ? "auto" : "manual",
      }),
    );
  } catch {}
}
const selectors = createSelectors({
  api,
  getMarket: market,
  onError: toast,
  onCatalog: (value) => {
    setFilterCatalog(value);
    localePreferences = value.locales;
    marketControls.setPreferences(localePreferences);
  },
});
createAppIconManager({ api, getMarket: market });
const qs = (obj) =>
  new URLSearchParams(
    Object.entries(obj).filter(([, v]) => v !== "" && v != null),
  ).toString();
initFilterBars({
  chooseCountry: (options) => selectors.chooseCountry(options),
  suggest: (field, q, form) =>
    field === "developer-directory"
      ? api("/research/developers?" + qs({ store: market().store, q })).then(
          (rows) => rows.map((r) => r.name),
        )
      : api(
          "/apps/filter-options?" +
            qs({
              ...market(),
              store: form?.elements.store?.value || market().store,
              field,
              q,
            }),
        ),
});
const kindLabel = (v) =>
  ({
    first_verified: "首次验证",
    metadata_changed: "资料变更",
    region_unavailable: "地区不可用",
    reappeared: "重新出现",
    keyword_move: "关键词排名变化",
    chart_move: "榜单排名变化",
    collection_error: "采集失败",
    low_rating: "低分评论",
    keyword_targets: "关键词采集",
    observation_targets: "榜单、评论与推荐采集",
  })[v] || v;
let revision = 0;
const accounts = createAccounts({ esc, toast, onReady: render });
$("#navigation").innerHTML = navigationMarkup(icon);
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").style.display = "none"), 4500);
}
async function api(path, body, method) {
  if (body === undefined && (!method || method === "GET")) {
    const [pathname, search] = path.split("?"),
      params = new URLSearchParams(search);
    const scoped = [
      "/overview",
      "/pool",
      "/daily",
      "/watchlist",
      "/targets",
      "/keywords",
      "/alerts",
      "/ads",
      "/exports",
      "/research/comparisons",
      "/research/collection/tasks",
      "/research/collection/activity",
    ];
    if (scoped.includes(pathname)) {
      params.set("store", market().store);
      path = pathname + "?" + params;
    }
  }
  const response = await fetch("/api" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...accounts.headers(),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.status === 401) {
    revision++;
    $("#content").replaceChildren();
    accounts.show();
    throw new Error("请登录账户");
  }
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {
      data = { detail: "服务暂时不可用" };
    }
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  }
  return response.json();
}
function panel(title, body, extra = "") {
  return `<section class="panel"><div class="panel-heading"><h2>${title}</h2>${extra}</div>${body}</section>`;
}
function empty(
  message = "尚无数据",
  detail = "请调整筛选，或添加对应数据后刷新。",
) {
  return `<div class="empty"><strong>${esc(message)}</strong>${esc(detail)}</div>`;
}
function table(headers, rows) {
  return rows.length
    ? `<div class="table-wrap data-table-wrap"><table class="data-table">${
        headers[0]?.includes('type="checkbox"')
          ? `<colgroup><col class="table-selection-col">${headers
              .slice(1)
              .map(() => "<col>")
              .join("")}</colgroup>`
          : ""
      }<thead><tr>${headers.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((x) => `<td>${x ?? "—"}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : empty();
}
const stateLabels = {
  available: "在架观察",
  region_unavailable: "地区不可用",
  suspected_missing: "待确认",
  ok: "成功",
  error: "失败",
  queued: "排队中",
  running: "处理中",
  ready: "可下载",
  observed: "已观察",
  not_observed: "本次未观察到",
};
function badge(value) {
  const cls = ["error", "region_unavailable"].includes(value)
    ? "error"
    : ["suspected_missing", "queued", "running"].includes(value)
      ? "warn"
      : value
        ? ""
        : "neutral";
  return `<span class="badge ${cls}">${esc(stateLabels[value] || value || "未验证")}</span>`;
}
function move(value) {
  return value == null
    ? "—"
    : `<span class="${value > 0 ? "up" : value < 0 ? "down" : ""}">${value > 0 ? "↑ " + value : value < 0 ? "↓ " + Math.abs(value) : "持平"}</span>`;
}
function appIconMarkup(store, id, name, imageURL, country, language) {
  const m = market(store),
    lang = language || m.language;
  return `<span class="appicon-wrap" data-icon-store="${esc(store)}" data-icon-app="${esc(id)}" data-icon-country="${esc(country || m.country)}" data-icon-language="${esc(lang)}" data-icon-source="${esc(imageURL || "")}" data-icon-name="${esc(name || id)}"><img class="appicon" alt="" hidden><span class="appicon appicon-placeholder">${storeLogo(store)}</span><button type="button" class="appicon-refresh link" data-icon-refresh aria-label="补采图标">${icon("refresh")}</button></span>`;
}
function appLink(
  store,
  id,
  name,
  imageURL,
  country,
  language,
  { showId = true } = {},
) {
  const m = market(store),
    lang = language || m.language;
  return `<div class="appcell">${appIconMarkup(store, id, name, imageURL, country, language)}<div><button class="link appname" title="${esc(name || id)}" data-app="${esc(id)}" data-store="${esc(store)}" data-country="${esc(country || m.country)}" data-language="${esc(lang)}">${esc(name || id)}</button>${showId ? `<div class="appid">${market().store === "all" ? `<span class="store-scope-badge">${storeLogo(store)} ${store === "apple" ? "App Store" : "Google Play"}</span> · ` : ""}${esc(id)}</div>` : ""}</div></div>`;
}
function field(label, name, value = "", type = "text", extra = "") {
  return `<label>${label}<input name="${name}" value="${esc(value)}" type="${type}" ${extra}></label>`;
}
function select(label, name, options, selected = "") {
  return `<label>${label}<select name="${name}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(selected) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
}
function formData(form) {
  return Object.fromEntries(new FormData(form));
}
function bindForm(id, callback) {
  const f = $(id);
  if (!f) return;
  if (f.hasAttribute("data-filter-bar"))
    return bindFilterBar(f, callback, toast);
  f.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", f) || $("button", f);
    if (button) button.disabled = true;
    try {
      await callback(formData(f), f);
    } catch (e) {
      toast(e.message);
    } finally {
      if (button) button.disabled = false;
    }
  });
}
function controlButtons(resource, row) {
  return `<div class="actions"><button class="secondary small" data-control="${resource}" data-id="${row.id}" data-action="${row.enabled ? "pause" : "resume"}">${row.enabled ? "暂停" : "恢复"}</button>${resource === "rules" ? "" : `<button class="secondary small" data-control="${resource}" data-id="${row.id}" data-action="refresh">安排刷新</button>`}</div>`;
}
function chart(points, invert = false, formatAt = stamp, options = {}) {
  return monitoringChart(points, invert, formatAt, options);
}
function bars(rows) {
  return monitoringBars(rows);
}
function evidence(report) {
  const r = report.latest_success;
  return `<div class="callout">${r ? `观察于 ${esc(stamp(r.observed_at))} · ${esc(r.source)} · ${esc(r.coverage)} · 实际返回 ${r.returned_count} 条` : "尚无成功观察"} ${report.stale ? " · 数据待更新" : ""}${report.latest_attempt?.outcome === "error" ? ` · 最近失败：${esc(report.latest_attempt.error)}` : ""}</div>`;
}
function jsonBlock(value) {
  return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
}
function metric(label, value, caption) {
  return `<div class="metric"><div class="caption">${label}</div><div class="value">${num(value)}</div><div class="smallprint">${caption}</div></div>`;
}
function mount(html, rev, hint) {
  if (rev != null && rev !== revision) return false;
  $("#content").innerHTML = html;
  if (hint) setWorkspaceHint(document, hint);
  $("#workspace-actions").replaceChildren(
    ...$("#content").querySelectorAll("[data-workspace-action]"),
  );
  return true;
}

async function overview(rev) {
  const data = await api("/overview");
  const c = data.counts;
  const failures = Object.entries(data.failures).flatMap(([kind, rows]) =>
    rows.map((row) => [
      esc(kindLabel(kind)),
      row.id,
      row.failures,
      esc(row.last_error),
    ]),
  );
  if (
    !mount(
      `<div class="overview-actions"><a href="#groups"><strong>应用分组</strong><span>查看国家覆盖与应用指标 →</span></a><a href="#events"><strong>事件记录</strong><span>按日期复核应用变化 →</span></a><a href="#collection"><strong>采集中心</strong><span>管理任务进度与异常 →</span></a></div><div class="cards overview-metrics">${metric("监控目标", c.watchlist, "应用 × 国家 × 语言")}${metric("跟踪关键词", c.keyword_targets, "独立市场词任务")}${metric("未读告警", c.unread_alerts, "待查看的观察事件")}${metric("已知应用池", null, "<span id=pool-caption>正在读取统计…</span>").replace('class="value"', 'class="value" id="pool-count"')}</div><div class="overview-main">${panel(
        "最新应用变化",
        table(
          ["应用", "地区", "事件", "观察时间"],
          data.recent_events.map((r) => [
            appLink(r.store, r.app_id, r.name, null, r.country, r.language),
            esc(selectors.countryName(r.country)),
            esc(kindLabel(r.kind)),
            stamp(r.observed_at),
          ]),
        ),
        '<a href="#events">全部事件 →</a>',
      )}${panel("采集异常", failures.length ? table(["类型", "任务 ID", "失败次数", "最近原因"], failures) : empty("暂无采集异常", "已监控任务尚无连续失败记录。"), '<a href="#collection">管理任务 →</a>')}</div><details class="panel overview-coverage"><summary>市场覆盖与新鲜度 <span class="sub">${data.freshness.length} 个地区状态</span></summary>${table(
        ["商店", "国家 / 语言", "状态", "已观察应用", "最后成功"],
        data.freshness.map((r) => [
          esc(r.store === "apple" ? "App Store" : "Google Play"),
          esc(selectors.countryName(r.country)) +
            " / " +
            esc(r.language || "—"),
          badge(r.state),
          num(r.count),
          stamp(r.last_success),
        ]),
      )}</details>`,
      rev,
      data.worker?.next_at
        ? `后台心跳 ${stamp(data.worker.next_at)}`
        : "等待后台首次心跳",
    )
  )
    return;
  api("/pool")
    .then((pool) => {
      if (rev !== revision) return;
      $("#pool-count").textContent = num(pool.total);
      $("#pool-caption").textContent =
        pool.items.map((x) => x.store + " " + num(x.count)).join(" · ") +
        " · " +
        stamp(pool.observed_at);
    })
    .catch(() => {
      if (rev === revision && $("#pool-caption"))
        $("#pool-caption").textContent = "精确统计暂不可用，请刷新重试";
    });
}
const appRows = createAppDetailRows({
  api,
  esc,
  stamp,
  num,
  market,
  appLink,
  appIconMarkup,
  countryName: (code) => selectors.countryName(code),
  categoryName: (store, category) => selectors.categoryName(store, category),
  chartName: (store, id) =>
    selectors.chartDefinitions(store).find((c) => c.id === id)?.name || id,
});
const appInsights = createAppInsights({
  appRows,
  api,
  esc,
  stamp,
  num,
  panel,
  table,
  chart,
  bars,
  metric,
  market,
  toast,
  qs,
});
bindMonitoringCharts();
const groupWorkspace = createGroupWorkspace({
  appIconMarkup,
  categoryName: (store, category) => selectors.categoryName(store, category),
  chartName: (store, collection) =>
    selectors.chartDefinitions(store).find((chart) => chart.id === collection)
      ?.name || collection,
  api,
  esc,
  qs,
  panel,
  table,
  metric,
  chart,
  stamp,
  num,
  mount,
  toast,
  can: (resource, action) => accounts.can(resource, action),
  countryName: (code) => selectors.countryName(code),
});
async function applications(rev, filters = {}, page = 1, limit = 50) {
  const data = await api(
    "/apps?" + qs({ ...market(), ...filters, page, limit }),
  );
  if (
    !mount(
      `<div id="store-discovery"></div><div class="application-layout">${selectors.categorySidebar(market().store, filters.category)}<section class="panel">${filterBar(
        {
          id: "app-search",
          values: filters,
          fields: [
            {
              name: "q",
              label: "应用名称 / ID",
              source: "app",
              wide: true,
              matchKey: "q_match",
              pickedMode: "id",
              options: data.items.map((r) => [
                r.app_id,
                r.payload?.name || r.name || r.app_id,
              ]),
            },
            {
              html: selectors.categoryField(
                market().store,
                filters.category || "",
                "pool-category",
              ),
            },
            {
              name: "developer",
              label: "开发者",
              source: "developer",
              matchKey: "developer_match",
              options: data.items
                .map((r) => r.payload?.developer)
                .filter(Boolean),
            },
            {
              name: "state",
              label: "状态",
              type: "panel",
              options: [
                ["", "全部状态"],
                ["available", "在架观察"],
                ["region_unavailable", "地区不可用"],
                ["suspected_missing", "待确认"],
              ],
            },
          ],
          after: `<details class="advanced-filters"${["min_rating", "min_ratings", "pricing", "updated_since", "released_since"].some((k) => filters[k]) ? " open" : ""}><summary>更多条件：评分、售价与商店日期</summary><div class="toolbar">${filterFields(
            "app-search-more",
            [
              {
                name: "min_rating",
                label: "最低商店评分",
                type: "number",
                min: 0,
                max: 5,
                step: "any",
                options: [1, 2, 3, 4, 4.5, 5],
              },
              {
                name: "min_ratings",
                label: "最低累计评分数",
                type: "number",
                min: 0,
                step: 1,
                options: [100, 1000, 10000],
              },
              {
                name: "pricing",
                label: "售价",
                type: "select",
                options: [
                  ["", "全部"],
                  ["free", "免费"],
                  ["paid", "付费"],
                ],
              },
              {
                name: "updated_since",
                label: "商店更新时间不早于",
                type: "date",
              },
              {
                name: "released_since",
                label: "商店发布日期不早于",
                type: "date",
              },
            ],
            filters,
          )}</div><p class="sub">仅筛选当前市场已观察到相应字段的记录。</p></details>`,
        },
      )}<div class="bulk-toolbar"><button type="button" id="bulk-monitor" class="secondary" disabled>批量加入监控</button><button type="button" id="bulk-compare" class="secondary" disabled>保存竞品集合</button><span id="pool-selection-count" class="selection-count">选择当前页应用；监控最多50个，对比2–6个。</span></div>${await appRows(data.items, { leadingColumns: [{ key: "select", label: "选择", heading: '<input id="pool-select-all" type="checkbox" aria-label="选择本页全部应用">', cell: (r) => `<input type="checkbox" data-pool-select="${esc(r.app_id)}" aria-label="选择 ${esc(r.name || r.app_id)}">` }] })}${pagination({ id: "app-pages", offset: ((data.page || page) - 1) * limit, limit, hasMore: data.has_more ?? !!data.next, prevId: "prev-apps", nextId: "next-apps", sizes: [25, 50, 100, 200] })}</section></div>`,
      rev,
    )
  )
    return;
  if (market().store === "all") {
    $("#pool-selection-count").textContent =
      "可跨商店加入应用分组；独立监控和竞品集合需选择单个商店。";
  }
  const groupButton = document.createElement("button");
  groupButton.type = "button";
  groupButton.id = "bulk-group";
  groupButton.className = "secondary";
  groupButton.disabled = true;
  groupButton.textContent = "加入应用分组";
  groupButton.hidden = !(
    accounts.can("groups", "write") && accounts.can("watchlist", "write")
  );
  $(".bulk-toolbar").prepend(groupButton);
  appInsights.discovery();
  $("[name=state]", $("#app-search")).value = filters.state || "";
  $("[name=pricing]", $("#app-search")).value = filters.pricing || "";
  const picked = () =>
    Array.from(document.querySelectorAll("[data-pool-select]:checked")).map(
      (n) => n.dataset.poolSelect,
    );
  function selection() {
    const count = picked().length;
    $("#pool-selection-count").textContent = "已选 " + count + " 个应用";
    $("#bulk-monitor").disabled =
      market().store === "all" || count < 1 || count > 50;
    $("#bulk-compare").disabled =
      market().store === "all" || count < 2 || count > 6;
    groupButton.disabled = count < 1;
  }
  $("#pool-select-all")?.addEventListener("change", (event) => {
    document
      .querySelectorAll("[data-pool-select]")
      .forEach((n) => (n.checked = event.target.checked));
    selection();
  });
  document
    .querySelectorAll("[data-pool-select]")
    .forEach((n) => n.addEventListener("change", selection));
  $("#bulk-monitor").addEventListener("click", () =>
    researchPages.planForApps(picked()),
  );
  $("#bulk-compare").addEventListener("click", () =>
    researchPages.setForApps(picked()),
  );
  groupButton.onclick = () =>
    groupWorkspace
      .addSelected(
        data.items
          .filter((row) => picked().includes(row.app_id))
          .map((row) => ({
            store: row.store,
            app_id: row.app_id,
            country: row.country || market().country,
            language: row.language || market().language,
          })),
      )
      .catch((e) => toast(e.message));

  selectors.mountSidebar(market().store, filters.category || "", (category) =>
    applications(++revision, { ...formData($("#app-search")), category }).catch(
      (e) => toast(e.message),
    ),
  );
  $("#pool-category").addEventListener("change", () =>
    applications(++revision, formData($("#app-search"))).catch((e) =>
      toast(e.message),
    ),
  );
  bindForm("#app-search", (data) => applications(++revision, data));
  bindPagination(document, "app-pages", (start, size) =>
    applications(++revision, filters, start / size + 1, size),
  );
}
let appProgressTimer = null;
const detailLogs = createTaskLogViewer({ api, esc, stamp });
async function appDetail(store, id, rev = ++revision) {
  $("#page-title").textContent = "应用详情";
  $("#global-store").value = store;
  saveMarket();
  const m = { ...market(), store, app_id: id };
  const data = await api(
    `/apps/${encodeURIComponent(store)}/${encodeURIComponent(id)}?` +
      qs({ country: m.country, language: m.language }),
  );
  const p = data.listing?.payload || {};
  if (
    !mount(
      `<div class="panel-heading"><div>${appLink(store, id, p.name || data.app.name, p.icon)}<p class="sub"><span class="member-store">${storeLogo(store)}<span>${store === "apple" ? "App Store" : "Google Play"}</span></span> · ${esc(m.country.toUpperCase())} · ${esc(m.language)} · ${esc(p.developer || "开发者待采集")}</p></div><div class="actions"><button id="collect-app-now">立即采集全部公开信息</button><button id="quick-watch" class="secondary">管理采集配置</button><button class="secondary" id="open-aso-workbench">ASO 工作台</button></div></div><div id="app-collection-progress"></div>${panel("应用明细", await appRows([{ ...data.app, ...p, store, app_id: id }], { scope: m }))}<div class="cards">${metric("商店评分", p.rating, "来自最近一次商店观察")}${metric("评分数", p.ratings, "累计公开评分数量")}${metric("版本快照", data.snapshots.length, "显示最近最多100次变更")}${metric("历史命中过的关键词", data.keywords.coverage.length, "包含当前未命中词；有效覆盖见下方监控")}</div><div class="grid2">${panel("当前商店资料", `<h3>${esc(p.name || "等待采集")}</h3><p>版本 ${esc(p.version || "—")} · ${esc(data.app.category_name || p.category || "—")}</p><p>${esc(p.summary || "")}</p><details><summary>描述与版本说明</summary><div class="review-text">${esc(p.description || "暂无描述")}</div><h3 class="spacer">版本说明</h3><div class="review-text">${esc(p.release_notes || "暂无版本说明")}</div></details>${safeURL(p.url) ? `<a href="${safeURL(p.url)}" target="_blank" rel="noopener">查看商店原页 ↗</a>` : ""}`)}${panel(
        "评分变化",
        chart(
          [...data.snapshots]
            .reverse()
            .map((r) => ({ at: r.observed_at, value: r.payload?.rating })),
          false,
          stamp,
          { label: "评分", domain: [0, 5], precision: 2 },
        ),
      )}</div>${appInsights.facts(p, data.app)}<div id="monitoring-trends"></div><div id="app-market-matrix"></div><div id="app-keyword-markets"></div>${panel("素材与版本历史", `<label>选择历史快照<select id="snapshot-choice">${data.snapshots.length ? "" : '<option value="">暂无已保存的快照</option>'}${data.snapshots.map((r, i) => `<option value="${i}">${esc(stamp(r.observed_at))} · ${esc(r.payload?.version || "")}</option>`).join("")}</select></label><div id="snapshot-view"></div>`)}${panel(
        "关键词覆盖",
        table(
          ["关键词", "最近位置", "变化", "数据时间", "状态"],
          data.keywords.coverage.map((r) => [
            `<button class="link" data-keyword="${r.target.id}">${esc(r.target.keyword)}</button>`,
            num(r.position),
            move(r.change),
            stamp(r.latest_success?.observed_at),
            badge(r.stale ? "queued" : r.status),
          ]),
        ),
      )}${panel(
        "榜单表现",
        `<p class="sub">当前国家与语言的总榜及应用分类榜。本站派生榜会单独标记，未命中只表示不在本次返回范围。</p>${table(
          ["榜单 / 分类", "位置", "基榜位置 / 变化", "观察时间 / 来源", "状态"],
          (data.chart_rankings || []).map((r) => [
            `${esc(r.name)}${r.mode === "derived" ? '<span class="badge warn">本站派生</span>' : ""}<div class="sub">${esc(selectors.categoryName(store, r.category))}</div>`,
            num(r.position),
            num(r.base_position) + " / " + move(r.change),
            stamp(r.observed_at) +
              `<div class="sub">${esc(r.source || "待采集")}</div>`,
            r.status === "unsupported"
              ? esc(r.reason)
              : r.status === "not_checked"
                ? "未采集"
                : r.position !== null
                  ? "本次榜单内"
                  : `未进入本次 ${r.returned_count} 条结果${r.reason ? '<div class="sub">' + esc(r.reason) + "</div>" : ""}`,
          ]),
        )}`,
      )}${panel("变更时间线", data.events.length ? `<div class="timeline">${data.events.map((r) => `<article><strong>${esc(kindLabel(r.kind))}</strong><div class="sub">${esc(stamp(r.observed_at))}</div><details><summary>查看字段差异</summary>${jsonBlock(JSON.parse(r.changes))}</details></article>`).join("")}</div>` : empty())}${panel("ASO 核查与候选词", '<button id="aso-check" class="secondary">根据已存记录生成建议</button><div id="aso-result" class="spacer"></div>')}`,
      rev,
      [
        data.listing
          ? `最近观察 ${stamp(data.listing.last_success)} · ${stateLabels[data.listing.state] || data.listing.state}`
          : "等待首次采集",
        data.job?.last_error && `最近失败 ${data.job.last_error}`,
      ]
        .filter(Boolean)
        .join(" · "),
    )
  )
    return;
  const content = $("#content");
  content
    .querySelector(":scope > .panel-heading")
    ?.classList.add("app-detail-hero");
  const sections = [
    ["profile", "商店资料"],
    ["performance", "监控趋势"],
    ["markets", "国家 / 地区"],
    ["rankings", "关键词与榜单"],
    ["history", "版本与事件"],
    ["aso", "ASO 核查"],
  ];
  const panels = new Map();
  const nav = document.createElement("nav");
  nav.className = "detail-section-tabs";
  nav.setAttribute("aria-label", "应用详情内容");
  nav.setAttribute("role", "tablist");
  const wrapper = document.createElement("div");
  wrapper.className = "detail-sections";
  for (const [key, label] of sections) {
    const pane = document.createElement("section");
    pane.id = "detail-section-" + key;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", "detail-tab-" + key);
    pane.className = "detail-section";
    panels.set(key, pane);
    wrapper.append(pane);
    nav.insertAdjacentHTML(
      "beforeend",
      `<button type="button" id="detail-tab-${key}" data-detail-section="${key}" role="tab" aria-controls="${pane.id}" class="secondary">${label}</button>`,
    );
  }
  const titles = {
    应用明细: "profile",
    应用档案: "profile",
    素材与版本历史: "history",
    变更时间线: "history",
    关键词覆盖: "rankings",
    榜单表现: "rankings",
    "ASO 核查与候选词": "aso",
  };
  const ids = {
    "monitoring-trends": "performance",
    "app-market-matrix": "markets",
    "app-keyword-markets": "rankings",
  };
  for (const el of [...content.children]) {
    const key =
      ids[el.id] ||
      titles[el.querySelector("h2")?.textContent] ||
      (el.classList.contains("grid2") ? "profile" : "");
    if (key) panels.get(key).append(el);
  }
  content.append(nav, wrapper);
  const loaded = new Set();
  const showSection = (key) => {
    panels.forEach((el, k) => (el.hidden = k !== key));
    nav
      .querySelectorAll("[data-detail-section]")
      .forEach((b) =>
        b.setAttribute(
          "aria-selected",
          String(b.dataset.detailSection === key),
        ),
      );
    if (!loaded.has(key)) {
      loaded.add(key);
      if (key === "performance") appInsights.monitoring(m);
      if (key === "markets")
        researchPages
          .marketMatrix(store, id, rev)
          .catch((e) => toast(e.message));
    }
  };
  nav.addEventListener("click", (e) => {
    const button = e.target.closest("[data-detail-section]");
    if (button) showSection(button.dataset.detailSection);
  });
  showSection("profile");
  const progressState = {
    running: "采集中",
    complete: "本轮采集完成",
    partial_failure: "本轮部分任务失败",
    failed: "配置失败",
  };
  const refreshProgress = (report) => {
    if (rev !== revision || !$("#app-collection-progress")) return;
    const active = report?.state === "running";
    $("#collect-app-now").disabled = active;
    $("#collect-app-now").textContent = active
      ? "正在采集…"
      : "立即采集全部公开信息";
    $("#app-collection-progress").innerHTML = report
      ? panel(
          "本轮采集进度",
          `<div class="callout">${esc(progressState[report.state] || report.state)} · ${report.finished}/${report.total} 项已完成 · ${report.errors} 项失败</div><p class="sub">${esc(report.scope)}</p><button id="app-plan-logs" class="secondary">查看本轮计划日志</button><details class="spacer"><summary>展开子任务状态</summary>${table(
            ["采集项", "目标", "状态", "日志"],
            report.tasks.map((t) => [
              esc(
                {
                  details: "详情与素材",
                  reviews: "最近评论",
                  keywords: "关键词",
                  chart: "榜单",
                  featured: "编辑推荐",
                }[t.kind],
              ),
              esc(t.label),
              esc(
                {
                  configuring: "配置中",
                  scheduled: "等待执行",
                  pending: "等待领取",
                  running: "采集中",
                  complete: "完成",
                  failed: "失败",
                  paused: "已暂停",
                  error: "等待重试",
                }[t.state] || t.state,
              ),
              t.ref
                ? `<button class="link" data-app-log-resource="${t.ref.resource}" data-app-log-id="${t.ref.id}">日志</button>`
                : "等待配置",
            ]),
          )}</details>`,
        )
      : '<p class="sub">一键操作会创建或启用当前市场的相关采集任务：详情与素材、近期评论、可用榜单、已跟踪关键词和应用名搜索；公开来源无法保证全历史评论或全网关键词。</p>';
    $("#app-plan-logs")?.addEventListener("click", () =>
      detailLogs
        .open("plans", report.id, "应用采集计划")
        .catch((e) => toast(e.message)),
    );
    document
      .querySelectorAll("[data-app-log-resource]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            detailLogs
              .open(
                b.dataset.appLogResource,
                b.dataset.appLogId,
                "应用采集任务",
              )
              .catch((e) => toast(e.message))),
      );
    if (active)
      appProgressTimer = setTimeout(async () => {
        if (rev !== revision) return;
        try {
          const next = await api(
            `/apps/${store}/${encodeURIComponent(id)}/collection?` +
              qs({ country: m.country, language: m.language }),
          );
          if (rev !== revision) return;
          if (next?.state !== "running") {
            await appDetail(store, id, ++revision);
          } else refreshProgress(next);
        } catch (e) {
          if (rev === revision) {
            toast(e.message);
            appProgressTimer = setTimeout(() => refreshProgress(report), 3000);
          }
        }
      }, 3000);
  };
  refreshProgress(data.collection_request);
  $("#collect-app-now").onclick = async () => {
    const b = $("#collect-app-now");
    b.disabled = true;
    try {
      const result = await api(
        `/apps/${store}/${encodeURIComponent(id)}/collect`,
        { country: m.country, language: m.language },
      );
      if (rev === revision) {
        toast("已安排采集，可查看下方进度和日志");
        refreshProgress(result);
      }
    } catch (e) {
      toast(e.message);
      if (rev === revision) b.disabled = false;
    }
  };

  $("#open-aso-workbench").addEventListener("click", () => {
    location.hash =
      "aso/" + encodeURIComponent(store) + "/" + encodeURIComponent(id);
  });
  const renderSnapshot = () => {
    const i = Number($("#snapshot-choice").value),
      snapshot = data.snapshots[i],
      previous = data.snapshots[i + 1];
    if (!snapshot) {
      $("#snapshot-view").innerHTML = empty();
      return;
    }
    const q = snapshot.payload || {};
    const changes = Object.fromEntries(
      [...new Set([...Object.keys(q), ...Object.keys(previous?.payload || {})])]
        .filter(
          (k) =>
            JSON.stringify(q[k]) !== JSON.stringify(previous?.payload?.[k]),
        )
        .map((k) => [k, { previous: previous?.payload?.[k], current: q[k] }]),
    );
    $("#snapshot-view").innerHTML =
      `<p class="sub">版本 ${esc(q.version || "—")} · ${esc(stamp(snapshot.observed_at))}</p><div class="screenshots">${[
        q.icon,
        ...(q.screenshots || []),
      ]
        .filter(safeURL)
        .map(
          (u) => `<img src="${safeURL(u)}" alt="商店历史素材" loading="lazy">`,
        )
        .join(
          "",
        )}</div><details><summary>与前一快照的字段差异</summary>${jsonBlock(changes)}</details>`;
  };
  Promise.all([
    api("/discovery/keywords/markets?" + qs({ store, app_id: id })),
    api("/discovery/reports?" + qs(m)),
  ])
    .then(([coverage, reports]) => {
      if (rev !== revision) return;
      $("#app-keyword-markets").innerHTML = panel(
        "关键词市场覆盖",
        `<p class="sub">${esc(coverage.note)}</p>${table(
          ["国家", "语言", "已跟踪词", "有效词", "应用覆盖词", "Top10"],
          coverage.items.map((r) => [
            esc(r.country),
            esc(r.language),
            r.tracked,
            r.fresh,
            r.covered,
            r.top10,
          ]),
        )}<p class="sub">当前市场已导入第三方报告 ${reports.length} 份。报告词表与以上实采覆盖独立展示。</p><a class="button secondary" href="#keywords">进入关键词中心查看报告与词库</a>`,
      );
    })
    .catch((e) => {
      if (rev === revision) $("#app-keyword-markets").textContent = e.message;
    });
  renderSnapshot();
  $("#snapshot-choice").addEventListener("change", renderSnapshot);
  $("#quick-watch").addEventListener("click", () => {
    researchPages.planForApps([id]);
  });
  $("#aso-check").addEventListener("click", async () => {
    try {
      const r = await api("/suggestions", m);
      $("#aso-result").innerHTML = r.reason
        ? empty(r.reason)
        : `${table(
            ["建议", "依据"],
            r.actions.map((a) => [esc(a.title), esc(a.evidence)]),
          )}<h3 class="spacer">元数据候选词</h3><div class="pillrow">${r.metadata_candidates.map((w) => `<span class="pill">${esc(w)}</span>`).join("")}</div><p class="sub">${esc(r.note)}</p>`;
    } catch (e) {
      toast(e.message);
    }
  });
}
function collectionLink(kind) {
  return `<a class="button secondary" data-workspace-action href="#collection?new=${kind}">前往采集中心</a>`;
}
let chartFilters = { store: "", collection: "top-free", category: "" };
const chartCategories = { google: "", apple: "" };
async function chartPage(rev) {
  const m = market();
  if (m.store === "all") return multiCharts(rev);
  if (chartFilters.store !== m.store)
    chartFilters = { store: m.store, collection: "top-free", category: "" };
  const definitions = selectors.chartDefinitions(m.store);
  const data = await api(
    "/charts/view?" +
      qs({
        ...m,
        collection: chartFilters.collection,
        category: chartFilters.category,
      }),
  );
  const spec = data.definition;
  if (
    !mount(
      `${panel(
        "榜单筛选",
        `${filterBar({ id: "chart-view-form", values: chartFilters, submit: "查看榜单", fields: [{ type: "select", name: "collection", label: "榜单", options: definitions.map((d) => [d.id, d.name + (d.mode === "derived" ? "（本站派生）" : "")]) }, { html: selectors.categoryField(m.store, chartFilters.category, "chart-view-category", "chart") }], actions: `<a class="button secondary" href="#collection?new=chart&collection=${encodeURIComponent(spec.base_collection || chartFilters.collection)}&category=${encodeURIComponent(chartFilters.category)}">配置采集</a>${data.target ? '<button id="refresh-current-chart" type="button">立即采集基榜</button>' : ""}` })}`,
      )}<div class="callout">${spec.mode === "derived" ? "本站派生榜 · " : "商店直接榜单 · "}${esc(spec.note)}${data.reason ? "<p>" + esc(data.reason) + "</p>" : ""}</div>${panel(
        esc(spec.name),
        `<p class="sub">${esc(m.country.toUpperCase())} / ${esc(m.language)} · ${esc(selectors.categoryName(m.store, chartFilters.category))} · 观察于 ${stamp(data.latest_success?.observed_at)} · ${esc(data.latest_success?.source || "尚未采集")} · 本次返回 ${data.items.length} 条</p>${await appRows(
          data.items,
          {
            scope: m,
            leadingColumns: [
              {
                key: "position",
                label: "位置",
                cell: (r) => num(r._source.position),
              },
              {
                key: "base",
                label: "基榜位置",
                cell: (r) => num(r._source.base_position),
              },
              {
                key: "change",
                label: "排名变化",
                cell: (r) => move(r._source.change),
              },
            ],
          },
        )}${!data.latest_success ? '<p class="sub">当前市场尚无此基榜快照，请通过采集中心创建任务。</p>' : ""}`,
      )}`,
      rev,
    )
  )
    return;
  const apply = async () => {
    chartFilters = { store: m.store, ...formData($("#chart-view-form")) };
    if (
      definitions.find((d) => d.id === chartFilters.collection)
        ?.category_supported === false &&
      chartFilters.category
    ) {
      chartFilters.category = "";
      toast("此公开榜单仅支持总榜，已清除分类筛选");
    }
    await chartPage(++revision);
  };
  bindForm("#chart-view-form", apply);
  $('#chart-view-form [name="collection"]').addEventListener("change", () =>
    apply().catch((e) => toast(e.message)),
  );
  $("#chart-view-category").addEventListener("change", () =>
    apply().catch((e) => toast(e.message)),
  );
  $("#refresh-current-chart")?.addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try {
      await api("/research/collection/tasks/batch", {
        tasks: [{ resource: "targets", id: data.target.id }],
        action: "refresh",
      });
      toast("已安排基榜采集，可在采集中心查看日志");
    } catch (error) {
      toast(error.message);
    } finally {
      if (rev === revision) $("#refresh-current-chart").disabled = false;
    }
  });
}
async function multiCharts(rev) {
  const stores = ["google", "apple"];
  const categoryField = (store) =>
    selectors
      .categoryField(
        store,
        chartCategories[store],
        `tracking-${store}-category`,
        "chart",
      )
      .replace('name="category"', `name="${store}_category"`)
      .replace(
        ">应用分类<",
        `>${store === "google" ? "Google Play" : "App Store"} 分类<`,
      );
  const results = await Promise.all(
    stores.map(async (store) => {
      const m = market(store),
        id = chartFilters.collection || "top-free";
      try {
        return {
          m,
          data: await api(
            "/charts/view?" +
              qs({ ...m, collection: id, category: chartCategories[store] }),
          ),
        };
      } catch (error) {
        return { m, error: error.message };
      }
    }),
  );
  if (
    !mount(
      `${filterBar({ id: "multi-chart-form", values: chartFilters, submit: "查看榜单", fields: [{ type: "select", name: "collection", label: "榜单", options: selectors.chartDefinitions("google").map((d) => [d.id, d.name]) }, ...stores.map((store) => ({ html: categoryField(store) }))] })}<div class="multi-store-grid">${(
        await Promise.all(
          results.map(async ({ m, data, error }) =>
            panel(
              `<span class="store-caption">${storeLogo(m.store)} ${m.store === "apple" ? "App Store" : "Google Play"}</span>`,
              error
                ? `<p class="errorbox">${esc(error)}</p>`
                : `<p class="sub">${esc(m.country.toUpperCase())} / ${esc(m.language)} · ${esc(selectors.categoryName(m.store, chartCategories[m.store]))} · ${esc(data.definition.name)} · ${data.definition.mode === "derived" ? "本站派生" : "商店直接榜单"}</p><p class="sub">${esc(data.definition.note)} · ${stamp(data.latest_success?.observed_at)}</p>${data.reason ? `<p class="callout">${esc(data.reason)}</p>` : ""}${await appRows(
                    data.items,
                    {
                      scope: m,
                      leadingColumns: [
                        {
                          key: "position",
                          label: "排名",
                          cell: (r) => num(r._source.position),
                        },
                        {
                          key: "change",
                          label: "变化",
                          cell: (r) => move(r._source.change),
                        },
                      ],
                    },
                  )}<button class="secondary" data-chart-store="${m.store}">管理此商店榜单</button>`,
            ),
          ),
        )
      ).join("")}</div>`,
      rev,
    )
  )
    return;
  bindForm("#multi-chart-form", async (v) => {
    chartFilters = { store: "all", collection: v.collection, category: "" };
    for (const store of stores) {
      chartCategories[store] = v[store + "_category"] || "";
      if (
        selectors.chartDefinitions(store).find((d) => d.id === v.collection)
          ?.category_supported === false
      )
        chartCategories[store] = "";
    }
    await multiCharts(++revision);
  });
  for (const store of stores)
    $(`#tracking-${store}-category`).addEventListener("change", () =>
      $("#multi-chart-form").requestSubmit(),
    );
  document.querySelectorAll("[data-chart-store]").forEach((button) => {
    button.onclick = async () => {
      $("#global-store").value = button.dataset.chartStore;
      saveMarket();
      try {
        await discoveryPages.charts(++revision, {
          collection: chartFilters.collection,
          category: chartCategories[button.dataset.chartStore],
        });
      } catch (error) {
        toast(error.message);
      }
    };
  });
}
let detailFilters = {},
  detailOffset = 0,
  detailLimit = 50;
async function watchlist(rev) {
  const data = await api(
    "/availability/watched?" +
      qs({
        ...detailFilters,
        store: market().store,
        offset: detailOffset,
        limit: detailLimit,
      }),
  );
  if (
    !mount(
      collectionLink("details") +
        panel(
          "重点监控结果",
          filterBar({
            id: "detail-task-filter",
            values: detailFilters,
            fields: [
              { name: "country", label: "国家 / 地区", type: "country" },
              { name: "language", label: "语言", type: "language" },
              {
                name: "enabled",
                label: "监控",
                type: "panel",
                options: [
                  ["", "全部"],
                  ["1", "监控中"],
                  ["0", "已暂停"],
                ],
              },
              {
                name: "state",
                label: "观察状态",
                type: "panel",
                options: [
                  ["", "全部状态"],
                  ["available", "在架"],
                  ["suspected_missing", "待复核"],
                  ["region_unavailable", "地区不可用"],
                  ["unobserved", "尚未验证"],
                ],
              },
              {
                name: "group_name",
                label: "分类",
                type: "panel",
                options: [["", "全部分类"], ...data.groups],
              },
              { name: "q", label: "应用名称 / ID", source: "app", wide: true },
            ],
          }) +
            (await appRows(data.items, {
              trailingColumns: [
                {
                  key: "schedule",
                  label: "周期 / 下次采集",
                  cell: (r) =>
                    `${r._source.refresh_hours}h<div class="sub">${stamp(r._source.due)}</div>`,
                },
                {
                  key: "manage",
                  label: "管理",
                  cell: () => '<a href="#collection">管理任务</a>',
                },
              ],
            })) +
            pagination({
              id: "detail-task-pages",
              offset: data.offset,
              limit: data.limit,
              total: data.total,
              sizes: [25, 50, 100, 200],
            }),
        ),
      rev,
    )
  )
    return;
  bindFilterBar(
    $("#detail-task-filter"),
    async (values) => {
      detailFilters = values;
      detailOffset = 0;
      await watchlist(++revision);
    },
    toast,
  );
  bindPagination(document, "detail-task-pages", (offset, limit) => {
    detailOffset = offset;
    detailLimit = limit;
    return watchlist(++revision);
  });
}
async function observations(rev, kind) {
  const rows = await api("/targets?kind=" + kind);
  mount(
    (kind === "featured"
      ? panel(
          "App Store Today 编辑推荐",
          '<p>记录各地区 App Store Today 页出现的推荐故事与关联应用，用于查看推荐曝光和历史变化。卡片顺序是页面展示位置，不是下载或收入排名。</p><p class="sub">仅支持 App Store；Google Play 暂无同类采集来源。先配置推荐采集，再点击下方目标查看日期快照。</p>',
        )
      : "") +
      collectionLink(kind) +
      panel(
        "采集结果",
        table(
          ["目标", "市场", "范围 / 周期", "最近状态", "管理"],
          rows.map((r) => [
            `<button class="link" data-target="${r.id}">${esc(r.app_id || r.collection || "App Store Today 推荐快照")}</button><div class="sub">${esc(selectors.categoryName(r.store, r.category))}</div>`,
            `${esc(r.store)} · ${esc(r.country)} / ${esc(r.language)}`,
            `${r.depth} 条 · ${r.refresh_hours}h`,
            r.failures
              ? badge("error") + `<div class="sub">${esc(r.last_error)}</div>`
              : badge(
                  !r.enabled
                    ? "已暂停"
                    : r.latest_success_id
                      ? "已有快照"
                      : "queued",
                ),
            '<a href="#collection">管理任务</a>',
          ]),
        ),
      ),
    rev,
  );
}
async function targetDetail(id, runId, rev = ++revision) {
  const report = await api(
    "/targets/" + id + (runId ? "?run_id=" + runId : ""),
  );
  const t = report.target;
  $("#global-store").value = t.store;
  $("#global-country").value = t.country;
  $("#global-language").value = t.language;
  saveMarket();
  $("#page-title").textContent =
    t.kind === "featured"
      ? "商店编辑推荐历史"
      : t.kind === "reviews"
        ? "评论采集历史"
        : "榜单历史";
  const hint = `${t.store} · ${t.country.toUpperCase()} / ${t.language} · ${t.collection || t.app_id || "Today"} · ${selectors.categoryName(t.store, t.category)}`;
  const rows =
    t.kind === "reviews"
      ? table(
          ["评分", "评论", "版本", "发表时间"],
          report.items.map((r) => [
            r.rating + " ★",
            `<strong>${esc(r.title || "")}</strong><div class="review-text">${esc(r.content)}</div>`,
            esc(r.version),
            esc(r.published_at),
          ]),
        )
      : t.kind === "featured"
        ? table(
            ["页面卡片顺序", "推荐故事 / 专题", "关联应用"],
            report.items.map((r) => [
              r.position,
              `<strong>${esc(typeof r.name === "string" ? r.name : JSON.stringify(r.name))}</strong>${safeURL(r.url) ? `<div><a href="${safeURL(r.url)}" target="_blank" rel="noopener">查看故事 ↗</a></div>` : ""}`,
              (r.apps || [])
                .map((a) => appLink("apple", a.app_id, a.name))
                .join(""),
            ]),
          )
        : await appRows(report.items, {
            scope: t,
            leadingColumns: [
              {
                key: "position",
                label: "快照排名",
                cell: (r) => num(r._source.position),
              },
              {
                key: "change",
                label: "变化",
                cell: (r) => move(r._source.change),
              },
            ],
          });
  if (
    !mount(
      panel(
        "历史时间点",
        evidence(report) +
          `<select id="run-choice" aria-label="历史时间点"><option value="">最新成功观察</option>${report.history.map((r) => `<option value="${r.id}" ${r.id === runId ? "selected" : ""}>${esc(stamp(r.observed_at))} · ${r.outcome} · ${r.returned_count ?? "—"} 条</option>`).join("")}</select>`,
      ) +
        panel(t.kind === "featured" ? "推荐故事快照" : "观察记录", rows) +
        (t.kind === "featured"
          ? panel(
              "推荐关联应用明细",
              await appRows(
                [
                  ...new Map(
                    report.items
                      .flatMap((card) => card.apps || [])
                      .map((a) => [a.app_id, a]),
                  ).values(),
                ],
                { scope: t },
              ),
            )
          : "") +
        (t.kind === "chart"
          ? panel(
              "本次离开观察范围",
              table(
                ["应用", "上次位置"],
                report.left_observed_range.map((r) => [
                  appLink(t.store, r.app_id, r.name),
                  r.position,
                ]),
              ),
            )
          : ""),
      rev,
      hint,
    )
  )
    return;
  $("#run-choice").addEventListener("change", (e) =>
    targetDetail(id, Number(e.target.value) || undefined).catch((x) =>
      toast(x.message),
    ),
  );
}
async function keywordPage(rev) {
  const m = market();
  const overviewRequest = discoveryPages.overview();
  const rows = (await api("/keywords")).filter(
    (r) =>
      (m.store === "all" || r.store === m.store) &&
      r.country === m.country &&
      r.language === market(r.store).language,
  );
  const overview = await overviewRequest;
  if (
    !mount(
      overview +
        collectionLink("keywords") +
        panel(
          "跟踪词库",
          table(
            ["关键词", "商店 / 市场", "深度 / 周期", "最近尝试", "管理"],
            rows.map((r) => [
              `<button class="link" data-keyword="${r.id}">${esc(r.keyword)}</button>`,
              `${esc(r.store)} · ${esc(r.country)} / ${esc(r.language)}`,
              `${r.depth} / ${r.refresh_hours}h`,
              r.last_error
                ? esc(r.last_error)
                : r.latest_success_id
                  ? "已有观察"
                  : "等待采集",
              '<a href="#collection">管理任务</a>',
            ]),
          ),
        ),
      rev,
    )
  )
    return;
  discoveryPages.bindOverview(() => keywordPage(++revision));
}
async function keywordDetail(id, rev = ++revision) {
  const r = await api("/keywords/" + id);
  $("#global-store").value = r.target.store;
  $("#global-country").value = r.target.country;
  $("#global-language").value = r.target.language;
  saveMarket();
  $("#page-title").textContent = r.target.keyword;
  if (
    !mount(
      `${panel(
        "搜索排名",
        evidence(r) +
          (await appRows(r.ranks, {
            scope: r.target,
            leadingColumns: [
              {
                key: "position",
                label: "位置",
                cell: (a) => num(a._source.position),
              },
              {
                key: "change",
                label: "变化",
                cell: (a) => move(a._source.change),
              },
            ],
            trailingColumns: [
              {
                key: "history",
                label: "历史",
                cell: (a) =>
                  `<button class="link" data-rank-history="${id}" data-rank-app="${esc(a.app_id)}">查看曲线</button>`,
              },
            ],
          })),
      )}<div id="rank-history"></div>`,
      rev,
      `${r.target.store} · ${r.target.country.toUpperCase()} / ${r.target.language}`,
    )
  )
    return;
}
async function rankHistory(id, appId) {
  const r = await api(`/keywords/${id}/history?` + qs({ app_id: appId }));
  $("#rank-history").innerHTML = panel(
    "排名历史 · " + esc(appId),
    chart(
      [...r.history]
        .reverse()
        .map((x) => ({ at: x.observed_at, value: x.position })),
      true,
    ) +
      table(
        ["观察时间", "位置", "状态", "覆盖范围"],
        r.history.map((x) => [
          stamp(x.observed_at),
          num(x.position),
          badge(x.status || x.outcome),
          esc(x.coverage),
        ]),
      ),
  );
}
async function comparison(rev) {
  return researchPages.competitors(rev);
}
async function reviewPage(rev) {
  return researchPages.reviews(rev);
}
async function marketPage(rev) {
  const r = await api("/market?" + qs(market()));
  mount(
    `<div class="grid2">${panel("分类分布 · 已观察应用", bars(r.categories.map((x) => ({ ...x, label: x.store + " / " + x.label }))))}${panel(
      "开发者应用数",
      table(
        ["开发者", "商店", "已观察应用"],
        r.developers.map((x) => [esc(x.label), esc(x.store), num(x.count)]),
      ),
    )}</div>${panel(
      "近 30 天事件",
      table(
        ["日期（UTC）", "事件", "数量"],
        r.activity.map((x) => [
          new Date(x.day * 1000).toISOString().slice(0, 10),
          esc(x.kind),
          num(x.count),
        ]),
      ),
    )}`,
    rev,
  );
}
async function adPage(rev) {
  const r = await api("/ads");
  if (
    !mount(
      `${panel(
        "导入真实记录",
        `<form id="ad-import"><div class="toolbar">${select(
          "数据类型",
          "kind",
          [
            ["observations", "广告观察证据"],
            ["reports", "自有账户日报"],
          ],
        )}${select("格式", "format", [
          ["json", "JSON 数组"],
          ["csv", "CSV（包含表头）"],
        ])}<button id="ad-example" class="secondary" type="button">填入格式示例</button></div><label>数据内容<textarea name="content" required></textarea></label><button type="submit" class="spacer">验证并导入</button></form><p class="sub">示例仅用于说明字段，不会自动保存。日报按日期、应用、国家、广告系列、关键词、币种与来源去重更新。</p>`,
      )}${panel(
        "广告观察",
        table(
          ["应用", "市场 / 关键词", "位置", "时间", "证据"],
          r.observations.map((a) => [
            appLink(a.store, a.app_id),
            esc(a.country) + " / " + esc(a.keyword),
            esc(a.placement),
            stamp(a.observed_at),
            `<a href="${safeURL(a.evidence_url)}" target="_blank" rel="noopener">${esc(a.source)} ↗</a>`,
          ]),
        ),
      )}${panel(
        "自有账户实际日报",
        table(
          [
            "日期 / 应用",
            "市场 / 系列 / 关键词",
            "展示",
            "点击",
            "安装",
            "支出",
          ],
          r.reports.map((a) => [
            esc(a.report_date) +
              '<div class="appid">' +
              esc(a.app_id) +
              "</div>",
            esc(a.country) + " / " + esc(a.campaign) + " / " + esc(a.keyword),
            num(a.impressions),
            num(a.taps),
            num(a.installs),
            esc(a.currency) + " " + esc(a.spend),
          ]),
        ),
      )}`,
      rev,
    )
  )
    return;
  $("#ad-example").addEventListener("click", () => {
    const form = $("#ad-import");
    $("[name=format]", form).value = "json";
    const value =
      $("[name=kind]", form).value === "reports"
        ? {
            report_date: "2026-11-03",
            app_id: "123456789",
            country: "us",
            campaign: "请填写实际系列",
            keyword: "请填写实际关键词",
            impressions: 0,
            taps: 0,
            installs: 0,
            spend: "0.00",
            currency: "USD",
            source: "请填写报表来源",
          }
        : {
            store: "apple",
            app_id: "123456789",
            country: "us",
            keyword: "请填写实际关键词",
            observed_at: Math.floor(Date.now() / 1000),
            source: "请填写证据来源",
            evidence_url: "https://example.com/replace-with-evidence",
            placement: "search_ad",
          };
    $("[name=content]", form).value = JSON.stringify([value], null, 2);
  });
  bindForm("#ad-import", async (data) => {
    const body =
      data.format === "csv"
        ? { csv: data.content }
        : { rows: JSON.parse(data.content) };
    await api("/ads/" + data.kind + "/import", body);
    toast("数据已导入");
    await adPage(revision);
  });
}
async function alertPage(rev) {
  const [r, d] = await Promise.all([api("/alerts"), api("/daily")]);
  if (
    !mount(
      `${panel(
        "新增告警规则",
        `<form id="rule-form" class="toolbar">${field("名称", "name", "", "text", "required")}${select(
          "触发事件",
          "kind",
          [
            ["metadata_changed", "元数据变化"],
            ["region_unavailable", "地区不可用"],
            ["reappeared", "重新出现"],
            ["keyword_move", "关键词位次变化"],
            ["chart_move", "榜单位次变化"],
            ["collection_error", "采集失败"],
            ["low_rating", "低分评论"],
          ],
        )}${field("阈值（位次差 / 评论星级）", "threshold", 5, "number", 'min="0" step="1" required')}${field("限定监控分组（可空）", "group_name")}<button type="submit">保存规则</button></form>`,
      )}${panel(
        "规则",
        table(
          ["名称", "类型", "阈值 / 分组", "操作"],
          r.rules.map((a) => [
            esc(a.name),
            esc(kindLabel(a.kind)),
            a.threshold + " / " + esc(a.group_name || "全部"),
            controlButtons("rules", a),
          ]),
        ),
      )}${panel(
        "告警收件箱",
        table(
          ["时间", "规则", "证据", "状态"],
          r.items.map((a) => [
            stamp(a.observed_at),
            esc(a.title),
            `<details><summary>${esc(kindLabel(a.kind))}</summary>${jsonBlock(JSON.parse(a.payload))}</details>`,
            a.acknowledged
              ? badge("已读")
              : `<button class="secondary small" data-ack="${a.id}">标为已读</button>`,
          ]),
        ),
      )}<div class="grid2">${panel("24 小时应用变更", bars(d.events.map((x) => ({ ...x, label: kindLabel(x.kind) }))))}${panel("24 小时采集结果", table(["任务", "结果", "次数"], [...d.collections.map((x) => ["榜单 / 评论 / 推荐", badge(x.outcome), x.count]), ...d.keywords.map((x) => ["关键词", badge(x.outcome), x.count])]))}</div>`,
      rev,
    )
  )
    return;
  bindForm("#rule-form", async (data) => {
    await api("/alerts/rules", { ...data, threshold: Number(data.threshold) });
    toast("规则已保存，将处理创建后的事件");
    await alertPage(revision);
  });
}
async function exportPage(rev) {
  const rows = await api("/exports");
  if (
    !mount(
      `${panel(
        "新建导出",
        `<form id="export-form" class="toolbar">${select("数据集", "kind", [
          ["apps", "应用池"],
          ["events", "变更事件"],
          ["snapshots", "详情与素材历史"],
          ["listings", "地区状态"],
          ["reviews", "评论"],
          ["keyword_ranks", "关键词快照行"],
          ["observation_items", "榜单与推荐快照行"],
          ["alerts", "告警"],
          ["ad_observations", "广告观察"],
          ["ad_reports", "广告日报"],
          ["aso_drafts", "ASO草稿"],
          ["aso_draft_versions", "ASO修订历史"],
          ["comparison_sets", "竞品集合"],
          ["event_annotations", "复盘注记"],
          ["collection_plans", "采集计划"],
        ])}${field("过滤条件（JSON 对象）", "filters", "{}", "text", "required")}<button type="submit">创建导出</button></form><p class="sub">示例：{"store":"google"} 或 {"store":"apple","country":"us"}；关键词与榜单快照可按 run_id 导出。</p>`,
      )}${panel(
        "导出任务",
        table(
          ["数据集", "过滤条件", "创建时间", "行数", "状态 / 下载"],
          rows.map((r) => [
            esc(kindLabel(r.kind)),
            esc(r.filters),
            stamp(r.created_at),
            num(r.rows_written),
            badge(r.status) +
              (r.status === "ready"
                ? ` <a href="/api/exports/${r.id}/download">下载 JSONL</a>`
                : r.error
                  ? '<div class="sub">' + esc(r.error) + "</div>"
                  : ""),
          ]),
        ),
      )} `,
      rev,
    )
  )
    return;
  const exportMarket = market().store;
  if (exportMarket !== "all") {
    const supported = [
      "apps",
      "events",
      "snapshots",
      "listings",
      "reviews",
      "ad_observations",
      "aso_drafts",
      "comparison_sets",
      "event_annotations",
    ];
    $('#export-form [name="filters"]').value = JSON.stringify({
      store: exportMarket,
    });
    $('#export-form [name="kind"]')
      .querySelectorAll("option")
      .forEach((o) => {
        if (!supported.includes(o.value)) {
          o.disabled = true;
          o.textContent += "（请切换全部商店）";
        }
      });
  }
  bindForm("#export-form", async (data) => {
    const filters = JSON.parse(data.filters);
    if (exportMarket !== "all") filters.store = exportMarket;
    await api("/exports", {
      kind: data.kind,
      filters,
    });
    toast("导出已排队");
    await exportPage(revision);
  });
}

const researchPages = createResearchPages({
  appRows,
  $,
  api,
  esc,
  stamp,
  num,
  panel,
  table,
  chart,
  bars,
  metric,
  field,
  select,
  formData,
  bindForm,
  mount,
  market,
  toast,
  selectors,
  appLink,
  empty,
  controlButtons,
  currentRevision: () => revision,
  nextRevision: () => ++revision,
  reviewTargets: (appId = "") => {
    location.hash =
      "collection?new=reviews" +
      (appId ? "&app=" + encodeURIComponent(appId) : "");
  },
});
const availabilityPage = createAvailabilityPage({
  appRows,
  api,
  market,
  qs,
  mount,
  panel,
  metric,
  table,
  chart,
  bars,
  esc,
  stamp,
  num,
  appLink,
  toast,
  nextRevision: () => ++revision,
});
const discoveryPages = createDiscoveryPages({
  appRows,
  $,
  api,
  esc,
  stamp,
  num,
  panel,
  table,
  metric,
  field,
  select,
  bindForm,
  mount,
  market,
  selectMarket: async (m) => {
    $("#global-store").value = m.store;
    $("#global-country").value = m.country;
    $("#global-language").value = m.language;
    saveMarket();
    await render();
  },
  toast,
  selectors,
  appLink,
  nextRevision: () => ++revision,
  currentRevision: () => revision,
});
const routes = {
  chart: createChartDetail({
    appRows,
    api,
    esc,
    stamp,
    num,
    panel,
    table,
    field,
    select,
    bindForm,
    mount,
    market,
    selectors,
    appLink,
    $,
    toast,
  }),
  globalcharts: createGlobalCharts({
    $,
    api,
    esc,
    stamp,
    panel,
    table,
    field,
    select,
    bindForm,
    mount,
    market,
    toast,
    selectors,
    appLink,
    nextRevision: () => ++revision,
    currentRevision: () => revision,
  }),
  availability: availabilityPage,
  groups: groupWorkspace.render,
  overview,
  events: researchPages.events,
  aso: researchPages.aso,
  developers: researchPages.developers,
  collection: researchPages.collectionCenter,
  apps: applications,
  watchlist,
  charts: (rev) =>
    market().store === "all" ? chartPage(rev) : discoveryPages.charts(rev),
  featured: (r) => observations(r, "featured"),
  keywords: (rev) =>
    market().store === "all" ? keywordPage(rev) : discoveryPages.keywords(rev),
  compare: comparison,
  reviews: reviewPage,
  market: marketPage,
  ads: adPage,
  alerts: alertPage,
  exports: exportPage,
  datasources: createDataSourcePage({
    $,
    api,
    esc,
    panel,
    table,
    mount,
    toast,
  }),
};
async function render() {
  if (!accounts.user) return;
  const rev = ++revision;
  const requestedPage = (location.hash.slice(1) || "overview").split("?")[0];
  updateWorkspace(document, requestedPage, accounts.can);
  mount('<div class="empty">正在加载观察记录…</div>', rev);
  try {
    await accounts.refresh();
  } catch {
    if (rev === revision) accounts.show();
    return;
  }
  if (rev !== revision) return;
  const resource = routeResource(
    (location.hash.slice(1) || "overview").split("?")[0],
  );
  if (resource === "account") {
    location.hash = "administration?own=1";
    return;
  }
  clearTimeout(appProgressTimer);
  $("#task-logs")?.close();
  $("#collection-editor")?.remove();
  $("#research-dialog")?.close();
  $("#group-dialog")?.remove();
  const name = (location.hash.slice(1) || "overview").split("?")[0];
  const workspace = updateWorkspace(document, name, accounts.can);
  const ownAccount =
    resource === "administration" &&
    new URLSearchParams(location.hash.split("?")[1] || "").get("own") === "1";
  if (!accounts.can(resource) && !ownAccount) {
    ++revision;
    $("#page-title").textContent = "暂无访问权限";
    $("#content").innerHTML =
      '<div class="empty"><h2>暂无访问权限</h2><p>请联系管理员开通此功能，或从左侧选择已授权页面。</p><a href="#administration?own=1">用户设置</a></div>';
    return;
  }
  if (resource === "administration") {
    ++revision;
    $("#page-title").textContent = "账户管理";
    const accountRoot = document.createElement("div");
    $("#content").replaceChildren(accountRoot);
    try {
      await accounts.management(accountRoot);
    } catch (error) {
      toast(error.message);
    }
    return;
  }
  clearTimeout(appProgressTimer);
  $("#task-logs")?.close();
  $("#collection-editor")?.remove();
  $("#research-dialog")?.close();
  const route = routes[name] || overview;
  try {
    const parts = name.split("/");
    if (name === "chart") {
      const p = new URLSearchParams(location.hash.split("?")[1] || "");
      if (
        !["apple", "google"].includes(p.get("store")) ||
        !/^[a-z]{2}$/.test(p.get("country") || "")
      )
        throw Error("榜单链接的商店或国家无效");
      $("#global-store").value = p.get("store");
      $("#global-country").value = p.get("country");
      $("#global-language").value = p.get("language") || "auto";
      saveMarket();
    }
    if (["app", "aso"].includes(parts[0]) && parts.length === 3) {
      $("#global-store").value = decodeURIComponent(parts[1]);
      saveMarket();
    }
    await selectors.ready();
    selectors.syncCountry();
    if (
      workspace.scope === "market" &&
      !selectors.supportsMarket(market().store, market().country)
    ) {
      mount(
        '<div class="callout">该地区没有独立的 App Store 市场，请选择可用国家或地区。</div><button id="choose-supported-country" class="secondary">选择国家 / 地区</button>',
        rev,
      );
      $("#choose-supported-country")?.addEventListener("click", () =>
        $("#country-trigger").click(),
      );
      return;
    }
    if (parts[0] === "aso" && parts.length === 3) {
      $("#global-store").value = decodeURIComponent(parts[1]);
      saveMarket();
      await researchPages.aso(rev, decodeURIComponent(parts[2]));
    } else if (parts[0] === "app" && parts.length === 3)
      await appDetail(
        decodeURIComponent(parts[1]),
        decodeURIComponent(parts[2]),
        rev,
      );
    else if (parts[0] === "target" && parts.length === 2)
      await targetDetail(Number(parts[1]), undefined, rev);
    else if (parts[0] === "keyword" && parts.length === 2)
      await keywordDetail(Number(parts[1]), rev);
    else await route(rev);
  } catch (e) {
    if (rev === revision)
      mount(
        `<div class="errorbox"><strong>加载失败</strong><p>${esc(e.message)}</p><button class="secondary" id="retry-load">重试</button></div>`,
      );
    $("#retry-load")?.addEventListener("click", render);
  }
}
document.addEventListener("click", async (event) => {
  const el = event.target.closest("button");
  if (!el) return;
  try {
    if (el.dataset.app) {
      $("#global-store").value = el.dataset.store;
      $("#global-country").value = el.dataset.country;
      if (
        $("#global-language").value !== "auto" ||
        market(el.dataset.store).language !== el.dataset.language
      )
        $("#global-language").value = el.dataset.language;
      saveMarket();
      const nextHash =
        "app/" +
        encodeURIComponent(el.dataset.store) +
        "/" +
        encodeURIComponent(el.dataset.app);
      if (location.hash.slice(1) === nextHash) await render();
      else location.hash = nextHash;
    } else if (el.dataset.target) location.hash = "target/" + el.dataset.target;
    else if (el.dataset.keyword)
      location.hash = "keyword/" + el.dataset.keyword;
    else if (el.dataset.rankHistory)
      await rankHistory(Number(el.dataset.rankHistory), el.dataset.rankApp);
    else if (el.dataset.control) {
      await api(`/control/${el.dataset.control}/${el.dataset.id}`, {
        action: el.dataset.action,
      });
      toast(
        el.dataset.action === "refresh"
          ? "已安排刷新，后台将按限速执行"
          : "任务状态已更新",
      );
      await render();
    } else if (el.dataset.ack) {
      await api(`/alerts/${el.dataset.ack}/acknowledge`, {});
      await alertPage(revision);
    }
  } catch (e) {
    toast(e.message);
  }
});
$("#refresh-page").addEventListener("click", render);
["global-store", "global-country", "global-language"].forEach((id) =>
  $("#" + id).addEventListener("change", () => {
    saveMarket();
    if (location.hash.slice(1).split("?")[0] === "chart") {
      const m = market();
      if (m.store === "all") {
        location.hash = "charts";
        return;
      }
      const p = Object.fromEntries(
        new URLSearchParams(location.hash.split("?")[1] || ""),
      );
      location.hash =
        "chart?" +
        qs({
          ...p,
          ...m,
          category: id === "global-store" ? "" : p.category,
          offset: 0,
        });
      return;
    }
    const parts = location.hash.slice(1).split("/");
    if (
      id === "global-store" &&
      ["app", "aso"].includes(parts[0]) &&
      parts[1] !== market().store
    ) {
      toast("已切换商店，请选择该商店中的应用");
      location.hash = "apps";
      return;
    }
    if (parts[0] === "target") {
      location.hash = "charts";
      return;
    }
    if (parts[0] === "keyword") {
      location.hash = "keywords";
      return;
    }
    render();
  }),
);
document.addEventListener(
  "error",
  (event) => {
    const image = event.target;
    if (
      image?.tagName === "IMG" &&
      image.classList.contains("appicon") &&
      !image.closest(".appicon-wrap")
    ) {
      const fallback = document.createElement("span");
      fallback.className = "appicon";
      fallback.innerHTML = icon("apps");
      image.replaceWith(fallback);
    }
  },
  true,
);
window.addEventListener("hashchange", render);
accounts.start().then((ready) => {
  if (ready) render();
});
