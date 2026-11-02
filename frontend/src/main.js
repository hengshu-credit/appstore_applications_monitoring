"use strict";
import "./style.css";
import "./theme.css";
import "./fonts.css";
import "./selectors.css";
import "./research.css";
import { createResearchPages } from "./research.js";
import { createSelectors } from "./selectors.js";
import { initTheme } from "./theme.js";
initTheme();
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
const market = () => ({
  store: $("#global-store").value,
  country: $("#global-country").value.trim().toLowerCase(),
  language: $("#global-language").value.trim().toLowerCase(),
});
try {
  const saved = JSON.parse(sessionStorage.getItem("appmon-market") || "null");
  if (
    saved &&
    ["google", "apple"].includes(saved.store) &&
    /^[a-z]{2}$/.test(saved.country) &&
    /^[a-z-]{2,16}$/.test(saved.language)
  ) {
    $("#global-store").value = saved.store;
    $("#global-country").value = saved.country;
    $("#global-language").value = saved.language;
  }
} catch {}
function saveMarket() {
  selectors.syncCountry();
  try {
    sessionStorage.setItem("appmon-market", JSON.stringify(market()));
  } catch {}
}
const selectors = createSelectors({ api, getMarket: market, onError: toast });
const qs = (obj) =>
  new URLSearchParams(
    Object.entries(obj).filter(([, v]) => v !== "" && v != null),
  ).toString();
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
let accessToken = "",
  revision = 0;
const nav = [
  ["overview", "◈", "监控概览"],
  ["events", "▥", "事件中心"],
  ["apps", "▦", "应用库"],
  ["watchlist", "◎", "重点监控"],
  ["charts", "↗", "榜单中心"],
  ["featured", "✦", "精品推荐"],
  ["keywords", "⌕", "关键词中心"],
  ["aso", "✎", "ASO 工作台"],
  ["compare", "⇄", "竞品集合"],
  ["reviews", "▤", "评论工作台"],
  ["market", "◷", "市场分布"],
  ["developers", "▣", "开发者"],
  ["collection", "⟳", "采集中心"],
  ["ads", "◇", "广告观察"],
  ["alerts", "◉", "告警与日报"],
  ["exports", "↓", "数据导出"],
];
$("#navigation").innerHTML = nav
  .map(
    ([id, icon, label]) =>
      `<a href="#${id}" data-nav="${id}"><span class="nav-icon">${icon}</span>${label}</a>`,
  )
  .join("");
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").style.display = "none"), 4500);
}
async function api(path, body, method) {
  const response = await fetch("/api" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(accessToken ? { Authorization: "Bearer " + accessToken } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.status === 401) {
    if (!$("#login").open) $("#login").showModal();
    throw new Error("需要访问令牌");
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
    ? `<div class="table-wrap"><table><thead><tr>${headers.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((x) => `<td>${x ?? "—"}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : empty();
}
function badge(value) {
  const labels = {
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
  const cls = ["error", "region_unavailable"].includes(value)
    ? "error"
    : ["suspected_missing", "queued", "running"].includes(value)
      ? "warn"
      : value
        ? ""
        : "neutral";
  return `<span class="badge ${cls}">${esc(labels[value] || value || "未验证")}</span>`;
}
function move(value) {
  return value == null
    ? "—"
    : `<span class="${value > 0 ? "up" : value < 0 ? "down" : ""}">${value > 0 ? "↑ " + value : value < 0 ? "↓ " + Math.abs(value) : "持平"}</span>`;
}
function appLink(store, id, name, icon, country, language) {
  const img = safeURL(icon);
  return `<div class="appcell">${img ? `<img class="appicon" src="${img}" alt="" loading="lazy">` : '<span class="appicon">◈</span>'}<div><button class="link appname" data-app="${esc(id)}" data-store="${esc(store)}" data-country="${esc(country || market().country)}" data-language="${esc(language || market().language)}">${esc(name || id)}</button><div class="appid">${esc(id)}</div></div></div>`;
}
function field(label, name, value = "", type = "text", extra = "") {
  return `<label>${label}<input name="${name}" value="${esc(value)}" type="${type}" ${extra}></label>`;
}
function select(label, name, options) {
  return `<label>${label}<select name="${name}">${options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label>`;
}
function formData(form) {
  return Object.fromEntries(new FormData(form));
}
function bindForm(id, callback) {
  const f = $(id);
  if (!f) return;
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
function chart(points, invert = false, formatAt = stamp) {
  if (!points.some((p) => p.value != null))
    return empty("暂无可绘制的历史", "至少需要一个成功观察时间点。");
  const w = 800,
    h = 190,
    pad = 35,
    values = points.filter((p) => p.value != null).map((p) => p.value),
    lo = invert ? 1 : Math.min(...values),
    hi = Math.max(lo + 1, ...values);
  const x = (i) => pad + ((w - 2 * pad) * i) / Math.max(1, points.length - 1),
    y = (v) =>
      25 +
      (h - 55) * (invert ? (v - lo) / (hi - lo) : 1 - (v - lo) / (hi - lo));
  let segments = [],
    current = [];
  points.forEach((p, i) => {
    if (p.value == null) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push([x(i), y(p.value), p]);
  });
  if (current.length) segments.push(current);
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="观察历史趋势">${[lo, hi].map((v) => `<line class="gridline" x1="${pad}" x2="${w - pad}" y1="${y(v)}" y2="${y(v)}"/><text x="0" y="${y(v) + 4}">${esc(v)}</text>`).join("")}${segments.map((s) => `<polyline points="${s.map((p) => p.slice(0, 2).join(",")).join(" ")}"/>${s.map(([xx, yy, p]) => `<circle cx="${xx}" cy="${yy}" r="3"><title>${esc(formatAt(p.at))}：${esc(p.value)}</title></circle>`).join("")}`).join("")}<text x="${pad}" y="${h - 3}">${esc(formatAt(points[0].at))}</text><text text-anchor="end" x="${w - pad}" y="${h - 3}">${esc(formatAt(points.at(-1).at))}</text></svg>`;
}
function bars(rows) {
  const max = Math.max(1, ...rows.map((r) => Number(r.count)));
  return (
    rows
      .map(
        (r) =>
          `<div class="barrow"><span>${esc(r.label || r.rating || "未知")}</span><svg class="bar-chart" width="100%" height="10" viewBox="0 0 100 10" preserveAspectRatio="none"><rect width="100" height="10" class="bar-track"/><rect width="${(Number(r.count) / max) * 100}" height="10" class="bar-value"/></svg><span>${num(r.count)}</span></div>`,
      )
      .join("") || empty()
  );
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
function mount(html, rev) {
  if (rev != null && rev !== revision) return false;
  $("#content").innerHTML = html;
  return true;
}

async function overview(rev) {
  const data = await api("/overview");
  const c = data.counts;
  mount(
    `<p class="lead">从应用变化到搜索表现，查看你的重点市场与采集进度。</p><div class="cards">${metric("已知应用池", null, "<span id=pool-caption>正在读取精确行数…</span>").replace('class="value"', 'class="value" id="pool-count"')}${metric("重点应用", c.watchlist, "按商店 / 国家 / 语言独立监控")}${metric("跟踪关键词", c.keyword_targets, "保存有序搜索快照")}${metric("未读告警", c.unread_alerts, "基于观察证据触发")}</div><div class="callout">后台最近心跳：${esc(stamp(data.worker?.next_at))}。页面刷新读取已存数据；“安排刷新”由后台任务执行。</div><div class="grid2">${panel(
      "地区覆盖",
      table(
        ["商店", "国家", "状态", "已观察应用", "最后成功"],
        data.freshness.map((r) => [
          esc(r.store),
          esc(r.country.toUpperCase()),
          badge(r.state),
          num(r.count),
          stamp(r.last_success),
        ]),
      ),
    )}${panel(
      "采集异常",
      Object.entries(data.failures)
        .map(([kind, rows]) =>
          rows.length
            ? table(
                ["任务类型 / ID", "连续失败", "原因"],
                rows.map((r) => [
                  esc(kindLabel(kind)) + " / " + r.id,
                  r.failures,
                  esc(r.last_error),
                ]),
              )
            : '<p class="sub">' + esc(kindLabel(kind)) + "：暂无失败记录</p>",
        )
        .join(""),
    )}</div>${panel(
      "最新变更",
      table(
        ["应用", "地区", "事件", "观察时间"],
        data.recent_events.map((r) => [
          appLink(r.store, r.app_id, r.name, null, r.country, r.language),
          esc(r.country),
          esc(kindLabel(r.kind)),
          stamp(r.observed_at),
        ]),
      ),
    )}`,
    rev,
  );
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
async function applications(rev, filters = {}) {
  const data = await api("/apps?" + qs({ ...market(), ...filters }));
  if (
    !mount(
      `<p class="lead">搜索已知应用池，按当前市场查看在架状态与商店资料。历史种子未验证前不会标为在架。</p><div class="application-layout">${selectors.categorySidebar(market().store, filters.category)}<section class="panel"><form id="app-search" class="toolbar">${field("应用名称 / 精确 ID", "q", filters.q || "", "text", 'class="wide"')}${selectors.categoryField(market().store, filters.category || "", "pool-category")}${field("开发者（精确名称）", "developer", filters.developer || "")}${select(
        "状态",
        "state",
        [
          ["", "全部"],
          ["available", "在架观察"],
          ["region_unavailable", "地区不可用"],
          ["suspected_missing", "待确认"],
        ],
      )}<button type="submit">筛选</button><details class="advanced-filters"><summary>更多条件：评分、售价与商店日期</summary><div class="toolbar">${field("最低商店评分", "min_rating", filters.min_rating || "", "number", 'min="0" max="5" step="0.1"')}${field("最低累计评分数", "min_ratings", filters.min_ratings || "", "number", 'min="0" step="1"')}${select(
        "售价",
        "pricing",
        [
          ["", "全部"],
          ["free", "免费"],
          ["paid", "付费"],
        ],
      )}${field("商店更新时间不早于", "updated_since", filters.updated_since || "", "date")}${field("商店发布日期不早于", "released_since", filters.released_since || "", "date")}</div><p class="sub">仅筛选已观察且具备相应字段的地区记录；未知日期不会当作0。</p></details></form><div class="bulk-toolbar"><button type="button" id="bulk-monitor" class="secondary" disabled>批量加入监控</button><button type="button" id="bulk-compare" class="secondary" disabled>保存竞品集合</button><span id="pool-selection-count" class="selection-count">选择当前页应用；监控最多50个，对比2–6个。</span></div>${table(
        [
          '<input id="pool-select-all" type="checkbox" aria-label="选择本页全部应用">',
          "应用",
          "商店 / 分类",
          "状态",
          "评分 / 评分数",
          "最后成功",
        ],
        data.items.map((r) => [
          `<input type="checkbox" data-pool-select="${esc(r.app_id)}" aria-label="选择 ${esc(r.payload?.name || r.name || r.app_id)}">`,
          appLink(
            r.store,
            r.app_id,
            r.payload?.name || r.name,
            r.payload?.icon,
          ),
          esc(r.store) +
            '<div class="sub">' +
            esc(r.category_name || r.category || "未分类") +
            "</div>",
          badge(r.state),
          `${num(r.payload?.rating)} / ${num(r.payload?.ratings)}`,
          stamp(r.last_success),
        ]),
      )}${data.next ? '<button id="next-apps" class="secondary spacer">下一页</button>' : ""}</section></div>`,
      rev,
    )
  )
    return;
  $("[name=state]", $("#app-search")).value = filters.state || "";
  $("[name=pricing]", $("#app-search")).value = filters.pricing || "";
  const picked = () =>
    Array.from(document.querySelectorAll("[data-pool-select]:checked")).map(
      (n) => n.dataset.poolSelect,
    );
  function selection() {
    const count = picked().length;
    $("#pool-selection-count").textContent = "已选 " + count + " 个应用";
    $("#bulk-monitor").disabled = count < 1 || count > 50;
    $("#bulk-compare").disabled = count < 2 || count > 6;
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
  $("#next-apps")?.addEventListener("click", () =>
    applications(++revision, { ...filters, ...data.next }).catch((e) =>
      toast(e.message),
    ),
  );
}
async function appDetail(store, id, rev = ++revision) {
  $("#page-title").textContent = "应用详情";
  const data = await api(
    `/apps/${encodeURIComponent(store)}/${encodeURIComponent(id)}?` +
      qs(market()),
  );
  const p = data.listing?.payload || {},
    m = { ...market(), store, app_id: id };
  if (
    !mount(
      `<div class="panel-heading"><div>${appLink(store, id, p.name || data.app.name, p.icon)}<p class="sub">${esc(store)} · ${esc(m.country.toUpperCase())} · ${esc(m.language)} · ${esc(p.developer || "开发者待采集")}</p></div><div class="actions"><button id="quick-watch">加入重点监控</button><button class="secondary" id="open-aso-workbench">ASO 工作台</button></div></div><div class="callout">最近成功：${esc(stamp(data.listing?.last_success))} · ${data.listing ? esc(data.listing.state) : "未采集"}${data.job?.last_error ? " · 最近失败：" + esc(data.job.last_error) : ""}</div><div class="cards">${metric("商店评分", p.rating, "来自最近一次商店观察")}${metric("评分数", p.ratings, "累计公开评分数量")}${metric("版本快照", data.snapshots.length, "显示最近最多100次变更")}${metric("覆盖关键词", data.keywords.coverage.length, "仅限本系统已跟踪词库")}</div><div class="grid2">${panel("当前商店资料", `<h3>${esc(p.name || "等待采集")}</h3><p>版本 ${esc(p.version || "—")} · ${esc(data.app.category_name || p.category || "—")}</p><p>${esc(p.summary || "")}</p><details><summary>描述与版本说明</summary><div class="review-text">${esc(p.description || "暂无描述")}</div><h3 class="spacer">版本说明</h3><div class="review-text">${esc(p.release_notes || "暂无版本说明")}</div></details>${safeURL(p.url) ? `<a href="${safeURL(p.url)}" target="_blank" rel="noopener">查看商店原页 ↗</a>` : ""}`)}${panel("评分变化", chart([...data.snapshots].reverse().map((r) => ({ at: r.observed_at, value: r.payload?.rating }))))}</div><div id="app-market-matrix"></div>${panel("素材与版本历史", `<label>选择历史快照<select id="snapshot-choice">${data.snapshots.map((r, i) => `<option value="${i}">${esc(stamp(r.observed_at))} · ${esc(r.payload?.version || "")}</option>`).join("")}</select></label><div id="snapshot-view"></div>`)}${panel(
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
      )}${panel("变更时间线", data.events.length ? `<div class="timeline">${data.events.map((r) => `<article><strong>${esc(kindLabel(r.kind))}</strong><div class="sub">${esc(stamp(r.observed_at))}</div><details><summary>查看字段差异</summary>${jsonBlock(JSON.parse(r.changes))}</details></article>`).join("")}</div>` : empty())}${panel("ASO 核查与候选词", '<button id="aso-check" class="secondary">根据已存记录生成建议</button><div id="aso-result" class="spacer"></div>')}`,
      rev,
    )
  )
    return;
  researchPages.marketMatrix(store, id, rev).catch((e) => toast(e.message));
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
  renderSnapshot();
  $("#snapshot-choice").addEventListener("change", renderSnapshot);
  $("#quick-watch").addEventListener("click", async () => {
    try {
      await api("/watchlist", m);
      toast("已加入重点监控");
    } catch (e) {
      toast(e.message);
    }
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
async function watchlist(rev) {
  const rows = await api("/watchlist");
  if (
    !mount(
      `<p class="lead">重点应用使用独立工作进程，避免被百万应用基线队列拖延。国家与语言使用顶部设置。</p>${panel("添加重点应用", `<form id="watch-form" class="toolbar">${field("应用 ID", "app_id", "", "text", "required")}${field("监控分组", "group_name", "重点应用", "text", "required")}${field("刷新周期（小时）", "refresh_hours", 24, "number", 'min="0.25" max="8760" step="0.25" required')}<button type="submit">添加监控</button></form>`)}${panel(
        "监控列表",
        table(
          ["应用", "市场 / 分组", "状态", "周期 / 下次采集", "操作"],
          rows.map((r) => [
            appLink(r.store, r.app_id, r.name, null, r.country, r.language),
            `${esc(r.country)} / ${esc(r.language)}<div class="sub">${esc(r.group_name)}</div>`,
            badge(r.state) +
              `<div class="sub">${r.enabled ? "监控中" : "已暂停"}${r.failures ? " · " + r.failures + "次失败" : ""}</div>`,
            r.refresh_hours + 'h<div class="sub">' + stamp(r.due) + "</div>",
            controlButtons("watchlist", r),
          ]),
        ),
      )}`,
      rev,
    )
  )
    return;
  bindForm("#watch-form", async (data) => {
    await api("/watchlist", {
      ...market(),
      ...data,
      refresh_hours: Number(data.refresh_hours),
    });
    toast("监控目标已保存");
    await watchlist(revision);
  });
}
async function observations(rev, kind) {
  const rows = await api("/targets?kind=" + kind);
  const isReview = kind === "reviews",
    isFeatured = kind === "featured";
  if (
    !mount(
      `<p class="lead">${isFeatured ? "记录 Apple Today 推荐卡片及其应用关联。卡片展示顺序与商店榜单排名分别保存。" : isReview ? "持续采集最近评论，保留编辑前的历史观察。公开来源返回范围有限。" : "按商店、国家、分类保存有序榜单；新入榜、掉出观察范围与名次变化均可回溯。"}</p>${panel(
        "配置采集目标",
        `<form id="target-form" class="toolbar">${isReview ? field("应用 ID", "app_id", "", "text", "required") : ""}${
          !isReview && !isFeatured
            ? select("榜单", "collection", [
                ["top-free", "免费榜"],
                ["top-paid", "付费榜"],
                ["top-grossing", "商店畅销排行"],
              ]) +
              selectors.categoryField(
                market().store,
                "",
                "chart-category",
                "chart",
              )
            : ""
        }${field("最多保留条数", "depth", 100, "number", 'min="1" max="500" required')}${field("周期（小时）", "refresh_hours", 24, "number", 'min="0.25" max="8760" step="0.25" required')}<button type="submit">添加目标</button></form>${isFeatured ? '<p class="sub">此模块固定使用 Apple，国家与语言沿用顶部设置。</p>' : ""}`,
      )}${panel(
        "采集任务",
        table(
          ["目标", "市场", "范围 / 周期", "状态", "操作"],
          rows.map((r) => [
            `<button class="link" data-target="${r.id}">${esc(r.app_id || r.collection || "Apple Today")}</button><div class="sub">${esc(selectors.categoryName(r.store, r.category))}</div>`,
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
            controlButtons("targets", r),
          ]),
        ),
      )}`,
      rev,
    )
  )
    return;
  bindForm("#target-form", async (data) => {
    await api("/targets", {
      ...market(),
      ...data,
      kind,
      ...(isFeatured ? { store: "apple" } : {}),
      depth: Number(data.depth),
      refresh_hours: Number(data.refresh_hours),
    });
    toast("采集目标已保存");
    await observations(revision, kind);
  });
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
      ? "推荐历史"
      : t.kind === "reviews"
        ? "评论采集历史"
        : "榜单历史";
  const header = `<p class="lead">${esc(t.store)} · ${esc(t.country)} / ${esc(t.language)} · ${esc(t.collection || t.app_id || "Today")} · ${esc(selectors.categoryName(t.store, t.category))}</p>`;
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
            ["卡片顺序", "标题 / 故事", "关联应用"],
            report.items.map((r) => [
              r.position,
              `<strong>${esc(typeof r.name === "string" ? r.name : JSON.stringify(r.name))}</strong>${safeURL(r.url) ? `<div><a href="${safeURL(r.url)}" target="_blank" rel="noopener">查看故事 ↗</a></div>` : ""}`,
              (r.apps || [])
                .map((a) => appLink("apple", a.app_id, a.name))
                .join(""),
            ]),
          )
        : table(
            ["排名", "应用", "开发者", "变化"],
            report.items.map((r) => [
              r.position,
              appLink(t.store, r.app_id, r.name, r.icon),
              esc(r.developer),
              move(r.change),
            ]),
          );
  if (
    !mount(
      header +
        evidence(report) +
        panel(
          "历史时间点",
          `<select id="run-choice" aria-label="历史时间点"><option value="">最新成功观察</option>${report.history.map((r) => `<option value="${r.id}" ${r.id === runId ? "selected" : ""}>${esc(stamp(r.observed_at))} · ${r.outcome} · ${r.returned_count ?? "—"} 条</option>`).join("")}</select>`,
        ) +
        panel(t.kind === "featured" ? "推荐卡片" : "观察记录", rows) +
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
  const rows = await api("/keywords");
  if (
    !mount(
      `<p class="lead">为每个国家与请求语言维护词库。排名来自实际返回顺序，未出现不等于零排名。</p>${panel("添加关键词", `<form id="keyword-form" class="toolbar">${field("关键词（支持任意语言）", "keyword", "", "text", "required")}${field("结果上限", "depth", 50, "number", 'min="1" max="200" required')}${field("周期（小时）", "refresh_hours", 24, "number", 'min="0.25" step="0.25" required')}<button type="submit">开始跟踪</button></form><details><summary>批量导入 JSON</summary><form id="keyword-import"><label>关键词数组<textarea name="json" placeholder='[{"store":"google","country":"th","language":"th","keyword":"เงินกู้"}]' required></textarea></label><button type="submit" class="spacer">导入词库</button></form></details>`)}${panel(
        "跟踪词库",
        table(
          ["关键词", "商店 / 市场", "深度 / 周期", "最近尝试", "操作"],
          rows.map((r) => [
            `<button class="link" data-keyword="${r.id}">${esc(r.keyword)}</button>`,
            `${esc(r.store)} · ${esc(r.country)} / ${esc(r.language)}`,
            `${r.depth} / ${r.refresh_hours}h`,
            r.last_error
              ? esc(r.last_error)
              : r.latest_success_id
                ? "已有观察"
                : "等待采集",
            controlButtons("keywords", r),
          ]),
        ),
      )}`,
      rev,
    )
  )
    return;
  bindForm("#keyword-form", async (data) => {
    await api("/keywords", [
      {
        ...market(),
        ...data,
        depth: Number(data.depth),
        refresh_hours: Number(data.refresh_hours),
      },
    ]);
    toast("关键词已保存");
    await keywordPage(revision);
  });
  bindForm("#keyword-import", async (data) => {
    await api("/keywords", JSON.parse(data.json));
    toast("词库已导入");
    await keywordPage(revision);
  });
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
      `<p class="lead">${esc(r.target.store)} · ${esc(r.target.country)} / ${esc(r.target.language)}</p>${evidence(r)}${panel(
        "搜索排名",
        table(
          ["位置", "应用", "变化", "历史"],
          r.ranks.map((a) => [
            a.position,
            appLink(r.target.store, a.app_id, a.name),
            move(a.change),
            `<button class="link" data-rank-history="${id}" data-rank-app="${esc(a.app_id)}">查看曲线</button>`,
          ]),
        ),
      )}<div id="rank-history"></div>`,
      rev,
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
    `<div class="callout">${esc(r.scope)}</div><div class="grid2">${panel("分类分布 · 已观察应用", bars(r.categories.map((x) => ({ ...x, label: x.store + " / " + x.label }))))}${panel(
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
      `<p class="lead">汇总有来源证据的广告观察与自有广告账户实际报表。自然搜索排名不会被当作广告投放记录。</p>${panel(
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
      `<p class="lead">告警以可追溯的变更和采集记录为依据，重复观察自动去重。日报覆盖最近 24 小时。</p>${panel(
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
      `<p class="lead">导出由后台流式处理，完成后下载 JSONL。可按商店、应用或市场过滤；空条件导出所选数据集全部记录。</p>${panel(
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
          ["review_workflow", "评论处理记录"],
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
  bindForm("#export-form", async (data) => {
    await api("/exports", {
      kind: data.kind,
      filters: JSON.parse(data.filters),
    });
    toast("导出已排队");
    await exportPage(revision);
  });
}

const researchPages = createResearchPages({
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
  reviewTargets: () => {
    location.hash = "collection";
  },
});
const routes = {
  overview,
  events: researchPages.events,
  aso: researchPages.aso,
  developers: researchPages.developers,
  collection: researchPages.collectionCenter,
  apps: applications,
  watchlist,
  charts: (r) => observations(r, "chart"),
  featured: (r) => observations(r, "featured"),
  keywords: keywordPage,
  compare: comparison,
  reviews: reviewPage,
  market: marketPage,
  ads: adPage,
  alerts: alertPage,
  exports: exportPage,
};
async function render() {
  $("#research-dialog")?.close();
  const name = location.hash.slice(1) || "overview",
    route = routes[name] || overview,
    rev = ++revision;
  $("#page-title").textContent = (nav.find(
    (n) => n[0] === name.split("/")[0],
  ) || nav[0])[2];
  document
    .querySelectorAll("[data-nav]")
    .forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  mount('<div class="empty">正在加载观察记录…</div>');
  try {
    await selectors.ready();
    selectors.syncCountry();
    const parts = name.split("/");
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
bindForm("#login-form", async (data, form) => {
  accessToken = data.token;
  try {
    await api("/session", {});
    $("#login").close();
    form.reset();
    accessToken = "";
    await render();
  } catch (e) {
    $("#login-error").textContent = e.message;
    accessToken = "";
  }
});
$("#login").addEventListener("cancel", (event) => event.preventDefault());
$("#refresh-page").addEventListener("click", render);
["global-store", "global-country", "global-language"].forEach((id) =>
  $("#" + id).addEventListener("change", () => {
    saveMarket();
    render();
  }),
);
window.addEventListener("hashchange", render);
render();
