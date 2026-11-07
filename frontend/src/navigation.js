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
      { label: "评论记录", pages: ["reviews"] },
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
  overview: ["监控概览", "store", "查看所选商店的采集进度与异常。"],
  groups: ["应用分组", "group", "按国家对比组内应用的状态与指标。"],
  availability: ["市场上下架", "market", "按市场核查上下架变化。"],
  events: ["事件记录", "market", "对照变更前后的商店记录。"],
  alerts: ["告警与日报", "store", "查看告警和最近 24 小时日报。"],
  apps: ["应用库", "market", "勾选应用可批量加入监控分组。"],
  globalcharts: ["全球排行榜", "countries", "选择国家并排比较各地榜单。"],
  charts: ["榜单追踪", "market", "按市场追踪榜单排名与历史快照。"],
  featured: [
    "商店编辑推荐",
    "market",
    "查看 App Store Today 推荐故事、关联应用及历史曝光记录。",
  ],
  keywords: ["关键词研究", "market", "按市场查看关键词排名。"],
  aso: ["ASO 文案", "market", "编辑本地商店文案草稿。"],
  compare: ["竞品对比", "market", "对比同一市场的应用表现。"],
  developers: ["开发者", "store", "按商店开发者标识查询旗下应用。"],
  market: ["市场分布", "market", "仅统计当前市场已观察到的应用。"],
  ads: ["广告观察", "store", "查看广告观察证据与账户日报。"],
  reviews: ["评论记录", "market", "按应用筛选评论原文与采集历史。"],
  collection: ["采集任务", "collection", "点击任务分类筛选采集记录。"],
  watchlist: ["详情监控任务", "store", "按应用设置详情复查周期。"],
  exports: ["数据导出", "store", "按数据集和条件导出 JSONL。"],
  datasources: ["数据源配置", "none", "配置采集授权或导入数据文件。"],
  administration: ["账户管理", "none", "管理账户、角色权限与个人设置。"],
};

export function navigationMarkup(icon) {
  return navigationSections
    .map(
      (section) =>
        `<div class="nav-section"><p class="nav-section-label">${section.label}</p>${section.modules.map((module) => `<a href="#${module.pages[0]}" aria-label="${module.label}" title="${module.label}" data-nav="${module.pages[0]}" data-pages="${module.pages.join(" ")}"><span class="nav-icon">${icon(module.pages[0])}</span><span class="nav-text">${module.label}</span></a>`).join("")}</div>`,
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
    description:
      {
        app: "查看该应用的市场表现与采集历史。",
        chart: "查看当前榜单快照中的应用。",
        target: "查看该目标的采集记录。",
        keyword: "查看该关键词的搜索排名。",
      }[part] || info[2],
  };
}

export function setWorkspaceHint(doc, text) {
  const hint = doc.querySelector("#workspace-description");
  hint.textContent = text;
  hint.title = text;
}

export function updateWorkspace(document, name, can) {
  const current = workspacePage(name);
  const query = new URLSearchParams(
    document.defaultView?.location.hash.split("?")[1] || "",
  );
  const collectionView = query.has("new")
    ? "create"
    : query.get("view") || "tasks";
  if (current.page === "collection")
    current.title =
      {
        tasks: "采集任务",
        lineage: "采集血缘",
        pipelines: "基础采集流程",
        create: "新建采集计划",
      }[collectionView] || current.title;
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
  let crumbs = document.querySelector("#page-breadcrumbs");
  if (!crumbs) {
    crumbs = document.createElement("nav");
    crumbs.id = "page-breadcrumbs";
    crumbs.setAttribute("aria-label", "面包屑");
    document.querySelector("#page-title").before(crumbs);
  }
  const parents = [];
  if (current.page !== "overview") parents.push(["#overview", "工作台"]);
  if (module && module.label !== current.title && current.page !== "overview")
    parents.push(["#" + module.pages[0], module.label]);
  crumbs.innerHTML = parents
    .map(
      ([href, label]) =>
        `<a href="${href}">${label}</a><span aria-hidden="true">/</span>`,
    )
    .join("");
  crumbs.hidden = !parents.length;
  setWorkspaceHint(document, current.description);
  const tabs = document.querySelector("#workspace-pages");
  const pages = (module?.pages || []).filter((page) => can(page));
  tabs.hidden = pages.length < 2;
  tabs.innerHTML = pages
    .map(
      (page) =>
        `<a href="#${page}" ${page === current.page ? 'aria-current="page"' : ""}>${pageInfo[page][0]}</a>`,
    )
    .join("");
  if (["collection", "watchlist"].includes(current.page)) {
    const links = [
      ["#collection", "采集任务", "tasks"],
      ["#watchlist", "详情监控任务", "watchlist"],
      ["#collection?view=lineage", "采集血缘", "lineage"],
      ["#collection?view=pipelines", "基础采集流程", "pipelines"],
    ].filter(([, , key]) =>
      can(key === "watchlist" ? "watchlist" : "collection"),
    );
    tabs.hidden = false;
    const selected =
      current.page === "watchlist" ? "watchlist" : collectionView;
    tabs.innerHTML = links
      .map(
        ([href, label, key]) =>
          `<a href="${href}" ${key === selected ? 'aria-current="page"' : ""}>${label}</a>`,
      )
      .join("");
  }
  const globalMarket = ["market", "collection"].includes(current.scope);
  document.querySelector(".store-field").hidden =
    !globalMarket && !["store", "countries"].includes(current.scope);
  document.querySelector(".country-field").hidden = !globalMarket;
  document.querySelector(".language-field").hidden = !globalMarket;
  document.querySelector("#workspace-actions")?.replaceChildren();
  return current;
}
