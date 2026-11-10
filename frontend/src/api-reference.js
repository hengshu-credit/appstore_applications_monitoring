const METHODS = new Set([
  "get",
  "post",
  "put",
  "patch",
  "delete",
  "head",
  "options",
  "trace",
]);
const GROUPS = [
  ["应用与分组", ["/api/apps", "/api/groups", "/api/discovery/store"]],
  ["榜单与推荐", ["/api/charts", "/api/discovery/growth"]],
  [
    "关键词与 ASO",
    [
      "/api/keywords",
      "/api/discovery/keywords",
      "/api/discovery/reports",
      "/api/research/aso",
      "/api/suggestions",
    ],
  ],
  [
    "采集与监控",
    [
      "/api/watchlist",
      "/api/targets",
      "/api/control",
      "/api/research/collection",
      "/api/research/plans",
    ],
  ],
  ["评论记录", ["/api/reviews", "/api/research/reviews"]],
  [
    "事件与告警",
    [
      "/api/events",
      "/api/alerts",
      "/api/daily",
      "/api/research/events",
      "/api/research/annotations",
    ],
  ],
  [
    "市场研究",
    [
      "/api/market",
      "/api/compare",
      "/api/ads",
      "/api/research/developers",
      "/api/research/markets",
      "/api/research/comparisons",
      "/api/availability",
    ],
  ],
  ["数据源与导出", ["/api/data-sources", "/api/exports", "/api/baseline"]],
  ["账户与权限", ["/api/auth"]],
  ["基础与概览", []],
];

export function prepareApiSpec(raw) {
  if (
    !raw ||
    typeof raw !== "object" ||
    !/^3\./.test(raw.openapi) ||
    !raw.paths ||
    typeof raw.paths !== "object" ||
    Array.isArray(raw.paths)
  ) {
    throw new Error("服务返回的内容不是有效的 OpenAPI 3 接口定义。");
  }
  const spec = structuredClone(raw);
  const tags = new Set();
  for (const [path, item] of Object.entries(spec.paths)) {
    for (const [method, operation] of Object.entries(item)) {
      if (!METHODS.has(method)) continue;
      if (!operation.tags?.length) {
        const group = GROUPS.find(([, prefixes]) =>
          prefixes.some(
            (prefix) => path === prefix || path.startsWith(prefix + "/"),
          ),
        );
        operation.tags = [group?.[0] || "基础与概览"];
      }
      operation.tags.forEach((tag) => tags.add(tag));
    }
  }
  const existing = new Map((spec.tags || []).map((tag) => [tag.name, tag]));
  spec.tags = [...new Set([...GROUPS.map(([name]) => name), ...tags])]
    .filter((name) => tags.has(name))
    .map((name) => existing.get(name) || { name });
  return spec;
}

export function filterApiSpec(spec, query) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return spec;
  const paths = {};
  for (const [path, item] of Object.entries(spec.paths)) {
    const operations = Object.entries(item).filter(
      ([method, operation]) =>
        METHODS.has(method) &&
        [
          path,
          method,
          operation.summary,
          operation.description,
          operation.operationId,
          ...(operation.tags || []),
        ]
          .join(" ")
          .toLocaleLowerCase()
          .includes(needle),
    );
    if (operations.length)
      paths[path] = Object.fromEntries([
        ...Object.entries(item).filter(([key]) => !METHODS.has(key)),
        ...operations,
      ]);
  }
  const tags = new Set(
    Object.values(paths).flatMap((item) =>
      Object.entries(item).flatMap(([method, operation]) =>
        METHODS.has(method) ? operation.tags || [] : [],
      ),
    ),
  );
  return {
    ...spec,
    paths,
    tags: spec.tags.filter((tag) => tags.has(tag.name)),
  };
}

export function apiGroups(spec) {
  const counts = new Map();
  let total = 0;
  for (const item of Object.values(spec.paths)) {
    for (const [method, operation] of Object.entries(item)) {
      if (!METHODS.has(method)) continue;
      total++;
      for (const tag of operation.tags || [])
        counts.set(tag, (counts.get(tag) || 0) + 1);
    }
  }
  return {
    total,
    groups: spec.tags
      .filter((tag) => counts.has(tag.name))
      .map((tag) => ({ ...tag, count: counts.get(tag.name) })),
  };
}

export function createApiDocs({ doc, fetcher, renderReference }) {
  const win = doc.defaultView;
  const get = (id) => doc.getElementById(id);
  const status = get("api-doc-status"),
    search = get("api-doc-search"),
    retry = get("api-doc-retry");
  let spec,
    reference,
    activeGroup,
    loading = false;
  let stopNavigation = () => {};
  function navigateGroup(name) {
    stopNavigation();
    const root = get("api-reference");
    let idleTimer, timer;
    const observer = new win.MutationObserver(schedule);
    const stop = () => {
      observer.disconnect();
      win.removeEventListener("scroll", schedule, true);
      win.removeEventListener("wheel", stop);
      win.removeEventListener("touchstart", stop);
      win.clearTimeout(idleTimer);
      win.clearTimeout(timer);
    };
    function schedule() {
      win.clearTimeout(idleTimer);
      idleTimer = win.setTimeout(align, 150);
    }
    function align() {
      const heading = [...root.querySelectorAll(".opblock-tag")].find(
        (element) => element.dataset.tag === name,
      );
      if (!heading || heading.dataset.isOpen === "false") return;
      if (
        root.querySelector(".operations-virtual") &&
        reference.layoutSelectors?.getScrollToVirtualizedOperation?.()
      ) {
        schedule();
        return;
      }
      // Virtualizer measurements can adjust the scroll repeatedly. Align once they settle.
      stop();
      heading.scrollIntoView({ block: "start", behavior: "instant" });
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-is-open"],
    });
    win.addEventListener("scroll", schedule, { passive: true, capture: true });
    win.addEventListener("wheel", stop, { passive: true });
    win.addEventListener("touchstart", stop, { passive: true });
    stopNavigation = stop;
    timer = win.setTimeout(stop, 3000);
    const key = ["operations-tag", name];
    reference.layoutActions.clearScrollTo();
    reference.layoutActions.show(key, true);
    reference.layoutActions.scrollToVirtualizedOperation(key);
    schedule();
  }
  function render() {
    stopNavigation();
    const visible = filterApiSpec(spec, search.value);
    const { total, groups } = apiGroups(visible);
    get("api-doc-count").textContent =
      `${total} 个接口 · ${groups.length} 个分组`;
    get("api-doc-groups").replaceChildren(
      ...groups.map(({ name, count }) => {
        const link = doc.createElement("a");
        link.href = "#/" + encodeURIComponent(name);
        link.dataset.apiGroup = name;
        if (name === activeGroup) link.setAttribute("aria-current", "location");
        link.addEventListener("click", (event) => {
          if (
            event.button !== 0 ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey ||
            event.altKey
          )
            return;
          event.preventDefault();
          // Expand and mount the group before positioning its heading below the header.
          navigateGroup(name);
          activeGroup = name;
          get("api-doc-groups")
            .querySelectorAll("a")
            .forEach((item) => {
              if (item === link) item.setAttribute("aria-current", "location");
              else item.removeAttribute("aria-current");
            });
        });
        const label = doc.createElement("span"),
          badge = doc.createElement("span");
        label.textContent = name;
        badge.textContent = count;
        link.append(label, badge);
        return link;
      }),
    );
    get("api-doc-empty").hidden = total > 0;
    get("api-reference").hidden = total === 0;
    reference = renderReference({
      domNode: get("api-reference"),
      spec: visible,
      deepLinking: true,
      docExpansion: "list",
      displayRequestDuration: false,
      defaultModelsExpandDepth: -1,
      defaultModelExpandDepth: 2,
      validatorUrl: null,
      supportedSubmitMethods: [],
      persistAuthorization: false,
      queryConfigEnabled: false,
      showExtensions: false,
    });
  }
  async function load() {
    if (loading) return;
    loading = true;
    status.hidden = false;
    retry.hidden = true;
    get("api-doc-login").hidden = true;
    get("api-doc-status-title").textContent = "正在加载接口文档";
    get("api-doc-status-message").textContent =
      "正在读取当前服务的 OpenAPI 定义…";
    try {
      const response = await fetcher("/api/openapi.json", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (response.status === 401) {
        get("api-doc-login").hidden = false;
        throw new Error("请先在观星控制台登录管理员账户，再点击重新加载。");
      }
      if (response.status === 403)
        throw new Error("当前账户没有接口文档访问权限，请使用管理员账户。");
      if (!response.ok)
        throw new Error(
          `接口定义加载失败（HTTP ${response.status}），请稍后重试。`,
        );
      spec = prepareApiSpec(await response.json());
      get("api-doc-version").textContent =
        `v${spec.info?.version || "—"} · OpenAPI ${spec.openapi}`;
      search.disabled = false;
      render();
      status.hidden = true;
    } catch (error) {
      get("api-doc-status-title").textContent = "暂时无法显示接口文档";
      get("api-doc-status-message").textContent =
        error instanceof SyntaxError
          ? "服务返回的内容不是 JSON，请确认 API 服务已启动。"
          : error.message || "网络连接失败，请稍后重试。";
      retry.hidden = false;
    } finally {
      loading = false;
    }
  }
  search.addEventListener("input", () => {
    if (spec) render();
  });
  retry.addEventListener("click", load);
  return { load };
}
