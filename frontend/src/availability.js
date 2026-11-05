export function createAvailabilityPage(ctx) {
  const {
    api,
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
  const labels = {
    available: "已观察在架",
    suspected_missing: "待复核",
    region_unavailable: "地区不可用",
  };
  const names = {
    first_verified: "首次验证",
    reappeared: "重新出现",
    region_unavailable: "地区不可用",
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
    const options = (values, selected, empty) =>
      `<option value="">${empty}</option>` +
      values
        .map(
          (v) =>
            `<option value="${esc(v)}" ${selected === v ? "selected" : ""}>${esc(v)}</option>`,
        )
        .join("");
    anchor.innerHTML = `<p class="sub">按下方商店、国家和分类查看关注应用；每个应用按国家独立复核。未验证不代表下架。</p>
      <form id="watched-filter" class="toolbar"><label>商店<select name="store">${[
        ["all", "全部商店"],
        ["google", "Google Play"],
        ["apple", "App Store"],
      ]
        .map(
          ([v, n]) =>
            `<option value="${v}" ${watchedFilters.store === v ? "selected" : ""}>${n}</option>`,
        )
        .join("")}</select></label>
      <label>国家<select name="country">${options(data.countries, watchedFilters.country, "全部国家")}</select></label>
      <label>分类标记<select name="group_name">${options(data.groups, watchedFilters.group_name, "全部分类")}</select></label>
      <label class="wide">应用名称 / ID<input name="q" value="${esc(watchedFilters.q)}" placeholder="搜索关注应用"></label><button>筛选</button></form>
      <p>共 ${num(data.total)} 条国家观察记录 · 当前 ${data.total ? data.offset + 1 : 0}–${data.offset + data.items.length}</p>
      ${table(
        [
          "应用",
          "商店 / 国家 / 语言",
          "分类标记",
          "观星复核状态",
          "监控",
          "最近检查",
        ],
        data.items.map((row) => [
          appLink(
            row.store,
            row.app_id,
            row.name,
            null,
            row.country,
            row.language,
          ),
          `${row.store === "apple" ? "App Store" : "Google Play"} · ${esc(row.country.toUpperCase())} / ${esc(row.language)}`,
          `<span class="badge">${esc(row.group_name)}</span>`,
          esc(labels[row.state] || row.state || "未验证") +
            (row.last_error
              ? `<div class="sub">${esc(row.last_error)}</div>`
              : ""),
          row.enabled ? "监控中" : "已暂停",
          stamp(row.checked_at),
        ]),
      )}
      <div class="toolbar"><button id="watched-prev" class="secondary" ${data.offset === 0 ? "disabled" : ""}>上一页</button><button id="watched-next" class="secondary" ${data.has_more ? "" : "disabled"}>下一页</button><a class="button secondary" href="#collection">管理采集与复核</a></div>`;
    anchor.querySelector("#watched-filter").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      watchedFilters = Object.fromEntries(
        ["store", "country", "group_name", "q"].map((k) => [
          k,
          form.elements[k].value,
        ]),
      );
      watchedOffset = 0;
      bindWatched().catch((e) => toast(e.message));
    };
    for (const [selector, direction] of [
      ["#watched-prev", -1],
      ["#watched-next", 1],
    ])
      anchor.querySelector(selector).onclick = () => {
        watchedOffset = Math.max(0, data.offset + direction * data.limit);
        bindWatched().catch((e) => toast(e.message));
      };
  }
  async function render(rev, days = 30) {
    const m = { ...market() },
      r = await api("/availability?" + qs({ ...m, days }));
    const count = (state) =>
      r.counts.find((c) => c.state === state)?.count || 0;
    if (
      !mount(
        `<p class="lead">上下架观察 · ${esc(m.country.toUpperCase())} / ${esc(m.language)}</p><p class="sub">${esc(r.scope)}</p><div class="cards">${Object.entries(
          labels,
        )
          .map(([s, label]) =>
            metric(label, count(s), "最近有效状态，含待刷新记录"),
          )
          .join(
            "",
          )}${metric("未验证的重点应用", r.unobserved_watched, "当前市场已启用但尚无观察")}</div>${panel("观察范围与新鲜度", `<form id="availability-period" class="toolbar"><label>趋势区间<select name="days">${[7, 30, 90, 365].map((n) => `<option value="${n}" ${n === Number(days) ? "selected" : ""}>最近${n}天</option>`).join("")}</select></label><button>查看</button><a class="button secondary" href="#collection">管理采集与复核</a></form><p>统计时间 ${stamp(r.observed_at)} · 最近48小时检查 ${num(r.counts.reduce((s, c) => s + Number(c.fresh), 0))} / 已观察 ${num(r.counts.reduce((s, c) => s + c.count, 0))} 个应用。请求失败保留原状态。</p>`)}<div class="grid2">${Object.entries(
          labels,
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
                        value: h.counts[state]?.count || 0,
                        note: `${label} · 快照采集 ${stamp(h.observed_at)} · 最近48小时检查 ${h.counts[state]?.fresh || 0}`,
                      })),
                    ),
                )
                .join(""),
            ),
          )
          .join(
            "",
          )}${panel("区间采集结果", bars(r.attempts.map((a) => ({ label: { ok: "成功访问", missing: "单次未查到", error: "采集失败" }[a.outcome] || a.outcome, count: a.count }))))}</div>${panel(
          "待复核与采集异常",
          `<p class="sub">最近最多${r.limit}条。缺失次数须满足采集配置的次数和间隔，网络错误单独重试。</p>${table(
            ["应用", "有效状态", "已计入缺失次数", "最近检查", "采集错误"],
            r.pending.map((a) => [
              appLink(
                a.store || m.store,
                a.app_id,
                a.name,
                null,
                a.country,
                a.language,
              ),
              esc(labels[a.state] || a.state),
              num(a.misses),
              stamp(a.checked_at),
              esc(a.last_error || "—"),
            ]),
          )}`,
        )}${panel(
          "状态事件",
          `<p class="sub">最近最多${r.limit}条。首次验证是衡枢·观星观察起点；重新出现和地区不可用均保留历史证据。</p>${table(
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
