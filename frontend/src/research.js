import { createCollectionCenter } from "./collection-center.js";
// Business workflows built on observed records. Shared visual tokens live in theme.css.
export function createResearchPages(ctx) {
  const {
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
    currentRevision,
    nextRevision,
  } = ctx;
  const query = (obj) =>
    new URLSearchParams(
      Object.entries(obj).filter(([, v]) => v !== "" && v != null),
    ).toString();
  const statusNames = {
    queued: "待配置",
    running: "配置中",
    complete: "已配置",
    error: "失败",
    replied: "已观察到回复",
    not_observed: "未观察到回复",
    unknown: "来源未提供回复状态",
  };
  const status = (v) =>
    `<span class="badge ${v === "error" ? "warn" : ""}">${esc(statusNames[v] || v)}</span>`;
  const kindNames = {
    details: "应用详情",
    keywords: "关键词",
    reviews: "评论",
    chart: "分类榜单",
    featured: "精品推荐",
  };
  const coverageNames = {
    one_regional_listing: "单地区详情",
    first_response: "首个搜索响应",
    paginated_search: "已续采至目标或来源末尾",
    chart_source_limit: "来源实际榜单范围（未达目标数量）",
    reviews_source_limit: "评论来源窗口上限（非历史全量）",
    recent_reviews_bounded: "近期评论窗口",
    returned_chart_range: "实际返回榜单",
    visible_editorial_cards: "可见推荐卡片",
  };
  const dateInput = (v) =>
    v ? new Date(v * 1000).toISOString().slice(0, 10) : "";
  const link = (url) => {
    try {
      const u = new URL(url);
      return ["https:", "http:"].includes(u.protocol) ? esc(u.href) : "";
    } catch {
      return "";
    }
  };
  const eventNames = {
    first_verified: "首次验证",
    metadata_changed: "资料变化",
    region_unavailable: "地区不可用",
    reappeared: "重新出现",
  };
  const stateNames = {
    available: "已观察在架",
    suspected_missing: "待确认",
    region_unavailable: "地区不可用",
  };
  const fieldNames = {
    name: "名称",
    version: "版本",
    subtitle: "副标题",
    keywords: "后台关键词",
    description: "描述",
    summary: "简短描述",
    screenshots: "截图",
    icon: "图标",
    price: "售价",
    developer: "开发者",
    release_notes: "更新日志",
    rating: "评分",
    ratings: "评分数",
  };
  const marketCaption = (m) =>
    `${m.store === "all" ? "全部商店" : m.store === "apple" ? "App Store" : "Google Play"} · ${selectors.countryName(m.country)} / ${m.language === "auto" ? "自动语言" : m.language}`;
  function dialog(title, html) {
    const old = $("#research-dialog");
    old?.remove();
    const el = document.createElement("dialog");
    el.id = "research-dialog";
    el.className = "research-dialog";
    el.innerHTML = `<form method="dialog" class="picker-heading"><h2>${esc(title)}</h2><button class="secondary small" aria-label="关闭详情">关闭</button></form>${html}`;
    document.body.append(el);
    el.showModal();
    return el;
  }
  function valueView(field, value) {
    if (value == null) return '<span class="sub">未提供</span>';
    if (["icon", "screenshots"].includes(field)) {
      const urls = Array.isArray(value) ? value : [value];
      return `<div class="asset-comparison">${urls
        .filter(link)
        .map(
          (u) =>
            `<img src="${link(u)}" alt="${esc(fieldNames[field])}" loading="lazy">`,
        )
        .join("")}</div>`;
    }
    return `<div class="text-value">${esc(typeof value === "object" ? JSON.stringify(value) : value)}</div>`;
  }
  function changesView(changes) {
    const fields = Object.entries(changes);
    return fields.length
      ? fields
          .map(
            ([k, v]) =>
              `<article class="change-field"><h3>${esc(fieldNames[k] || k)}</h3><div class="before-after"><div><span class="sub">之前</span>${valueView(k, v?.old)}</div><div><span class="sub">之后</span>${valueView(k, v?.new)}</div></div></article>`,
          )
          .join("")
      : empty("没有字段差异", "这是状态事件，详情保留在原始观察记录中。");
  }
  function bindButton(selector, action) {
    $(selector)?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await action();
      } catch (e) {
        toast(e.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  async function events(rev, filters = {}) {
    const data = await api(
      "/research/events?" + query({ ...market(), ...filters }),
    );
    const total = data.summary.reduce((a, r) => a + Number(r.count), 0),
      changed =
        data.summary.find((r) => r.kind === "metadata_changed")?.count || 0;
    const days = new Map();
    for (const row of data.daily)
      days.set(row.day, (days.get(row.day) || 0) + Number(row.count));
    if (
      !mount(
        `<p class="lead">汇总版本、文案、素材、售价与地区状态的真实变化，用前后对照复盘每次调整。</p><div class="cards">${metric("观察事件", total, "当前市场与筛选区间")}${metric("资料变化", changed, "包含版本、素材、价格等字段")}${metric("地区不可用", data.summary.find((r) => r.kind === "region_unavailable")?.count || 0, "保留多次确认口径")}${metric("人工注记", data.annotations.length, "手工记录，不冒充采集事件")}</div>${panel(
          "事件筛选",
          `<form id="event-filter" class="toolbar">${field("应用 ID", "app_id", filters.app_id || "")}${select("事件", "kind", [["", "全部"], ...Object.entries(eventNames)])}${select("变更字段", "field", [["", "全部"], ...Object.entries(fieldNames)])}${field("关注分组", "group_name", filters.group_name || "")}${select(
            "观察区间",
            "days",
            [
              ["7", "最近7天"],
              ["30", "最近30天"],
              ["90", "最近90天"],
              ["365", "最近365天"],
            ],
          )}<button type="submit">查询</button><button type="button" class="secondary" id="add-annotation">添加复盘注记</button></form><p class="sub">${esc(data.scope)}</p>`,
        )}${panel("每日观察日历（UTC）", `<div class="event-calendar">${[...days].map(([at, count]) => `<button class="calendar-cell secondary" data-event-day="${at}"><span>${esc(dateInput(at))}</span><strong>${num(count)}</strong><small>观察事件</small></button>`).join("") || empty("区间内暂无事件")}</div><div id="day-selection" class="sub"></div>`)}${panel("变更记录 · 最近300条", `<div id="event-table"></div>`)}${panel(
          "人工复盘注记",
          table(
            ["事件时间", "应用", "标题", "备注 / 证据"],
            data.annotations.map((r) => [
              stamp(r.event_at),
              appLink(r.store, r.app_id),
              esc(r.title),
              esc(r.note) +
                (link(r.evidence_url)
                  ? ` <a href="${link(r.evidence_url)}" target="_blank" rel="noopener">证据 ↗</a>`
                  : ""),
            ]),
          ),
        )}`,
        rev,
      )
    )
      return;
    const form = $("#event-filter");
    form.elements.kind.value = filters.kind || "";
    form.elements.field.value = filters.field || "";
    form.elements.days.value = filters.days || "30";
    const renderRows = (rows) => {
      $("#event-table").innerHTML = table(
        ["时间", "应用", "事件", "变化字段", "对照"],
        rows.map((r) => [
          stamp(r.observed_at),
          appLink(r.store, r.app_id, r.name, null, r.country, r.language),
          esc(eventNames[r.kind] || r.kind),
          Object.keys(r.changes)
            .map((k) => esc(fieldNames[k] || k))
            .join("、") || "—",
          `<button class="link" data-event-detail="${r.id}">前后对照</button>`,
        ]),
      );
    };
    renderRows(data.items);
    bindForm("#event-filter", (v) => events(nextRevision(), v));
    $("#content").addEventListener("click", async function handler(event) {
      if (rev !== currentRevision()) {
        this.removeEventListener("click", handler);
        return;
      }
      const detail = event.target.closest("[data-event-detail]"),
        day = event.target.closest("[data-event-day]");
      if (day) {
        const at = Number(day.dataset.eventDay);
        renderRows(
          data.items.filter(
            (r) => Math.floor(r.observed_at / 86400) * 86400 === at,
          ),
        );
        $("#day-selection").textContent =
          "当前列表：" + dateInput(at) + "（UTC）；重新查询恢复全部";
      }
      if (detail) {
        const row = data.items.find(
          (r) => r.id === Number(detail.dataset.eventDetail),
        );
        dialog(
          eventNames[row.kind] || row.kind,
          `<p class="sub">${esc(marketCaption(row))} · 观察于 ${esc(stamp(row.observed_at))}</p>${changesView(row.changes)}<div class="callout">变化与排名同时出现只是复盘线索，不表示因果关系。</div>${appLink(row.store, row.app_id, row.name, null, row.country, row.language)}`,
        );
      }
    });
    bindButton("#add-annotation", () => {
      if (market().store === "all") {
        toast("请先在顶部选择注记所属的应用商店");
        return;
      }
      dialog(
        "添加复盘注记",
        `<form id="annotation-form" class="form-grid">${field("应用ID", "app_id", filters.app_id || "", "text", "required")}${field("事件日期（UTC）", "day", dateInput(Date.now() / 1000), "date", "required")}${field("标题", "title", "", "text", 'required maxlength="255"')}${field("证据链接（可空）", "evidence_url", "", "url")}<label class="full">复盘说明<textarea name="note" maxlength="4000"></textarea></label><button type="submit">保存注记</button></form>`,
      );
      bindForm("#annotation-form", async (v) => {
        await api("/research/annotations", {
          ...market(),
          ...v,
          event_at: Date.parse(v.day + "T00:00:00Z") / 1000,
        });
        $("#research-dialog").close();
        toast("注记已保存");
        await events(nextRevision(), filters);
      });
    });
  }

  async function aso(rev, appId = "") {
    if (market().store === "all") {
      mount(
        panel(
          "选择应用商店",
          '<p class="sub">ASO 文案和字段各有不同，请在顶部选择 Google Play 或 App Store 后编辑。</p>',
        ),
        rev,
      );
      return;
    }
    if (
      !mount(
        `<p class="lead">编辑本地元数据草稿，对照实际关键词排名与文本覆盖；保存版本后再到自己的商店后台发布。</p>${panel("选择应用市场", `<form id="aso-load" class="toolbar">${field("应用 ID", "app_id", appId, "text", "required")}<button type="submit">加载已采文案</button></form><p class="sub">${esc(marketCaption(market()))}。Apple后台关键词和未采集副标题需由用户提供。</p>`)}<div id="aso-workspace">${empty("选择应用开始文案核查", "公开详情会作为基准快照；不会读取竞品私有后台。")}</div>`,
        rev,
      )
    )
      return;
    bindForm("#aso-load", (v) => aso(nextRevision(), v.app_id));
    if (!appId) return;
    const m = { ...market(), app_id: appId };
    const data = await api("/research/aso?" + query(m));
    if (rev !== currentRevision()) return;
    let activeDraft = null;
    const fields = data.checks.counters;
    $("#aso-workspace").innerHTML =
      `<div class="callout">公开文案最近成功：${esc(stamp(data.last_success))} · 基准快照 ${data.snapshot?.id || "暂无"} · 未提供字段：${data.unavailable_fields.map((k) => esc(fieldNames[k] || k)).join("、") || "无"}</div><div class="research-grid"><section class="panel"><h2>文案编辑</h2><form id="aso-editor">${field("草稿名称", "draft_name", "文案优化草稿", "text", 'required maxlength="120"')}${fields.map((f) => `<label class="spacer">${esc(f.label)}<textarea data-aso-field="${f.field}" name="${f.field}" rows="${f.field === "description" ? 7 : 2}">${esc(data.metadata[f.field])}</textarea><span class="field-counter" data-counter="${f.field}"></span></label>`).join("")}<label class="spacer">本次修改说明<textarea name="note" rows="2" maxlength="4000"></textarea></label><div class="toolbar spacer"><button type="submit">保存本地草稿</button><button type="button" class="secondary" id="aso-check">核查文案</button><button type="button" class="secondary" id="aso-new">另存新草稿</button></div></form></section><section class="panel"><h2>商店文案预览</h2><div class="listing-preview">${appLink(m.store, m.app_id, data.metadata.name || m.app_id, data.creative?.icon, m.country, m.language)}<div><h3 id="preview-title"></h3><p id="preview-summary" class="sub"></p><span class="badge neutral">本地草稿 · 未发布</span></div></div><h3 class="spacer">核查词库</h3><label>每行一个关键词<textarea id="aso-words">${esc(data.keywords.map((w) => w.keyword).join("\n"))}</textarea></label><p class="sub">文本命中不代表被商店索引；排名来自当前已跟踪词库。</p><div id="aso-findings"></div><div id="aso-coverage"></div></section></div>${panel(
        "草稿版本库",
        '<div id="aso-draft-list"></div>',
      )}<div id="aso-versions"></div>${panel("ASO报告词库", '<div id="aso-library"></div>')}`;
    let libraryQuery = "",
      libraryOffset = 0,
      libraryRequest = 0;
    async function loadLibrary() {
      const request = ++libraryRequest;
      const words = await api(
        "/research/aso/keywords?" +
          query({ ...m, q: libraryQuery, offset: libraryOffset }),
      );
      if (rev !== currentRevision() || request !== libraryRequest) return;
      $("#aso-library").innerHTML =
        `<p class="sub">当前应用共 ${words.total} 个自然搜索词。${esc(words.note)}</p><form id="aso-library-search" class="toolbar">${field("筛选报告词", "q", libraryQuery)}<button type="submit">筛选</button></form><div class="toolbar"><button type="button" id="aso-library-add">本页加入核查词库</button><button type="button" id="aso-library-prev" class="secondary" ${libraryOffset ? "" : "disabled"}>上一页</button><button type="button" id="aso-library-next" class="secondary" ${libraryOffset + words.limit < words.total ? "" : "disabled"}>下一页</button><span>每页 ${words.limit} 词，核查最多500词</span></div>${table(
          ["关键词", "最佳报告排名", "最近报告日期"],
          words.items.map((r) => [
            esc(r.keyword),
            r.best_report_position,
            stamp(r.latest_report_at),
          ]),
        )}`;
      bindForm("#aso-library-search", async (v) => {
        libraryQuery = v.q;
        libraryOffset = 0;
        await loadLibrary();
      });
      bindButton("#aso-library-prev", async () => {
        libraryOffset = Math.max(0, libraryOffset - words.limit);
        await loadLibrary();
      });
      bindButton("#aso-library-next", async () => {
        libraryOffset += words.limit;
        await loadLibrary();
      });
      bindButton("#aso-library-add", () => {
        const current = $("#aso-words")
          .value.split(/\r?\n/)
          .map((v) => v.trim())
          .filter(Boolean);
        const merged = [
          ...new Set([...current, ...words.items.map((r) => r.keyword)]),
        ];
        if (merged.length > 500) {
          toast("核查词库最多500词，请先减少现有词或筛选报告词");
          return;
        }
        $("#aso-words").value = merged.join("\n");
        toast(`核查词库已更新，共 ${merged.length} 词`);
      });
    }
    loadLibrary().catch((e) => {
      if (rev === currentRevision()) toast(e.message);
    });
    function renderDraftList() {
      $("#aso-draft-list").innerHTML = data.drafts.length
        ? table(
            ["名称", "版本", "最近保存", "操作"],
            data.drafts.map((d) => [
              esc(d.name),
              "v" + d.revision,
              stamp(d.updated_at),
              `<button class="link" data-draft-id="${d.id}">加载 / 历史</button>`,
            ]),
          )
        : empty("还没有本地草稿", "保存文案后，会在此显示可回溯的修订记录。");
    }
    renderDraftList();
    const editor = $("#aso-editor");
    const metadata = () =>
      Object.fromEntries(
        fields.map((f) => [f.field, editor.elements[f.field].value]),
      );
    function counters() {
      for (const f of fields) {
        const value = editor.elements[f.field].value;
        const n =
          f.unit === "utf8_bytes"
            ? new TextEncoder().encode(value).length
            : Array.from(value).length;
        const node = $('[data-counter="' + f.field + '"]');
        node.textContent =
          n +
          " / " +
          f.limit +
          " " +
          (f.unit === "utf8_bytes" ? "UTF-8字节" : "字符");
        node.classList.toggle("over-limit", n > f.limit);
      }
      $("#preview-title").textContent =
        editor.elements.name.value || "应用名称";
      $("#preview-summary").textContent =
        editor.elements.subtitle?.value ||
        editor.elements.summary?.value ||
        "副标题 / 简短描述";
    }
    const words = () =>
      $("#aso-words")
        .value.split("\n")
        .map((w) => w.trim())
        .filter(Boolean);
    async function check() {
      const result = await api("/research/aso/check", {
        store: m.store,
        metadata: metadata(),
        keywords: words(),
      });
      if (rev !== currentRevision()) return;
      $("#aso-findings").innerHTML = `<h3 class="spacer">文案检查</h3>${
        result.findings.length
          ? table(
              ["字段", "发现", "证据"],
              result.findings.map((f) => [
                esc(f.field),
                esc(f.message),
                esc(f.evidence),
              ]),
            )
          : '<p class="sub">当前未发现长度或重复词问题。</p>'
      }`;
      $("#aso-coverage").innerHTML =
        `<h3 class="spacer">关键词文本覆盖</h3>${table(
          ["关键词", "实际观察位置", "命中字段"],
          result.coverage.map((w) => {
            const rank = data.keywords.find((k) => k.keyword === w.keyword);
            return [
              esc(w.keyword),
              rank && !rank.stale ? num(rank.position) : "未知 / 过期",
              w.fields.map(esc).join("、") || "未命中",
            ];
          }),
        )}`;
    }
    editor.addEventListener("input", counters);
    counters();
    bindButton("#aso-check", check);
    async function save(asNew = false) {
      const saved = await api("/research/aso/drafts", {
        ...m,
        ...(activeDraft && !asNew
          ? { id: activeDraft.id, revision: activeDraft.revision }
          : {}),
        name: editor.elements.draft_name.value,
        metadata: metadata(),
        note: editor.elements.note.value,
      });
      activeDraft = { id: saved.id, revision: saved.revision };
      toast("已保存本地草稿 v" + saved.revision + "，未发布到商店");
      await loadDraft(saved.id);
    }
    bindForm("#aso-editor", () => save());
    bindButton("#aso-new", () => save(true));
    async function loadDraft(id) {
      const draft = await api("/research/aso/drafts/" + id);
      if (rev !== currentRevision()) return;
      activeDraft = draft;
      data.drafts = [draft, ...data.drafts.filter((d) => d.id !== draft.id)];
      renderDraftList();
      editor.elements.draft_name.value = draft.name;
      editor.elements.note.value = draft.note;
      for (const f of fields)
        editor.elements[f.field].value = draft.metadata[f.field] || "";
      counters();
      await check();
      $("#aso-versions").innerHTML = panel(
        "修订历史 · " + esc(draft.name),
        table(
          ["版本", "保存时间", "修改说明", "操作"],
          draft.versions.map((v) => [
            "v" + v.revision,
            stamp(v.saved_at),
            esc(v.note),
            `<button class="link" data-restore-version="${v.revision}">载入此版本</button>`,
          ]),
        ),
      );
      $("#aso-versions").onclick = (event) => {
        const b = event.target.closest("[data-restore-version]");
        if (b) {
          const version = draft.versions.find(
            (v) => v.revision === Number(b.dataset.restoreVersion),
          );
          for (const f of fields)
            editor.elements[f.field].value = version.metadata[f.field] || "";
          editor.elements.note.value = "基于 v" + version.revision + " 修改";
          counters();
          toast("历史文案已载入，保存会生成新修订");
        }
      };
    }
    $("#aso-workspace").addEventListener("click", (event) => {
      const b = event.target.closest("[data-draft-id]");
      if (b)
        loadDraft(Number(b.dataset.draftId)).catch((e) => toast(e.message));
    });
    await check();
  }

  async function reviews(rev, filters = {}) {
    const data = await api(
      "/research/reviews?" + query({ ...market(), ...filters }),
    );
    const mean = data.summary.mean_review_rating;
    const pageActions = `<div class="actions"><span class="sub">第 ${Math.floor((filters.offset || 0) / 50) + 1} 页 · ${num(data.items.length)} 条</span><button id="reviews-prev" class="secondary small" ${(filters.offset || 0) > 0 ? "" : "disabled"}>上一页</button><button id="reviews-next" class="secondary small" ${data.next_offset == null ? "disabled" : ""}>下一页</button></div>`;
    const cards = data.items
      .map(
        (r, i) =>
          `<article class="review-card"><div class="review-card-head">${appLink(r.store, r.app_id, r.app_name)}<span class="review-stars">${esc(r.rating)} ★</span><span class="review-market"><img class="country-flag" src="/flags/${esc(r.country)}.svg" alt="">${esc(selectors.countryName(r.country))} · ${esc(r.country.toUpperCase())} / ${esc(r.language)}</span></div>${r.title ? `<strong>${esc(r.title)}</strong>` : ""}<p class="review-body">${esc(r.content)}</p><div class="review-card-foot"><span>${esc(r.version || "版本未知")} · 发表 ${esc(r.published_at || "未知")}<small class="sub">首次观察 ${stamp(r.first_seen)} · 最近观察 ${stamp(r.last_seen)}</small></span><div class="actions"><button class="secondary small" data-review-app="${i}">仅看此应用</button><button class="secondary small" data-review-detail="${i}">查看记录</button></div></div></article>`,
      )
      .join("");
    if (
      !mount(
        `<div class="cards">${metric("匹配评论", data.summary.total, "当前筛选的已采集记录")}${metric("评论平均星级", mean == null ? null : Number(mean).toFixed(2), "评论样本均值，非商店累计评分")}${metric("覆盖应用", data.summary.apps, "当前筛选内按商店区分应用")}${metric("当前页记录", data.items.length, market().store === "all" ? "每店每页最多50条" : "每页最多50条")}</div>${panel("评论筛选", `<form id="review-record-filter" class="toolbar">${field("应用 ID", "app_id", filters.app_id || "")}${select("星级", "rating", [["", "全部"], ...["1", "2", "3", "4", "5"].map((v) => [v, v + "星"])], filters.rating || "")}${field("应用版本", "version", filters.version || "")}${field("评论内容 / 标题", "q", filters.q || "")}${field("首次观察开始日（UTC）", "since_day", dateInput(filters.since), "date")}<button type="submit">查询</button><button type="button" class="secondary" id="configure-reviews">配置评论采集</button></form><p class="sub">${esc(data.scope)}</p>`)}<div class="grid2">${panel("星级分布", bars(data.ratings))}${panel(
          "首次观察趋势（UTC日期）",
          chart(
            data.daily.map((r) => ({ at: r.day, value: r.count })),
            false,
            dateInput,
          ),
        )}</div>${panel("已采集评论", cards || empty("当前筛选没有评论", "可调整筛选或先配置该应用的评论采集。"), pageActions)}`,
        rev,
      )
    )
      return;
    bindForm("#review-record-filter", (v) => {
      const { since_day, ...rest } = v;
      return reviews(nextRevision(), {
        ...rest,
        since: since_day ? Date.parse(since_day + "T00:00:00Z") / 1000 : null,
      });
    });
    bindButton("#reviews-prev", () =>
      reviews(nextRevision(), {
        ...filters,
        offset: Math.max(0, (filters.offset || 0) - 50),
      }),
    );
    bindButton("#reviews-next", () =>
      reviews(nextRevision(), { ...filters, offset: data.next_offset }),
    );
    bindButton("#configure-reviews", () =>
      ctx.reviewTargets(
        $("#review-record-filter").elements.app_id.value.trim(),
      ),
    );
    $("#content")
      .querySelectorAll("[data-review-app]")
      .forEach((button) => {
        button.onclick = () =>
          reviews(nextRevision(), {
            ...filters,
            app_id: data.items[Number(button.dataset.reviewApp)].app_id,
            offset: 0,
          }).catch((e) => toast(e.message));
      });
    $("#content")
      .querySelectorAll("[data-review-detail]")
      .forEach((button) => {
        button.onclick = () => {
          const r = data.items[Number(button.dataset.reviewDetail)];
          const key = {
            store: r.store,
            app_id: r.app_id,
            country: r.country,
            language: r.language,
            review_id: r.review_id,
          };
          const el = dialog(
            "评论记录详情",
            `<dl class="detail-facts">${[
              ["应用", r.app_name || r.app_id],
              ["应用 ID", r.app_id],
              ["市场", marketCaption(r)],
              ["评论 ID", r.review_id],
              ["星级", r.rating],
              ["应用版本", r.version || "未知"],
              ["发表时间", r.published_at || "未知"],
              ["首次观察", stamp(r.first_seen)],
              ["最近观察", stamp(r.last_seen)],
            ]
              .map(
                ([label, value]) => `<dt>${label}</dt><dd>${esc(value)}</dd>`,
              )
              .join(
                "",
              )}</dl>${r.title ? `<h3>${esc(r.title)}</h3>` : ""}<p class="review-body">${esc(r.content)}</p>${r.public_reply ? `<section class="review-public-reply"><h3>商店公开回复（采集记录）</h3><p class="review-body">${esc(r.public_reply)}</p></section>` : ""}<button class="secondary" id="review-revisions">查看采集历史</button><p class="sub">最近100次采集记录，按应用、商店、国家、语言和评论 ID 区分。</p><div id="review-history"></div>`,
          );
          bindButton("#review-revisions", async () => {
            const history = await api("/research/reviews/history", key);
            if (!el.isConnected || !el.open) return;
            el.querySelector("#review-history").innerHTML = table(
              ["观察时间", "星级", "版本", "标题 / 原文", "公开回复"],
              history.items.map((v) => [
                stamp(v.observed_at),
                esc(v.payload.rating),
                esc(v.payload.version || "—"),
                `${esc(v.payload.title || "")}<p class="review-body">${esc(v.payload.content || "")}</p>`,
                esc(v.payload.reply || "—"),
              ]),
            );
          });
        };
      });
  }

  async function competitors(rev) {
    const sets = await api("/research/comparisons");
    if (
      !mount(
        `<p class="lead">把反复研究的竞品保存为同市场集合。应用角色由你指定，跨商店和跨市场排名不会混合相加。</p>${panel("保存竞品集合", `<form id="set-form" class="form-grid">${field("集合名称", "name", "", "text", "required")}${field("主比较应用ID", "primary", "", "text", "required")}<label class="full">对照应用ID（每行一个，最多5个）<textarea name="competitors" required></textarea></label><label class="check-label"><input type="checkbox" name="owned">主比较应用是我的自有应用</label><button type="submit">保存集合</button></form>`)}${panel(
          "我的竞品集合",
          table(
            ["名称", "市场", "成员", "操作"],
            sets.map((s) => [
              esc(s.name),
              esc(marketCaption(s)),
              s.members.length,
              `<button class="link" data-comparison-id="${s.id}">打开对比</button>`,
            ]),
          ),
        )}<div id="set-detail"></div>`,
        rev,
      )
    )
      return;
    if (market().store === "all")
      $("#set-form").innerHTML =
        '<p class="sub">已展示两家商店的竞品集合。新建同市场集合前，请在顶部选择单个商店。</p>';
    bindForm("#set-form", async (v) => {
      const ids = [v.primary, ...v.competitors.split(/\s+/).filter(Boolean)];
      await api("/research/comparisons", {
        name: v.name,
        members: ids.map((app_id, i) => ({
          ...market(),
          app_id,
          role: i === 0 && v.owned ? "own" : "competitor",
        })),
      });
      toast("竞品集合已保存");
      await competitors(nextRevision());
    });
    $("#content").addEventListener("click", async function handler(event) {
      if (rev !== currentRevision()) {
        this.removeEventListener("click", handler);
        return;
      }
      const b = event.target.closest("[data-comparison-id]");
      if (!b) return;
      try {
        const data = await api(
          "/research/comparisons/" + b.dataset.comparisonId,
        );
        if (rev !== currentRevision()) return;
        const r = data.report,
          s = data.set;
        $("#set-detail").innerHTML =
          `<div class="callout">${esc(s.name)} · ${esc(marketCaption(s))} · 仅比较当前有效观察；空值为未观察或过期。</div>${panel(
            "资料对照",
            table(
              [
                "应用 / 角色",
                "开发者",
                "版本",
                "商店评分 / 评分数",
                "售价",
                "观察时间",
              ],
              r.apps.map((a, i) => [
                appLink(
                  a.store,
                  a.app_id,
                  a.payload?.name,
                  a.payload?.icon,
                  a.country,
                  a.language,
                ) +
                  `<div class="sub">${s.members[i].role === "own" ? "自有应用" : "对照应用"}</div>`,
                esc(a.payload?.developer),
                esc(a.payload?.version),
                num(a.payload?.rating) + " / " + num(a.payload?.ratings),
                a.payload?.price == null
                  ? "未知"
                  : esc(a.payload.currency) + " " + num(a.payload.price),
                stamp(a.listing?.last_success),
              ]),
            ),
          )}${panel(
            "关键词交集与缺口",
            `<p class="sub">共同覆盖 ${r.common.length} 个词；主比较应用的观察缺口 ${r.gaps_for_first.length} 个。</p>` +
              table(
                [
                  "关键词",
                  ...r.apps.map((a) => esc(a.payload?.name || a.app_id)),
                ],
                r.keywords.map((k) => [
                  esc(k.keyword),
                  ...k.positions.map(num),
                ]),
              ),
          )}${panel(
            "近期动态",
            table(
              ["应用", "事件", "观察时间"],
              r.apps.flatMap((a) =>
                a.recent_events.map((e) => [
                  esc(a.payload?.name || a.app_id),
                  esc(eventNames[e.kind] || e.kind),
                  stamp(e.observed_at),
                ]),
              ),
            ),
          )}<button class="secondary" id="monitor-set">为此集合创建监控计划</button>`;
        bindButton("#monitor-set", async () => {
          for (const key of ["store", "country", "language"]) {
            document.querySelector("#global-" + key).value = s[key];
          }
          document
            .querySelector("#global-store")
            .dispatchEvent(new Event("change", { bubbles: true }));
          planForApps(s.members.map((m) => m.app_id));
        });
      } catch (e) {
        toast(e.message);
      }
    });
  }

  async function developers(rev, q = "") {
    const rows = await api(
      "/research/developers?" + query({ store: market().store, q }),
    );
    if (
      !mount(
        `<p class="lead">以商店开发者标识查看已观察产品和地区版本，不按同名推断跨商店公司关系。</p>${panel(
          "开发者检索",
          `<form id="developer-filter" class="toolbar">${field("开发者名称", "q", q)}<button type="submit">搜索</button></form>` +
            table(
              [
                "开发者",
                "商店开发者ID",
                "已观察应用",
                "市场数",
                "最新成功",
                "详情",
              ],
              rows.map((r, i) => [
                esc(r.name),
                esc(r.developer_id || "未知"),
                r.apps,
                r.markets,
                stamp(r.last_success),
                `<button class="link" data-developer="${i}">产品与地区</button>`,
              ]),
            ),
        )}<div id="developer-detail"></div>`,
        rev,
      )
    )
      return;
    bindForm("#developer-filter", (v) => developers(nextRevision(), v.q));
    $("#content").addEventListener("click", async function handler(event) {
      if (rev !== currentRevision()) {
        this.removeEventListener("click", handler);
        return;
      }
      const b = event.target.closest("[data-developer]");
      if (b) {
        try {
          const dev = rows[Number(b.dataset.developer)],
            data = await api(
              "/research/developer-apps?" +
                query({
                  store: dev.store,
                  name: dev.name,
                  developer_id: dev.developer_id,
                }),
            );
          if (rev !== currentRevision()) return;
          $("#developer-detail").innerHTML = panel(
            esc(dev.name),
            `<p class="sub">${esc(data.scope)}</p>` +
              table(
                ["应用", "市场", "版本", "状态", "最近成功"],
                data.items.map((r) => [
                  appLink(
                    r.store,
                    r.app_id,
                    r.payload?.name,
                    r.payload?.icon,
                    r.country,
                    r.language,
                  ),
                  esc(marketCaption(r)),
                  esc(r.payload?.version),
                  esc(stateNames[r.state] || r.state || "未知"),
                  stamp(r.last_success),
                ]),
              ),
          );
        } catch (e) {
          toast(e.message);
        }
      }
    });
  }

  const collectionCenter = createCollectionCenter(ctx);

  async function marketMatrix(store, appId, rev) {
    const data = await api(
      `/research/markets/${encodeURIComponent(store)}/${encodeURIComponent(appId)}`,
    );
    if (rev !== currentRevision() || !$("#app-market-matrix")) return;
    $("#app-market-matrix").innerHTML = panel(
      "地区与版本矩阵",
      `<p class="sub">${esc(data.scope)}</p>` +
        table(
          ["市场", "版本", "地区状态", "售价", "累计评分", "最后成功", "详情"],
          data.items.map((r) => [
            esc(marketCaption(r)),
            esc(r.payload?.version || "未知"),
            esc(stateNames[r.state] || r.state || "未知"),
            r.payload?.price == null
              ? "未知"
              : esc(r.payload.currency) + " " + num(r.payload.price),
            num(r.payload?.rating),
            stamp(r.last_success),
            appLink(
              r.store,
              r.app_id,
              "查看地区详情",
              null,
              r.country,
              r.language,
            ),
          ]),
        ),
    );
  }
  function planForApps(ids) {
    collectionCenter.prepare(ids);
  }
  function setForApps(ids) {
    dialog(
      "保存竞品集合",
      `<p>已选 ${ids.length} 个同市场应用，均按对照应用保存。</p><form id="bulk-set" class="toolbar">${field("集合名称", "name", "", "text", "required")}<button type="submit">保存集合</button></form>`,
    );
    bindForm("#bulk-set", async (v) => {
      await api("/research/comparisons", {
        name: v.name,
        members: ids.map((app_id) => ({
          ...market(),
          app_id,
          role: "competitor",
        })),
      });
      $("#research-dialog").close();
      toast("竞品集合已保存");
      location.hash = "compare";
    });
  }
  return {
    events,
    aso,
    reviews,
    competitors,
    developers,
    collectionCenter,
    marketMatrix,
    planForApps,
    setForApps,
  };
}
