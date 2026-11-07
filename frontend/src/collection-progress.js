export const taskLabels = {
  scheduled: "定时采集",
  pending: "等待采集",
  running: "采集中",
  paused: "已暂停",
  error: "失败待重试",
  done: "已完成",
  out_of_scope: "未启用市场",
};
const shortLabels = {
  scheduled: "定时",
  pending: "待采",
  running: "采集中",
  paused: "暂停",
  error: "待重试",
  done: "完成",
  out_of_scope: "未启用",
};
const symbols = {
  scheduled: "◷",
  pending: "○",
  running: "↻",
  paused: "Ⅱ",
  error: "!",
  done: "✓",
  out_of_scope: "–",
};
export const taskMark = (state) =>
  `<i class="collection-state-mark" data-state="${state}" aria-hidden="true">${symbols[state]}</i>`;
const integer = (value) => Math.max(0, Math.trunc(Number(value) || 0));
const percentage = (value) =>
  value > 0 && value < 0.1 ? "<0.1%" : `${value.toFixed(1)}%`;

export function taskProgress(
  {
    summary = {},
    total,
    collected,
    kind = "",
    selected = false,
    activeState = "",
  },
  { num, esc },
) {
  const entries = Object.entries(taskLabels).filter(
    ([state]) =>
      (!kind || state !== "out_of_scope") &&
      (state !== "done" || kind.startsWith("aso_")) &&
      (state !== "scheduled" || !kind.startsWith("aso_")),
  );
  const count = integer(
    total ?? entries.reduce((sum, [state]) => sum + integer(summary[state]), 0),
  );
  const known = Number.isFinite(collected),
    sampled = known ? Math.min(count, integer(collected)) : 0;
  const coverage = count ? (sampled / count) * 100 : 0;
  const coverageText = known ? `${num(sampled)} / ${num(count)}` : "统计中";
  return `<div class="category-coverage" title="已有成功记录的任务数 / 任务总数；后续失败或暂停仍保留历史成功记录，不代表本轮全部完成。">
    <div><span>成功覆盖</span><strong>${known ? (count ? esc(percentage(coverage)) : "—") : "—"}</strong></div>
    <div class="coverage-track" role="meter" aria-label="已有成功记录的任务" aria-valuemin="0" aria-valuemax="${count || 1}" aria-valuenow="${sampled}" aria-valuetext="${coverageText}"><span style="width:${coverage}%"></span></div>
    <small>${coverageText}</small>
  </div><div class="category-counts" aria-label="各状态任务数量">${entries
    .map(([state, label]) => {
      const value = integer(summary[state]),
        percent = count ? (value / count) * 100 : 0;
      const caption = `${label}：${num(value)} · ${percentage(percent)}`;
      const attrs = kind
        ? `data-category="${esc(kind)}" data-category-state="${state}" aria-pressed="${selected && activeState === state}"`
        : "data-pool-card";
      return `<button type="button" ${attrs} data-state="${state}" title="${caption}" aria-label="${caption}">
      <span class="state-caption">${taskMark(state)}<span>${shortLabels[state]}</span></span>
      <span class="state-track" aria-hidden="true"><span style="width:${percent}%"></span></span>
      <b>${num(value)}</b></button>`;
    })
    .join("")}</div>`;
}
