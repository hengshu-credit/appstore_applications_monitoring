export function createChartDetail(ctx) {
  const {
    api,
    esc,
    stamp,
    num,
    panel,
    table,
    field,
    select,
    bindForm,
    mount,
    market,
    selectors,
    appLink,
    $,
    toast,
  } = ctx;
  const query = (o) =>
    new URLSearchParams(
      Object.entries(o).filter(([, v]) => v !== "" && v != null),
    ).toString();
  const clipped = (text) =>
    `<span class="chart-detail-text" title="${esc(text ?? "")}">${esc(text ?? "—")}</span>`;
  return async function chartDetail(rev) {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const scope = {
      ...market(),
      collection: params.get("collection") || "top-free",
      category: params.get("category") || "",
      as_of: params.get("as_of") || null,
      q: params.get("q") || "",
      offset: Number(params.get("offset") || 0),
      limit: Number(params.get("limit") || 50),
    };
    const d = await api("/charts/detail?" + query(scope));
    const country = scope.country.toUpperCase(),
      name = d.definition.name;
    const update = (values) => {
      location.hash = "chart?" + query({ ...scope, ...values });
    };
    const rows = d.items.map((r) => {
      const p = r.metadata || {};
      const price =
        p.price == null || !Number.isFinite(Number(p.price))
          ? "—"
          : Number(p.price) === 0
            ? "免费"
            : `${num(p.price)} ${p.currency || ""}`;
      return [
        r.position,
        appLink(
          scope.store,
          r.app_id,
          r.name,
          r.icon,
          scope.country,
          scope.language,
        ),
        clipped(r.developer),
        clipped(
          p.category_id || p.category
            ? selectors.categoryName(scope.store, p.category_id || p.category)
            : "—",
        ),
        clipped(price),
        num(p.rating ?? r.rating),
        num(p.ratings),
        num(p.installs_reported),
        clipped(p.version),
        clipped(
          p.updated == null
            ? "—"
            : typeof p.updated === "number"
              ? stamp(p.updated)
              : p.updated,
        ),
        clipped(
          r.metadata_observed_at
            ? (r.metadata_origin === "chart" ? "榜单：" : "详情：") +
                stamp(r.metadata_observed_at)
            : "仅榜单资料",
        ),
      ];
    });
    const quality = {
      fresh: "有效观察",
      historical: "历史快照",
      paused: "采集已暂停",
      failed: "最近采集失败，保留成功快照",
      stale: "历史数据待更新",
      missing: "尚未采集",
      insufficient_history: "缺少可比较历史",
      partial: "部分资料缺失",
    };
    if (
      !mount(
        `<div class="panel-heading"><div><h2>${esc(country)} · ${esc(name)}</h2><p class="sub">${esc(selectors.categoryName(scope.store, scope.category))} · ${esc(scope.language)} · ${esc(d.definition.mode === "derived" ? "本站派生榜" : "商店榜单")}</p></div><a class="button secondary" href="#globalcharts">返回全球排行榜</a></div><form id="chart-detail-filter" class="toolbar">${field("搜索应用 / ID / 开发者", "q", scope.q)}${select(
          "每页应用",
          "limit",
          [
            [25, "25个"],
            [50, "50个"],
            [100, "100个"],
          ],
          scope.limit,
        )}<button type="submit">筛选</button>${d.target ? '<button type="button" id="chart-detail-refresh" class="secondary">安排榜单刷新</button>' : ""}</form><div class="toolbar"><button id="chart-detail-prev" class="secondary" ${d.offset ? "" : "disabled"}>上一页</button><span>当前 ${d.total ? Math.min(d.offset + 1, d.total) : 0}–${Math.min(d.offset + d.items.length, d.total)} / ${d.total} 个应用；该快照共 ${d.chart_count} 个，基榜返回 ${d.base_returned_count} 个</span><button id="chart-detail-next" class="secondary" ${d.has_more ? "" : "disabled"}>下一页</button></div>${panel("榜单应用资料", `<div class="chart-detail-table">${`<details><summary>榜单来源与口径</summary><p>${esc(d.latest_success?.source || "暂无来源")} · ${esc(d.definition.note)} ${esc(d.reason)} ${esc(d.metadata_note)} ${esc(d.metadata_warning)}</p></details>` + table(["名次", "应用", "开发者", "分类", "售价", "评分", "评分数", "公开累计安装数（全球）", "版本", "商店更新", "资料观察时间"], rows)}</div>`)}<p class="sub">点击应用名称进入该市场的应用详情，可采集尚缺资料；这里只展示来源实际返回的榜单范围。</p>`,
        rev,
        [
          quality[d.quality] || "尚未采集",
          d.latest_success?.observed_at && stamp(d.latest_success.observed_at),
        ]
          .filter(Boolean)
          .join(" · "),
      )
    )
      return;
    $("#page-title").textContent = "榜单详情";
    bindForm("#chart-detail-filter", async (v) =>
      update({ q: v.q, limit: Number(v.limit), offset: 0 }),
    );
    $("#chart-detail-prev").onclick = () =>
      update({ offset: Math.max(0, d.offset - d.limit) });
    $("#chart-detail-next").onclick = () =>
      update({ offset: d.offset + d.limit });
    const refresh = $("#chart-detail-refresh");
    if (refresh)
      refresh.onclick = async () => {
        refresh.disabled = true;
        try {
          await api(`/control/targets/${d.target.id}`, { action: "refresh" });
          toast("已安排采集；本页仍保留当前快照，可通过顶部刷新查看结果");
        } catch (e) {
          toast(e.message);
        } finally {
          refresh.disabled = false;
        }
      };
  };
}
