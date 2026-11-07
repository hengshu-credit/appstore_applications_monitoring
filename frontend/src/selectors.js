const pickerEscape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const searchKey = (value) =>
  String(value ?? "")
    .toLocaleLowerCase()
    .replace(/[\s_-]+/g, "");
export function matchesCountry(country, query) {
  return [country.name, country.english, country.code, ...country.aliases].some(
    (v) => searchKey(v).includes(searchKey(query)),
  );
}
export function matchesCategory(category, query) {
  return [
    category.name,
    category.english,
    category.id,
    ...category.aliases,
  ].some((v) => searchKey(v).includes(searchKey(query)));
}

export function createSelectors({
  api,
  getMarket,
  onError,
  onCatalog,
  win = window,
}) {
  const doc = win.document;
  let catalog,
    loading,
    countryRegion = "",
    categoryState,
    countrySelection,
    countrySingle;
  const countsCache = new Map();
  const countryDialog = doc.createElement("dialog");
  countryDialog.id = "country-picker";
  countryDialog.className = "selection-dialog";
  countryDialog.setAttribute("aria-labelledby", "country-picker-title");
  countryDialog.innerHTML = `<form method="dialog" class="picker-heading"><h2 id="country-picker-title">选择国家 / 地区</h2><button class="secondary small" aria-label="关闭国家选择">关闭</button></form><input class="picker-search" type="search" placeholder="搜索国家/地区" aria-label="搜索国家或地区"><div class="picker-bulk" hidden><button type="button" class="secondary" data-country-action="all">全部国家 / 地区</button><button type="button" class="secondary" data-country-action="clear">清空选择</button><span class="sub" id="country-selection-count" role="status"></span></div><div class="picker-scroll" id="country-options"></div><p class="sub picker-note">地区按地理位置分组；热门地区为快捷入口。Apple 暂未支持的地区不可选。</p><div class="picker-footer" hidden><button type="button" class="secondary" data-country-action="cancel">取消</button><button type="button" data-country-action="apply">应用所选地区</button></div>`;
  doc.body.append(countryDialog);
  const categoryDialog = doc.createElement("dialog");
  categoryDialog.id = "category-picker";
  categoryDialog.className = "selection-dialog";
  categoryDialog.setAttribute("aria-labelledby", "category-picker-title");
  categoryDialog.innerHTML = `<form method="dialog" class="picker-heading"><h2 id="category-picker-title">选择应用分类</h2><button class="secondary small" aria-label="关闭分类选择">关闭</button></form><div class="category-tabs" role="tablist" aria-label="分类类型"><button type="button" role="tab" data-group="apps">应用</button><button type="button" role="tab" data-group="games">游戏</button><button type="button" class="secondary total-category" data-select-category="">总榜 / 全部</button></div><input type="search" class="picker-search" placeholder="搜索分类" aria-label="搜索商店分类"><div id="category-options" class="category-grid" role="tabpanel"></div><p class="sub picker-note">使用当前商店的官方分类，切换商店后重新选择。</p>`;
  doc.body.append(categoryDialog);

  async function ready() {
    if (catalog) return catalog;
    if (!loading)
      loading = api("/catalog")
        .then((value) => {
          catalog = value;
          onCatalog?.(value);
          syncCountry();
          return value;
        })
        .catch((error) => {
          loading = null;
          throw error;
        });
    return loading;
  }
  function countryName(code) {
    return (
      catalog?.countries.find((c) => c.code === code)?.name ||
      code?.toUpperCase() ||
      "选择地区"
    );
  }
  function categoryName(store, id) {
    if (!id) return "全部分类";
    if (id === "__apps__" || id === "APPLICATION") return "全部应用";
    if (id === "__unknown__") return "未分类";
    return catalog?.categories[store]?.find((c) => c.id === id)?.name || id;
  }
  function syncCountry() {
    const code = getMarket().country;
    const value = doc.querySelector("#country-value");
    if (value)
      value.innerHTML = `${catalog?.countries.some((c) => c.code === code) ? `<img class="country-flag" src="/flags/${pickerEscape(code)}.svg" alt="">` : ""}<span>${pickerEscape(countryName(code))}</span>`;
  }
  function countryButton(c) {
    const disabled = countrySingle
      ? !countrySingle.allowed.has(c.code)
      : countrySelection
        ? !countrySelection.allowed.has(c.code)
        : getMarket().store === "apple" && !c.apple_supported;
    const selected = countrySingle
      ? countrySingle.value === c.code
      : countrySelection
        ? countrySelection.selected.has(c.code)
        : getMarket().country === c.code;
    return `<button type="button" class="country-option" data-country-code="${c.code}" aria-pressed="${selected}" ${disabled ? "disabled" : ""} title="${pickerEscape(c.name + " · " + c.english + " (" + c.code.toUpperCase() + ")" + (disabled ? " · 所选商店暂不支持此地区" : ""))}"><img class="country-flag" src="/flags/${c.code}.svg" alt="" loading="lazy"><span>${pickerEscape(c.name)}</span><small>${c.code.toUpperCase()}</small>${countrySelection ? '<span class="choice-check" aria-hidden="true">✓</span>' : ""}</button>`;
  }
  function selectionHeading(name, rows) {
    const available = rows.filter((c) => countrySelection?.allowed.has(c.code));
    const complete =
      available.length &&
      available.every((c) => countrySelection.selected.has(c.code));
    return `<h3>${pickerEscape(name)}</h3>${countrySelection ? `<button type="button" class="secondary picker-group-toggle" data-country-group="${available.map((c) => c.code).join(",")}" ${available.length ? "" : "disabled"} aria-label="${complete ? "取消选择" : "全选"}${pickerEscape(name)}">${complete ? "清空此地区" : "全选此地区"}</button>` : ""}`;
  }
  function renderCountries() {
    const query = countryDialog.querySelector("input").value;
    let html = "";
    if (query.trim()) {
      const matches = catalog.countries.filter((c) => matchesCountry(c, query));
      html = `${countrySelection ? `<div class="picker-section-heading">${selectionHeading("搜索结果", matches)}</div>` : ""}<div class="country-grid">${matches.map(countryButton).join("")}</div>${matches.length ? "" : '<p class="empty">未找到匹配地区</p>'}`;
    } else {
      const popular = catalog.popular
        .map((code) => catalog.countries.find((c) => c.code === code))
        .filter(Boolean);
      html = `<div class="picker-section-heading">${selectionHeading("热门地区", popular)}</div><div class="country-grid">${popular.map(countryButton).join("")}</div>`;
      for (const continent of catalog.continents) {
        const countries = catalog.countries.filter(
          (c) =>
            c.continent === continent.id &&
            (continent.id !== "asia" ||
              !countryRegion ||
              c.region === countryRegion),
        );
        const name =
          continent.id === "asia" && countryRegion
            ? catalog.asia_regions.find((r) => r.id === countryRegion)?.name
            : continent.name;
        html += `<section><div class="picker-section-heading">${selectionHeading(name, countries)}${continent.id === "asia" ? `<div class="region-tabs" role="tablist" aria-label="亚洲分区">${[{ id: "", name: "全亚洲" }, ...catalog.asia_regions].map((r) => `<button type="button" role="tab" aria-selected="${r.id === countryRegion}" data-region="${r.id}">${r.name}</button>`).join("")}</div>` : ""}</div><div class="country-grid">${countries.map(countryButton).join("")}</div></section>`;
      }
    }
    countryDialog.querySelector("#country-options").innerHTML =
      (countrySingle?.allowAll
        ? `<button type="button" class="country-option country-all" data-country-code="" aria-pressed="${!countrySingle.value}">全部国家 / 地区</button>`
        : "") + html;
    if (countrySelection) {
      countryDialog.querySelector("#country-selection-count").textContent =
        `已选 ${countrySelection.selected.size} / ${countrySelection.allowed.size} 个地区`;
      countryDialog.querySelector('[data-country-action="apply"]').disabled =
        !countrySelection.selected.size;
    }
  }
  async function chooseCountries({ selected, allowed, onApply, trigger }) {
    await ready();
    countrySingle = null;
    const available = new Set(allowed);
    countrySelection = {
      selected: new Set(selected.filter((c) => available.has(c))),
      allowed: available,
      onApply,
      trigger,
    };
    openCountries();
  }
  async function chooseCountry({ value, allowed, allowAll, onApply, trigger }) {
    await ready();
    countrySelection = null;
    countrySingle = {
      value,
      allowed: new Set(allowed),
      allowAll,
      onApply,
      trigger,
    };
    openCountries();
  }
  function openCountries() {
    countryDialog.querySelector("input").value = "";
    countryRegion = "";
    countryDialog.querySelector(".picker-bulk").hidden = !countrySelection;
    countryDialog.querySelector(".picker-footer").hidden = !countrySelection;
    countryDialog.classList.toggle("country-multiple", !!countrySelection);
    countryDialog.querySelector(".picker-note").textContent = countrySelection
      ? "地区按地理位置分组；支持全选、区域全选和搜索后批量选择。确认后应用筛选。"
      : "地区按地理位置分组；热门地区为快捷入口。所选商店暂未支持的地区不可选。";
    renderCountries();
    countryDialog.showModal();
    (
      countrySingle?.trigger ||
      countrySelection?.trigger ||
      doc.querySelector("#country-trigger")
    )?.setAttribute("aria-expanded", "true");
    countryDialog.querySelector("input").focus();
  }
  doc.querySelector("#country-trigger").addEventListener("click", async () => {
    try {
      await ready();
      countrySelection = null;
      countrySingle = null;
      openCountries();
    } catch (e) {
      onError?.(e.message);
    }
  });
  countryDialog
    .querySelector("input")
    .addEventListener("input", renderCountries);
  countryDialog.addEventListener("click", (event) => {
    const action = event.target.closest("[data-country-action]")?.dataset
      .countryAction;
    if (action && countrySelection) {
      if (action === "cancel") return countryDialog.close();
      if (action === "apply") {
        if (!countrySelection.selected.size) return;
        countrySelection.onApply([...countrySelection.selected]);
        return countryDialog.close();
      }
      countrySelection.selected = new Set(
        action === "all" ? countrySelection.allowed : [],
      );
      renderCountries();
      return;
    }
    const group = event.target.closest("[data-country-group]");
    if (group && countrySelection && !group.disabled) {
      const codes = group.dataset.countryGroup.split(",");
      const complete = codes.every((c) => countrySelection.selected.has(c));
      codes.forEach((c) =>
        complete
          ? countrySelection.selected.delete(c)
          : countrySelection.selected.add(c),
      );
      const key = group.dataset.countryGroup;
      renderCountries();
      countryDialog.querySelector(`[data-country-group="${key}"]`)?.focus();
      return;
    }
    const region = event.target.closest("[data-region]");
    if (region) {
      countryRegion = region.dataset.region;
      renderCountries();
      countryDialog.querySelector(`[data-region="${countryRegion}"]`)?.focus();
      return;
    }
    const option = event.target.closest("[data-country-code]");
    if (option && !option.disabled) {
      if (countrySingle) {
        countrySingle.onApply(option.dataset.countryCode);
        countryDialog.close();
        return;
      }
      if (countrySelection) {
        const code = option.dataset.countryCode;
        if (countrySelection.selected.has(code))
          countrySelection.selected.delete(code);
        else countrySelection.selected.add(code);
        const scroll = countryDialog.querySelector(".picker-scroll");
        const top = scroll.scrollTop;
        renderCountries();
        countryDialog
          .querySelector(`[data-country-code="${code}"]`)
          ?.focus({ preventScroll: true });
        scroll.scrollTop = top;
        return;
      }
      doc.querySelector("#global-country").value = option.dataset.countryCode;
      syncCountry();
      countryDialog.close();
      doc
        .querySelector("#global-country")
        .dispatchEvent(new win.Event("change", { bubbles: true }));
    }
  });
  countryDialog.addEventListener("close", () => {
    const trigger =
      countrySingle?.trigger ||
      countrySelection?.trigger ||
      doc.querySelector("#country-trigger");
    trigger?.setAttribute("aria-expanded", "false");
    if (trigger?.isConnected) trigger.focus();
    countrySelection = null;
    countrySingle = null;
  });

  function categoryField(
    store,
    value = "",
    id = "category-value",
    purpose = "pool",
  ) {
    if (store === "all")
      return `<div class="category-field"><span>应用分类</span><input type="hidden" id="${id}" name="category" value=""><button type="button" class="picker-trigger secondary" disabled title="选择单个商店可筛选官方分类">全部分类（多商店）</button></div>`;
    return `<div class="category-field"><span id="${id}-label">应用分类</span><input type="hidden" id="${id}" name="category" value="${pickerEscape(value)}"><button type="button" class="picker-trigger secondary category-trigger" data-category-input="${id}" data-category-store="${store}" data-category-purpose="${purpose}" aria-haspopup="dialog" aria-expanded="false" aria-labelledby="${id}-label ${id}-caption"><span id="${id}-caption">${pickerEscape(categoryName(store, value))}</span><span aria-hidden="true">⌄</span></button></div>`;
  }
  function renderCategories() {
    const state = categoryState;
    const query = categoryDialog.querySelector("input").value;
    const rows = catalog.categories[state.store].filter(
      (c) =>
        (query ? matchesCategory(c, query) : c.group === state.group) &&
        !(state.purpose === "chart" && c.legacy),
    );
    categoryDialog
      .querySelectorAll("[data-group]")
      .forEach((button) =>
        button.setAttribute(
          "aria-selected",
          String(button.dataset.group === state.group),
        ),
      );
    const allValue =
      state.purpose === "chart"
        ? state.store === "google"
          ? "APPLICATION"
          : ""
        : "__apps__";
    categoryDialog.querySelector("#category-options").innerHTML =
      (!query && state.group === "apps"
        ? `<button type="button" data-select-category="${allValue}" aria-pressed="${state.value === allValue}">${state.purpose === "chart" && state.store === "apple" ? "全部（总榜）" : "全部应用"}</button>`
        : "") +
      rows
        .map(
          (c) =>
            `<button type="button" data-select-category="${c.id}" aria-pressed="${state.value === c.id}" title="${pickerEscape(c.english + " · " + c.id)}">${c.parent_id ? "" : c.group === "games" ? "全部" : ""}${pickerEscape(c.name)}</button>`,
        )
        .join("") +
      (rows.length ? "" : '<p class="empty">未找到匹配分类</p>');
  }
  doc.addEventListener("click", async (event) => {
    const trigger = event.target.closest(".category-trigger");
    if (!trigger) return;
    try {
      await ready();
      const input = doc.getElementById(trigger.dataset.categoryInput);
      const store = trigger.dataset.categoryStore || getMarket().store;
      categoryState = {
        store,
        purpose: trigger.dataset.categoryPurpose,
        input,
        trigger,
        value: input.value,
        group:
          catalog.categories[store].find((c) => c.id === input.value)?.group ||
          "apps",
      };
      categoryDialog.querySelector("h2").textContent =
        (store === "apple" ? "App Store" : "Google Play") + " 分类";
      categoryDialog.querySelector("input").value = "";
      renderCategories();
      trigger.setAttribute("aria-expanded", "true");
      categoryDialog.showModal();
      categoryDialog.querySelector("input").focus();
    } catch (e) {
      onError?.(e.message);
    }
  });
  categoryDialog
    .querySelector("input")
    .addEventListener("input", renderCategories);
  categoryDialog.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-group]");
    if (tab) {
      categoryState.group = tab.dataset.group;
      categoryDialog.querySelector("input").value = "";
      renderCategories();
      return;
    }
    const option = event.target.closest("[data-select-category]");
    if (option) {
      categoryState.input.value = option.dataset.selectCategory;
      doc.getElementById(categoryState.input.id + "-caption").textContent =
        categoryName(categoryState.store, option.dataset.selectCategory);
      categoryState.input.dispatchEvent(
        new win.Event("change", { bubbles: true }),
      );
      categoryDialog.close();
    }
  });
  categoryDialog.addEventListener("close", () =>
    categoryState?.trigger.setAttribute("aria-expanded", "false"),
  );

  function categorySidebar(store, selected = "") {
    if (store === "all")
      return `<section class="panel category-sidebar"><h2>应用商店</h2><p class="sub">同时查看 Google Play 与 App Store，按商店交错分页。分类筛选需先选择单店。</p></section>`;
    return `<section class="panel category-sidebar"><h2>应用分类</h2><input type="search" id="category-tree-search" placeholder="搜索分类" aria-label="搜索应用池分类"><p class="smallprint" id="category-count-scope">按商店统计已知应用池 · 数量加载中</p><div id="category-tree"></div></section>`;
  }
  async function mountSidebar(store, selected, onSelect) {
    const container = doc.querySelector("#category-tree");
    if (!container) return;
    const input = doc.querySelector("#category-tree-search");
    let stats = countsCache.get(store);
    const count = (id) =>
      stats
        ? Number(
            id === ""
              ? stats.total
              : id === "__apps__"
                ? stats.apps
                : id === "__unknown__"
                  ? stats.unknown
                  : stats.counts[id] || 0,
          ).toLocaleString("zh-CN")
        : "…";
    const item = (id, name, nested = false) =>
      `<button type="button" class="category-tree-item ${nested ? "nested" : ""}" data-pool-category="${id}" aria-pressed="${selected === id}"><span>${pickerEscape(name)}</span><small>${count(id)}</small></button>`;
    function draw() {
      if (!container.isConnected) return;
      const query = input.value;
      const list = catalog.categories[store].filter((c) =>
        matchesCategory(c, query),
      );
      let html = query
        ? ""
        : item("", "全部分类") + item("__apps__", "全部应用");
      for (const group of ["apps", "games"]) {
        const rows = list.filter((c) => c.group === group);
        html += `<details open><summary>${group === "apps" ? "应用" : "游戏"}</summary>${rows.map((c) => item(c.id, c.name, !!c.parent_id)).join("")}</details>`;
      }
      if (!query) html += item("__unknown__", "未分类 / 未映射");
      container.innerHTML = html;
    }
    input.addEventListener("input", draw);
    container.addEventListener("click", (event) => {
      const button = event.target.closest("[data-pool-category]");
      if (button) onSelect(button.dataset.poolCategory);
    });
    draw();
    try {
      stats = await api("/categories/counts?store=" + store);
      countsCache.set(store, stats);
      if (container.isConnected) {
        draw();
        doc.querySelector("#category-count-scope").textContent =
          "已知应用池 · " +
          new Date(stats.observed_at * 1000).toLocaleTimeString("zh-CN", {
            hour12: false,
          }) +
          " 更新";
      }
    } catch {
      if (container.isConnected)
        doc.querySelector("#category-count-scope").textContent =
          "数量暂不可用，仍可选择分类";
    }
  }
  return {
    supportsMarket: (store, country) =>
      !!catalog?.countries.some(
        (c) => c.code === country && (store !== "apple" || c.apple_supported),
      ),
    chartDefinitions: (store) => catalog?.charts?.[store] || [],
    ready,
    chooseCountries,
    chooseCountry,
    syncCountry,
    countryName,
    languageName: (code) =>
      catalog?.locales?.languages?.[code] ||
      catalog?.locales?.languages?.[code.split("-")[0]] ||
      code,
    categoryName,
    categoryField,
    categorySidebar,
    mountSidebar,
  };
}
