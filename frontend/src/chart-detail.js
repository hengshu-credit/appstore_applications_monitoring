import { pagination, bindPagination } from "./pagination.js";
import { filterBar } from "./filter-bar.js";
export function createChartDetail(ctx) {
  const {
    api,
    appRows,
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
    const rows = await appRows(d.items, {
      scope,
      leadingColumns: [
        {
          key: "position",
          label: "名次",
          cell: (r) => num(r._source.position),
        },
        {
          key: "metadata_at",
          label: "榜单资料观察时间",
          cell: (r) =>
            clipped(
              r._source.metadata_observed_at
                ? stamp(r._source.metadata_observed_at)
                : "仅榜单资料",
            ),
        },
      ],
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
        `<div class="panel-heading"><div><h2>${esc(country)} · ${esc(name)}</h2><p class="sub">${esc(selectors.categoryName(scope.store, scope.category))} · ${esc(scope.language)} · ${esc(d.definition.mode === "derived" ? "本站派生榜" : "商店榜单")}</p></div><a class="button secondary" href="#globalcharts">返回全球排行榜</a></div>${filterBar(
          {
            id: "chart-detail-filter",
            values: scope,
            fields: [
              {
                name: "q",
                label: "搜索应用 / ID / 开发者",
                options: d.items
                  .flatMap((r) => [r.name, r.app_id, r.metadata?.developer])
                  .filter(Boolean),
              },
              {
                name: "limit",
                label: "每页应用",
                type: "select",
                options: [25, 50, 100].map((n) => [n, n + "个"]),
              },
            ],
            actions: d.target
              ? '<button type="button" id="chart-detail-refresh" class="secondary">安排榜单刷新</button>'
              : "",
          },
        )}${pagination({ id: "chart-detail-pages", offset: d.offset, limit: d.limit || scope.limit, total: d.total, hasMore: d.has_more, prevId: "chart-detail-prev", nextId: "chart-detail-next" })}<p class="sub">名次来自所选快照，应用指标展示当前市场明细。快照共 ${d.chart_count} 个应用，基榜返回 ${d.base_returned_count} 条。</p>${panel("榜单应用资料", `<div class="chart-app-details">${`<details><summary>榜单来源与口径</summary><p>${esc(d.latest_success?.source || "暂无来源")} · ${esc(d.definition.note)} ${esc(d.reason)} ${esc(d.metadata_note)} ${esc(d.metadata_warning)}</p></details>` + rows}</div>`)}<p class="sub">点击应用名称进入该市场的应用详情，可采集尚缺资料；这里只展示来源实际返回的榜单范围。</p>`,
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
    bindPagination(document, "chart-detail-pages", (offset) =>
      update({ offset }),
    );
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
