// Discovery views share the console's market controls and evidence semantics.
export function csvText(headers, rows) {
  const cell = (v) => {
    let text = String(v ?? "");
    if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return (
    "\ufeff" +
    [headers, ...rows].map((row) => row.map(cell).join(",")).join("\r\n")
  );
}

export function createDiscoveryPages(ctx) {
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
    nextRevision,
    currentRevision,
  } = ctx;
  const query = (o) =>
    new URLSearchParams(
      Object.entries(o).filter(([, v]) => v !== "" && v != null),
    ).toString();
  const single = () => {
    const m = market();
    if (m.store === "all") {
      mount(
        '<p class="callout">请选择 Google Play 或 App Store，分别查看市场榜单与关键词。</p>',
      );
      return null;
    }
    return m;
  };
  const act = (selector, fn) => {
    const el = $(selector);
    if (el)
      el.addEventListener("click", async () => {
        el.disabled = true;
        try {
          await fn();
        } catch (e) {
          toast(e.message);
        } finally {
          el.disabled = false;
        }
      });
  };
  const download = (name, headers, rows) => {
    const url = URL.createObjectURL(
      new Blob([csvText(headers, rows)], { type: "text/csv;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const selection = (rows, key) =>
    rows.filter(
      (r) =>
        document.querySelector(
          `input[data-pick="${key}"][value="${CSS.escape(String(r[key]))}"]`,
        )?.checked,
    );
  const pick = (key, value) =>
    `<input type="checkbox" data-pick="${key}" value="${esc(value)}" aria-label="选择 ${esc(value)}">`;
  let chartsState = {
    mode: "store",
    collection: "top-free",
    category: "",
    days: "7",
    as_of: "",
    q: "",
    metric: "ratings",
    pricing: "",
    monetization: "",
    new_days: "0",
  };
  let chartStore = "",
    keywordTab = "coverage",
    candidateOffset = 0,
    reportId = "",
    reportSheet = "ASO关键词",
    reportOffset = 0,
    keywordQuery = "",
    appQuery = "",
    signalId = "";

  async function charts(rev) {
    const m = single();
    if (!m) return;
    if (chartStore !== m.store) {
      chartsState.category = "";
      chartStore = m.store;
    }
    const s = chartsState,
      definitions = selectors.chartDefinitions(m.store);
    if (s.as_of && s.as_of > new Date().toLocaleDateString("sv-SE"))
      throw Error("观察日期不能晚于今天");
    const at = s.as_of
      ? Math.min(Date.parse(s.as_of + "T23:59:59") / 1000, Date.now() / 1000)
      : null;
    const d = await api(
      s.mode === "store"
        ? "/charts/view?" +
            query({
              ...m,
              collection: s.collection,
              category: s.category,
              as_of: at,
              compare_days: Number(s.days),
            })
        : "/discovery/growth?" +
            query({
              ...m,
              days: Number(s.days),
              as_of: at,
              metric: s.metric,
              category: s.category,
              q: s.q,
              pricing: s.pricing,
              monetization: s.monetization,
              new_days: Number(s.new_days),
            }),
    );
    const rows =
      s.mode === "store"
        ? (d.items || []).filter(
            (r) =>
              !s.q ||
              (r.name || r.app_id).toLowerCase().includes(s.q.toLowerCase()),
          )
        : d.items || [];
    const status = {
      entered_range: "新进入已观察范围",
      unknown_depth: "原快照深度不足",
      observed: "可比较",
      missing_baseline: "历史基线不足",
      unavailable: "字段未提供",
      counter_correction: "累计数回调",
      incompatible: "来源口径变化",
    };
    const headers =
      s.mode === "store"
        ? ["选择", "位置", "应用", "开发者", "基榜位置", "名次升幅", "入榜状态"]
        : [
            "选择",
            "应用",
            "分类",
            "公开累计安装数（全球）",
            "累计评分数",
            "窗口增量",
            "环比前窗口",
            "基线观察时间",
            "实际跨度（天）",
            "状态",
            "观察时间",
          ];
    const cells = rows.map((r) =>
      s.mode === "store"
        ? [
            pick("app_id", r.app_id),
            r.position,
            appLink(m.store, r.app_id, r.name, r.icon),
            esc(r.developer || "—"),
            r.base_position,
            r.change == null
              ? "—"
              : (r.change > 0 ? "↑ " : r.change < 0 ? "↓ " : "") +
                Math.abs(r.change),
            esc(status[r.entry_status] || "—"),
          ]
        : [
            pick("app_id", r.app_id),
            appLink(m.store, r.app_id, r.name),
            esc(selectors.categoryName(m.store, r.category)),
            num(r.installs_reported),
            num(r.ratings),
            num(r.delta),
            r.growth_pct == null ? "—" : num(r.growth_pct) + "%",
            stamp(r.baseline_at),
            num(r.actual_days),
            esc(status[r.status]),
            stamp(r.observed_at),
          ],
    );
    const notes =
      s.mode === "store"
        ? `${d.definition.mode === "derived" ? "本站派生：" : "商店直接榜单："}${d.definition.note || ""}。${{ missing: "尚未采集", paused: "采集已暂停", failed: "最近采集失败，显示保留快照", stale: "快照已过期", insufficient_history: "缺少可比较历史", partial: "部分应用信息缺失", fresh: "有效快照", historical: "历史快照" }[d.quality] || ""}。当前快照：${stamp(d.latest_success?.observed_at)}；比较快照：${stamp(d.previous_success?.observed_at)}。${d.comparison_available ? "" : "暂无可比较快照。"}${d.reason || ""}`
        : d.reason;
    if (
      !mount(
        `<form id="chart-view-form" class="toolbar">${select(
          "视图",
          "mode",
          [
            ["store", "商店榜单"],
            ["growth", "增长与新应用"],
          ],
          s.mode,
        )}${select(
          "榜单",
          "collection",
          definitions.map((d) => [d.id, d.name]),
          s.collection,
        )}${selectors.categoryField(m.store, s.category, "discovery-category", "chart")}${field("观察日期（本地时区）", "as_of", s.as_of, "date")}${select(
          "比较窗口",
          "days",
          [
            [1, "1天"],
            [7, "7天"],
            [14, "14天"],
            [30, "30天"],
          ],
          s.days,
        )}${field("搜索应用", "q", s.q)}<button type="submit">查看</button><a class="button secondary" href="#collection">配置采集</a><details class="advanced-filters" ${s.mode === "growth" ? "open" : ""}><summary>增长指标与筛选</summary><div class="toolbar">${select(
          "指标",
          "metric",
          [
            ["ratings", "评分数净增长"],
            ["installs_reported", "Android公开安装数变化"],
          ],
          s.metric,
        )}${select(
          "价格",
          "pricing",
          [
            ["", "全部"],
            ["free", "免费"],
            ["paid", "付费"],
          ],
          s.pricing,
        )}${select(
          "商业化",
          "monetization",
          [
            ["", "全部"],
            ["ads", "广告"],
            ["iap", "内购"],
            ["both", "广告和内购"],
          ],
          s.monetization,
        )}${select(
          "发布于近",
          "new_days",
          [
            [0, "不限"],
            [7, "7天"],
            [30, "30天"],
            [90, "90天"],
          ],
          s.new_days,
        )}</div></details></form><p class="callout">${esc(notes)}</p><div class="toolbar"><button id="discovery-select" class="secondary">选择前50项</button><button id="discovery-watch" class="secondary">加入重点监控</button><button id="discovery-csv" class="secondary">导出当前结果</button>${d.target ? '<button id="discovery-refresh" class="secondary">立即采集基榜</button>' : ""}<details><summary>显示列</summary>${headers.map((h, i) => `<label><input type="checkbox" data-show-column="${i}" checked>${esc(h)}</label>`).join("")}</details><span class="sub">显示 ${rows.length} 条${d.total != null ? ` / ${d.total} 条` : ""}；增长列表最多200条。未采集历史不补零。</span></div>${panel(s.mode === "store" ? esc(d.definition.name) : "增长发现", '<div id="discovery-results">' + table(headers, cells) + "</div>")}${
          s.mode === "store"
            ? panel(
                "离开已观察榜单范围",
                table(
                  ["应用", "原位置"],
                  (d.exited_items || []).map((r) => [
                    appLink(m.store, r.app_id, r.app_id),
                    r.previous_position,
                  ]),
                ),
              )
            : ""
        }`,
        rev,
      )
    )
      return;
    bindForm("#chart-view-form", async (v) => {
      chartsState = { ...s, ...v, category: $("#discovery-category").value };
      if (rev === currentRevision()) await charts(nextRevision());
    });
    $('#chart-view-form [name="collection"]').addEventListener("change", () =>
      $("#chart-view-form").requestSubmit(),
    );
    document.querySelectorAll("[data-show-column]").forEach((el) =>
      el.addEventListener("change", () => {
        document
          .querySelectorAll(
            `#discovery-results tr > :nth-child(${Number(el.dataset.showColumn) + 1})`,
          )
          .forEach((cell) => (cell.hidden = !el.checked));
      }),
    );
    act("#discovery-refresh", async () => {
      await api(`/control/targets/${d.target.id}`, { action: "refresh" });
      toast("已安排采集基榜");
    });
    act("#discovery-select", () => {
      document
        .querySelectorAll('[data-pick="app_id"]')
        .forEach((el, i) => (el.checked = i < 50));
    });
    act("#discovery-watch", async () => {
      const selected = selection(rows, "app_id");
      if (!selected.length || selected.length > 50)
        throw Error("请选择1–50个应用");
      let done = 0;
      for (const r of selected) {
        await api("/watchlist", {
          ...m,
          app_id: r.app_id,
          group_name: "榜单发现",
          refresh_hours: 24,
        });
        done++;
      }
      toast(`已加入监控 ${done} 个应用`);
    });
    act("#discovery-csv", () =>
      download(
        "衡枢·观星榜单.csv",
        [
          "商店",
          "国家",
          "语言",
          "应用ID",
          "名称",
          "排名",
          "名次变化",
          "指标",
          "增量",
          "增长率",
          "基线观察时间",
          "实际跨度（天）",
          "观察时间",
          "口径",
        ],
        rows.map((r) => [
          m.store,
          m.country,
          m.language,
          r.app_id,
          r.name,
          r.position,
          r.change,
          s.mode === "growth" ? s.metric : s.collection,
          r.delta,
          r.growth_pct,
          r.baseline_at,
          r.actual_days,
          r.observed_at || d.latest_success?.observed_at,
          notes,
        ]),
      ),
    );
  }

  let keywordMarket = "";
  let coverageOffsets = { apps: 0, keywords: 0, app_keywords: 0 };
  const pager = (kind, page, rows) => {
    const p = page || {
      total: rows.length,
      offset: 0,
      limit: 100,
      has_more: false,
    };
    return `<div class="toolbar"><button type="button" class="secondary" data-coverage-page="${kind}" data-offset="${Math.max(0, p.offset - p.limit)}" ${p.offset ? "" : "disabled"}>上一页</button><span>共 ${p.total} 条 · 每页 ${p.limit} 条 · 当前 ${p.total ? Math.min(p.offset + 1, p.total) : 0}–${Math.min(p.offset + rows.length, p.total)}</span><button type="button" class="secondary" data-coverage-page="${kind}" data-offset="${p.offset + p.limit}" ${p.has_more ? "" : "disabled"}>下一页</button></div>`;
  };
  async function keywords(rev) {
    const m = single();
    if (!m) return;
    const signature = [m.store, m.country, m.language].join("/");
    if (signature !== keywordMarket) {
      if (keywordMarket && keywordMarket.split("/")[0] !== m.store)
        appQuery = "";
      keywordMarket = signature;
      candidateOffset = 0;
      reportOffset = 0;
      reportId = "";
      signalId = "";
      coverageOffsets = { apps: 0, keywords: 0, app_keywords: 0 };
    }
    let content = "",
      data;
    if (keywordTab === "coverage") {
      data = await api(
        "/discovery/keywords/coverage?" +
          query({
            ...m,
            app_id: appQuery,
            q: keywordQuery,
            app_offset: coverageOffsets.apps,
            keyword_offset: coverageOffsets.keywords,
            app_keyword_offset: coverageOffsets.app_keywords,
          }),
      );
      content = `<div class="cards">${metric("跟踪词数", data.stats.tracked, "本市场词库规模")}${metric("有效词数", data.stats.fresh, "最新成功、未过期、深度一致")}</div><p class="callout">${esc(data.note)}</p><form id="coverage-app-form" class="toolbar">${field("查看应用ID的关键词", "app_id", appQuery)}${field("筛选词表关键词", "q", keywordQuery)}<button type="submit">查看</button></form>`;
      if (appQuery)
        content += panel(
          "应用关键词",
          table(
            ["关键词", "位置", "有效", "来源", "时间"],
            data.app_keywords.map((r) => [
              `<button class="link" data-keyword="${r.id}">${esc(r.keyword)}</button>`,
              r.position,
              r.fresh ? "有效" : "待更新",
              esc(r.source),
              stamp(r.observed_at),
            ]),
          ) +
            pager("app_keywords", data.pages?.app_keywords, data.app_keywords),
        );
      content += panel(
        "应用关键词覆盖榜（当前词库）",
        table(
          ["应用", "覆盖词数", "Top 3", "Top 10", "Top 50", "Top 100"],
          data.apps.map((r) => [
            appLink(m.store, r.app_id, r.name),
            r.covered,
            r.top3,
            r.top10,
            r.top50,
            r.top100,
          ]),
        ) + pager("apps", data.pages?.apps, data.apps),
      );
      content += panel(
        "市场关键词搜索排名",
        table(
          ["关键词", "返回结果数", "采集深度", "数据状态", "时间"],
          data.keywords.map((r) => [
            `<button class="link" data-keyword="${r.id}">${esc(r.keyword)}</button>`,
            r.returned_count,
            r.depth,
            r.fresh ? "有效" : esc(r.last_error || "等待更新"),
            stamp(r.observed_at),
          ]),
        ) + pager("keywords", data.pages?.keywords, data.keywords),
      );
    } else if (keywordTab === "candidates") {
      data = await api(
        "/discovery/keywords/candidates?" +
          query({ ...m, q: keywordQuery, offset: candidateOffset }),
      );
      const sources = await api("/discovery/keywords/sources");
      content = `<p class="callout">${esc(sources.note)}</p><details><summary>导入词库</summary><form id="manual-words" class="toolbar"><label class="wide">关键词（每行一个）<textarea name="words" rows="4" required></textarea></label>${field("目标国家（逗号分隔，同语言）", "countries", m.country)}${field("来源名称", "source", "手工词库")}${field("来源网址（可选）", "source_url", "")}${field("来源期间", "source_period", new Date().toLocaleDateString("sv-SE"))}<button type="submit">导入候选词</button></form><form id="frequency-words" class="toolbar">${select(
        "开源词库语言",
        "language",
        sources.frequency_languages.map((v) => [v, v]),
        m.language,
      )}${field("目标国家（逗号分隔）", "countries", m.country)}${field("词数上限", "limit", 500, "number", 'min="1" max="5000"')}<button type="submit">下载 FrequencyWords 候选词</button></form><p class="sub">OpenSubtitles 2018，CC BY-SA 4.0。语料词频不代表搜索量；导入后先筛选，未自动采集。</p></details><form id="candidate-search" class="toolbar">${field("筛选词", "q", keywordQuery)}<button type="submit">筛选</button></form><div class="toolbar"><button id="candidate-all" class="secondary">选择本页</button><button id="candidate-track">追踪所选词</button><span>共 ${data.total} 个词、${data.source_records ?? data.total} 条来源记录，每页100词</span><button id="candidate-prev" class="secondary" ${candidateOffset ? "" : "disabled"}>上一页</button><button id="candidate-next" class="secondary" ${candidateOffset + 100 < data.total ? "" : "disabled"}>下一页</button></div>${table(
        ["选择", "关键词", "语料词频", "来源", "期间", "许可", "追踪状态"],
        data.items.map((r) => [
          pick("id", r.id),
          esc(r.keyword),
          num(r.corpus_frequency),
          `<button type="button" class="link" data-candidate-sources="${r.id}">${esc(r.source)}${r.source_count > 1 ? ` 等 ${r.source_count} 个来源` : ""}</button>`,
          r.report_observed_at
            ? stamp(r.report_observed_at)
            : esc(r.source_period),
          esc(r.license_name),
          r.tracked_id ? (r.tracked_enabled ? "已追踪" : "已暂停") : "未追踪",
        ]),
      )}`;
    } else if (keywordTab === "aso-library") {
      data = await api(
        "/discovery/keywords/aso-library?" +
          query({ ...m, q: keywordQuery, offset: candidateOffset }),
      );
      data.items = data.items.map((r) => ({ ...r, id: r.candidate_id }));
      content = `<div class="cards">${metric("ASO自然搜索词", data.total, "本市场去重词数")}${metric("报告来源", data.counts.reports, `${data.counts.apps} 个应用`)}</div><p class="callout">${esc(data.note)}</p><form id="candidate-search" class="toolbar">${field("筛选词", "q", keywordQuery)}<button type="submit">筛选</button></form><div class="toolbar"><button id="candidate-all" class="secondary">选择本页</button><button id="candidate-track">追踪所选词</button><button id="candidate-prev" class="secondary" ${candidateOffset ? "" : "disabled"}>上一页</button><button id="candidate-next" class="secondary" ${candidateOffset + 100 < data.total ? "" : "disabled"}>下一页</button><span>共 ${data.total} 词，每页100词</span></div>${table(
        [
          "选择",
          "关键词",
          "最佳报告排名",
          "来源应用数",
          "来源报告数",
          "最近报告日期",
          "来源",
        ],
        data.items.map((r) => [
          r.id ? pick("id", r.id) : "待加入候选库",
          esc(r.keyword),
          r.best_report_position,
          r.apps,
          r.reports,
          stamp(r.latest_report_at),
          r.id
            ? `<button type="button" class="link" data-candidate-sources="${r.id}">查看来源</button>`
            : "—",
        ]),
      )}`;
    } else if (keywordTab === "reports") {
      const reports = await api("/discovery/reports?" + query(m));
      const totals = await api("/discovery/report-summary?" + query(m));
      if (!reports.some((r) => String(r.id) === reportId))
        reportId = reports.length ? String(reports[0].id) : "";
      data = reportId
        ? await api(
            `/discovery/reports/${reportId}${reportSheet === "__rejections__" ? "/rejections" : ""}?` +
              query({
                sheet: reportSheet,
                offset: reportOffset,
                q: keywordQuery,
              }),
          )
        : { items: [], total: 0 };
      const report = reports.find((r) => String(r.id) === reportId);
      content = `<div class="cards">${metric("已导入报告", totals.counts.reports, "当前商店、市场、追踪语言")}${metric("关键词记录", totals.counts.records, "保留应用、词表和日期")}${metric("去重词数", totals.counts.unique_keywords, "NFKC与空白规范化")}${metric("多应用重合词", totals.shared_between_apps, "不同应用报告共享的关键词")}</div><p class="sub">${esc(totals.note)} ASO/竞价词表间重合 ${totals.shared_between_types} 词。另有 ${totals.rejected_rows || 0} 行不可追踪字符，原文已单独留存，不计入词库。</p><details><summary>导入应用报告（xlsx）</summary><form id="report-upload" class="toolbar"><label>报告文件（最多3.5MB）<input name="file" type="file" accept=".xlsx" required></label>${field("应用商店ID", "app_id", "", "text", "required")}<button type="submit">预览报告</button></form><div id="report-preview"></div></details><p class="callout">第三方报告与衡枢·观星实采排名分别保留。ASO自然搜索词、ASA竞价词独立展示；报告日期不等于每个关键词的更新时间。</p><form id="report-filter" class="toolbar">${select(
        "已导入报告",
        "report_id",
        reports.map((r) => [r.id, r.app_id + " / " + r.filename]),
        reportId,
      )}${select(
        "词表",
        "sheet",
        [
          ["ASO关键词", "ASO关键词"],
          ["ASA竞价词", "ASA竞价词"],
          ["谷歌付费关键词", "谷歌付费关键词"],
          ["__rejections__", "不可追踪记录（原文）"],
        ],
        reportSheet,
      )}${field("筛选词", "q", keywordQuery)}<button type="submit">查看</button></form>${report ? `<p class="sub">来源：${esc(report.source)} · 关键词报告日期：${stamp(report.observed_at)} · 原文件包含 ${report.summary.map((s) => esc(s.sheet) + " " + s.rows + "行").join("；")}</p>` : ""}<div class="toolbar"><button id="report-all" class="secondary">选择本页</button><button id="report-promote" ${reportSheet === "__rejections__" ? "disabled" : ""}>所选词加入候选词库</button><button id="report-prev" class="secondary" ${reportOffset ? "" : "disabled"}>上一页</button><button id="report-next" class="secondary" ${reportOffset + 100 < data.total ? "" : "disabled"}>下一页</button><span>${data.total} 行，每页100行</span></div>${table(
        ["选择", "关键词", "报告排名", "搜索指数", "流行度", "原始信息"],
        data.items.map((r) => [
          pick("source_row", r.source_row),
          esc(r.keyword),
          r.position,
          num(r.search_index),
          num(r.popularity),
          `<details><summary>详细字段</summary>${r.reason ? `<p>${esc(r.reason)}</p>` : ""}${table(
            ["字段", "值"],
            r.payload.headers.map((h, i) => [
              esc(h || "未命名列"),
              esc(r.payload.values[i]),
            ]),
          )}</details>`,
        ]),
      )}`;
    } else if (keywordTab === "hot") {
      data = await api(
        "/discovery/keywords/signals?" +
          query({ ...m, run_id: signalId || null }),
      );
      const provider = await api("/discovery/keywords/apple-popularity");
      const official =
        m.store === "apple"
          ? `<section class="panel"><h2>Apple官方周/月搜索热度</h2><p class="sub">${esc(provider.note)} 授权配置：${provider.configured ? "已配置，查询时验证权限" : "待配置"}</p><form id="apple-popularity-form" class="toolbar">${select(
              "统计分类",
              "genre",
              provider.genres.map((g) => [
                g,
                {
                  BUSINESS: "商务",
                  EDUCATION: "教育",
                  ENTERTAINMENT: "娱乐",
                  FINANCE: "财务",
                  FOOD_DRINK: "美食佳饮",
                  GAMES: "游戏",
                  HEALTH_FITNESS: "健康健身",
                  LIFESTYLE: "生活",
                  NEW_PUBLICATION: "新闻出版",
                  PHOTO_VIDEO: "摄影与视频",
                  PRODUCTIVITY_UTILITIES: "效率与工具",
                  SHOPPING: "购物",
                  SOCIAL_NETWORKING: "社交",
                  SPORTS: "体育",
                  TRAVEL: "旅行",
                }[g] || g,
              ]),
              "PRODUCTIVITY_UTILITIES",
            )}${select(
              "周期",
              "period",
              [
                ["weekly", "周（日–六）"],
                ["monthly", "自然月"],
              ],
              "weekly",
            )}${field("周期开始（UTC）", "start", provider.default_start, "date")}${field("最多条数", "limit", 200, "number", 'min="1" max="2000"')}<button type="submit" ${provider.configured ? "" : "disabled"}>查询并保存热度</button></form><details><summary>导入已取得的Apple API响应</summary><form id="apple-popularity-import"><label>响应JSON（result.rows）<textarea name="response" rows="4" required></textarea></label><button type="submit">按上方统计范围导入</button></form></details><p id="apple-popularity-result" role="status"></p></section>`
          : "";
      content =
        official +
        `<p class="callout">${esc(data.note)} 外部报告中的搜索指数属于单应用词表指标，不自动转为全市场热搜榜。</p><form id="signal-select" class="toolbar">${select(
          "来源快照",
          "run_id",
          data.runs.map((r) => [r.id, r.source + " / " + stamp(r.observed_at)]),
          data.selected?.id,
        )}<button type="submit">查看</button></form>${data.selected ? `<p>${esc(data.selected.source)} · ${esc(data.selected.score_unit)} · ${stamp(data.selected.observed_at)}</p><button id="signal-promote" class="secondary">本快照关键词加入候选库</button>` : ""}${table(
          ["来源排名", "关键词", "来源分数"],
          data.items.map((r) => [r.position, esc(r.keyword), num(r.score)]),
        )}<details><summary>导入有来源的热搜或搜索热度快照</summary><form id="signal-import"><label>JSON：source、source_url、kind、observed_at、score_unit、items（keyword、score）<textarea name="json" rows="6" required></textarea></label><button type="submit">导入快照</button></form></details>`;
    } else {
      data = await api(
        "/discovery/keywords/markets?" +
          query({ store: m.store, app_id: appQuery }),
      );
      content = `<p class="callout">${esc(data.note)}</p><form id="coverage-app-form" class="toolbar">${field("应用ID（可选）", "app_id", appQuery)}<button type="submit">查看市场覆盖</button></form>${table(
        ["国家", "语言", "跟踪词", "有效词", "应用覆盖词", "应用Top10"],
        data.items.map((r) => [
          esc(r.country),
          esc(r.language),
          r.tracked,
          r.fresh,
          appQuery ? r.covered : "—",
          appQuery ? r.top10 : "—",
        ]),
      )}<details><summary>各国家默认请求语言建议</summary>${table(
        ["国家", "默认语言"],
        Object.entries(data.recommended_languages).map(([c, v]) => [
          esc(c),
          esc(v.default || "en"),
        ]),
      )}</details>`;
    }
    if (
      !mount(
        `<div class="toolbar">${[
          ["coverage", "覆盖与排名"],
          ["candidates", "候选词库"],
          ["aso-library", "ASO词库"],
          ["reports", "应用报告"],
          ["hot", "热搜来源"],
          ["markets", "市场覆盖"],
        ]
          .map(
            ([v, label]) =>
              `<button type="button" data-keyword-tab="${v}" class="${v === keywordTab ? "" : "secondary"}">${label}</button>`,
          )
          .join(
            "",
          )}<a class="button secondary" href="#collection?new=keywords">采集中心</a></div>${content}`,
        rev,
      )
    )
      return;
    document.querySelectorAll("[data-keyword-tab]").forEach((el) =>
      el.addEventListener("click", () => {
        keywordTab = el.dataset.keywordTab;
        candidateOffset = 0;
        keywords(nextRevision()).catch((e) => toast(e.message));
      }),
    );
    bindForm("#coverage-app-form", async (v) => {
      appQuery = v.app_id;
      keywordQuery = v.q ?? keywordQuery;
      coverageOffsets = { apps: 0, keywords: 0, app_keywords: 0 };
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    bindForm("#manual-words", async (v) => {
      await api("/discovery/keywords/candidates", {
        ...v,
        words: v.words.split(/\r?\n/).filter(Boolean),
        markets: v.countries
          .split(",")
          .map((country) => ({ ...m, country: country.trim() })),
      });
      toast("候选词已入库");
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    bindForm("#frequency-words", async (v) => {
      await api("/discovery/keywords/frequency", {
        language: v.language,
        limit: Number(v.limit),
        markets: v.countries.split(",").map((country) => ({
          ...m,
          country: country.trim(),
          language: v.language,
        })),
      });
      toast("开源候选词已入库");
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    bindForm("#candidate-search", async (v) => {
      keywordQuery = v.q;
      candidateOffset = 0;
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    document.querySelectorAll("[data-coverage-page]").forEach((button) =>
      button.addEventListener("click", () => {
        coverageOffsets[button.dataset.coveragePage] = Number(
          button.dataset.offset,
        );
        keywords(nextRevision()).catch((e) => toast(e.message));
      }),
    );
    document.querySelectorAll("[data-candidate-sources]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          const r = await api(
            `/discovery/keywords/candidates/${button.dataset.candidateSources}/sources`,
          );
          if (rev !== currentRevision()) return;
          let dialog = $("#candidate-sources-dialog");
          if (!dialog) {
            dialog = document.createElement("dialog");
            dialog.id = "candidate-sources-dialog";
            document.querySelector("#content").append(dialog);
          }
          dialog.innerHTML = `<h2>${esc(r.keyword)} 的来源</h2><p class="sub">最多显示${r.limit}条来源；报告日期与追踪语言分别保留。</p>${table(
            ["来源", "应用ID / 署名", "日期 / 语料期", "许可"],
            r.items.map((row) => [
              esc(row.source),
              esc(row.source_app_id || row.attribution),
              row.report_observed_at
                ? stamp(row.report_observed_at)
                : esc(row.source_period),
              esc(row.license_name),
            ]),
          )}<button id="candidate-sources-close">关闭</button>`;
          $("#candidate-sources-close").onclick = () => dialog.close();
          dialog.showModal();
        } catch (e) {
          toast(e.message);
        }
      }),
    );
    act("#candidate-all", () =>
      document
        .querySelectorAll('[data-pick="id"]')
        .forEach((el) => (el.checked = true)),
    );
    act("#candidate-track", async () => {
      const r = await api("/discovery/keywords/track", {
        ids: selection(data.items, "id").map((r) => r.id),
      });
      toast(`新增追踪 ${r.targets} 个，已有 ${r.existing} 个（保留原配置）`);
    });
    act("#candidate-prev", async () => {
      candidateOffset = Math.max(0, candidateOffset - 100);
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    act("#candidate-next", async () => {
      candidateOffset += 100;
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    bindForm("#report-filter", async (v) => {
      reportId = v.report_id;
      reportSheet = v.sheet;
      keywordQuery = v.q;
      reportOffset = 0;
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    act("#report-all", () =>
      document
        .querySelectorAll('[data-pick="source_row"]')
        .forEach((el) => (el.checked = true)),
    );
    act("#report-promote", async () => {
      await api("/discovery/reports/promote", {
        report_id: Number(reportId),
        sheet: reportSheet,
        source_rows: selection(data.items, "source_row").map(
          (r) => r.source_row,
        ),
      });
      toast("已加入候选词库，可在候选词库选择追踪");
    });
    act("#report-prev", async () => {
      reportOffset = Math.max(0, reportOffset - 100);
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    act("#report-next", async () => {
      reportOffset += 100;
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    bindForm("#report-upload", async (v, form) => {
      const file = form.querySelector('[type="file"]').files[0];
      if (!file || file.size > 3500000)
        throw Error("请选择不超过3.5MB的xlsx报告");
      const content = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(Error("读取文件失败"));
        reader.readAsDataURL(file);
      });
      const payload = {
        ...m,
        app_id: v.app_id,
        filename: file.name,
        content_base64: content,
      };
      if (rev !== currentRevision()) return;
      const preview = await api("/discovery/reports/preview", payload);
      if (rev !== currentRevision() || !$("#report-preview")) return;
      $("#report-preview").innerHTML = panel(
        "导入预览",
        `<p>${esc(preview.note)}</p><p>识别市场：${esc(preview.inferred.country || "未识别")}，商店：${esc(preview.inferred.store || "未识别")}；将导入到 ${esc(m.country)} / ${esc(m.language)}，应用 ${esc(v.app_id)}</p>${table(
          ["工作表", "数据行"],
          preview.summary.map((r) => [esc(r.sheet), r.rows]),
        )}<button id="report-confirm" type="button">确认导入 ${preview.keyword_rows} 行关键词</button>`,
      );
      act("#report-confirm", async () => {
        const result = await api("/discovery/reports/import", payload);
        reportId = String(result.id);
        reportOffset = 0;
        toast(result.duplicate ? "此报告已导入" : "报告已导入");
        if (rev === currentRevision()) await keywords(nextRevision());
      });
    });
    const popularitySpec = () => {
      const f = $("#apple-popularity-form");
      return {
        ...m,
        genre: f.elements.genre.value,
        period: f.elements.period.value,
        start: f.elements.start.value,
        limit: Number(f.elements.limit.value),
      };
    };
    const popularityDone = async (result) => {
      signalId = String(result.id);
      toast(
        `已保存 ${result.returned} 条${result.possibly_truncated ? "，达到查询上限，非完整榜单" : ""}`,
      );
      if (rev === currentRevision()) await keywords(nextRevision());
    };
    bindForm("#apple-popularity-form", async () =>
      popularityDone(
        await api("/discovery/keywords/apple-popularity", popularitySpec()),
      ),
    );
    bindForm("#apple-popularity-import", async (v) =>
      popularityDone(
        await api("/discovery/keywords/apple-popularity/import", {
          ...popularitySpec(),
          response: JSON.parse(v.response),
        }),
      ),
    );
    act("#signal-promote", async () => {
      await api(
        `/discovery/keywords/signals/${data.selected.id}/candidates`,
        {},
      );
      toast("来源词已加入候选库，尚未启动追踪");
    });
    bindForm("#signal-select", async (v) => {
      signalId = v.run_id;
      if (rev === currentRevision()) await keywords(nextRevision());
    });
    bindForm("#signal-import", async (v) => {
      await api("/discovery/keywords/signals", { ...JSON.parse(v.json), ...m });
      toast("来源快照已保存");
      signalId = "";
      if (rev === currentRevision()) await keywords(nextRevision());
    });
  }
  return { charts, keywords };
}
