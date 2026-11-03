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
              chart(
                r.history.map((h) => ({
                  at: h.day,
                  value: h.counts[state]?.count || 0,
                  note: `${label} · 快照采集 ${stamp(h.observed_at)} · 最近48小时检查 ${h.counts[state]?.fresh || 0}`,
                })),
              ),
            ),
          )
          .join(
            "",
          )}${panel("区间采集结果", bars(r.attempts.map((a) => ({ label: { ok: "成功访问", missing: "单次未查到", error: "采集失败" }[a.outcome] || a.outcome, count: a.count }))))}</div>${panel(
          "待复核与采集异常",
          `<p class="sub">最近最多${r.limit}条。缺失次数须满足采集配置的次数和间隔，网络错误单独重试。</p>${table(
            ["应用", "有效状态", "已计入缺失次数", "最近检查", "采集错误"],
            r.pending.map((a) => [
              appLink(m.store, a.app_id, a.name),
              esc(labels[a.state] || a.state),
              num(a.misses),
              stamp(a.checked_at),
              esc(a.last_error || "—"),
            ]),
          )}`,
        )}${panel(
          "状态事件",
          `<p class="sub">最近最多${r.limit}条。首次验证是观星观察起点；重新出现和地区不可用均保留历史证据。</p>${table(
            ["应用", "事件", "观察时间"],
            r.events.map((e) => [
              appLink(m.store, e.app_id, e.name),
              esc(names[e.kind]),
              stamp(e.observed_at),
            ]),
          )}`,
        )}`,
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
  }
  return render;
}
