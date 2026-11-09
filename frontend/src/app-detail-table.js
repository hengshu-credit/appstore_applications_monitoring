import { storeLogo } from "./icons.js";

let sequence = 0;
export function createAppDetailTable(ctx) {
  const {
    esc,
    stamp,
    num,
    countryName = (c) => c.toUpperCase(),
    categoryName = (_s, c) => c,
    chartName = (_s, c) =>
      ({
        "top-free": "免费榜",
        "top-paid": "付费榜",
        "top-grossing": "畅销榜",
      })[c] || c,
    appIconMarkup = () => "",
  } = ctx;
  const storeName = (s) => (s === "apple" ? "App Store" : "Google Play");
  const stateNames = {
    available: "已验证在架",
    suspected_missing: "本次未检出",
    region_unavailable: "地区不可用",
    unverified: "无商店记录",
  };
  const classifications = {
    available: "在架",
    region_unavailable: "地区不可用",
    suspected_missing: "商店未检出",
    unverified: "暂无依据",
  };
  const permissionNames = {
    ok: "已采集",
    pending: "待采集",
    running: "采集中",
    not_scheduled: "未配置",
    stale: "待刷新",
    error: "采集异常",
    paused: "已暂停",
    unsupported: "公开接口未提供",
  };
  const detailTaskNames = {
    not_scheduled: "尚未配置",
    paused: "已暂停",
    running: "采集中",
    retry: "等待重试",
    scheduled: "按期采集",
    pending: "排队待采",
  };
  const permissionGroupName = (name) =>
    ({
      Location: "位置",
      Contacts: "通讯录",
      Camera: "相机",
      Microphone: "麦克风",
      SMS: "短信",
      Phone: "电话",
      Storage: "存储",
      Identity: "身份",
      Other: "其他",
      Calendar: "日历",
      "Wi-Fi connection information": "无线网络",
      "Device & app history": "应用历史",
      "Photos/Media/Files": "照片与文件",
      "Device ID & call information": "设备与通话信息",
    })[name] || name;
  const decimalText = (value) => {
    const [whole, part = ""] = String(value).split(".");
    const fraction = part.replace(/0+$/, "");
    return (
      whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") +
      (fraction ? "." + fraction : "")
    );
  };
  const compactCount = (value) => {
    const count = Number(value);
    if (count >= 1e12) return count.toExponential(1);
    return count >= 10000
      ? new Intl.NumberFormat("zh-CN", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(count)
      : num(value);
  };
  const corePermissions = [
    "Location",
    "Contacts",
    "Camera",
    "Microphone",
    "SMS",
    "Phone",
    "Storage",
  ];
  function permissionValue(row, name) {
    if (row.store !== "google") return null;
    if (Object.hasOwn(row.permission_overrides || {}, name))
      return row.permission_overrides[name];
    if (row.permission_success == null && row.permission_state !== "ok")
      return null;
    return (row.permission_groups || []).includes(name);
  }
  const developerSources = {
    store: "商店详情",
    watchlist: "导入名单",
    seed: "公开种子快照",
  };
  function developerCaption(row) {
    const name = row.developer;
    const id = row.developer_id;
    const caption = name || "-";
    const hints = [];
    if (name)
      hints.push(
        `开发者名称 · ${name}（${developerSources[row.developer_source] || "商店详情"}）`,
      );
    if (id)
      hints.push(
        `开发者 ID · ${id}（${developerSources[row.developer_id_source] || "商店详情"}）`,
      );
    if (!hints.length)
      hints.push(
        row.details_state === "paused"
          ? "详情采集已暂停，暂无开发者记录"
          : "暂无开发者记录",
      );
    return `<small class="member-developer" title="${esc(hints.join(" / "))}">${esc(caption)}</small>`;
  }
  return function detailTable(data, options = {}) {
    const prefix = options.id || `app-detail-${++sequence}`;
    const writable = Boolean(options.writable);
    const columns = [];
    const add = (key, label, section, cell) =>
      columns.push({
        key,
        label,
        section,
        cell,
        heading: (options.leadingColumns || []).find((c) => c.key === key)
          ?.heading,
      });
    const number = (v) => (v == null ? "-" : num(v));
    const permissionKeys = [
      ...new Set([
        ...corePermissions,
        ...(data.permission_categories || []),
        ...data.items.flatMap((r) => [
          ...(r.permission_groups || []),
          ...Object.keys(r.permission_overrides || {}),
        ]),
      ]),
    ];
    if (writable)
      add(
        "select",
        "选择",
        "",
        (r) =>
          `<input type="checkbox" data-group-check="${r.id}" aria-label="选择 ${esc(r.name || r.app_id)}">`,
      );
    for (const col of options.leadingColumns || [])
      add(col.key, col.label, col.section || "", col.cell);
    add("app", "应用", "", (r) =>
      options.appCell
        ? options.appCell(r, developerCaption(r))
        : `<div class="member-identity">${appIconMarkup(r.store, r.app_id, r.name, r.icon, r.country, r.language)}<div><button class="link member-app-name" data-group-member="${r.id}" title="${esc(r.app_id)}">${esc(r.name || r.app_id)}</button>${developerCaption(r)}</div></div>`,
    );
    add(
      "store",
      "应用商店",
      "",
      (r) =>
        `<span class="member-store">${storeLogo(r.store)}<span>${esc(storeName(r.store))}</span></span>`,
    );
    add("category", "类别", "", (r) =>
      esc(r.category ? categoryName(r.store, r.category) : "-"),
    );
    add(
      "country",
      "市场",
      "",
      (r) =>
        `<span class="member-country" title="${esc(storeName(r.store))} · ${esc(r.country.toUpperCase())}"><img class="country-flag" alt="" src="/flags/${esc(r.country)}.svg">${esc(countryName(r.country))}<small>${esc(r.language)}</small></span>`,
    );
    add("state", "状态", "状态监控", (r) => {
      const state = r.classification || r.state || "unverified";
      return `<span class="member-state" data-status="${esc(state)}" title="${esc(stateNames[r.state] || "")}">${esc(classifications[state])}</span>`;
    });
    add(
      "task",
      "详情采集",
      "状态监控",
      (r) =>
        `<span title="${esc(r.last_error || stamp(r.last_success))}">${esc(detailTaskNames[r.details_state] || "-")}</span>`,
    );
    add(
      "permission_state",
      "采集状态",
      "权限采集",
      (r) =>
        `<span title="${esc(r.permission_error || stamp(r.permission_success))}">${esc(permissionNames[r.permission_state] || "待采集")}</span>`,
    );
    add("permission_count", "项数", "权限采集", (r) =>
      number(r.permission_count),
    );
    permissionKeys.forEach((name, index) =>
      add("perm" + index, permissionGroupName(name), "权限采集", (r) => {
        const value = permissionValue(r, name),
          manual = Object.hasOwn(r.permission_overrides || {}, name);
        const label =
          r.store === "apple"
            ? "此商店未提供"
            : value == null
              ? "尚无采集结果"
              : value
                ? "已声明"
                : "未声明";
        return `<span class="permission-mark" data-permission="${esc(name)}" data-value="${value}" title="${esc(permissionGroupName(name))} · ${label}${manual ? " · 明细修正" : ""}" aria-label="${label}">${value == null ? "-" : value ? "✓" : "×"}</span>`;
      }),
    );
    const metricKey = (f) =>
      JSON.stringify([f.metric, Boolean(f.is_estimate), f.currency || ""]);
    const series = new Map(
      (data.money || [])
        .concat(data.items.flatMap((r) => r.metrics || []))
        .map((f) => [metricKey(f), f]),
    );
    if (![...series.values()].some((f) => f.metric === "downloads"))
      series.set("no-downloads", { metric: "downloads" });
    if (![...series.values()].some((f) => f.metric === "revenue"))
      series.set("no-revenue", { metric: "revenue" });
    [...series.entries()]
      .sort(
        ([a, f], [b, g]) =>
          f.metric.localeCompare(g.metric) || a.localeCompare(b),
      )
      .forEach(([key, f], index) => {
        const name = f.metric === "downloads" ? "下载量" : "收入";
        const label = key.startsWith("no-")
          ? name
          : `${name} · ${f.is_estimate ? "估算" : "实际"}${f.currency ? " " + f.currency : ""}`;
        const fact = (r) =>
          (r.metrics || []).find((item) => metricKey(item) === key);
        add("fact" + index, label, name, (r) => {
          const item = fact(r);
          if (!item)
            return `<span title="${name === "下载量" ? "暂无国家/日期下载数据，公开累计安装数另列" : "暂无授权收入数据"}">-</span>`;
          const change = item.change_pct;
          const growth =
            change == null
              ? ""
              : `<small class="member-growth" data-up="${change >= 0}" title="较上一等长期间">${change >= 0 ? "+" : ""}${change.toFixed(1)}%</small>`;
          const mix = item.market_mix || [];
          const regionLabel = mix
            .map(
              (part) => `${countryName(part.country)} ${part.pct.toFixed(1)}%`,
            )
            .join(" · ");
          const bar = mix.length
            ? `<span class="member-market-mix" role="img" aria-label="${esc("已收录市场占比 " + regionLabel)}" title="${esc("已收录市场占比 " + regionLabel)}">${mix.map((part) => `<i style="flex-grow:${Number(part.pct)};--mix-index:${part.country.charCodeAt(0) * 26 + part.country.charCodeAt(1)}"></i>`).join("")}</span>`
            : "";
          const value =
            Math.abs(Number(item.value)) >= 10000
              ? compactCount(item.value)
              : decimalText(item.value);
          const local = mix.find((part) => part.country === r.country);
          const share =
            local && mix.length > 1
              ? `<small class="member-share">${esc(countryName(r.country))} ${local.pct.toFixed(1)}%</small>`
              : "";
          return `<span title="${esc(label)} · ${esc(decimalText(item.value))} · ${num(item.days)} 天">${esc(value)}</span>${growth}${bar}${share}`;
        });
        if (name === "下载量" && index === 0)
          add("installs", "累计安装下限", name, (r) =>
            r.installs == null
              ? "-"
              : `<span title="全球累计安装下限">${num(r.installs)}+</span>`,
          );
      });
    add("rating", "评分", "评分", (r) =>
      r.rating == null
        ? "-"
        : `<span class="member-score">${Number(r.rating).toFixed(2)}</span>`,
    );
    add("ratings", "评分数", "评分", (r) => number(r.ratings));
    add(
      "yesterday_reviews",
      "昨日新增评论",
      "评分",
      (r) =>
        `<span title="已采集且发表于昨日 UTC 的评论">${number(r.yesterday_reviews)}</span>`,
    );
    const wordLabel = (items) =>
      (items || [])
        .map((item) => `${item.keyword} #${item.position}`)
        .join(" · ");
    add(
      "keywords",
      "关键词覆盖",
      "关键词",
      (r) =>
        `<span title="${esc(wordLabel(r.top_keywords) || (r.keyword_count == null ? "当前市场暂无有效关键词采样" : "当前有效采样未命中本应用"))}">${number(r.keyword_count)}</span>`,
    );
    add(
      "top3_count",
      "TOP3 关键词",
      "关键词",
      (r) =>
        `<span title="${esc(wordLabel(r.top_keywords?.filter((item) => item.position <= 3)) || "有效样本中排名前三的关键词数")}">${number(r.keyword_top3_count)}</span>`,
    );
    const boardOrder = [
      "top-free",
      "top-paid",
      "top-grossing",
      "trending",
      "new-free",
      "new-grossing",
      "new-paid",
    ];
    const boardKey = (collection, category) =>
      JSON.stringify([Boolean(category), collection]);
    const boards = new Map();
    for (const category of [false, true])
      for (const collection of boardOrder.slice(0, 3))
        boards.set(boardKey(collection, category), { collection, category });
    const positions = new Map();
    for (const row of data.items) {
      const ranks = new Map();
      positions.set(row.id, ranks);
      for (const chart of row.chart_positions || []) {
        const category =
          chart.category === "APPLICATION" && row.store === "google"
            ? ""
            : chart.category;
        if (category && category !== row.category) continue;
        const key = boardKey(chart.collection, category);
        boards.set(key, {
          collection: chart.collection,
          category: Boolean(category),
        });
        ranks.set(key, chart);
      }
    }
    [...boards]
      .sort(
        ([, a], [, b]) =>
          Number(a.category) - Number(b.category) ||
          (boardOrder.indexOf(a.collection) < 0
            ? 99
            : boardOrder.indexOf(a.collection)) -
            (boardOrder.indexOf(b.collection) < 0
              ? 99
              : boardOrder.indexOf(b.collection)),
      )
      .forEach(([key, board], index) =>
        add(
          "chart" + index,
          chartName("google", board.collection),
          board.category ? "分类榜单" : "通用榜单",
          (r) => {
            const item = positions.get(r.id).get(key);
            const scope = board.category
              ? r.category
                ? categoryName(r.store, r.category)
                : "类别未知"
              : "全市场";
            if (!item?.sampled)
              return `<span title="${esc(scope)} · 暂无有效采样">-</span>`;
            return item.position == null
              ? ""
              : `<b class="member-rank" title="${esc(scope)}">${num(item.position)}</b>`;
          },
        ),
      );
    add(
      "developer_id",
      "开发者 ID",
      "商店资料",
      (r) =>
        `<span title="${esc(developerSources[r.developer_id_source] || "暂无已采集 ID")}">${esc(r.developer_id || "-")}</span>`,
    );
    add("app_id", "App ID", "商店资料", (r) => esc(r.app_id));
    add("price", "价格", "商店资料", (r) =>
      r.price == null
        ? "-"
        : Number(r.price) === 0
          ? "免费"
          : esc(decimalText(r.price) + " " + (r.currency || "")),
    );
    add("languages", "支持语言", "商店资料", (r) =>
      Array.isArray(r.languages)
        ? `<span title="${esc(r.languages.join(" / "))}">${num(r.languages.length)}</span>`
        : "-",
    );
    add("iap", "内购", "商店资料", (r) =>
      r.has_iap == null ? "-" : r.has_iap ? "有" : "无",
    );
    add("version", "版本", "商店资料", (r) => esc(r.version || "-"));
    add("updated", "商店更新", "商店资料", (r) =>
      r.updated
        ? esc(
            typeof r.updated === "number"
              ? stamp(r.updated)
              : String(r.updated).slice(0, 10),
          )
        : "-",
    );
    const groupOrder = [
      "状态监控",
      "商店资料",
      "通用榜单",
      "分类榜单",
      "排行榜",
      "权限采集",
      "下载量",
      "收入",
      "评分",
      "关键词",
    ];
    columns.sort(
      (a, b) =>
        (a.section ? groupOrder.indexOf(a.section) + 1 : 0) -
        (b.section ? groupOrder.indexOf(b.section) + 1 : 0),
    );
    if (writable)
      add(
        "action",
        "操作",
        "",
        () =>
          '<button type="button" class="link" data-adjust-row>调整</button>',
      );
    for (const col of options.trailingColumns || [])
      add(col.key, col.label, col.section || "", col.cell);
    const bands = [];
    const headersFor = new Map();
    for (const column of columns) {
      const name = ["app", "store", "category", "country"].includes(column.key)
        ? "应用信息"
        : column.section;
      let band = bands.at(-1);
      if (!band || !name || band.name !== name) {
        band = { name, id: prefix + "-band-" + bands.length, columns: [] };
        bands.push(band);
      }
      band.columns.push(column);
      const leaf = prefix + "-field-" + column.key;
      headersFor.set(column.key, name ? band.id + " " + leaf : leaf);
    }
    const colgroups = bands
      .map((band) => `<colgroup span="${band.columns.length}"></colgroup>`)
      .join("");
    const headings = bands
      .map(({ name, id, columns: children }) => {
        if (name)
          return `<th id="${id}" scope="colgroup" colspan="${children.length}"><span class="member-band-label">${esc(name)}</span></th>`;
        const c = children[0];
        const title = c.heading
          ? c.heading
          : c.key === "select"
            ? data.items.length
              ? '<input type="checkbox" id="group-select-page" aria-label="选择本页全部应用">'
              : ""
            : c.heading || esc(c.label);
        return `<th id="${prefix}-field-${c.key}" scope="col" rowspan="2" data-col="${c.key}">${title}</th>`;
      })
      .join("");
    const subheads = bands
      .filter((band) => band.name)
      .flatMap((band) =>
        band.columns.map(
          (c) =>
            `<th id="${prefix}-field-${c.key}" scope="col" headers="${band.id}" data-col="${c.key}">${esc(c.label)}</th>`,
        ),
      )
      .join("");
    const rows = data.items
      .map(
        (r) =>
          `<tr data-member-row="${r.id}">${columns.map((c) => `<td data-col="${c.key}" headers="${headersFor.get(c.key)}">${c.cell(r)}</td>`).join("")}</tr>`,
      )
      .join("");
    return `<div class="group-member-scroll" role="region" aria-label="应用全指标明细" tabindex="0"><table class="group-member-table">${colgroups}<thead><tr>${headings}</tr><tr>${subheads}</tr></thead><tbody>${rows || `<tr><td colspan="${columns.length}" class="empty">没有匹配的应用</td></tr>`}</tbody></table></div>`;
  };
}
