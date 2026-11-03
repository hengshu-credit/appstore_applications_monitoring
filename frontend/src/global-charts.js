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
      depth: "10",
    },
    offset = 0,
    lastStore = "";
  const qs = (o) =>
    new URLSearchParams(
      Object.entries(o).filter(([, v]) => v !== "" && v != null),
    ).toString();
  return async function render(rev) {
    const store = market().store;
    if (store === "all") {
      mount(
        '<p class="callout">请选择一个商店查看全球排行榜。Google Play 与 App Store 的排名独立展示。</p>',
        rev,
      );
      return;
    }
    if (lastStore !== store) {
      filters.category = "";
      offset = 0;
      lastStore = store;
    }
    const at = filters.as_of
      ? Math.min(
          Date.parse(filters.as_of + "T23:59:59") / 1000,
          Date.now() / 1000,
        )
      : null;
    const data = await api(
      "/charts/global?" + qs({ store, ...filters, as_of: at, offset }),
    );
    const selected = new Set(data.selected_codes),
      definitions = selectors.chartDefinitions(store),
      types = new Set(filters.collections.split(","));
    const cards = data.countries
      .map(
        (c) =>
          `<section class="panel global-country"><div class="panel-heading"><h2>${esc(c.name)} <span class="sub">${esc(c.country.toUpperCase())}</span></h2><span class="sub">${esc(c.language)}</span></div><div class="global-lists">${c.boards
            .map(
              (b) =>
                `<section class="global-chart"><h3>${esc(b.definition.name)}${b.definition.mode === "derived" ? "（派生）" : ""}</h3><p class="smallprint">${b.unsupported ? "此组合不支持" : b.fresh ? "有效观察" : b.observed_at ? "历史数据待更新" : "尚未采集"} · ${stamp(b.observed_at)}<br>返回 ${b.returned_count} 条${b.last_error ? " · " + esc(b.last_error) : ""}</p>${b.reason ? `<p class="smallprint">${esc(b.reason)}</p>` : ""}${table(
                  ["名次", "应用"],
                  b.items.map((r) => [
                    r.position,
                    appLink(
                      store,
                      r.app_id,
                      r.name,
                      r.icon,
                      c.country,
                      c.language,
                    ),
                  ]),
                )}</section>`,
            )
            .join("")}</div></section>`,
      )
      .join("");
    if (
      !mount(
        `<p class="lead">在同一页面并列查看各国免费、付费、畅销及其他榜单。</p><form id="global-chart-form" class="toolbar"><details class="global-country-picker"><summary>选择国家 / 地区（${data.total_countries}）</summary><input id="global-country-search" type="search" placeholder="搜索国家、英文或代码" aria-label="搜索全球榜单国家"><div class="actions"><button type="button" id="global-country-all" class="secondary">全选</button><button type="button" id="global-country-clear" class="secondary">清空</button></div><div class="global-country-options">${data.available_countries.map((c) => `<label data-country-option="${esc((c.name + " " + c.english + " " + c.code).toLowerCase())}"><input type="checkbox" name="country" value="${esc(c.code)}" ${selected.has(c.code) ? "checked" : ""}>${esc(c.name)} (${esc(c.code.toUpperCase())})</label>`).join("")}</div></details><details><summary>榜单类型（${types.size}）</summary>${definitions.map((d) => `<label><input type="checkbox" name="collection" value="${d.id}" ${types.has(d.id) ? "checked" : ""}>${esc(d.name)}</label>`).join("")}</details>${selectors.categoryField(store, filters.category, "global-category", "chart")}${field("观察日期（本地时区）", "as_of", filters.as_of, "date")}${select(
          "每榜显示",
          "depth",
          [
            [10, "Top 10"],
            [20, "Top 20"],
            [50, "Top 50"],
          ],
          filters.depth,
        )}<button type="submit">查看各国榜单</button><button type="button" id="global-configure" class="secondary">配置所选范围采集</button></form><div id="global-plan-preview"></div><p class="callout">${esc(data.note)} 本页有效 ${data.fresh_boards} / ${data.requested_boards} 个榜单，已有观察 ${data.observed_boards} 个。</p><div class="toolbar"><button id="global-prev" class="secondary" ${offset ? "" : "disabled"}>上一组国家</button><span>第 ${offset + 1}–${Math.min(offset + data.countries.length, data.total_countries)} 个 / 共 ${data.total_countries} 个国家；每页${data.page_size}个</span><button id="global-next" class="secondary" ${data.has_more ? "" : "disabled"}>下一组国家</button><a class="button secondary" href="#collection">查看采集进度</a></div><div class="global-board-grid">${cards}</div>`,
        rev,
      )
    )
      return;
    const getFilters = () => {
      const form = $("#global-chart-form");
      const countries = [
          ...form.querySelectorAll('[name="country"]:checked'),
        ].map((e) => e.value),
        collections = [
          ...form.querySelectorAll('[name="collection"]:checked'),
        ].map((e) => e.value);
      if (!countries.length || !collections.length)
        throw Error("至少选择一个国家和一种榜单");
      return {
        countries: countries.join(","),
        collections: collections.join(","),
        category: $("#global-category").value,
        as_of: form.elements.as_of.value,
        depth: form.elements.depth.value,
      };
    };
    bindForm("#global-chart-form", async () => {
      filters = getFilters();
      offset = 0;
      await render(nextRevision());
    });
    $("#global-country-search").addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase();
      document
        .querySelectorAll("[data-country-option]")
        .forEach((el) => (el.hidden = !el.dataset.countryOption.includes(q)));
    });
    for (const [id, checked] of [
      ["global-country-all", true],
      ["global-country-clear", false],
    ])
      $("#" + id).onclick = () =>
        document
          .querySelectorAll('[name="country"]')
          .forEach((el) => (el.checked = checked));
    $("#global-prev").onclick = () => {
      offset = Math.max(0, offset - data.page_size);
      render(nextRevision()).catch((e) => toast(e.message));
    };
    $("#global-next").onclick = () => {
      offset += data.page_size;
      render(nextRevision()).catch((e) => toast(e.message));
    };
    $("#global-configure").onclick = async () => {
      try {
        const spec = { ...getFilters(), store, depth: 100, refresh_hours: 24 };
        const preview = await api("/charts/global/preview", spec);
        if (rev !== currentRevision()) return;
        $("#global-plan-preview").innerHTML = panel(
          "采集计划预览",
          `<p>${preview.countries}个国家，${preview.total}个独立基榜目标，采集Top100，每24小时更新。${esc(preview.note)}${preview.unsupported.length ? ` 跳过 ${preview.unsupported.length} 个不支持的组合。` : ""}</p><button id="global-plan-confirm">确认创建采集计划</button>`,
        );
        $("#global-plan-confirm").onclick = async () => {
          const button = $("#global-plan-confirm");
          button.disabled = true;
          try {
            const result = await api("/charts/global/plan", spec);
            if (rev === currentRevision())
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
