export function bindGlobalChartHover({
  root,
  api,
  esc,
  scope,
  win = window,
  delay = 180,
}) {
  const tip = win.document.createElement("div");
  tip.id = "global-app-summary";
  tip.className = "global-app-summary";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  root.append(tip);
  const cache = new Map();
  let active,
    timer,
    generation = 0;
  const cells = [...root.querySelectorAll(".appcell")];
  const identity = (cell) => {
    const name = cell?.querySelector("[data-app]");
    return (
      name && {
        store: name.dataset.store,
        app_id: name.dataset.app,
        name: name.textContent,
        element: name,
      }
    );
  };
  const same = (a, b) => a && b && a.store === b.store && a.app_id === b.app_id;
  const place = (cell) => {
    const r = cell.getBoundingClientRect();
    const width = tip.getBoundingClientRect().width,
      height = tip.getBoundingClientRect().height;
    tip.style.left = `${Math.max(8, Math.min(r.left, win.innerWidth - width - 8))}px`;
    tip.style.top = `${Math.max(8, r.bottom + height + 8 < win.innerHeight ? r.bottom + 8 : r.top - height - 8)}px`;
  };
  const dismiss = () => {
    generation++;
    win.clearTimeout(timer);
    active?.element.removeAttribute("aria-describedby");
    tip.hidden = true;
  };
  const clear = () => {
    dismiss();
    active = null;
    cells.forEach((cell) =>
      cell
        .closest(".global-ranked-app, tr")
        ?.classList.remove("same-ranked-app"),
    );
  };
  const show = (cell) => {
    const app = identity(cell);
    if (
      !app ||
      (same(app, active) && active.element === app.element && !tip.hidden)
    )
      return;
    clear();
    active = app;
    const version = generation;
    cells.forEach((item) =>
      item
        .closest(".global-ranked-app, tr")
        ?.classList.toggle("same-ranked-app", !!same(identity(item), app)),
    );
    app.element.setAttribute("aria-describedby", tip.id);
    tip.innerHTML = `<strong>${esc(app.name)}</strong><p>正在汇总所选国家的各榜单…</p>`;
    tip.hidden = false;
    place(cell);
    timer = win.setTimeout(async () => {
      const key = JSON.stringify([app.store, app.app_id]);
      try {
        if (!cache.has(key)) {
          const query = new URLSearchParams({
            ...scope,
            store: app.store,
            app_id: app.app_id,
          });
          cache.set(
            key,
            api("/charts/global/app-summary?" + query).catch((error) => {
              cache.delete(key);
              throw error;
            }),
          );
        }
        const data = await cache.get(key);
        if (!root.isConnected || generation !== version) return;
        tip.innerHTML = `<strong>${esc(app.name)}</strong><span class="sub"> · ${app.store === "apple" ? "App Store" : "Google Play"}</span>${data.charts
          .map(
            (s) =>
              `<section><b>${esc(s.name)}${s.mode === "derived" ? "（派生）" : ""}</b><div>Top ${data.depth}：${s.ranked_countries} 个国家 / 有效观察 ${s.observed_countries} 个 / 所选 ${s.selected_countries} 个</div><div>第 1 名 ${s.top1} · Top 3 ${s.top3} · Top 10 ${s.top10} · 最佳 ${s.best_rank ?? "—"} · 中位 ${s.median_rank ?? "—"}</div><div class="sub">${
                s.matches
                  .slice(0, 6)
                  .map((m) => `${esc(m.name)} #${m.position}`)
                  .join(" · ") || "有效快照中暂未观察到"
              }${s.matches.length > 6 ? ` · 等 ${s.matches.length} 个国家` : ""}</div><div class="sub">缺失 ${s.missing_countries} · 过期/失败 ${s.stale_countries} · 历史不足 ${s.insufficient_countries} · 不支持 ${s.unsupported_countries} · 返回不足 Top ${data.depth} ${s.short_countries}</div></section>`,
          )
          .join("")}<small>${esc(data.note)}</small>`;
        place(cell);
      } catch {
        if (!root.isConnected || generation !== version) return;
        tip.innerHTML = `<strong>${esc(app.name)}</strong><p>汇总暂不可用，重新悬停可重试。</p>`;
        place(cell);
      }
    }, delay);
  };
  root.addEventListener("mouseover", (event) => {
    const cell = event.target.closest(".appcell");
    if (cell && !cell.contains(event.relatedTarget)) show(cell);
  });
  root.addEventListener("mouseout", (event) => {
    const cell = event.target.closest(".appcell");
    if (
      cell &&
      !cell.contains(event.relatedTarget) &&
      !tip.contains(event.relatedTarget)
    )
      dismiss();
  });
  root.addEventListener("focusin", (event) =>
    show(event.target.closest(".appcell")),
  );
  root.addEventListener("focusout", (event) => {
    if (!event.target.closest(".appcell")?.contains(event.relatedTarget))
      dismiss();
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") dismiss();
  });
  tip.addEventListener("mouseleave", (event) => {
    if (!event.relatedTarget?.closest?.(".appcell")) dismiss();
  });
  root.addEventListener(
    "scroll",
    (event) => {
      if (!tip.contains(event.target)) dismiss();
    },
    true,
  );
  return { clear };
}
