// Page responsibilities and navigation share one registry. Permission keys and
// historical routes remain unchanged when pages are grouped into workspaces.
export const navigationSections = [
  {
    label: "监控",
    modules: [
      { label: "工作台", pages: ["overview"] },
      { label: "应用分组", pages: ["groups"] },
      { label: "市场上下架", pages: ["availability"] },
      { label: "事件与告警", pages: ["events", "alerts"] },
    ],
  },
  {
    label: "分析",
    modules: [
      { label: "应用库", pages: ["apps"] },
      { label: "排行榜", pages: ["globalcharts", "charts", "featured"] },
      { label: "关键词与 ASO", pages: ["keywords", "aso"] },
      { label: "市场研究", pages: ["compare", "developers", "market", "ads"] },
      { label: "评论工作台", pages: ["reviews"] },
    ],
  },
  {
    label: "管理",
    modules: [
      { label: "采集中心", pages: ["collection", "watchlist"] },
      { label: "数据管理", pages: ["exports", "datasources"] },
      { label: "账户管理", pages: ["administration"] },
    ],
  },
];

export const pageInfo = {
  overview: [
    "监控概览",
    "store",
    "先看采集覆盖与异常，再进入分组处理关注的应用。",
  ],
  groups: [
    "应用分组",
    "group",
    "按业务分类管理一组 APP，在同一看板查看国家分布、上下架、权限、排名与经营指标。",
  ],
  availability: [
    "市场上下架",
    "market",
    "查看当前市场的状态快照和上下架事件；成组的应用监控请进入应用分组。",
  ],
  events: [
    "事件记录",
    "market",
    "追踪已观察到的变化，核查事件证据并记录处理结果。",
  ],
  alerts: ["告警与日报", "store", "设置提醒规则、处理告警并查看日报。"],
  apps: [
    "应用库",
    "market",
    "发现与检索应用，查看档案，勾选应用加入监控分组。",
  ],
  globalcharts: [
    "全球排行榜",
    "countries",
    "跨国家浏览排行榜，进入具体市场查看应用排名。",
  ],
  charts: ["榜单追踪", "market", "查看当前市场的榜单与已保存的采样历史。"],
  featured: ["精品推荐", "market", "查看商店推荐位及已采集的推荐记录。"],
  keywords: [
    "关键词研究",
    "market",
    "发现关键词、查看搜索结果和应用的有效排名。",
  ],
  aso: [
    "ASO 文案",
    "market",
    "结合应用详情和关键词覆盖，管理本地商店文案草稿。",
  ],
  compare: [
    "竞品对比",
    "market",
    "对比同市场的少量竞品；批量分类监控请使用应用分组。",
  ],
  developers: ["开发者", "store", "按开发者查看已知应用及关联资料。"],
  market: ["市场分布", "market", "分析已观察应用的分类、开发者与事件分布。"],
  ads: ["广告观察", "store", "管理广告观察证据与自有账户报表。"],
  reviews: ["评论工作台", "market", "检索已采集评论，分派问题并记录处理结果。"],
  collection: [
    "采集任务",
    "collection",
    "统一配置任务、运行周期和限速，查看队列、日志与数据质量。",
  ],
  watchlist: [
    "详情监控任务",
    "store",
    "管理单个 APP 的详情复查任务；按业务打包查看数据请进入应用分组。",
  ],
  exports: ["数据导出", "store", "选择数据集与范围，创建导出并下载已有结果。"],
  datasources: ["数据源配置", "none", "管理采集连接、授权配置与可用能力。"],
  administration: ["账户管理", "none", "管理账户、角色权限与个人设置。"],
};

export function navigationMarkup(icon) {
  return navigationSections
    .map(
      (section) =>
        `<div class="nav-section"><p class="nav-section-label">${section.label}</p>${section.modules.map((module) => `<a href="#${module.pages[0]}" data-nav="${module.pages[0]}" data-pages="${module.pages.join(" ")}"><span class="nav-icon">${icon(module.pages[0])}</span><span>${module.label}</span></a>`).join("")}</div>`,
    )
    .join("");
}

export function workspacePage(name) {
  const part = name.split("/")[0];
  const page =
    {
      app: "apps",
      keyword: "keywords",
      target: "collection",
      chart: "globalcharts",
    }[part] || part;
  const info = pageInfo[page] || pageInfo.overview;
  const detail = {
    app: "应用档案",
    keyword: "关键词档案",
    target: "采集目标详情",
    chart: "榜单详情",
  }[part];
  return {
    page,
    title: detail || info[0],
    scope: ["keyword", "target", "chart"].includes(part) ? "detail" : info[1],
    description: info[2],
  };
}

export function updateWorkspace(document, name, can) {
  const current = workspacePage(name);
  const module = navigationSections
    .flatMap((s) => s.modules)
    .find((m) => m.pages.includes(current.page));
  document.querySelectorAll("#navigation [data-pages]").forEach((a) => {
    const active = a.dataset.pages.split(" ").includes(current.page);
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.querySelector("#page-title").textContent = current.title;
  document.querySelector("#workspace-description").textContent =
    current.description;
  const tabs = document.querySelector("#workspace-pages");
  const pages = (module?.pages || []).filter((page) => can(page));
  tabs.hidden = pages.length < 2;
  tabs.innerHTML = pages
    .map(
      (page) =>
        `<a href="#${page}" ${page === current.page ? 'aria-current="page"' : ""}>${pageInfo[page][0]}</a>`,
    )
    .join("");
  const globalMarket = ["market", "collection"].includes(current.scope);
  document.querySelector(".store-field").hidden =
    !globalMarket && !["store", "countries"].includes(current.scope);
  document.querySelector(".country-field").hidden = !globalMarket;
  document.querySelector(".language-field").hidden = !globalMarket;
  const note = document.querySelector("#workspace-scope");
  note.textContent = {
    group: "范围：看板内的商店与国家筛选",
    store: "当前商店 · 全部国家",
    countries: "当前商店 · 国家范围在榜单内筛选",
    none: "全平台配置",
    detail: "范围：当前档案所记录的市场",
    collection: "列表按商店筛选 · 新建默认使用所选地区",
    market: "范围：当前商店、国家与请求语言",
  }[current.scope];
  return current;
}
