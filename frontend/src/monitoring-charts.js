const chartEscape = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const numeric = (v) =>
  (typeof v === "number" || (typeof v === "string" && v.trim() !== "")) &&
  Number.isFinite(Number(v));
const numberLabel = (value, precision = 2, compact = false) => {
  if (compact && value !== 0 && Math.abs(value) < 0.01)
    return value.toExponential(1);
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: precision,
    ...(value !== 0 && Math.abs(value) < 10 ** -precision
      ? { maximumSignificantDigits: 3 }
      : {}),
    ...(compact && Math.abs(value) >= 10000 ? { notation: "compact" } : {}),
  }).format(value);
};
const dateLabel = (at) =>
  new Date(at * 1000).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });

function timeTicks(first, last) {
  const span = last - first;
  const label = (at) => {
    const d = new Date(at * 1000);
    const date = d.toLocaleDateString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      ...(span > 365 * 86400 ? { year: "numeric" } : {}),
    });
    return span <= 86400
      ? `${date} ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`
      : date;
  };
  const seen = new Set();
  const ticks = Array.from({ length: span ? 5 : 1 }, (_, i) => {
    const at = span ? first + (span * i) / 4 : first;
    return { at, x: span ? i * 25 : 50, label: label(at) };
  }).filter((t) => !seen.has(t.label) && seen.add(t.label));
  if (ticks.length === 1) ticks[0].x = 50;
  return ticks;
}

function valueScale(values, invert, domain) {
  if (!values.length) return { lo: 0, hi: 1, ticks: [] };
  let lo = invert ? 1 : Math.min(0, ...values),
    hi = Math.max(invert ? 1 : 0, ...values);
  if (hi <= lo) hi = lo + 1;
  if (
    Array.isArray(domain) &&
    domain.length === 2 &&
    domain.every(Number.isFinite) &&
    domain[0] < domain[1]
  ) {
    lo = Math.min(domain[0], ...values);
    hi = Math.max(domain[1], ...values);
    if (lo === domain[0] && hi === domain[1])
      return {
        lo,
        hi,
        ticks:
          hi - lo === 1
            ? [lo, hi]
            : Array.from({ length: 6 }, (_, i) => lo + ((hi - lo) * i) / 5),
      };
  }
  const raw = (hi - lo) / 4,
    power = 10 ** Math.floor(Math.log10(raw));
  if (!Number.isFinite(raw) || !power) return { lo, hi, ticks: [lo, hi] };
  let step = [1, 2, 2.5, 5, 10].find((n) => n * power >= raw) * power;
  if (invert) step = Math.max(1, Math.ceil(step));
  const lower = invert ? 1 : Math.floor(lo / step) * step;
  const upper = Math.ceil(hi / step) * step;
  if (!Number.isFinite(lower) || !Number.isFinite(upper))
    return { lo, hi, ticks: [lo, hi] };
  lo = lower;
  hi = upper;
  const ticks = [lo];
  for (
    let v = Math.ceil((lo + step / 100) / step) * step;
    v <= hi + step / 100 && ticks.length < 8;
    v += step
  )
    ticks.push(Number(v.toPrecision(12)));
  return { lo, hi, ticks };
}

export function monitoringChart(
  input,
  invert = false,
  formatAt = (at) => new Date(at * 1000).toLocaleString("zh-CN"),
  options = {},
) {
  const points = (input || [])
    .filter((p) => numeric(p.at) && Math.abs(Number(p.at)) <= 8640000000000)
    .map((p) => ({
      ...p,
      at: Number(p.at),
      value:
        typeof p.value === "number" && Number.isFinite(p.value)
          ? p.value
          : null,
    }))
    .sort((a, b) => a.at - b.at);
  if (!points.length) return '<div class="plot-empty">暂无历史观察</div>';
  const values = points.filter((p) => p.value !== null).map((p) => p.value);
  const scale = valueScale(values, invert, options.domain);
  const first = points[0].at,
    last = points.at(-1).at;
  const x = (p) =>
    last === first ? 50 : ((p.at - first) / (last - first)) * 100;
  const magnitude = Math.max(Math.abs(scale.lo), Math.abs(scale.hi)) || 1;
  const y = (v) => {
    const relative =
      (v / magnitude - scale.lo / magnitude) /
      (scale.hi / magnitude - scale.lo / magnitude);
    return (invert ? relative : 1 - relative) * 100;
  };
  const label = options.label || (invert ? "排名" : "数值");
  const valueLabel = (v) =>
    v === null
      ? "未观测"
      : (invert ? "#" : "") +
        numberLabel(v, options.precision ?? (invert ? 0 : 2));
  const segments = [];
  let segment = [];
  for (const p of points) {
    if (p.value === null) {
      if (segment.length) segments.push(segment);
      segment = [];
    } else segment.push(p);
  }
  if (segment.length) segments.push(segment);
  const ticks = timeTicks(first, last);
  const controls = points
    .map((p, i) => {
      let left = i ? (x(points[i - 1]) + x(p)) / 2 : 0,
        right = i < points.length - 1 ? (x(p) + x(points[i + 1])) / 2 : 100;
      if (right - left < 0.08) {
        left = Math.max(0, x(p) - 0.04);
        right = Math.min(100, left + 0.08);
      }
      const anchor = ((x(p) - left) / (right - left)) * 100;
      const date = formatAt(p.at),
        value = valueLabel(p.value),
        note =
          p.note ||
          (p.value === null ? "本次未观测到有效数值，曲线保留断线。" : "");
      return `<button type="button" class="plot-point chart-datum" tabindex="${i === 0 ? 0 : -1}" aria-pressed="false" aria-label="${chartEscape(`${date} · ${label} ${value} ${note}`)}" data-date="${chartEscape(date)}" data-value="${chartEscape(value)}" data-raw="${p.value ?? ""}" data-note="${chartEscape(note)}" data-series="${chartEscape(options.label || (invert ? "排名" : ""))}" data-plot-left="${left}" data-plot-width="${right - left}" data-point-x="${anchor}" data-point-y="${p.value === null ? 100 : y(p.value)}"><span class="plot-cursor" aria-hidden="true"></span><span class="${p.value === null ? "plot-gap" : "plot-dot"}" aria-hidden="true"></span></button>`;
    })
    .join("");
  return `<figure class="interactive-plot" aria-label="${chartEscape(options.label || "观察趋势")}，方向键浏览，回车固定提示"><div class="plot-viewport"><div class="plot-y-axis" aria-hidden="true">${scale.ticks.map((v) => `<span data-plot-top="${y(v)}">${chartEscape(numberLabel(v, 2, true))}</span>`).join("")}</div><div class="plot-canvas"><svg class="chart plot-lines" viewBox="0 0 1000 200" preserveAspectRatio="none" aria-hidden="true" focusable="false">${scale.ticks.map((v) => `<line class="gridline" x1="0" x2="1000" y1="${y(v) * 2}" y2="${y(v) * 2}" vector-effect="non-scaling-stroke"/>`).join("")}${segments
    .filter((s) => s.length > 1)
    .map(
      (s) =>
        `<polyline points="${s.map((p) => `${x(p) * 10},${y(p.value) * 2}`).join(" ")}" vector-effect="non-scaling-stroke"/>`,
    )
    .join(
      "",
    )}</svg>${controls}${!values.length ? '<span class="plot-no-values">暂无有效数值</span>' : ""}</div><div class="plot-x-axis" aria-hidden="true">${ticks.map((t, i) => `<span class="plot-x-tick" data-anchor="${t.x === 0 ? "start" : t.x === 100 ? "end" : "middle"}" data-optional="${i % 2 === 1 && i < ticks.length - 1}" data-plot-left="${t.x}">${chartEscape(t.label)}</span>`).join("")}</div></div><figcaption class="plot-footer"><span>${points.length === 1 ? "单次观察 · " : ""}${chartEscape(dateLabel(first))}${dateLabel(first) !== dateLabel(last) ? " – " + chartEscape(dateLabel(last)) : ""} · 本地时间</span><span>悬停查看 · 点击固定</span></figcaption></figure>`;
}

export function monitoringBars(rows) {
  if (!rows?.length) return '<div class="plot-empty">暂无数据</div>';
  const counts = rows.map((r) =>
    numeric(r.count) && Number(r.count) >= 0 ? Number(r.count) : null,
  );
  const max = Math.max(1, ...counts.filter((v) => v !== null));
  return `<div class="bar-list" role="group" aria-label="分类数量，方向键浏览，回车固定提示">${rows
    .map((r, i) => {
      const label = String(r.label ?? r.rating ?? "未知"),
        count = counts[i],
        value = count === null ? "未观测" : numberLabel(count);
      return `<button type="button" class="barrow chart-datum" tabindex="${i === 0 ? 0 : -1}" aria-pressed="false" aria-label="${chartEscape(label + "：" + value)}" data-series="${chartEscape(label)}" data-value="${chartEscape(value)}" data-raw="${count ?? ""}" data-note="${chartEscape(r.note || "")}"><span class="bar-label">${chartEscape(label)}</span><svg class="bar-chart" width="100%" height="6" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true"><rect width="100" height="6" class="bar-track"/><rect width="${count === null ? 0 : (count / max) * 100}" height="6" class="bar-value"/></svg><span class="bar-count">${chartEscape(value)}</span></button>`;
    })
    .join("")}</div>`;
}

const bindings = new WeakMap();
let tooltipSequence = 0;
export function bindMonitoringCharts(root = document) {
  if (bindings.has(root)) return bindings.get(root);
  const doc = root.nodeType === 9 ? root : root.ownerDocument,
    win = doc.defaultView;
  // Apply geometry through CSS properties; HTML style attributes are blocked
  // by the production CSP. Cover initial charts and later asynchronous renders.
  const positions = {
    "data-plot-left": "left",
    "data-plot-top": "top",
    "data-plot-width": "width",
    "data-point-x": "--point-x",
    "data-point-y": "--point-y",
  };
  const selector = Object.keys(positions)
    .map((a) => `[${a}]`)
    .join(",");
  const place = (node) => {
    if (!node.querySelectorAll) return;
    const elements = [
      ...(node.matches?.(selector) ? [node] : []),
      ...node.querySelectorAll(selector),
    ];
    for (const element of elements) {
      if (!element.closest(".interactive-plot")) continue;
      for (const [attribute, property] of Object.entries(positions)) {
        const raw = element.getAttribute(attribute);
        if (raw !== null && raw.trim() !== "" && Number.isFinite(Number(raw)))
          element.style.setProperty(property, `${Number(raw)}%`);
      }
    }
  };
  place(root);
  const tip = doc.createElement("div");
  tip.className = "chart-tooltip";
  tip.id = `chart-tooltip-${++tooltipSequence}`;
  tip.hidden = true;
  tip.setAttribute("role", "tooltip");
  doc.body.append(tip);
  let active = null,
    pinned = false,
    pointerFocus = null,
    restoring = false;
  const datum = (target) => target?.closest?.(".chart-datum");
  const group = (point) => point.closest(".interactive-plot,.bar-list");
  const clear = () => {
    active?.classList.remove("is-active", "selected");
    active?.setAttribute("aria-pressed", "false");
    active?.removeAttribute("aria-describedby");
    active = null;
    pinned = false;
    tip.hidden = true;
  };
  const position = (point) => {
    const r = (
      point.querySelector(".plot-dot,.plot-gap") || point
    ).getBoundingClientRect();
    const width = tip.offsetWidth || 260,
      height = tip.offsetHeight || 120;
    const x = r.left + r.width / 2,
      y = r.top + r.height / 2;
    tip.style.left = `${Math.max(8, Math.min(win.innerWidth - width - 8, x + 14 + width < win.innerWidth - 8 ? x + 14 : x - width - 14))}px`;
    tip.style.top = `${Math.max(8, Math.min(win.innerHeight - height - 8, y - height - 12 > 8 ? y - height - 12 : y + 14))}px`;
  };
  const show = (point, pin = false) => {
    if (!point?.isConnected) return;
    clear();
    active = point;
    pinned = pin;
    const owner = point.closest("dialog[open]") || doc.body;
    if (tip.parentElement !== owner) owner.append(tip);
    group(point)
      ?.querySelectorAll(".chart-datum")
      .forEach((p) => (p.tabIndex = p === point ? 0 : -1));
    point.classList.add("is-active");
    point.classList.toggle("selected", pin);
    point.setAttribute("aria-pressed", String(pin));
    point.setAttribute("aria-describedby", tip.id);
    const title =
      point.dataset.series ||
      point.closest(".panel")?.querySelector("h2,h3")?.textContent ||
      "观察数据";
    const raw = point.dataset.raw,
      display = point.dataset.value;
    tip.setAttribute("role", pin ? "dialog" : "tooltip");
    tip.setAttribute("aria-label", title);
    const exact =
      raw !== "" &&
      Number(String(display).replace(/[#,]/g, "")) !== Number(raw);
    tip.innerHTML = `<div class="chart-tip-heading"><strong>${chartEscape(title)}</strong>${pin ? '<span class="chart-tip-pin">已固定</span>' : ""}<button type="button" class="chart-tip-close" tabindex="${pin ? 0 : -1}" aria-label="关闭图表提示">×</button></div><div class="chart-tip-value">${chartEscape(display)}</div>${point.dataset.date ? `<time>${chartEscape(point.dataset.date)}</time>` : ""}${exact ? `<small>原始值 ${chartEscape(raw)}</small>` : ""}${point.dataset.note ? `<p>${chartEscape(point.dataset.note)}</p>` : ""}`;
    tip.hidden = false;
    position(point);
  };
  const listeners = [];
  const on = (target, type, handler, options) => {
    target.addEventListener(type, handler, options);
    listeners.push(() => target.removeEventListener(type, handler, options));
  };
  on(root, "pointerover", (event) => {
    const point = datum(event.target);
    if (point && !pinned && active !== point) show(point);
  });
  on(root, "pointerout", (event) => {
    if (
      !pinned &&
      active &&
      !group(active)?.contains(event.relatedTarget) &&
      !tip.contains(event.relatedTarget)
    )
      clear();
  });
  on(tip, "pointerleave", () => {
    if (!pinned) clear();
  });
  on(root, "focusin", (event) => {
    const point = datum(event.target);
    if (point && point !== pointerFocus && !restoring) show(point, pinned);
  });
  on(root, "focusout", (event) => {
    if (
      !pinned &&
      datum(event.target) &&
      !group(event.target)?.contains(event.relatedTarget) &&
      !tip.contains(event.relatedTarget)
    )
      clear();
  });
  on(doc, "pointerdown", (event) => {
    pointerFocus = datum(event.target);
    if (!pointerFocus && !tip.contains(event.target)) clear();
  });
  on(doc, "pointerup", () => {
    pointerFocus = null;
  });
  on(root, "click", (event) => {
    const point = datum(event.target);
    if (point) {
      if (active === point && pinned) clear();
      else show(point, true);
    }
  });
  on(tip, "click", (event) => {
    if (event.target.closest(".chart-tip-close")) {
      const point = active;
      clear();
      restoring = true;
      point?.focus();
      restoring = false;
    }
  });
  on(root, "keydown", (event) => {
    pointerFocus = null;
    if (event.key === "Escape" && active) {
      event.preventDefault();
      const point = active,
        restore = tip.contains(doc.activeElement);
      clear();
      if (restore) {
        restoring = true;
        point.focus();
        restoring = false;
      }
      return;
    }
    const point = datum(event.target);
    if (!point) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (active === point && pinned) clear();
      else show(point, true);
      return;
    }
    const points = [...group(point).querySelectorAll(".chart-datum")],
      i = points.indexOf(point);
    let next;
    if (["ArrowRight", "ArrowDown"].includes(event.key))
      next = Math.min(points.length - 1, i + 1);
    if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = Math.max(0, i - 1);
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = points.length - 1;
    if (next != null) {
      event.preventDefault();
      points[next].focus();
    }
  });
  on(
    doc,
    "scroll",
    (event) => {
      if (!tip.contains(event.target)) clear();
    },
    true,
  );
  on(win, "resize", clear);
  const observer = new win.MutationObserver((records) => {
    for (const record of records)
      for (const node of record.addedNodes) place(node);
    if (active && !active.isConnected) clear();
  });
  observer.observe(root, { childList: true, subtree: true });
  const cleanup = () => {
    listeners.forEach((fn) => fn());
    observer.disconnect();
    tip.remove();
    bindings.delete(root);
  };
  bindings.set(root, cleanup);
  return cleanup;
}
