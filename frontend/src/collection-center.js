import { icon, storeLogo } from "./icons.js";
import { createTaskLogViewer } from "./task-logs.js";
export const taskKinds = {
  details: "应用详情",
  keywords: "关键词排名",
  chart: "分类榜单",
  reviews: "用户评论",
  featured: "编辑推荐",
  icons: "应用图标",
};
const states = {
  scheduled: "定时采集",
  pending: "等待采集",
  running: "采集中",
  paused: "已暂停",
  error: "失败待重试",
};

export function createCollectionCenter(ctx) {
  const logs = createTaskLogViewer(ctx);
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
    section = "tasks";
  let preset = null;
  const options = (obj) => Object.entries(obj);
  const refresh = () => render(nextRevision());
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
    editor(
      "编辑采集任务",
      `<p class="sub">${esc(taskKinds[row.kind])} · ${esc(row.label)} · ${esc(row.country.toUpperCase())} / ${esc(row.language)}</p><form id="edit-task-form" class="form-grid">${field("刷新周期（小时）", "refresh_hours", row.refresh_hours, "number", 'min="0.25" max="8760" step="0.25" required')}${row.resource === "watchlist" ? field("监控分组", "group_name", row.group_name, "text", "required") : row.resource === "icons" ? "" : field("采集结果上限", "depth", row.depth, "number", `min="1" max="${row.resource === "keywords" ? 200 : 500}" required`)}<p class="sub full">保留已有历史及启用状态。需要切换应用或市场时，请新建独立任务。</p><div class="full form-actions"><button type="submit">保存设置</button></div></form>`,
    );
    bindForm("#edit-task-form", async (v) => {
      await api(
        `/research/collection/tasks/${row.resource}/${row.id}`,
        {
          refresh_hours: Number(v.refresh_hours),
          ...(row.resource === "watchlist"
            ? { group_name: v.group_name }
            : row.resource === "icons"
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
          Object.entries(taskKinds).filter(([k]) => k !== "icons"),
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
      <p class="sub full">结果上限不保证实际返回数量。已有相同目标会更新设置，并保留暂停状态与历史。</p><div class="full form-actions"><button type="submit">预览采集计划</button></div></form><div id="plan-preview"></div><details class="plan-history"><summary>导入已有关键词词库（JSON）</summary><form id="keyword-import"><label>包含商店、国家、语言和关键词的数组<textarea name="json" required placeholder='[{"store":"google","country":"th","language":"th","keyword":"เงินกู้"}]'></textarea></label><button type="submit" class="spacer">校验并导入词库</button></form><p class="sub">支持跨市场批量导入；整批验证，保留已有任务历史和暂停状态。</p></details>`;
  }
  function bindCreation(rev) {
    $("#back-to-tasks").onclick = () => {
      section = "tasks";
      preset = null;
      refresh().catch((e) => toast(e.message));
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
      if (rev === currentRevision()) await refresh();
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
          Object.entries(flags).filter(([key]) => key !== "keywords"),
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
          await refresh();
        } catch (err) {
          toast(err.message);
          b.disabled = false;
        }
      };
    });
  }
  async function render(rev) {
    closeEditor();
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
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
        location.pathname + location.search + "#collection",
      );
    }
    filters.store = market().store;
    const [tasks, pipelines, quality] = await Promise.all([
      api(
        "/research/collection/tasks?" +
          new URLSearchParams({ ...filters, page, limit: 50 }),
      ),
      api("/research/collection/pipelines"),
      api("/research/collection/activity"),
    ]);
    const s = tasks.summary;
    const tab = (key, label) =>
      `<button class="secondary ${section === key ? "selected" : ""}" type="button" data-collection-tab="${key}" role="tab" aria-selected="${section === key}">${label}</button>`;
    const body =
      section === "create"
        ? panel("配置目标", creationForm())
        : section === "pipelines"
          ? panel(
              "后台基础采集",
              `<p class="sub">这些流程由全池监控服务执行，设置保存在数据库，当前请求 / 批次完成后应用。重点任务由上方任务列表独立管理。</p><div class="pipeline-grid">${pipelines.map((p) => `<form class="pipeline-card" data-pipeline="${p.id}"><h3>${esc(p.name)}</h3><p class="sub">${p.id === "seed" ? "检查 AppGoblin GitHub 公开历史包，不代表官网最新付费或登录下载。" : p.id === "pool" ? "按国家与语言检查已有应用池；百万应用需要持续排队。" : "从榜单、搜索词和已验证开发者补充应用 ID。"}</p><label class="check-label"><input name="enabled" type="checkbox" ${p.enabled ? "checked" : ""}>启用此流程</label>${field("检查周期（小时）", "refresh_hours", p.refresh_hours, "number", 'min="0.25" max="8760" step="0.25" required')}${p.id !== "seed" ? field("国家代码（逗号分隔）", "countries", p.countries.join(", "), "text", "required") + field("请求语言", "language", p.language, "text", "required") : ""}${p.id === "discovery" ? `<label>发现关键词（每行一个）<textarea name="search_terms">${esc(p.search_terms.join("\n"))}</textarea></label><label>Google 分类 ID（逗号分隔）<input name="google_categories" value="${esc(p.google_categories.join(", "))}"></label>` : ""}<button type="submit" class="secondary">保存流程设置</button><button type="button" class="secondary" data-pipeline-logs="${p.id}">查看运行日志</button><p role="status" class="sub pipeline-feedback"></p></form>`).join("")}</div>`,
            )
          : `${panel(
              "采集任务",
              `<form id="task-filters" class="toolbar">${field("搜索目标", "q", filters.q, "search", 'placeholder="应用 ID / 名称 / 关键词"')}${select("采集类型", "kind", [["", "全部类型"], ...options(taskKinds)], filters.kind)}${select(
                "商店",
                "store",
                [
                  ["all", "全部商店"],
                  ["google", "Google Play"],
                  ["apple", "App Store"],
                ],
                filters.store,
              )}${select("任务状态", "state", [["", "全部状态"], ...options(states)], filters.state)}${field("国家代码", "country", filters.country, "text", 'maxlength="2" placeholder="全部"')}<button type="submit" class="secondary">查询</button><button type="button" class="secondary" id="reset-task-filters">重置</button></form><div class="task-bulk"><span id="selected-count">已选 0 项</span><button type="button" class="secondary small" data-batch="refresh">立即采集</button><button type="button" class="secondary small" data-batch="pause">暂停</button><button type="button" class="secondary small" data-batch="resume">恢复</button><span class="sub">仅操作当前页选中项，最多 50 个</span></div>${table(
                [
                  '<input type="checkbox" id="select-all-tasks" aria-label="选择当前页全部任务">',
                  "采集类型 / 目标",
                  "商店 / 市场",
                  "周期 / 深度",
                  "最近成功 / 下次到期",
                  "状态",
                  "操作",
                ],
                tasks.items.map((t, index) => [
                  `<input type="checkbox" data-task-index="${index}" aria-label="选择任务 ${esc(t.kind === "chart" ? selectors.chartDefinitions(t.store).find((d) => d.id === t.collection)?.name || t.label : t.label)}">`,
                  `<span class="task-kind">${icon(t.kind === "details" ? "apps" : t.kind === "chart" ? "charts" : t.kind)}${esc(taskKinds[t.kind])}</span><div class="task-label">${t.app_id ? appLink(t.store, t.app_id, t.label, null, t.country, t.language) : esc(t.kind === "chart" ? selectors.chartDefinitions(t.store).find((d) => d.id === t.collection)?.name || t.label : t.label)}</div>${t.category ? `<div class="sub">${esc(selectors.categoryName(t.store, t.category))}</div>` : ""}`,
                  `<span class="store-caption">${storeLogo(t.store)}${t.store === "apple" ? "App Store" : "Google Play"}</span><div class="sub">${esc(t.country.toUpperCase())} · ${esc(t.language)}</div>`,
                  `${t.refresh_hours} 小时<div class="sub">${t.depth ? num(t.depth) + " 条" : t.kind === "icons" ? "图标缓存" : "单应用详情"}</div>`,
                  `${stamp(t.success_at)}<div class="sub">${t.enabled ? "到期 " + (t.due ? stamp(t.due) : "尽快执行") : "已暂停"}</div>`,
                  `<span class="badge ${t.task_state === "error" ? "warn" : t.task_state === "running" ? "good" : ""}">${esc(states[t.task_state])}</span>`,
                  `<div class="task-actions"><button class="link" data-task-logs="${index}">日志${t.task_state === "running" ? " · 实时" : ""}</button><button class="link" data-edit-task="${index}">编辑</button><button class="link" data-task-action="${index}" data-action="${t.enabled ? "pause" : "resume"}">${t.enabled ? "暂停" : "恢复"}</button><button class="link" data-task-action="${index}" data-action="refresh" ${t.enabled ? "" : "disabled"}>立即采集</button></div>`,
                ]),
              )}<div class="task-pagination"><span>共 ${num(tasks.total)} 个任务 · 第 ${page} / ${Math.max(1, Math.ceil(tasks.total / 50))} 页</span><div class="actions"><button class="secondary small" id="previous-tasks" ${page <= 1 ? "disabled" : ""}>上一页</button><button class="secondary small" id="next-tasks" ${tasks.has_more ? "" : "disabled"}>下一页</button></div></div>`,
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
          `<button class="link" data-plan-logs="${p.id}">日志</button> ${p.status === "error" ? `<button class="link" data-retry-plan="${p.id}">重试未完成部分</button>` : ""}`,
        ]),
      )}</details>`;
    if (
      !mount(
        `<div class="collection-heading"><p class="lead">在一个地方配置全部采集流程，查看任务进度并处理失败。</p><button id="new-collection">${icon("plus")}新建采集计划</button></div><div class="cards collection-metrics">${metric("筛选结果", tasks.total, "已配置的独立采集目标")}${metric("采集中 / 等待", s.running + s.pending, "依照限速与队列执行")}${metric("失败待重试", s.error, "保留最近成功结果")}${metric("已暂停", s.paused, "恢复后继续按周期采集")}</div><div class="collection-status"><span class="status-dot"></span>后台最近心跳：${esc(stamp(quality.heartbeat?.next_at))}<span class="sub">所有市场独立保存 · 不改写历史</span></div><div class="collection-tabs" role="tablist" aria-label="采集管理视图">${tab("tasks", "采集任务")}${tab("pipelines", "基础采集流程")}${section === "create" ? tab("create", "新建计划") : ""}</div>${body}`,
        rev,
      )
    )
      return;
    const storeFilter = $('#task-filters [name="store"]');
    if (storeFilter)
      storeFilter.onchange = () => {
        $("#global-store").value = storeFilter.value;
        $("#global-store").dispatchEvent(
          new Event("change", { bubbles: true }),
        );
      };
    $("#new-collection").onclick = () => {
      section = "create";
      preset = null;
      refresh().catch((e) => toast(e.message));
    };
    document.querySelectorAll("[data-collection-tab]").forEach(
      (b) =>
        (b.onclick = () => {
          section = b.dataset.collectionTab;
          refresh().catch((e) => toast(e.message));
        }),
    );
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
      await refresh();
    });
    $("#reset-task-filters").onclick = () => {
      filters = { kind: "", store: "", state: "", country: "", q: "" };
      page = 1;
      refresh().catch((e) => toast(e.message));
    };
    $("#previous-tasks").onclick = () => {
      page--;
      refresh().catch((e) => toast(e.message));
    };
    $("#next-tasks").onclick = () => {
      page++;
      refresh().catch((e) => toast(e.message));
    };
    const selected = () =>
      [...document.querySelectorAll("[data-task-index]:checked")].map(
        (el) => tasks.items[Number(el.dataset.taskIndex)],
      );
    const selectionChange = () => {
      $("#selected-count").textContent = `已选 ${selected().length} 项`;
      document
        .querySelectorAll("[data-batch]")
        .forEach((b) => (b.disabled = !selected().length));
    };
    const selectAll = $("#select-all-tasks");
    if (selectAll)
      selectAll.onchange = (e) => {
        document
          .querySelectorAll("[data-task-index]")
          .forEach((c) => (c.checked = e.target.checked));
        selectionChange();
      };
    document
      .querySelectorAll("[data-task-index]")
      .forEach((c) => (c.onchange = selectionChange));
    selectionChange();
    async function control(action, rows) {
      const controls = [
        ...document.querySelectorAll(
          "[data-edit-task],[data-task-action],[data-batch],#new-collection",
        ),
      ];
      const disabled = controls.map((b) => b.disabled);
      controls.forEach((b) => (b.disabled = true));
      try {
        await api("/research/collection/tasks/batch", {
          action,
          tasks: rows.map((t) => ({ resource: t.resource, id: t.id })),
        });
        toast(
          action === "refresh"
            ? "已安排采集，后台按限速执行"
            : "任务状态已更新",
        );
        if (rev === currentRevision()) await refresh();
      } finally {
        controls.forEach((b, i) => {
          if (b.isConnected) b.disabled = disabled[i];
        });
      }
    }
    document
      .querySelectorAll("[data-batch]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            control(b.dataset.batch, selected()).catch((e) =>
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
    if (location.hash === "#collection")
      refresh().catch((e) => toast(e.message));
    else location.hash = "collection";
  };
  return render;
}
