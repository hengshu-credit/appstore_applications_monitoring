const escapeFilter = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );

let catalog = {};
export function setFilterCatalog(value) {
  catalog = value || {};
  const names = catalog.locales?.languages || {};
  const codes = new Set(Object.keys(names));
  for (const country of Object.values(catalog.locales?.countries || {})) {
    for (const store of Object.values(country))
      for (const code of store.options || []) codes.add(code);
  }
  catalog = {
    ...catalog,
    filterLanguages: [...codes]
      .sort()
      .map((code) => [code, names[code] || names[code.split("-")[0]] || code]),
  };
}

function choices(field) {
  if (field.options)
    return field.type === "country"
      ? field.options.map((item) => {
          const row = Array.isArray(item) ? item : [item, item];
          return [
            row[0],
            row[1],
            catalog.countries?.find((c) => c.code === row[0])?.english ||
              row[2] ||
              "",
          ];
        })
      : field.options;
  if (field.type === "store")
    return [
      ["all", "全部商店"],
      ["google", "Google Play"],
      ["apple", "App Store"],
    ];
  if (field.type === "country")
    return [
      ["", "全部国家"],
      ...(catalog.countries || []).map((c) => [c.code, c.name, c.english]),
    ];
  if (field.type === "language")
    return [["", "全部语言"], ...(catalog.filterLanguages || [])];
  return [];
}
function decoration(type, value) {
  if (type === "store" && ["apple", "google"].includes(value))
    return `<img class="store-logo" src="/stores/${value}.svg" alt="">`;
  if (type === "country" && /^[a-z]{2}$/.test(value))
    return `<img class="country-flag" src="/flags/${value}.svg" alt="">`;
  return "";
}
function optionRows(options) {
  const seen = new Set(),
    rows = [];
  for (const item of options) {
    const [value, label, alias] = Array.isArray(item) ? item : [item, item];
    if (value == null || seen.has(String(value))) continue;
    seen.add(String(value));
    rows.push([String(value), String(label ?? value), String(alias || "")]);
  }
  return rows;
}
const optionMarkup = (rows, selected) =>
  rows
    .map(
      ([v, l, a]) =>
        `<option value="${escapeFilter(v)}" data-alias="${escapeFilter(a)}"${String(v) === String(selected) ? " selected" : ""}>${escapeFilter(l)}</option>`,
    )
    .join("");
function caption(type, value, label) {
  const code =
    ["country", "language"].includes(type) && value
      ? `<small>${escapeFilter(type === "country" ? value.toUpperCase() : value)}</small>`
      : "";
  return decoration(type, value) + `<span>${escapeFilter(label)}</span>` + code;
}
export function filterFields(id, fields, values = {}) {
  return fields
    .map((f) => {
      if (f.html !== undefined) return f.html;
      const uid = escapeFilter(id + "-" + f.name),
        label = escapeFilter(f.label),
        name = escapeFilter(f.name),
        type = f.type || "text",
        value = values[f.name] ?? f.value ?? "";
      const rows = optionRows(choices(f)),
        opts = optionMarkup(rows, value);
      if (type === "select")
        return `<label>${label}<select name="${name}">${opts}</select></label>`;
      const popup = `<div class="filter-popup" popover="manual" hidden><div role="listbox" id="${uid}-options" aria-label="${label}"></div></div>`;
      if (["store", "country", "language", "panel"].includes(type)) {
        const selected = rows.find((r) => r[0] === String(value)) ||
          rows[0] || ["", "请选择"];
        return `<div class="filter-control" data-filter-type="${type}"><span id="${uid}-label">${label}</span><select hidden name="${name}">${opts}</select><button class="picker-trigger secondary filter-trigger" type="button" aria-haspopup="listbox" aria-expanded="false" aria-controls="${uid}-options" aria-labelledby="${uid}-label ${uid}-caption"><span id="${uid}-caption" class="filter-caption">${caption(type, selected[0], selected[1])}</span><span aria-hidden="true">⌄</span></button>${popup}</div>`;
      }
      const required = f.required ? " required" : "";
      const attrs = ["min", "max", "step", "maxlength", "placeholder"]
        .filter((k) => f[k] != null)
        .map((k) => ` ${k}="${escapeFilter(f[k])}"`)
        .join("");
      if (type === "date")
        return `<label>${label}<input name="${name}" type="date" value="${escapeFilter(value)}"${required}></label>`;
      const match = f.matchKey
        ? `<input type="hidden" data-filter-match data-free="${escapeFilter(f.freeMode || "contains")}" data-picked="${escapeFilter(f.pickedMode || "exact")}" name="${escapeFilter(f.matchKey)}" value="${escapeFilter(values[f.matchKey] || f.freeMode || "contains")}">`
        : "";
      return `<div class="filter-control ${f.wide ? "wide" : ""}" data-filter-type="suggest" data-filter-source="${escapeFilter(f.source || "")}"><label for="${uid}">${label}</label><input id="${uid}" name="${name}" data-filter-input type="${type === "number" ? "number" : "text"}" role="combobox" autocomplete="off" aria-autocomplete="list" aria-expanded="false" aria-controls="${uid}-options" value="${escapeFilter(value)}"${attrs}${required}>${match}<datalist>${opts}</datalist>${popup}</div>`;
    })
    .join("");
}
export function filterBar({
  id,
  fields,
  values,
  submit = "筛选",
  actions = "",
  after = "",
  className = "",
  resetValues = {},
  resetSubmit = true,
  submitDisabled = false,
}) {
  return `<form id="${escapeFilter(id)}" class="toolbar filter-bar ${escapeFilter(className)}" data-filter-reset="${escapeFilter(JSON.stringify(resetValues))}" data-filter-reset-submit="${resetSubmit}" data-filter-bar>${filterFields(id, fields, values)}<button type="submit" ${submitDisabled ? "disabled" : ""}>${escapeFilter(submit)}</button><button type="button" class="secondary" data-filter-reset-button>重置</button>${actions}${after}</form>`;
}

export function bindFilterBar(form, onApply, onError) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.dataset.applying === "true") return;
    const submit = form.querySelector('button[type="submit"]');
    const reset = form.querySelector("[data-filter-reset-button]");
    if (submit.disabled) return;
    form.dataset.applying = "true";
    submit.disabled = true;
    reset.disabled = true;
    try {
      const values = Object.fromEntries(
        new form.ownerDocument.defaultView.FormData(form),
      );
      await onApply(values, form);
    } catch (error) {
      if (onError) onError(error.message);
      else throw error;
    } finally {
      delete form.dataset.applying;
      submit.disabled = false;
      reset.disabled = false;
    }
  });
}

// One delegated controller also handles forms replaced by pagination or a tab switch.
export function initFilterBars({ doc = document, win = window, suggest } = {}) {
  let active = null,
    timer,
    version = 0,
    cursor = -1,
    visible = [];
  const control = (r) => r.querySelector("[data-filter-input],.filter-trigger");
  function close() {
    ++version;
    win.clearTimeout(timer);
    if (!active) return;
    const popup = active.querySelector(".filter-popup");
    if (!popup.hidden && popup.isConnected) popup.hidePopover?.();
    popup.hidden = true;
    control(active).setAttribute("aria-expanded", "false");
    control(active).removeAttribute("aria-activedescendant");
    active = null;
    cursor = -1;
    visible = [];
  }
  function place() {
    if (!active?.isConnected) return close();
    const popup = active.querySelector(".filter-popup"),
      r = control(active).getBoundingClientRect();
    popup.style.width =
      Math.min(Math.max(r.width, 230), win.innerWidth - 16) + "px";
    const h = popup.getBoundingClientRect().height;
    popup.style.left =
      Math.max(
        8,
        Math.min(
          r.left,
          win.innerWidth - popup.getBoundingClientRect().width - 8,
        ),
      ) + "px";
    popup.style.top =
      Math.max(
        4,
        r.bottom + h + 4 > win.innerHeight && r.top > win.innerHeight - r.bottom
          ? r.top - h - 4
          : r.bottom + 4,
      ) + "px";
  }
  function draw(rows) {
    const root = active,
      popup = root.querySelector(".filter-popup"),
      box = popup.querySelector('[role="listbox"]'),
      type = root.dataset.filterType;
    visible = rows;
    cursor = -1;
    control(root).removeAttribute("aria-activedescendant");
    box.innerHTML = rows
      .map(
        ([v, l], i) =>
          `<button type="button" role="option" id="${box.id}-${i}" tabindex="-1" aria-selected="${root.querySelector("select")?.value === v}" data-filter-index="${i}">${caption(type, v, l)}${type === "suggest" && v !== l ? `<small>${escapeFilter(v)}</small>` : ""}</button>`,
      )
      .join("");
    if (!rows.length && type === "suggest") {
      popup.hidden = true;
      if (popup.isConnected) popup.hidePopover?.();
      control(root).setAttribute("aria-expanded", "false");
      return;
    }
    if (!rows.length) box.innerHTML = '<span class="sub">没有匹配项</span>';
    popup.hidden = false;
    popup.showPopover?.();
    control(root).setAttribute("aria-expanded", "true");
    place();
  }
  function update(query = "") {
    const root = active,
      token = ++version;
    win.clearTimeout(timer);
    const free = root.dataset.filterType === "suggest",
      needle = query.trim().toLowerCase();
    const local = Array.from(root.querySelectorAll("option"), (o) => [
      o.value,
      o.textContent,
      o.dataset.alias || "",
    ]).filter((r) => !needle || r.join(" ").toLowerCase().includes(needle));
    draw(free ? local.slice(0, 12) : local);
    if (!free || !suggest || !root.dataset.filterSource || needle.length < 2)
      return;
    timer = win.setTimeout(async () => {
      try {
        const extra = await suggest(
          root.dataset.filterSource,
          query.trim(),
          root.closest("form"),
        );
        if (
          token === version &&
          root.isConnected &&
          active === root &&
          cursor < 0
        )
          draw(optionRows([...extra, ...local]).slice(0, 12));
      } catch {
        /* The typed query remains usable when suggestions are unavailable. */
      }
    }, 250);
  }
  function open(root) {
    if (active !== root) close();
    active = root;
    const type = root.dataset.filterType,
      popup = root.querySelector(".filter-popup");
    if (
      ["country", "language"].includes(type) &&
      !popup.querySelector("input")
    ) {
      const search = doc.createElement("input");
      search.type = "search";
      search.placeholder = type === "country" ? "搜索国家或地区" : "搜索语言";
      search.setAttribute("aria-label", search.placeholder);
      popup.prepend(search);
    }
    const search = popup.querySelector("input");
    if (search) search.value = "";
    update(root.querySelector("[data-filter-input]")?.value || "");
    search?.focus();
  }
  function choose(index) {
    const root = active,
      row = visible[index];
    if (!row) return;
    const entry = root.querySelector("[data-filter-input]"),
      select = root.querySelector("select"),
      target = entry || select;
    target.value = row[0];
    if (select)
      root.querySelector(".filter-caption").innerHTML = caption(
        root.dataset.filterType,
        ...row,
      );
    const flag = root.querySelector("[data-filter-match]");
    if (flag) flag.value = flag.dataset.picked;
    control(root).focus();
    close();
    target.dispatchEvent(new win.Event("change", { bubbles: true }));
  }
  doc.addEventListener("focusin", (e) => {
    const root = e.target.closest?.("[data-filter-type]");
    if (e.target.matches?.("[data-filter-input]")) open(root);
    else if (active !== root) close();
  });
  doc.addEventListener("input", (e) => {
    const root = e.target.closest?.("[data-filter-type]");
    if (!root) return;
    if (e.target.matches("[data-filter-input]")) {
      const flag = root.querySelector("[data-filter-match]");
      if (flag) flag.value = flag.dataset.free;
      if (active !== root) open(root);
      else update(e.target.value);
    } else if (active === root && e.target.closest(".filter-popup"))
      update(e.target.value);
  });
  doc.addEventListener("pointerdown", (e) => {
    if (active && !active.contains(e.target)) close();
  });
  doc.addEventListener("mousedown", (e) => {
    if (e.target.closest?.("[data-filter-index]")) e.preventDefault();
  });
  doc.addEventListener("click", (e) => {
    const reset = e.target.closest?.("[data-filter-reset-button]");
    if (reset) {
      const form = reset.closest("form");
      close();
      if (form.dataset.applying === "true") return;
      const defaults = JSON.parse(form.dataset.filterReset || "{}");
      for (const input of form.elements) {
        if (!input.name || input.closest(".filter-popup")) continue;
        if (input.type === "checkbox" || input.type === "radio") {
          input.checked = input.defaultChecked;
        } else if (input.hasAttribute("data-filter-match")) {
          input.value = input.dataset.free;
        } else {
          input.value =
            defaults[input.name] ??
            (input.tagName === "SELECT" ? input.options[0]?.value || "" : "");
        }
        const root = input.closest("[data-filter-type]");
        if (input.tagName === "SELECT" && root)
          root.querySelector(".filter-caption").innerHTML = caption(
            root.dataset.filterType,
            input.value,
            input.selectedOptions[0]?.textContent || "",
          );
        if (input.name === "category") {
          const field = input.closest(".category-field");
          const cap = field?.querySelector('[id$="-caption"]');
          if (cap) cap.textContent = "全部分类";
        }
      }
      form.dispatchEvent(
        new win.CustomEvent("filter-reset", { bubbles: true }),
      );
      if (form.dataset.filterResetSubmit !== "false") form.requestSubmit();
      return;
    }

    const root = e.target.closest?.("[data-filter-type]");
    if (!root) return;
    const option = e.target.closest("[data-filter-index]");
    if (option && active === root) choose(Number(option.dataset.filterIndex));
    else if (e.target.closest(".filter-trigger")) {
      if (active === root) close();
      else open(root);
    }
  });
  doc.addEventListener("keydown", (e) => {
    const form = e.target.closest?.("form[data-filter-bar]");
    if (!form || e.isComposing || e.keyCode === 229) return;
    const root = e.target.closest("[data-filter-type]");
    if (root && ["ArrowDown", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      if (active !== root) open(root);
      if (!visible.length) return;
      cursor =
        (cursor + (e.key === "ArrowDown" ? 1 : -1) + visible.length) %
        visible.length;
      const options = root.querySelectorAll("[data-filter-index]");
      options.forEach((o, i) =>
        o.classList.toggle("highlighted", i === cursor),
      );
      control(root).setAttribute("aria-activedescendant", options[cursor].id);
      options[cursor].scrollIntoView?.({ block: "nearest" });
      return;
    }
    if (e.key === "Escape" && active) {
      e.preventDefault();
      const c = control(active);
      c.focus();
      close();
      return;
    }
    if (
      e.key === "Enter" &&
      (e.target.matches("input:not([type=checkbox])") ||
        (root && root === active && cursor >= 0))
    ) {
      e.preventDefault();
      if (root && root === active && cursor >= 0) choose(cursor);
      else if (root && e.target.closest(".filter-popup") && visible.length)
        choose(0);
      else close();
      if (form.isConnected) form.requestSubmit();
    }
  });
  doc.addEventListener("submit", close, true);
  win.addEventListener("hashchange", close);
  win.addEventListener("resize", close);
  win.addEventListener(
    "scroll",
    (e) => {
      if (
        active &&
        !active
          .querySelector(".filter-popup")
          .contains(e.target instanceof win.Node ? e.target : null)
      )
        close();
    },
    true,
  );
}
