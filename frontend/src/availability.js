import { listingStates } from "./app-detail-table.js";
import { pagination, bindPagination } from "./pagination.js";
import { filterBar, bindFilterBar } from "./filter-bar.js";
export function createAvailabilityPage(ctx) {
  const {
    api,
    appRows,
    market,
    qs,
    mount,
    panel,
    metric,
    table,
    chart,
    bars,
    esc,
    stamp,
    num,
    appLink,
    toast,
    nextRevision,
  } = ctx;
  const names = {
    first_verified: "首次验证",
    reappeared: "重新出现",
    region_unavailable: listingStates.region_unavailable,
  };
  let watchedFilters = { store: "all", country: "", group_name: "", q: "" },
    watchedOffset = 0,
    watchedRevision = 0;
  async function bindWatched() {
    const revision = ++watchedRevision;
    const anchor = document.querySelector("#availability-watched");
    const data = await api(
      "/availability/watched?" +
        qs({ ...watchedFilters, offset: watchedOffset, limit: 100 }),
    );
    if (!anchor?.isConnected || revision !== watchedRevision) return;
    anchor.innerHTML = `<p class="sub">名单跟随顶部商店与国家，包含该国全部请求语言。未知表示查询失败或暂无观察依据。跨国分类看板请进入<a href="#groups">应用分组</a>。</p>
      ${filterBar({
        id: "watched-filter", appScope: "country",
        values: watchedFilters,
        fields: [
          {
            name: "group_name",
            label: "分类标记",
            type: "panel",
            options: [["", "全部分类"], ...data.groups],
          },
          {
            name: "q",
            label: "应用名称 / ID",
            source: "app",
            wide: true,
            options: data.items.map((r) => [r.app_id, r.name || r.app_id]),
          },
        ],
      })}
      <p>共 ${num(data.total)} 条国家观察记录 · 当前 ${data.total ? data.offset + 1 : 0}–${data.offset + data.items.length}</p>
      ${await appRows(data.items, {
        leadingColumns: [
          {
            key: "watch_group",
            label: "分类标记",
            cell: (r) => esc(r._source.group_name),
          },
          {
            key: "watch_state",
            label: "监控",
            cell: (r) => (r._source.enabled ? "监控中" : "已暂停"),
          },
          {
            key: "checked",
            label: "最近检查",
            cell: (r) => stamp(r._source.checked_at),
          },
        ],
      })}
      ${pagination({ id: "watched-pages", offset: data.offset, limit: data.limit, total: data.total, hasMore: data.has_more, prevId: "watched-prev", nextId: "watched-next" })}<a class="button secondary" href="#collection">管理采集与复核</a>`;
    bindFilterBar(
      anchor.querySelector("#watched-filter"),
      async ({ group_name, q }) => {
        watchedFilters = { ...watchedFilters, group_name, q };
        watchedOffset = 0;
        await bindWatched();
      },
      toast,
    );
    bindPagination(anchor, "watched-pages", (start) => {
      watchedOffset = start;
      return bindWatched();
    });
  }
  async function render(rev, days = 30) {
    const m = { ...market() },
      r = await api("/availability?" + qs({ ...m, days }));
    const count = (state) =>
      r.counts.find((c) => c.state === state)?.count || 0;
    if (
      !mount(
        `<a class="button secondary" data-workspace-action href="#groups">应用分组看板</a><div class="cards">${Object.entries(
          listingStates,
        )
          .map(([s, label]) =>
            metric(label, count(s), s === "unverified" ? `查询失败或缺少依据，含 ${num(r.unobserved_watched)} 个待首次查询目标` : "按最近一次商店查询统计"),
          )
          .join(
            "",
          )}</div>${panel("观察范围与新鲜度", `${filterBar({ id: "availability-period", submit: "查看", values: { days }, fields: [{ name: "days", label: "趋势区间", type: "select", options: [7, 30, 90, 365].map((n) => [n, `最近${n}天`]) }], actions: '<a class="button secondary" href="#collection">管理采集与复核</a>' })}<p class="sub">${esc(r.scope)}</p><p>统计时间 ${stamp(r.observed_at)} · 最近48小时有效观察 ${num(r.counts.reduce((s, c) => s + Number(c.fresh), 0))} / 共 ${num(r.counts.reduce((s, c) => s + c.count, 0))} 个市场目标。商店明确查不到即标记为该市场下架，查询失败显示未知。</p>`)}<div class="grid2">${Object.entries(
          listingStates,
        )
          .map(([state, label]) =>
            panel(
              label + " · 每日状态快照（UTC）",
              (r.reports || [r])
                .map(
                  (part) =>
                    `<p class="sub">${part.market.store === "apple" ? "App Store" : "Google Play"} · ${esc(part.market.language)}</p>` +
                    chart(
                      part.history.map((h) => ({
                        at: h.day,
                        value: h.counts[state]?.count ?? (state === "unverified" ? null : 0),
                        note: `${label} · 快照采集 ${stamp(h.observed_at)} · 最近48小时有效观察 ${h.counts[state]?.fresh || 0}`,
                      })),
                    ),
                )
                .join(""),
            ),
          )
          .join(
            "",
          )}${panel("区间采集结果", bars(r.attempts.map((a) => ({ label: { ok: "成功访问", missing: listingStates.region_unavailable, error: "采集失败" }[a.outcome] || a.outcome, count: a.count }))))}</div>${panel(
          "查询失败的应用",
          `<p class="sub">最近最多${r.limit}条。按失败次数排序，错误原因保留供排查。</p>${table(
            ["应用", "当前状态", "失败次数", "上次取得详情", "采集错误"],
            r.pending.map((a) => [
              appLink(
                a.store || m.store,
                a.app_id,
                a.name,
                null,
                a.country,
                a.language,
              ),
              esc(listingStates[a.state] || "未知"),
              num(a.failures),
              stamp(a.last_success),
              esc(a.last_error || "—"),
            ]),
          )}`,
        )}${panel(
          "状态事件",
          `<p class="sub">最近最多${r.limit}条。首次验证是衡枢·观星观察起点；重新出现和下架均保留历史证据。</p>${table(
            ["应用", "事件", "观察时间"],
            r.events.map((e) => [
              appLink(
                e.store || m.store,
                e.app_id,
                e.name,
                null,
                e.country,
                e.language,
              ),
              esc(names[e.kind]),
              stamp(e.observed_at),
            ]),
          )}`,
        )}${panel("分类观察名单", '<div id="availability-watched" role="status">正在读取关注应用…</div>')}`,
        rev,
      )
    )
      return;
    if (
      watchedFilters.store !== m.store ||
      watchedFilters.country !== m.country
    )
      watchedOffset = 0;
    watchedFilters = { ...watchedFilters, store: m.store, country: m.country };
    document
      .querySelector("#availability-period")
      .addEventListener("submit", (e) => {
        e.preventDefault();
        render(
          nextRevision(),
          Number(new FormData(e.target).get("days")),
        ).catch((e) => toast(e.message));
      });
    bindWatched().catch((e) => toast(e.message));
  }
  return render;
}
