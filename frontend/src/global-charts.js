import { pagination, bindPagination } from "./pagination.js";
import { filterBar } from "./filter-bar.js";
import { bindGlobalChartHover } from "./global-chart-hover.js";
export function createGlobalCharts(ctx) {
  const {
    $,
    api,
    esc,
    stamp,
    panel,
    table,
    field,
    select,
    bindForm,
    mount,
    market,
    toast,
    selectors,
    appLink,
    nextRevision,
    currentRevision,
  } = ctx;
  let filters = {
      countries: "",
      collections: "top-free,top-paid,top-grossing",
      category: "",
      as_of: "",
      depth: "20",
      layout: "rows",
    },
    offset = 0,
    lastStore = "";
  const qs = (o) =>
    new URLSearchParams(
      Object.entries(o).filter(([, v]) => v !== "" && v != null),
    ).toString();
  const defaults = { ...filters };
  const normalizeLayout = (value) =>
    value === "columns" || value === "compare" ? "columns" : "rows";
  const validDate = (value) =>
    !value ||
    (/^\d{4}-\d{2}-\d{2}$/.test(value) &&
      !Number.isNaN(Date.parse(value)) &&
      new Date(value).toISOString().slice(0, 10) === value &&
      value <= new Date().toLocaleDateString("sv-SE"));
  const restore = (store, catalog) => {
    try {
      const all = JSON.parse(
        localStorage.getItem("appmon-global-chart-filters") || "{}",
      );
      const v = all[store];
      if (v && typeof v === "object") {
        const allowed = new Set(
          catalog.countries
            .filter((c) => store !== "apple" || c.apple_supported)
            .map((c) => c.code),
        );
        const countries =
          typeof v.countries === "string"
            ? [...new Set(v.countries.split(","))]
                .filter((c) => allowed.has(c))
                .join(",")
            : "";
        const choices = new Set(
          selectors
            .chartDefinitions(store === "all" ? "google" : store)
            .map((d) => d.id),
        );
        const collections =
          typeof v.collections === "string"
            ? [...new Set(v.collections.split(","))]
                .filter((c) => choices.has(c))
                .join(",")
            : "";
        return {
          countries: v.countries === "all" ? "all" : countries,
          collections: collections || defaults.collections,
          category:
            store !== "all" &&
            ((catalog.categories[store] || []).some(
              (c) => c.id === v.category && !c.legacy,
            ) ||
              (store === "google" && v.category === "APPLICATION"))
              ? v.category
              : "",
          as_of:
            typeof v.as_of === "string" && validDate(v.as_of) ? v.as_of : "",
          depth: ["10", "20", "50"].includes(v.depth)
            ? v.depth === "10" &&
              normalizeLayout(v.layout) === "rows" &&
              v.display_version !== 2
              ? "20"
              : v.depth
            : defaults.depth,
          layout: normalizeLayout(v.layout),
        };
      }
    } catch {}
    return { ...defaults };
  };
  const persist = (store) => {
    try {
      const all = JSON.parse(
        localStorage.getItem("appmon-global-chart-filters") || "{}",
      );
      all[store] = { ...filters, display_version: 2 };
      localStorage.setItem("appmon-global-chart-filters", JSON.stringify(all));
    } catch {}
  };
  return async function render(rev) {
    const store = market().store;
    const catalog = await selectors.ready();
    if (rev !== currentRevision()) return;
    if (lastStore !== store) {
      filters = restore(store, catalog);
      offset = 0;
      lastStore = store;
    }
    if (filters.as_of && filters.as_of > new Date().toLocaleDateString("sv-SE"))
      throw Error("观察日期不能晚于今天");
    const at = filters.as_of
      ? Math.min(
          Date.parse(filters.as_of + "T23:59:59") / 1000,
          Date.now() / 1000,
        )
      : null;
    const data = await api(
      "/charts/global?" + qs({ store, ...filters, as_of: at, offset }),
    );
    if (rev !== currentRevision()) return;
    const selected = new Set(data.selected_codes),
      definitions = selectors.chartDefinitions(
        store === "all" ? "google" : store,
      ),
      types = new Set(filters.collections.split(","));
    const detailUrl = (c, b) =>
      "#chart?" +
      qs({
        store: c.store || store,
        country: c.country,
        language: c.language,
        collection: b.collection,
        category: filters.category,
        as_of: at,
      });
    const boardTitle = (c, b, label) =>
      b.unsupported
        ? `<span class="chart-title-label" tabindex="0" title="${esc(boardMeta(b))}" aria-label="${esc(label + "，" + boardMeta(b))}">${esc(label)}</span>`
        : `<a class="chart-title-link" href="${esc(detailUrl(c, b))}" title="${esc(c.name + " " + b.definition.name + "：查看完整榜单 · " + boardMeta(b))}" aria-label="${esc(c.name + " " + b.definition.name + "：查看完整榜单，" + boardMeta(b))}"><span class="chart-title-label">${esc(label)}</span><svg class="chart-title-arrow" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false"><path d="m6 3 5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>`;
    const storeName = (c) =>
      c.store === "apple" ? "App Store" : "Google Play";
    const boardState = (b) =>
      b.unsupported
        ? "不支持"
        : b.quality === "insufficient_history"
          ? "历史不足"
          : b.last_error
            ? "采集异常"
            : b.lease_until > Date.now() / 1000
              ? "采集中"
              : b.fresh
                ? ""
                : b.observed_at
                  ? "待更新"
                  : "未采集";
    const boardMeta = (b) =>
      [
        boardState(b) || "有效观察",
        b.observed_at ? `更新时间：${stamp(b.observed_at)}` : "尚无成功采样",
        typeof b.returned_count === "number"
          ? `返回 ${b.returned_count} 条`
          : "",
        b.last_error,
        b.reason,
      ]
        .filter(Boolean)
        .join(" · ");
    const rankedItems = (b) =>
      [...b.items].sort((a, b) => a.position - b.position);
    const applications = (c, b) =>
      b.items.length
        ? `<ol class="global-chart-apps" role="list" aria-label="${esc(c.name + " " + b.definition.name)}">${rankedItems(
            b,
          )
            .map(
              (r) =>
                `<li class="global-ranked-app" value="${Number(r.position)}" style="--rank-position:${Number(r.position)}"><span class="global-position" aria-hidden="true">${Number(r.position)}</span>${appLink(
                  c.store || store,
                  r.app_id,
                  r.name,
                  r.icon,
                  c.country,
                  c.language,
                )}</li>`,
            )
            .join("")}</ol>`
        : `<div class="global-chart-empty"><strong>暂无数据</strong></div>`;
    const cards = data.countries
      .map(
        (c) =>
          `<section class="panel global-country" style="--chart-slots:${Math.max(1, ...c.boards.flatMap((b) => b.items.map((r) => Number(r.position))))}"><div class="panel-heading"><h2 class="global-country-title"><img class="country-flag" src="/flags/${esc(c.country)}.svg" alt="">${esc(c.name)}${store === "all" ? `<span class="sub">${storeName(c)}</span>` : ""}</h2><span class="sub">${esc(selectors.languageName(c.language))} <small>${esc(c.language)}</small></span></div><div class="global-lists" role="region" aria-label="${esc(c.name)}排行榜" tabindex="0">${c.boards
            .map(
              (b) =>
                `<section class="global-chart"><div class="global-chart-summary" title="${esc(boardMeta(b))}"><h3>${boardTitle(c, b, b.definition.name + (b.definition.mode === "derived" ? "（派生）" : ""))}</h3>${boardState(b) ? `<span class="global-chart-status" data-error="${Boolean(b.last_error)}">${esc(boardState(b))}</span>` : ""}</div><div class="global-chart-head" aria-hidden="true"><span>排名</span><span>应用</span></div>${applications(c, b)}</section>`,
            )
            .join("")}</div></section>`,
      )
      .join("");
    if (
      !mount(
        `${filterBar({
          id: "global-chart-form",
          values: filters,
          submit: "查看各国榜单",
          fields: [
            {
              html: `<div class="country-field"><span id="global-countries-label">国家 / 地区</span><button type="button" id="global-countries-trigger" class="picker-trigger secondary" aria-haspopup="dialog" aria-expanded="false" aria-labelledby="global-countries-label global-countries-caption"><span id="global-countries-caption">已选 ${selected.size} 个地区</span><span aria-hidden="true">⌄</span></button></div><div class="chart-type-field"><span id="global-types-label">榜单类型</span><button type="button" id="global-types-trigger" class="picker-trigger secondary" aria-expanded="false" aria-controls="global-types-options" aria-labelledby="global-types-label global-types-caption"><span id="global-types-caption">已选 ${types.size} 种榜单</span><span aria-hidden="true">⌄</span></button><div id="global-types-options" class="filter-popover" role="group" aria-label="榜单类型" hidden>${definitions.map((d) => `<label class="check-label" title="${esc(d.note || d.name)}"><input type="checkbox" name="collection" value="${d.id}" ${types.has(d.id) ? "checked" : ""}>${esc(d.name)}</label>`).join("")}</div></div>`,
            },
            {
              html: selectors.categoryField(
                store,
                filters.category,
                "global-category",
                "chart",
              ),
            },
            { type: "date", label: "观察日期（本地时区）", name: "as_of" },
            {
              type: "select",
              label: "每榜显示",
              name: "depth",
              options: [10, 20, 50].map((n) => [n, "Top " + n]),
            },
            {
              type: "select",
              label: "布局",
              name: "layout",
              options: [
                ["rows", "横向排列"],
                ["columns", "纵向排列"],
              ],
            },
          ],
          actions:
            '<button type="button" id="global-configure" class="secondary">配置所选范围采集</button><a class="button secondary" href="#collection">查看采集进度</a>',
        })}<div id="global-plan-preview"></div>${pagination({
          id: "global-pages",
          unit: "国",
          offset,
          limit: data.page_size,
          total: data.total_countries,
          hasMore: data.has_more,
          prevId: "global-prev",
          nextId: "global-next",
        })}<div id="global-rankings"><div class="global-board-grid" data-layout="${filters.layout}" style="--chart-display-depth:${Number(filters.depth)};--chart-count:${types.size}" role="region" aria-label="各国排行榜" tabindex="0">${cards}</div></div>`,
        rev,
        `各国独立排名 · 本页有效 ${data.fresh_boards}/${data.requested_boards} 榜，已观察 ${data.observed_boards} 榜`,
      )
    )
      return;
    const chartHover = bindGlobalChartHover({
      root: $("#global-rankings"),
      api,
      esc,
      scope: {
        countries: data.selected_codes.join(","),
        collections: filters.collections,
        category: filters.category,
        depth: filters.depth,
        ...(at == null ? {} : { as_of: at }),
      },
    });
    let resetRequested = false;
    $("#global-chart-form").addEventListener("filter-reset", () => {
      resetRequested = true;
      selected.clear();
      data.selected_codes.forEach((code) => selected.add(code));
      $("#global-chart-form")
        .querySelectorAll('[name="collection"]')
        .forEach(
          (el) =>
            (el.checked = defaults.collections.split(",").includes(el.value)),
        );
    });
    const getFilters = () => {
      const form = $("#global-chart-form");
      const countries = [...selected],
        collections = [
          ...form.querySelectorAll('[name="collection"]:checked'),
        ].map((e) => e.value);
      if (!countries.length || !collections.length)
        throw Error("至少选择一个国家和一种榜单");
      if (!validDate(form.elements.as_of.value))
        throw Error("请选择有效日期，且不能晚于今天");
      return {
        countries: [
          ...data.selected_codes.filter((c) => countries.includes(c)),
          ...countries.filter((c) => !data.selected_codes.includes(c)),
        ].join(","),
        collections: collections.join(","),
        category: $("#global-category").value,
        as_of: form.elements.as_of.value,
        depth: form.elements.depth.value,
        layout: form.elements.layout.value,
      };
    };
    bindForm("#global-chart-form", async () => {
      filters = resetRequested ? { ...defaults } : getFilters();
      persist(store);
      offset = 0;
      await render(nextRevision());
    });
    let previewRevision = 0;
    const invalidatePreview = () => {
      previewRevision++;
      $("#global-plan-preview").innerHTML = "";
    };
    $("#global-chart-form").addEventListener("change", invalidatePreview);
    $("#global-chart-form").addEventListener("input", invalidatePreview);
    $("#global-chart-form").elements.layout.addEventListener(
      "change",
      (event) => {
        filters.layout = normalizeLayout(event.target.value);
        chartHover.clear();
        $("#global-rankings .global-board-grid").dataset.layout =
          filters.layout;
        persist(store);
      },
    );
    const countryTrigger = $("#global-countries-trigger");
    countryTrigger.onclick = () =>
      selectors
        .chooseCountries({
          selected: [...selected],
          allowed: data.available_countries.map((c) => c.code),
          trigger: countryTrigger,
          onApply: (codes) => {
            selected.clear();
            codes.forEach((c) => selected.add(c));
            $("#global-countries-caption").textContent =
              `已选 ${selected.size} 个地区`;
            invalidatePreview();
          },
        })
        .catch((e) => toast(e.message));
    const typeTrigger = $("#global-types-trigger"),
      typeOptions = $("#global-types-options");
    const closeTypes = () => {
      typeOptions.hidden = true;
      typeTrigger.setAttribute("aria-expanded", "false");
    };
    typeTrigger.onclick = () => {
      typeOptions.hidden = !typeOptions.hidden;
      typeTrigger.setAttribute("aria-expanded", String(!typeOptions.hidden));
    };
    typeOptions.onchange = () => {
      $("#global-types-caption").textContent =
        `已选 ${typeOptions.querySelectorAll("input:checked").length} 种榜单`;
    };
    const typeField = typeTrigger.parentElement;
    typeField.addEventListener("focusout", () => {
      setTimeout(() => {
        if (!typeField.contains(document.activeElement)) closeTypes();
      }, 0);
    });
    typeField.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeTypes();
        typeTrigger.focus();
        e.preventDefault();
      }
    });
    // The form is replaced on navigation; no document listeners accumulate.
    $("#global-chart-form").addEventListener("click", (e) => {
      if (!typeField.contains(e.target)) closeTypes();
    });
    bindPagination(document, "global-pages", (start) => {
      offset = start;
      return render(nextRevision());
    });
    $("#global-configure").onclick = async () => {
      const requestRevision = ++previewRevision;
      try {
        const spec = { ...getFilters(), store, depth: 100, refresh_hours: 24 };
        const preview = await api("/charts/global/preview", spec);
        if (rev !== currentRevision() || requestRevision !== previewRevision)
          return;
        $("#global-plan-preview").innerHTML = panel(
          "采集计划预览",
          `<p>${preview.countries}个国家，${preview.total}个独立基榜目标，新目标采集Top100，每24小时更新；已有目标保留原设置。${esc(preview.note)}${preview.unsupported.length ? ` 跳过 ${preview.unsupported.length} 个不支持的组合。` : ""}</p><button id="global-plan-confirm">确认创建采集计划</button>`,
        );
        $("#global-plan-confirm").onclick = async () => {
          if (rev !== currentRevision() || requestRevision !== previewRevision)
            return;
          const button = $("#global-plan-confirm");
          button.disabled = true;
          try {
            const result = await api("/charts/global/plan", spec);
            if (
              rev === currentRevision() &&
              requestRevision === previewRevision
            )
              $("#global-plan-preview").innerHTML =
                `<p class="callout">计划 #${result.id} 已入队，共${result.total}个目标。可在采集中心查看实际成功和失败情况。</p>`;
          } catch (e) {
            toast(e.message);
            button.disabled = false;
          }
        };
      } catch (e) {
        toast(e.message);
      }
    };
  };
}
