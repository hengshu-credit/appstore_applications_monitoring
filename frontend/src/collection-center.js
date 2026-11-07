import { pagination, bindPagination } from "./pagination.js";
import { filterBar } from "./filter-bar.js";
import "./collection-center.css";
import { icon, storeLogo } from "./icons.js";
import { createTaskLogViewer } from "./task-logs.js";
import { createCollectionLineage } from "./collection-lineage.js";
export const taskKinds = {
  details: "应用详情",
  keywords: "关键词排名",
  chart: "分类榜单",
  reviews: "用户评论",
  featured: "编辑推荐",
  icons: "应用图标",
  screenshots: "应用截图",
  permissions: "声明权限",
  aso_popularity: "ASO 关键词热度",
  aso_import: "ASO 热度导入",
  aso_report: "ASO 报告入库",
};
const states = {
  scheduled: "定时采集",
  pending: "等待采集",
  running: "采集中",
  paused: "已暂停",
  error: "失败待重试",
  done: "已完成",
};

export function createCollectionCenter(ctx) {
  const logs = createTaskLogViewer(ctx);
  const lineage = createCollectionLineage(ctx);
  const {
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
    toast,
    selectors,
    appLink,
    currentRevision,
    nextRevision,
  } = ctx;
  let filters = { kind: "", store: "", state: "", country: "", q: "" },
    page = 1,
    pageSize = 50,
    section = "tasks";
  let preset = null,
    pendingPool,
    poolSnapshot,
    recentRegistry;
  const compact = (value) =>
    Number(value) >= 10000
      ? new Intl.NumberFormat("zh-CN", {
          notation: "compact",
          maximumFractionDigits: 1,
          maximumSignificantDigits: 3,
        }).format(value)
      : num(value);
  const options = (obj) => Object.entries(obj);
  const refresh = (reuse = false) => render(nextRevision(), reuse);
  let refreshTimer;
  function watchDataRevision(rev, baseline) {
    clearTimeout(refreshTimer);
    if (!Number.isInteger(baseline)) return;
    let dirty = false;
    for (const form of document.querySelectorAll(
      "#task-filters, #collection-limits",
    )) {
      form.addEventListener("input", () => {
        dirty = true;
      });
      form.addEventListener("change", () => {
        dirty = true;
      });
    }
    const editing = () =>
      dirty ||
      document.querySelector("#collection-editor, [data-task-index]:checked") ||
      document.activeElement?.closest(
        "#task-filters, #collection-limits, [data-pipeline], #plan-form, [data-task-action]",
      ) ||
      document.querySelector("#selected-count")?.textContent.includes("所有");
    const schedule = () => {
      refreshTimer = setTimeout(check, 5000);
    };
    async function check() {
      if (
        rev !== currentRevision() ||
        location.hash.split("?")[0] !== "#collection"
      )
        return;
      if (document.hidden || editing()) {
        schedule();
        return;
      }
      try {
        const result = await api("/research/collection/revision");
        if (rev !== currentRevision()) return;
        if (
          Number.isInteger(result.revision) &&
          result.revision !== baseline &&
          !editing()
        ) {
          await refresh();
          return;
        }
      } catch {
        /* Keep the current rows visible until the next successful check. */
      }
      schedule();
    }
    schedule();
  }
  function closeEditor() {
    $("#collection-editor")?.remove();
  }
  function editor(title, html) {
    closeEditor();
    const dialog = document.createElement("dialog");
    dialog.id = "collection-editor";
    dialog.className = "collection-editor";
    dialog.setAttribute("aria-labelledby", "editor-title");
    dialog.innerHTML = `<div class="picker-heading"><h2 id="editor-title">${title}</h2><button class="secondary small" type="button" data-close-editor>关闭</button></div>${html}`;
    document.body.append(dialog);
    dialog.querySelector("[data-close-editor]").onclick = () => dialog.close();
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
    return dialog;
  }
  async function edit(row) {
    const editRevision = currentRevision();
    if (row.resource === "sources") {
      const task = await api(`/research/collection/source-tasks/${row.id}`);
      if (editRevision !== currentRevision()) return;
      editor(
        "一次性任务详情",
        `<p>${esc(taskKinds[row.kind])} · ${esc(row.label)}</p><p>提交来源：${esc(task.source)} · 状态：${esc(states[task.status])}</p><p>提交后由消费者领取，完成后不自动排期。</p>${task.last_error ? `<p>${esc(task.last_error)}</p>` : ""}${task.result ? `<p>已保存 <strong>${num(task.result.returned ?? task.result.rows ?? 0)}</strong> 条数据${task.result.rejected_rows ? `，${num(task.result.rejected_rows)} 条异常记录已保留` : ""}。</p>${task.result.period_start ? `<p>统计范围：${esc(task.result.period_start)} 至 ${esc(task.result.period_end)}</p>` : ""}<a class="button secondary" href="#keywords">查看关键词数据</a>` : ""}`,
      );
      return;
    }
    editor(
      "编辑采集任务",
      `<p class="sub">${esc(taskKinds[row.kind])} · ${esc(row.label)} · ${esc(row.country.toUpperCase())} / ${esc(row.language)}</p><form id="edit-task-form" class="form-grid">${field("刷新周期（小时）", "refresh_hours", row.refresh_hours, "number", 'min="0.25" max="8760" step="0.25" required')}${row.resource === "watchlist" ? field("监控分组", "group_name", row.group_name, "text", "required") : ["icons", "permissions", "media"].includes(row.resource) ? "" : field("采集结果上限", "depth", row.depth, "number", `min="1" max="${row.resource === "keywords" ? 200 : 500}" required`)}<p class="sub full">保留已有历史及启用状态。需要切换应用或市场时，请新建独立任务。</p><div class="full form-actions"><button type="submit">保存设置</button></div></form>`,
    );
    bindForm("#edit-task-form", async (v) => {
      await api(
        `/research/collection/tasks/${row.resource}/${row.id}`,
        {
          refresh_hours: Number(v.refresh_hours),
          ...(row.resource === "watchlist"
            ? { group_name: v.group_name }
            : ["icons", "permissions", "media"].includes(row.resource)
              ? {}
              : { depth: Number(v.depth) }),
        },
        "PUT",
      );
      closeEditor();
      toast("采集设置已更新");
      if (editRevision === currentRevision()) await refresh();
    });
  }
  function creationForm() {
    if (market().store === "all")
      return '<div class="store-choice"><p>新建采集计划需要明确商店，请在顶部选择 Google Play 或 App Store；已有任务可在全部商店范围管理。</p><button id="back-to-tasks" class="secondary">返回任务列表</button></div>';
    const m = market(),
      kind = preset?.kind || "details";
    return `<div class="collection-create-heading"><div><h2>新建采集计划</h2><p class="sub">当前市场：${storeLogo(m.store)} ${m.store === "apple" ? "App Store" : "Google Play"} · ${esc(selectors.countryName(m.country))} · ${esc(m.language)}。可在页面右上角切换。</p></div><button type="button" class="secondary" id="back-to-tasks">返回任务列表</button></div><form id="plan-form" class="form-grid">
      ${field("计划名称", "name", "应用研究计划", "text", "required")}${field("监控分组", "group_name", "重点应用", "text", "required")}${field("刷新周期（小时）", "refresh_hours", 24, "number", 'min="0.25" max="8760" step="0.25" required')}
      <fieldset class="full task-types"><legend>选择需要采集的数据</legend>${Object.entries(
        Object.fromEntries(
          Object.entries(taskKinds).filter(
            ([k]) =>
              !["icons", "permissions", "screenshots"].includes(k) &&
              !k.startsWith("aso_"),
          ),
        ),
      )
        .map(
          ([k, label]) =>
            `<label class="check-label"><input type="checkbox" name="collect_${k}" ${kind === k ? "checked" : ""} ${k === "featured" && m.store !== "apple" ? "disabled" : ""}>${icon(k === "details" ? "apps" : k === "chart" ? "charts" : k)}<span>${label}${k === "featured" ? "（Apple）" : ""}</span></label>`,
        )
        .join("")}</fieldset>
      <label data-plan-field="apps">应用 ID（每行一个，最多 50 个）<textarea name="app_ids" placeholder="${m.store === "apple" ? "例如 310633997" : "例如 com.whatsapp"}">${esc(preset?.app_ids?.join("\n") || "")}</textarea></label>
      <label data-plan-field="keywords">关键词（每行一个，最多 100 个）<textarea name="keywords" placeholder="输入当前市场的关键词"></textarea></label>
      <div data-plan-field="chart">${selectors.categoryField(m.store, "", "plan-category", "chart")}${select(
        "榜单类型",
        "collection",
        selectors
          .chartDefinitions(m.store)
          .map((d) => [
            d.id,
            d.name + (d.mode === "derived" ? "（本站派生）" : ""),
          ]),
        preset?.collection || "top-free",
      )}<p class="sub">本站派生榜复用对应基榜，不作为官方榜单。</p></div>
      <div data-plan-field="observations">${field("榜单 / 评论 / 推荐结果上限", "depth", 100, "number", 'min="1" max="500" required')}</div>
      <div data-plan-field="keywords">${field("关键词结果上限", "keyword_depth", 50, "number", 'min="1" max="200" required')}</div>
      <p class="sub full">优先一次取足；接口限制时自动续采。来源上限会单独标明。已有目标保留暂停状态与历史。</p><div class="full form-actions"><button type="submit">预览采集计划</button></div></form><div id="plan-preview"></div><details class="plan-history"><summary>导入已有关键词词库（JSON）</summary><form id="keyword-import"><label>包含商店、国家、语言和关键词的数组<textarea name="json" required placeholder='[{"store":"google","country":"th","language":"th","keyword":"เงินกู้"}]'></textarea></label><button type="submit" class="spacer">校验并导入词库</button></form><p class="sub">支持跨市场批量导入；整批验证，保留已有任务历史和暂停状态。</p></details>`;
  }
  function bindCreation(rev) {
    $("#back-to-tasks").onclick = () => {
      section = "tasks";
      preset = null;
      location.hash = "#collection";
    };
    const form = $("#plan-form");
    if (!form) return;
    function showFields() {
      const enabled = (k) => form.elements[`collect_${k}`]?.checked;
      form.querySelectorAll("[data-plan-field]").forEach((el) => {
        const name = el.dataset.planField;
        el.hidden = !(name === "apps"
          ? enabled("details") || enabled("reviews")
          : name === "observations"
            ? enabled("chart") || enabled("reviews") || enabled("featured")
            : enabled(name));
      });
      $("#plan-preview").innerHTML = "";
    }
    form.addEventListener("change", showFields);
    form.addEventListener("input", () => {
      $("#plan-preview").innerHTML = "";
    });
    showFields();
    bindForm("#keyword-import", async (value) => {
      await api("/keywords", JSON.parse(value.json));
      toast("词库已导入");
      section = "tasks";
      preset = null;
      if (rev === currentRevision()) location.hash = "#collection";
    });
    bindForm("#plan-form", async (v, f) => {
      const flags = Object.fromEntries(
        Object.keys(taskKinds).map((k) => [
          k,
          !!f.elements[`collect_${k}`]?.checked,
        ]),
      );
      const spec = {
        ...market(),
        name: v.name,
        group_name: v.group_name,
        refresh_hours: Number(v.refresh_hours),
        app_ids:
          flags.details || flags.reviews
            ? v.app_ids.split(/\s+/).filter(Boolean)
            : [],
        keywords: flags.keywords
          ? v.keywords
              .split("\n")
              .map((x) => x.trim())
              .filter(Boolean)
          : [],
        depth: Number(v.depth),
        keyword_depth: Number(v.keyword_depth),
        collection: v.collection,
        category: v.category,
        ...Object.fromEntries(
          Object.entries(flags).filter(
            ([key]) => !["keywords", "permissions"].includes(key),
          ),
        ),
      };
      const result = await api("/research/plans/preview", spec);
      if (rev !== currentRevision()) return;
      $("#plan-preview").innerHTML =
        `<div class="callout spacer">将配置 ${result.total} 个采集目标，确认后交由后台执行。</div>${table(
          ["采集类型", "目标", "结果上限", "周期"],
          result.actions.map((a) => [
            esc(taskKinds[a.kind]),
            esc(
              a.value.app_id ||
                a.value.keyword ||
                a.value.category ||
                "总榜 / Today",
            ),
            a.value.depth || "单个应用",
            a.value.refresh_hours + " 小时",
          ]),
        )}<button id="commit-plan">确认创建计划</button>`;
      $("#commit-plan").onclick = async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
          await api("/research/plans", spec);
          toast("计划已提交，后台正在配置任务");
          section = "tasks";
          preset = null;
          location.hash = "#collection";
        } catch (err) {
          toast(err.message);
          b.disabled = false;
        }
      };
    });
  }
  async function render(rev, reuse = false) {
    clearTimeout(refreshTimer);
    closeEditor();
    const previousSection = section;
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    section = params.get("view") || (params.has("new") ? "create" : "tasks");
    reuse ||= previousSection !== section;
    if (params.has("new")) {
      section = "create";
      preset = {
        kind: params.get("new"),
        collection: params.get("collection") || "top-free",
        app_ids: params.get("app") ? [params.get("app")] : [],
      };
      history.replaceState(
        null,
        "",
        location.pathname + location.search + "#collection?view=create",
      );
    }
    filters.store = market().store;
    if (params.has("kind") && taskKinds[params.get("kind")]) {
      filters.kind = params.get("kind");
      filters.state = "";
      page = 1;
      section = "tasks";
      history.replaceState(
        null,
        "",
        location.pathname + location.search + "#collection",
      );
    }
    if (section === "lineage") {
      if (!mount('<section id="collection-lineage"></section>', rev)) return;
      await lineage.render($("#collection-lineage"), rev);
      return;
    }
    const registryPath =
      "/research/collection/tasks?" +
      new URLSearchParams({ ...filters, page, limit: pageSize });
    const cached =
      recentRegistry?.path === registryPath &&
      Date.now() - recentRegistry.at < 30000;
    const useCached = cached && (reuse || section !== "tasks");
    const registry = useCached
      ? recentRegistry.data
      : section !== "tasks"
        ? { items: [], summary: {}, categories: [], total: 0 }
        : api(registryPath);
    const [tasks, pipelines, quality, limits] = await Promise.all([
      registry,
      useCached
        ? recentRegistry.pipelines
        : api("/research/collection/pipelines?counts=false"),
      useCached ? recentRegistry.quality : api("/research/collection/activity"),
      useCached ? recentRegistry.limits : api("/research/collection/limits"),
    ]);
    if (rev !== currentRevision()) return;
    if (section === "tasks" && !useCached)
      recentRegistry = {
        path: registryPath,
        data: tasks,
        pipelines,
        quality,
        limits,
        at: Date.now(),
      };
    const stateLabels = { ...states, out_of_scope: "未启用市场" };
    const stateSymbols = {
      scheduled: "◷",
      pending: "○",
      running: "↻",
      paused: "Ⅱ",
      error: "!",
      done: "✓",
      out_of_scope: "–",
    };
    const stateMark = (state) =>
      `<i class="collection-state-mark" data-state="${state}" aria-hidden="true">${stateSymbols[state]}</i>`;
    const distribution = (summary, kind = "") => {
      const entries = Object.entries(stateLabels).filter(
        ([state]) =>
          (!kind || state !== "out_of_scope") &&
          (state !== "done" || kind.startsWith("aso_")) &&
          (state !== "scheduled" || !kind.startsWith("aso_")),
      );
      const total = entries.reduce(
        (sum, [state]) => sum + (summary[state] || 0),
        0,
      );
      let position = 0;
      return `<svg class="category-distribution" viewBox="0 0 1000 10" preserveAspectRatio="none" aria-hidden="true">${entries
        .filter(([state]) => summary[state] > 0)
        .map(([state]) => {
          const width = (summary[state] / total) * 1000,
            start = position;
          position += width;
          return `<rect x="${start}" width="${width}" height="10" data-state="${state}"></rect>`;
        })
        .join("")}</svg><div class="category-counts">${entries
        .map(([state, label]) => {
          const caption = `${label}：${num(summary[state] || 0)}`;
          const attrs = kind
            ? `data-category="${kind}" data-category-state="${state}" aria-pressed="${filters.kind === kind && filters.state === state}"`
            : "data-pool-card";
          return `<button type="button" ${attrs} title="${caption}" aria-label="${caption}">${stateMark(state)}<b>${compact(summary[state] || 0)}</b></button>`;
        })
        .join("")}</div>`;
    };
    const categoryCards = `<div class="collection-legend" aria-label="任务状态图例">${Object.entries(
      stateLabels,
    )
      .map(([state, label]) => `<span>${stateMark(state)}${label}</span>`)
      .join(
        "",
      )}</div><div class="collection-categories" aria-label="任务分类概览">${Object.entries(
      taskKinds,
    )
      .map(([kind, label]) => {
        const category = tasks.categories?.find((c) => c.kind === kind) || {
          total: 0,
          summary: {},
        };
        return `<article class="collection-category ${filters.kind === kind ? "active" : ""}"><button type="button" class="category-heading" data-category="${kind}" aria-pressed="${filters.kind === kind}"><span>${esc(label)}</span><strong title="${num(category.total)}">${compact(category.total)}</strong></button>${distribution(category.summary, kind)}</article>`;
      })
      .join("")}${pipelines
      .filter((p) => p.id === "pool")
      .map(
        (p) =>
          `<article class="collection-category" id="pool-task-card"><button type="button" class="category-heading" data-pool-card title="所有商店与市场；管理基础流程"><span>全池应用详情</span><strong class="stat-skeleton" aria-label="统计中"></strong></button><div class="pool-summary" role="status" aria-live="polite"><span class="stat-loading">正在统计全池…</span></div></article>`,
      )
      .join("")}</div>`;
    const body =
      section === "create"
        ? panel("配置目标", creationForm())
        : section === "pipelines"
          ? panel(
              "后台基础采集",
              `<p class="sub">这些流程由全池监控服务执行，设置保存在数据库，当前请求 / 批次完成后应用。重点任务由上方任务列表独立管理。</p><div class="pipeline-grid">${pipelines.map((p) => `<form class="pipeline-card" data-pipeline="${p.id}"><h3>${esc(p.name)}</h3><p class="sub">${p.id === "seed" ? "检查开源应用目录仓库中的历史数据包，按文件版本同步。" : p.id === "pool" ? "按国家与语言检查已有应用池；百万应用需要持续排队。" : "从榜单、搜索词和已验证开发者补充应用 ID。"}</p><label class="check-label"><input name="enabled" type="checkbox" ${p.enabled ? "checked" : ""}>启用此流程</label>${field("检查周期（小时）", "refresh_hours", p.refresh_hours, "number", 'min="0.25" max="8760" step="0.25" required')}${p.id !== "seed" ? field("国家代码（逗号分隔）", "countries", p.countries.join(", "), "text", "required") + field("请求语言", "language", p.language, "text", "required") : ""}${p.id === "discovery" ? `<label>发现关键词（每行一个）<textarea name="search_terms">${esc(p.search_terms.join("\n"))}</textarea></label><label>Google 分类 ID（逗号分隔）<input name="google_categories" value="${esc(p.google_categories.join(", "))}"></label>` : ""}<button type="submit" class="secondary">保存流程设置</button><button type="button" class="secondary" data-pipeline-logs="${p.id}">查看运行日志</button><p role="status" class="sub pipeline-feedback"></p></form>`).join("")}</div>`,
            )
          : `${panel(
              "采集任务",
              `${filterBar({
                id: "task-filters",
                values: filters,
                submit: "查询",
                fields: [
                  {
                    name: "q",
                    label: "搜索目标",
                    placeholder: "应用 ID / 名称 / 关键词",
                    options: tasks.items.map((r) => [
                      r.app_id || r.label,
                      r.label || r.app_id,
                    ]),
                  },
                  {
                    name: "kind",
                    label: "采集类型",
                    type: "panel",
                    options: [["", "全部类型"], ...options(taskKinds)],
                  },
                  { name: "store", label: "商店", type: "store" },
                  {
                    name: "state",
                    label: "任务状态",
                    type: "panel",
                    options: [["", "全部状态"], ...options(states)],
                  },
                  { name: "country", label: "国家 / 地区", type: "country" },
                  { name: "language", label: "请求语言", type: "language" },
                ],
              })}<div class="task-bulk"><span id="selected-count">已选 0 项</span><button type="button" class="secondary small" data-batch="refresh">立即采集</button><button type="button" class="secondary small" data-batch="pause">暂停</button><button type="button" class="secondary small" data-batch="resume">恢复</button><button type="button" class="secondary small" data-batch="interval">修改周期</button><button type="button" class="secondary small" id="retry-failed-tasks">重试筛选内失败任务</button><button type="button" class="secondary small" id="select-filtered-tasks">全选筛选结果（跨页）</button><button type="button" class="secondary small" id="select-every-task">全选配置任务（所有商店）</button><button type="button" class="secondary small" id="clear-task-selection">取消选择</button></div>${table(
                [
                  '<input type="checkbox" id="select-all-tasks" aria-label="选择当前页全部任务">',
                  "目标",
                  "App ID",
                  "采集类型",
                  "商店",
                  "国家 / 地区",
                  "语言",
                  "分类",
                  "周期",
                  "采集深度",
                  "最近成功",
                  "下次到期",
                  "状态",
                  "操作",
                ],
                tasks.items.map((t, index) => [
                  `<input type="checkbox" data-task-index="${index}" aria-label="选择任务 ${esc(t.kind === "chart" ? selectors.chartDefinitions(t.store).find((d) => d.id === t.collection)?.name || t.label : t.label)}">`,
                  t.app_id
                    ? appLink(
                        t.store,
                        t.app_id,
                        t.label,
                        null,
                        t.country,
                        t.language,
                        { showId: false },
                      )
                    : esc(
                        t.kind === "chart"
                          ? selectors
                              .chartDefinitions(t.store)
                              .find((d) => d.id === t.collection)?.name ||
                              t.label
                          : t.label,
                      ),
                  `<span class="task-app-id" title="${esc(t.app_id)}">${esc(t.app_id || "—")}</span>`,
                  `<span class="task-kind">${icon(t.kind === "details" ? "apps" : t.kind === "chart" ? "charts" : t.kind)}${esc(taskKinds[t.kind])}</span>`,
                  `<span class="store-caption">${storeLogo(t.store)}${t.store === "apple" ? "App Store" : "Google Play"}</span>`,
                  t.resource === "media"
                    ? "跨市场共享"
                    : `<span class="market-caption"><img class="country-flag" src="/flags/${esc(t.country)}.svg" alt="">${esc(selectors.countryName(t.country))}</span>`,
                  t.resource === "media"
                    ? "—"
                    : `${esc(selectors.languageName(t.language))} · ${esc(t.language)}`,
                  t.category
                    ? esc(selectors.categoryName(t.store, t.category))
                    : "—",
                  t.resource === "sources"
                    ? "一次性"
                    : `${t.refresh_hours} 小时`,
                  t.depth
                    ? num(t.depth) + " 条"
                    : t.kind === "permissions"
                      ? "权限声明"
                      : ["icons", "screenshots"].includes(t.kind)
                        ? "图片缓存"
                        : t.resource === "sources"
                          ? "按提交范围"
                          : "单应用详情",
                  stamp(t.success_at),
                  t.task_state === "done"
                    ? "—"
                    : t.enabled
                      ? t.due
                        ? stamp(t.due)
                        : "尽快执行"
                      : "已暂停",
                  `<span class="badge ${t.task_state === "error" ? "warn" : t.task_state === "running" ? "good" : ""}">${esc(states[t.task_state])}</span>`,
                  `<div class="task-actions">${t.resource === "media" && t.success_at ? `<a class="link" href="/api/media/${t.id}" target="_blank" rel="noopener">查看图片</a>` : ""}<button class="link" data-task-logs="${index}">日志${t.task_state === "running" ? " · 实时" : ""}</button><button class="link" data-edit-task="${index}">${t.resource === "sources" ? "详情" : "编辑"}</button><button class="link" data-task-action="${index}" data-action="${t.enabled ? "pause" : "resume"}">${t.enabled ? "暂停" : "恢复"}</button><button class="link" data-task-action="${index}" data-action="refresh" ${t.enabled ? "" : "disabled"}>${t.resource === "sources" ? "重新入队" : "立即采集"}</button></div>`,
                ]),
              ).replace(
                "data-table-wrap",
                "data-table-wrap collection-task-table",
              )}${pagination({ id: "task-pages", offset: (page - 1) * pageSize, limit: pageSize, total: tasks.total, hasMore: tasks.has_more, prevId: "previous-tasks", nextId: "next-tasks", sizes: [25, 50, 100, 200] })}</div>`,
            )}
      <details class="panel plan-history"><summary>计划执行记录 <span class="sub">最近 100 个</span></summary>${table(
        ["计划名称", "目标配置进度", "状态", "创建时间", "操作"],
        quality.plans.map((p) => [
          esc(p.name),
          p.completed + " / " + p.total,
          esc(
            {
              queued: "排队中",
              running: "配置中",
              complete: "已配置",
              error: "配置失败",
            }[p.status],
          ),
          stamp(p.created_at),
          `<button class="link" data-plan-collection="${p.id}">采集结果</button> <button class="link" data-plan-logs="${p.id}">日志</button> ${p.status === "error" ? `<button class="link" data-retry-plan="${p.id}">重试未完成部分</button>` : ""}`,
        ]),
      )}</details>`;
    if (
      !mount(
        `<button id="new-collection" data-workspace-action>${icon("plus")}新建采集计划</button>${section === "tasks" ? categoryCards + `<div class="collection-result-count">筛选结果 <strong>${num(tasks.total)}</strong></div>` : ""}<details class="panel collection-limits-panel"><summary>整体采集控频 · ${limits.paused ? "已暂停请求" : (limits.daily_request_limit ?? 0) ? "每日预算 " + num(limits.daily_request_limit ?? 0) + " 次" : "每日预算不限"}</summary><div class="spacer"><form id="collection-limits" class="form-grid">${field("整体最小请求间隔（秒）", "global_interval_seconds", limits.global_interval_seconds, "number", 'min="0.1" max="3600" step="0.1" required')}${field("Google 最小间隔（秒）", "google_interval_seconds", limits.google_interval_seconds, "number", 'min="0.1" max="3600" step="0.1" required')}${field("Apple 最小间隔（秒）", "apple_interval_seconds", limits.apple_interval_seconds, "number", 'min="0.1" max="3600" step="0.1" required')}${field("每日请求预算（UTC，0 为不限）", "daily_request_limit", limits.daily_request_limit ?? 0, "number", 'min="0" max="10000000" step="1" required')}${field("受限后整体冷却（秒）", "cooldown_seconds", limits.cooldown_seconds, "number", 'min="60" max="86400" required')}<label class="check-label"><input type="checkbox" name="paused" ${limits.paused ? "checked" : ""}>暂停所有商店采集请求</label><p class="sub full">同市场 App Store 详情每批最多 50 个，Google 详情、关键词、榜单、评论和权限每批最多 20 个；保留原采集周期与深度。每日预算耗尽后等待下一 UTC 日，所有采集 worker 共用额度，详情、关键词、榜单、评论、推荐和图标请求均计入。403 / 429 / 5xx 会触发共享冷却，并遵守更长的 Retry-After。保存后新请求生效，已发出的请求会完成。此开关不停止种子包同步和第三方数据源手动操作。</p><button type="submit">保存控频设置</button></form></div></details>${body}`,
        rev,
        quality.heartbeat?.next_at
          ? `后台心跳 ${stamp(quality.heartbeat.next_at)}`
          : "等待后台首次心跳",
      )
    )
      return;
    if (section === "tasks") watchDataRevision(rev, tasks.data_revision);
    document.querySelectorAll("[data-category]").forEach(
      (b) =>
        (b.onclick = () => {
          filters.kind = b.dataset.category;
          filters.state = b.dataset.categoryState || "";
          page = 1;
          section = "tasks";
          refresh(true).catch((e) => toast(e.message));
        }),
    );
    const poolCard = $("#pool-task-card");
    if (poolCard) {
      poolCard.onclick = (event) => {
        if (event.target.closest("[data-pool-card]"))
          location.hash = "#collection?view=pipelines";
        if (event.target.closest("[data-retry-pool]")) loadPool();
      };
      async function loadPool() {
        poolCard.querySelector(".pool-summary").innerHTML =
          '<span class="stat-loading">正在统计全池…</span>';
        try {
          if (!poolSnapshot || Date.now() - poolSnapshot.at > 60000) {
            pendingPool ||= api("/research/collection/pipelines")
              .then((rows) => {
                poolSnapshot = { rows, at: Date.now() };
                return rows;
              })
              .finally(() => (pendingPool = null));
            await pendingPool;
          }
          if (!poolCard.isConnected || rev !== currentRevision()) return;
          const info = poolSnapshot.rows.find((p) => p.id === "pool");
          if (!info) return;
          const count = poolCard.querySelector("strong");
          count.classList.remove("stat-skeleton");
          count.textContent = compact(info.total);
          count.title = num(info.total);
          poolCard.querySelector(".pool-summary").innerHTML = distribution(
            info.summary || {},
          );
          if (info.observed_at)
            poolCard.title = "统计时间 " + stamp(info.observed_at);
        } catch {
          if (poolCard.isConnected) {
            poolCard.querySelector("strong").classList.remove("stat-skeleton");
            poolCard.querySelector("strong").textContent = "—";
            poolCard.querySelector(".pool-summary").innerHTML =
              '<button type="button" class="secondary small" data-retry-pool>统计失败，点击重试</button>';
          }
        }
      }
      loadPool();
    }

    bindForm("#collection-limits", async (v, f) => {
      await api(
        "/research/collection/limits",
        {
          ...Object.fromEntries(
            [
              "global_interval_seconds",
              "google_interval_seconds",
              "apple_interval_seconds",
              "cooldown_seconds",
              "daily_request_limit",
            ].map((k) => [k, Number(v[k])]),
          ),
          paused: f.elements.paused.checked,
        },
        "PUT",
      );
      toast("整体控频设置已保存，新请求即时生效");
    });
    const storeFilter = $('#task-filters [name="store"]');
    if (storeFilter)
      storeFilter.onchange = () => {
        filters = Object.fromEntries(new FormData(storeFilter.form));
        page = 1;
        $("#global-store").value = storeFilter.value;
        $("#global-store").dispatchEvent(
          new Event("change", { bubbles: true }),
        );
      };
    $("#new-collection").onclick = () => {
      preset = null;
      location.hash = "#collection?view=create";
    };
    document.querySelectorAll("[data-task-logs]").forEach(
      (b) =>
        (b.onclick = () => {
          const t = tasks.items[Number(b.dataset.taskLogs)];
          logs
            .open(
              t.resource,
              t.id,
              `${taskKinds[t.kind]} · ${t.label} · ${t.country.toUpperCase()} / ${t.language}`,
            )
            .catch((e) => toast(e.message));
        }),
    );
    document
      .querySelectorAll("[data-pipeline-logs]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            logs
              .open(
                "pipelines",
                b.dataset.pipelineLogs,
                pipelines.find((p) => p.id === b.dataset.pipelineLogs).name,
              )
              .catch((e) => toast(e.message))),
      );
    document
      .querySelectorAll("[data-plan-logs]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            logs
              .open(
                "plans",
                b.dataset.planLogs,
                "采集计划 #" + b.dataset.planLogs,
              )
              .catch((e) => toast(e.message))),
      );
    if (quality.dispatcher) {
      const status = document.createElement("span");
      status.className = "sub";
      status.textContent = ` · 动态调度 · ${quality.dispatcher.workers.length} 个采集进程`;
      document.querySelector(".collection-result-count")?.append(status);
    }
    document.querySelectorAll("[data-plan-collection]").forEach(
      (button) =>
        (button.onclick = async () => {
          const dialog = editor("计划采集结果", "<p>正在读取共享目标结果…</p>");
          try {
            const result = await api(
              "/research/plans/" +
                button.dataset.planCollection +
                "/collection",
            );
            if (!dialog.isConnected) return;
            const content = document.createElement("div");
            content.innerHTML = `<p>已采集 ${num(result.finished)} / ${num(result.total)} · 失败 ${num(result.errors)}</p>${table(
              ["类型", "目标", "状态", "最近结果"],
              result.tasks.map((t) => [
                esc(taskKinds[t.kind]),
                esc(t.label),
                esc(
                  {
                    configuring: "配置中",
                    complete: "完成",
                    failed: "失败",
                    running: "采集中",
                    paused: "暂停",
                    pending: "排队",
                    scheduled: "定时",
                    error: "重试",
                  }[t.state] || t.state,
                ),
                stamp(t.observed_at),
              ]),
            )}`;
            dialog.querySelector("p").replaceWith(content);
          } catch (error) {
            toast(error.message);
          }
        }),
    );
    if (section === "create") {
      bindCreation(rev);
      return;
    }
    if (section === "pipelines") {
      document.querySelectorAll("[data-pipeline]").forEach((f) =>
        f.addEventListener("submit", async (e) => {
          e.preventDefault();
          const b = f.querySelector('button[type="submit"]');
          b.disabled = true;
          try {
            const v = Object.fromEntries(new FormData(f));
            await api(
              "/research/collection/pipelines/" + f.dataset.pipeline,
              {
                enabled: f.elements.enabled.checked,
                refresh_hours: Number(v.refresh_hours),
                ...(v.countries
                  ? {
                      countries: v.countries.split(/[,，\s]+/).filter(Boolean),
                      language: v.language,
                    }
                  : {}),
                ...(f.dataset.pipeline === "discovery"
                  ? {
                      search_terms: v.search_terms
                        .split("\n")
                        .map((t) => t.trim())
                        .filter(Boolean),
                      google_categories: v.google_categories
                        .split(/[,，\s]+/)
                        .filter(Boolean),
                    }
                  : {}),
              },
              "PUT",
            );
            f.querySelector(".pipeline-feedback").textContent =
              "已保存，将在当前请求 / 批次完成后应用";
            toast("流程设置已保存");
          } catch (err) {
            toast(err.message);
          } finally {
            b.disabled = false;
          }
        }),
      );
      return;
    }
    bindForm("#task-filters", async (v) => {
      filters = v;
      page = 1;
      if (v.store !== market().store) {
        $("#global-store").value = v.store;
        $("#global-store").dispatchEvent(
          new Event("change", { bubbles: true }),
        );
        return;
      }
      page = 1;
      await refresh();
    });

    bindPagination(document, "task-pages", (start, size) => {
      pageSize = size;
      page = start / size + 1;
      return refresh();
    });

    let selectionScope = "selected";
    const selected = () =>
      [...document.querySelectorAll("[data-task-index]:checked")].map(
        (el) => tasks.items[Number(el.dataset.taskIndex)],
      );
    const selectionChange = () => {
      $("#selected-count").textContent =
        selectionScope === "all"
          ? "已选全部配置任务（所有商店、所有页，不含全池）"
          : selectionScope === "filtered"
            ? `已选筛选结果 ${tasks.total} 项（所有页）`
            : `已选 ${selected().length} 项`;
      document
        .querySelectorAll("[data-batch]")
        .forEach(
          (b) =>
            (b.disabled =
              selectionScope === "selected"
                ? !selected().length
                : b.dataset.batch === "refresh" ||
                  (selectionScope === "filtered" && !tasks.total)),
        );
    };
    const selectAll = $("#select-all-tasks");
    if (selectAll)
      selectAll.onchange = (e) => {
        selectionScope = "selected";
        document
          .querySelectorAll("[data-task-index]")
          .forEach((c) => (c.checked = e.target.checked));
        selectionChange();
      };
    document.querySelectorAll("[data-task-index]").forEach(
      (c) =>
        (c.onchange = () => {
          selectionScope = "selected";
          selectionChange();
        }),
    );
    selectionChange();
    for (const [id, scope] of [
      ["select-filtered-tasks", "filtered"],
      ["select-every-task", "all"],
      ["clear-task-selection", "selected"],
    ]) {
      $("#" + id).onclick = () => {
        selectionScope = scope;
        document
          .querySelectorAll("[data-task-index]")
          .forEach((c) => (c.checked = scope !== "selected"));
        if (selectAll) selectAll.checked = scope !== "selected";
        selectionChange();
      };
    }
    async function control(action, rows, scope = "selected", extra = {}) {
      if (action === "interval" && extra.refresh_hours === undefined) {
        const scopeLabel =
          scope === "all"
            ? "所有商店的全部配置任务（不含全池）"
            : scope === "filtered"
              ? `当前筛选的 ${num(tasks.total)} 个任务`
              : `已选 ${rows.length} 个任务`;
        editor(
          "批量修改采集周期",
          `<p>${scopeLabel}</p><form id="batch-interval-form" class="form-grid">${field("刷新周期（小时）", "refresh_hours", 168, "number", 'min="0.25" max="8760" step="0.25" required')}<p class="sub full">从保存时间按新周期重新排期，取消当前租约并保留历史与暂停状态。延长周期可降低每日请求量。</p><button type="submit">保存周期</button></form>`,
        );
        bindForm("#batch-interval-form", async (v) => {
          await control(action, rows, scope, {
            refresh_hours: Number(v.refresh_hours),
          });
          closeEditor();
        });
        return;
      }
      if (
        scope !== "selected" &&
        !window.confirm(
          `确认${{ pause: "暂停", resume: "恢复", retry: "重试其中失败的", interval: "修改周期：" }[action]}${scope === "all" ? "所有商店的全部配置任务（不含全池）" : `当前筛选结果的 ${tasks.total} 个任务`}？将跨越所有分页，执行时匹配的任务均会更新。`,
        )
      )
        return;
      const controls = [
        ...document.querySelectorAll(
          "[data-edit-task],[data-task-action],[data-batch],#new-collection,#select-filtered-tasks,#select-every-task,#clear-task-selection,#retry-failed-tasks",
        ),
      ];
      const disabled = controls.map((b) => b.disabled);
      controls.forEach((b) => (b.disabled = true));
      try {
        const result = await api("/research/collection/tasks/batch", {
          action,
          scope,
          ...extra,
          ...(scope === "selected"
            ? { tasks: rows.map((t) => ({ resource: t.resource, id: t.id })) }
            : { filters: scope === "filtered" ? { ...filters } : {} }),
        });
        toast(
          action === "refresh"
            ? "已安排采集，后台按限速执行"
            : `已更新 ${result.updated} 个任务`,
        );
        if (rev === currentRevision()) await refresh();
      } finally {
        controls.forEach((b, i) => {
          if (b.isConnected) b.disabled = disabled[i];
        });
      }
    }
    $("#retry-failed-tasks").onclick = () =>
      control("retry", [], "filtered").catch((e) => toast(e.message));
    document
      .querySelectorAll("[data-batch]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            control(b.dataset.batch, selected(), selectionScope).catch((e) =>
              toast(e.message),
            )),
      );
    document
      .querySelectorAll("[data-task-action]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            control(b.dataset.action, [
              tasks.items[Number(b.dataset.taskAction)],
            ]).catch((e) => toast(e.message))),
      );
    document
      .querySelectorAll("[data-edit-task]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            edit(tasks.items[Number(b.dataset.editTask)]).catch((e) =>
              toast(e.message),
            )),
      );
    document.querySelectorAll("[data-retry-plan]").forEach(
      (b) =>
        (b.onclick = async () => {
          try {
            await api("/research/plans/" + b.dataset.retryPlan + "/retry", {});
            await refresh();
          } catch (e) {
            toast(e.message);
          }
        }),
    );
  }
  render.prepare = (ids) => {
    preset = { kind: "details", app_ids: ids };
    section = "create";
    if (location.hash === "#collection?view=create")
      refresh(true).catch((e) => toast(e.message));
    else location.hash = "#collection?view=create";
  };
  return render;
}
