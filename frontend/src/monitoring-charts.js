const chartEscape = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );

export function monitoringChart(
  input,
  invert = false,
  formatAt = (v) => new Date(v * 1000).toLocaleString("zh-CN"),
) {
  const points = input
    .filter((p) => Number.isFinite(Number(p.at)))
    .map((p) => ({
      ...p,
      value:
        typeof p.value === "number" && Number.isFinite(p.value)
          ? p.value
          : null,
    }))
    .sort((a, b) => a.at - b.at);
  if (!points.length) return '<p class="empty">暂无历史观察</p>';
  const w = 800,
    h = 240,
    pad = 48,
    top = 64,
    bottom = 205;
  const values = points.filter((p) => p.value !== null).map((p) => p.value);
  const lo = invert ? 1 : Math.min(0, ...values),
    hi = Math.max(lo + 1, ...values);
  const first = Number(points[0].at),
    last = Number(points.at(-1).at);
  const x = (p) =>
    last === first
      ? w / 2
      : pad + ((w - 2 * pad) * (Number(p.at) - first)) / (last - first);
  const y = (v) =>
    top +
    (bottom - top) * (invert ? (v - lo) / (hi - lo) : 1 - (v - lo) / (hi - lo));
  let segments = [],
    segment = [];
  for (const p of points) {
    if (p.value === null) {
      if (segment.length) segments.push(segment);
      segment = [];
    } else segment.push(p);
  }
  if (segment.length) segments.push(segment);
  const dots = points
    .map((p, i) => {
      const left = i ? (x(points[i - 1]) + x(p)) / 2 : pad,
        right = i < points.length - 1 ? (x(p) + x(points[i + 1])) / 2 : w - pad;
      const label = `${formatAt(p.at)} · ${p.value === null ? "未观测 / 失败 / 未命中" : (invert ? "排名 #" : "数值 ") + p.value}`;
      return `<g class="plot-point" tabindex="0" role="button" aria-label="${chartEscape(label + " " + (p.note || ""))}"><rect class="plot-hit" x="${left}" y="${top}" width="${Math.max(1, right - left)}" height="${bottom - top}"/>${p.value !== null ? `<circle cx="${x(p)}" cy="${y(p.value)}" r="4"/>` : ""}<g class="plot-tip"><line class="plot-guide" x1="${x(p)}" x2="${x(p)}" y1="${top}" y2="${bottom}"/><rect x="${pad}" y="2" width="${w - 2 * pad}" height="53"/><text x="${pad + 10}" y="22">${chartEscape(label)}</text><text x="${pad + 10}" y="43">${chartEscape((p.note || "观察时间；缺失点保留断线").slice(0, 110))}</text></g></g>`;
    })
    .join("");
  return `<div class="interactive-plot"><p class="sub">悬停、点击或 Tab 聚焦查看数据点 · 时间轴按实际观察间隔</p><svg class="chart" viewBox="0 0 ${w} ${h}" aria-label="可交互观察趋势">${[lo, hi].map((v) => `<line class="gridline" x1="${pad}" x2="${w - pad}" y1="${y(v)}" y2="${y(v)}"/><text x="3" y="${y(v) + 4}">${chartEscape(v)}</text>`).join("")}${segments.map((s) => `<polyline points="${s.map((p) => x(p) + "," + y(p.value)).join(" ")}"/>`).join("")}${dots}<text x="${pad}" y="234">${chartEscape(formatAt(first))}</text><text text-anchor="end" x="${w - pad}" y="234">${chartEscape(formatAt(last))}</text></svg></div>`;
}

export function bindMonitoringCharts(root = document) {
  root.addEventListener("click", (event) => {
    const point = event.target.closest?.(".plot-point");
    if (point) {
      const plot = point.closest("svg");
      plot
        .querySelectorAll(".selected")
        .forEach((p) => p.classList.remove("selected"));
      point.classList.add("selected");
    }
  });
  root.addEventListener("keydown", (event) => {
    const point = event.target.closest?.(".plot-point");
    if (!point) return;
    const points = [...point.closest("svg").querySelectorAll(".plot-point")],
      i = points.indexOf(point);
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      points[
        Math.max(
          0,
          Math.min(
            points.length - 1,
            i + (event.key === "ArrowRight" ? 1 : -1),
          ),
        )
      ].focus();
    }
    if (event.key === "Escape")
      point
        .closest("svg")
        .querySelectorAll(".selected")
        .forEach((p) => p.classList.remove("selected"));
  });
}
