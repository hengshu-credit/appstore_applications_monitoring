import { filterBar, bindFilterBar } from "./filter-bar.js";
export function createAppInsights(ctx) {
  const {
    api,
    appRows,
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
  } = ctx;
  function discovery() {
    const host = document.querySelector("#store-discovery");
    if (!host) return;
    if (market().store === "all") {
      host.innerHTML = panel(
        "搜索商店并入库",
        '<p class="sub">当前同时展示两家商店的应用库。搜索官方商店或入库时，请先在顶部选择对应商店，以明确应用 ID 的来源。</p>',
      );
      return;
    }
    host.innerHTML = panel(
      "搜索商店并入库",
      `<p class="sub">在当前商店与地区查找应用。支持名称、App ID／包名、官方商店链接。</p>${filterBar({ id: "store-search-form", submit: "搜索商店", resetSubmit: false, fields: [{ name: "query", label: "搜索内容", required: true, maxlength: 1000, wide: true, placeholder: "应用名称 / App ID / 官方商店链接", source: "app" }] })}<div id="store-search-results" aria-live="polite"></div>`,
    );
    const form = host.querySelector("form"),
      result = host.querySelector("#store-search-results");
    form.addEventListener("filter-reset", () => result.replaceChildren());
    bindFilterBar(
      form,
      async (values) => {
        const m = { ...market() };
        result.textContent = "正在查询商店…";
        try {
          const r = await api("/discovery/search", {
            ...m,
            query: values.query,
          });
          if (!host.isConnected) return;
          result.innerHTML = `<p class="sub">${esc(r.scope)} · ${esc(r.source)} · ${stamp(r.observed_at)} · ${esc(m.country.toUpperCase())}/${esc(m.language)}</p>${await appRows(
            r.items.map((a, i) => ({ ...a, _resultIndex: i })),
            {
              scope: m,
              trailingColumns: [
                {
                  key: "library",
                  label: "应用库",
                  cell: (a) => (a._source.in_library ? "已入库" : "尚未入库"),
                },
                {
                  key: "import",
                  label: "操作",
                  cell: (a) =>
                    `<button type="button" class="secondary" data-import="${a._source._resultIndex}">${a._source.in_library ? "加入监控并采集" : "入库并采集"}</button>`,
                },
              ],
            },
          )}`;
          result.querySelectorAll("[data-import]").forEach((b) =>
            b.addEventListener("click", async () => {
              b.disabled = true;
              try {
                const a = r.items[Number(b.dataset.import)];
                const imported = await api("/discovery/import", {
                  ...m,
                  app_id: a.app_id,
                });
                if (!host.isConnected) return;
                toast("已入库并创建采集计划，进度可在详情查看");
                b.outerHTML = `<a href="#app/${encodeURIComponent(m.store)}/${encodeURIComponent(imported.app.app_id)}?country=${encodeURIComponent(m.country)}&language=${encodeURIComponent(m.language)}">查看应用详情与采集进度</a>`;
              } catch (error) {
                toast(error.message);
                b.disabled = false;
              }
            }),
          );
        } catch (error) {
          result.textContent = error.message;
        }
      },
      toast,
    );
  }
  function facts(p, base) {
    const date = (v) =>
      v == null ? "—" : typeof v === "number" ? stamp(v) : String(v);
    const rows = [
      ["App ID", base.app_id],
      ["Bundle ID", p.bundle_id],
      ["开发者", p.developer],
      ["供应商", p.seller],
      ["分类", base.category_name || p.category],
      ["当前版本", p.version],
      ["商店发布日期", date(p.released)],
      ["商店更新时间", date(p.updated)],
      ["价格", p.price == null ? "—" : num(p.price) + " " + (p.currency || "")],
      [
        "安装量下限",
        p.installs == null ? "—" : num(p.installs) + "+（累计公开下限）",
      ],
      [
        "大小",
        p.size_bytes
          ? num(Math.round(Number(p.size_bytes) / 10485.76) / 100) + " MB"
          : "—",
      ],
      ["最低系统", p.minimum_os],
      ["年龄分级", p.age_rating || p.content_rating],
      ["开发者 ID", p.developer_id],
      ["开发者邮箱", p.developer_email],
      ["开发者网站", p.developer_website || p.developer_url],
      ["隐私政策", p.privacy_url],
      ["内购范围", p.iap_range],
      ["支持语言", (p.languages || []).join(" / ")],
      ["首次入库", stamp(base.first_seen)],
    ];
    return panel(
      "应用档案",
      `${table(
        ["项目", "内容"],
        rows.map(([k, v]) => [esc(k), esc(v ?? "-")]),
      )}<p class="sub">商店日期与本系统观察日期分开记录；空值表示来源未返回。</p>`,
    );
  }
  async function monitoring(m) {
    const host = document.querySelector("#monitoring-trends");
    if (!host) return;
    host.innerHTML = panel(
      "监控趋势",
      `<label>观察区间<select id="monitoring-days"><option value="7">近7天</option><option value="30" selected>近30天</option><option value="90">近90天</option><option value="365">近365天</option></select></label><div id="monitoring-content" aria-live="polite"></div>`,
    );
    const content = host.querySelector("#monitoring-content"),
      days = host.querySelector("select");
    let generation = 0;
    async function load() {
      const ticket = ++generation;
      content.textContent = "正在汇总当前市场观察…";
      try {
        const r = await api(
          `/apps/${m.store}/${encodeURIComponent(m.app_id)}/metrics?` +
            qs({ country: m.country, language: m.language, days: days.value }),
        );
        if (!host.isConnected || ticket !== generation) return;
        const c = r.coverage,
          known = c.fresh > 0;
        content.innerHTML = `<div class="monitoring-summary"><div class="cards">${metric("有效覆盖关键词", known ? c.covered : null, `最新有效样本 ${c.fresh} / 已跟踪 ${c.tracked}`)}${metric("TOP 3 关键词", known ? c.top3 : null, "同市场有效样本")}${metric("TOP 10 关键词", known ? c.top10 : null, "同市场有效样本")}${metric("日下载量", null, "尚未接入授权或估算数据")}</div></div><p class="sub">${esc(r.scope)}${r.truncated ? " 已达到返回上限，请缩短区间或缩小词库。" : ""}</p><div class="grid2">${panel("榜单 / 关键词排名", `<label>趋势序列<select id="monitoring-series">${r.series.map((s, i) => `<option value="${i}">${esc((s.kind === "chart" ? "榜单" : "关键词") + " · " + s.label + (s.fresh ? "" : " · 数据待刷新"))}</option>`).join("")}</select></label><div id="monitoring-ranks"></div>`)}${panel("当日采样关键词覆盖（UTC）", chart(r.coverage_daily || []))}${panel("当前市场可用性（1可访问 / 0未查到）", chart(r.availability || []))}${panel("评分数量变化", chart(r.snapshots.map((s) => ({ at: s.observed_at, value: s.payload.ratings, note: "累计公开评分数 · 版本 " + (s.payload.version || "未知") }))))}${panel("已采集评论星级分布", bars(r.review_distribution))}${panel("每日新采集评论（UTC）", chart(r.review_daily.map((p) => ({ ...p, note: "按首次采集日期计数，不等于商店当天新增评论" }))))}${panel("下载量与安装量", `<p class="callout">${esc(r.downloads.reason)}</p>${chart(r.snapshots.filter((s) => s.payload.installs != null).map((s) => ({ at: s.observed_at, value: s.payload.installs, note: "Google 累计安装量下限，不是日下载量" })))}`)}${panel(
          "清榜 / 清词核查",
          `<p class="sub">要求应用近48小时在架、同一来源/解析器/覆盖范围，原位置仍在返回深度内，连续两次间隔至少1小时缺失。至少3词触发批量掉词提示。普通掉榜也可能满足，需人工核查。</p>${
            r.signals.length
              ? r.signals
                  .map(
                    (s) =>
                      `<div class="callout">${esc(s.title)}</div>${table(
                        ["目标", "原排名", "证据快照", "观察时间"],
                        s.evidence.map((e) => [
                          esc(e.label),
                          num(e.baseline_position),
                          esc(e.run_ids.join(" → ")),
                          stamp(e.observed_at),
                        ]),
                      )}`,
                  )
                  .join("")
              : "<p>当前没有满足规则的异常证据；数据不足不代表正常。</p>"
          }`,
        )}</div>`;
        const render = () => {
          const s =
            r.series[Number(content.querySelector("#monitoring-series").value)];
          content.querySelector("#monitoring-ranks").innerHTML = s
            ? chart(s.points, true)
            : '<p class="empty">暂无排名历史，请配置并运行采集。</p>';
        };
        render();
        content
          .querySelector("#monitoring-series")
          .addEventListener("change", render);
      } catch (error) {
        if (host.isConnected && ticket === generation)
          content.textContent = error.message;
      }
    }
    days.addEventListener("change", load);
    await load();
  }
  return { discovery, facts, monitoring };
}
