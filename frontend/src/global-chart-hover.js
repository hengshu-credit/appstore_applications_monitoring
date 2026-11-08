export function bindGlobalChartHover({
  root,
  api,
  esc,
  scope,
  win = window,
  delay = 180,
  countryName = (c) => c.toUpperCase(),
  categoryName = (_s, c) => c,
}) {
  const doc = win.document,
    tip = doc.createElement("div");
  tip.id = "global-app-summary";
  tip.className = "global-app-summary";
  tip.setAttribute("role", "dialog");
  tip.setAttribute("aria-label", "应用信息");
  tip.hidden = true;
  root.append(tip);
  const cells = [...root.querySelectorAll(".appcell")],
    cache = new Map(),
    listeners = [];
  let active = null,
    pinned = false,
    timer,
    generation = 0,
    restoring = false;
  for (const cell of cells) {
    const wrap = cell.querySelector(".appicon-wrap"),
      name = cell.querySelector("[data-app]");
    if (!wrap || !name) continue;
    const button = doc.createElement("button");
    button.type = "button";
    button.className = "global-app-select";
    button.setAttribute(
      "aria-label",
      `选中 ${name.textContent} 并查看应用信息`,
    );
    button.setAttribute("aria-controls", tip.id);
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-pressed", "false");
    wrap.append(button);
  }
  const identity = (cell) => {
    const name = cell?.querySelector("[data-app]");
    return (
      name && {
        store: name.dataset.store,
        app_id: name.dataset.app,
        name: name.textContent,
        country: name.dataset.country || "",
        language: name.dataset.language || "",
        cell,
        element: cell.querySelector(".global-app-select") || name,
      }
    );
  };
  const same = (a, b) => a && b && a.store === b.store && a.app_id === b.app_id;
  const paint = () =>
    cells.forEach((cell) => {
      const selected = !!same(identity(cell), active),
        row = cell.closest(".global-ranked-app,tr");
      row?.classList.toggle("same-ranked-app", selected);
      row?.classList.toggle("ranked-app-pinned", selected && pinned);
      const button = cell.querySelector(".global-app-select");
      button?.setAttribute("aria-pressed", String(selected && pinned));
      button?.setAttribute(
        "aria-expanded",
        String(pinned && cell === active?.cell),
      );
    });
  const clear = () => {
    generation++;
    win.clearTimeout(timer);
    active = null;
    pinned = false;
    tip.hidden = true;
    paint();
  };
  const place = () => {
    if (!active || tip.hidden) return;
    const r = active.cell.getBoundingClientRect(),
      t = tip.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(r.left, win.innerWidth - t.width - 8))}px`;
    tip.style.top = `${Math.max(8, Math.min(win.innerHeight - t.height - 8, r.bottom + t.height + 8 < win.innerHeight ? r.bottom + 8 : r.top - t.height - 8))}px`;
  };
  const heading = (app) =>
    `<header class="global-app-summary-heading"><div><strong>${esc(app.name)}</strong><span class="sub">${app.store === "apple" ? "App Store" : "Google Play"} · ${esc(countryName(app.country))} ${esc(app.language)}</span></div><button type="button" class="secondary small" data-close-app-summary aria-label="关闭应用信息并取消选中">关闭</button></header>`;
  const profile = (r) => {
    if (!r) return '<p class="sub">应用资料暂不可用，可打开完整档案查看。</p>';
    const rows = [
      ["App ID", r.app_id],
      ["开发者", r.developer],
      ["开发者 ID", r.developer_id],
      ["类别", r.category ? categoryName(r.store, r.category) : null],
      ["版本", r.version],
      [
        "价格",
        r.price == null
          ? null
          : Number(r.price) === 0
            ? "免费"
            : r.price + " " + (r.currency || ""),
      ],
      [
        "评分 / 评分数",
        r.rating == null ? null : r.rating + " / " + (r.ratings ?? "-"),
      ],
      [
        "关键词覆盖 / TOP3",
        r.keyword_count == null
          ? null
          : r.keyword_count + " / " + (r.keyword_top3_count ?? "-"),
      ],
      [
        "权限采集",
        r.permission_state === "unsupported"
          ? "商店未提供公开清单"
          : r.permission_count == null
            ? null
            : r.permission_count + " 项",
      ],
      [
        "近30天下载 / 收入",
        (r.metrics || [])
          .map(
            (f) =>
              `${f.metric === "downloads" ? "下载" : "收入"} ${f.value} ${f.currency || ""}（${f.is_estimate ? "估算" : "实际"}）`,
          )
          .join("；") || null,
      ],
    ];
    return `<section><h3>当前市场应用资料</h3><table class="global-app-facts"><tbody>${rows.map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${esc(v ?? "-")}</td></tr>`).join("")}</tbody></table></section>`;
  };
  const summary = (data) =>
    `${data.charts
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
  const cached = (key, fetch) => {
    if (!cache.has(key))
      cache.set(
        key,
        fetch().catch((e) => {
          cache.delete(key);
          throw e;
        }),
      );
    return cache.get(key);
  };
  const hover = (cell) => {
    if (pinned || restoring) return;
    const app = identity(cell);
    if (!app) return;
    active = app;
    paint();
  };
  const pin = (cell) => {
    const app = identity(cell);
    if (!app) return;
    clear();
    active = app;
    pinned = true;
    paint();
    const version = generation;
    tip.innerHTML = heading(app) + '<p role="status">正在加载应用信息…</p>';
    tip.hidden = false;
    place();
    timer = win.setTimeout(async () => {
      const query = new URLSearchParams({
        ...scope,
        store: app.store,
        app_id: app.app_id,
      });
      const [stats, details] = await Promise.allSettled([
        cached(query.toString(), () =>
          api("/charts/global/app-summary?" + query),
        ),
        app.country && app.language
          ? cached(
              JSON.stringify([
                app.store,
                app.app_id,
                app.country,
                app.language,
              ]),
              () =>
                api("/apps/detail-rows", {
                  targets: [
                    {
                      store: app.store,
                      app_id: app.app_id,
                      country: app.country,
                      language: app.language,
                    },
                  ],
                }),
            )
          : Promise.resolve({ items: [] }),
      ]);
      if (!root.isConnected || generation !== version) return;
      const record =
        details.status === "fulfilled" ? details.value.items?.[0] : null;
      tip.innerHTML =
        heading(app) +
        profile(record) +
        (stats.status === "fulfilled"
          ? `<section><h3>所选国家榜单表现</h3>${summary(stats.value)}</section>`
          : '<p class="sub">榜单汇总暂不可用，重新点击图标可重试。</p>') +
        `<a class="button secondary" href="#app/${encodeURIComponent(app.store)}/${encodeURIComponent(app.app_id)}?country=${encodeURIComponent(app.country)}&language=${encodeURIComponent(app.language)}">打开完整档案</a>`;
      place();
    }, delay);
  };
  const on = (target, type, fn, options) => {
    target.addEventListener(type, fn, options);
    listeners.push(() => target.removeEventListener(type, fn, options));
  };
  on(root, "mouseover", (event) => {
    if (tip.contains(event.target)) return;
    const cell = event.target.closest(".appcell");
    if (cell && !cell.contains(event.relatedTarget)) hover(cell);
  });
  on(root, "mouseout", (event) => {
    if (pinned || tip.contains(event.target)) return;
    const cell = event.target.closest(".appcell");
    if (cell && !cell.contains(event.relatedTarget)) clear();
  });
  on(root, "focusin", (event) => {
    if (!tip.contains(event.target)) hover(event.target.closest(".appcell"));
  });
  on(root, "focusout", (event) => {
    if (
      !pinned &&
      event.target.closest(".appcell") &&
      !event.target.closest(".appcell").contains(event.relatedTarget)
    )
      clear();
  });
  on(root, "click", (event) => {
    if (tip.contains(event.target)) {
      if (event.target.closest("[data-close-app-summary]")) {
        const button = active?.element;
        clear();
        restoring = true;
        button?.focus();
        restoring = false;
      }
      return;
    }
    if (event.target.closest("[data-icon-refresh]")) return;
    const icon = event.target.closest(".appicon-wrap,.appicon");
    if (icon) {
      event.preventDefault();
      event.stopPropagation();
      pin(icon.closest(".appcell"));
    }
  });
  on(doc, "click", (event) => {
    if (tip.contains(event.target) || event.target.closest(".appcell")) return;
    if (
      !event.target.closest(
        'button,a,input,select,textarea,summary,[role="button"],[contenteditable="true"]',
      )
    )
      clear();
  });
  on(doc, "keydown", (event) => {
    if (event.key === "Escape" && active) {
      event.preventDefault();
      const restore = tip.contains(doc.activeElement),
        button = active.element;
      clear();
      if (restore) {
        restoring = true;
        button.focus();
        restoring = false;
      }
    }
  });
  on(
    doc,
    "scroll",
    (event) => {
      if (!pinned && !tip.contains(event.target)) clear();
    },
    true,
  );
  on(win, "resize", () => {
    if (pinned) place();
    else clear();
  });
  const observer = new win.MutationObserver(() => {
    if (!root.isConnected) destroy();
  });
  observer.observe(doc.body, { childList: true, subtree: true });
  function destroy() {
    clear();
    listeners.forEach((fn) => fn());
    observer.disconnect();
    tip.remove();
  }
  return { clear, destroy };
}
