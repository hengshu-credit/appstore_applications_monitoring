import { createAppDetailTable } from "./app-detail-table.js";
import { pagination, bindPagination } from "./pagination.js";
import { filterBar, bindFilterBar } from "./filter-bar.js";
export function createGroupWorkspace(ctx) {
  const {
    api,
    esc,
    qs,
    panel,
    table,
    metric,
    chart,
    stamp,
    num,
    mount,
    toast,
    can = () => true,
    countryName = (code) => code.toUpperCase(),
    categoryName = (_store, id) => id,
    chartName = (_store, id) =>
      ({
        "top-free": "免费榜",
        "top-paid": "付费榜",
        "top-grossing": "畅销榜",
      })[id] || id,
    appIconMarkup = () => "",
  } = ctx;
  const stateNames = {
    available: "已验证在架",
    suspected_missing: "本次未检出",
    region_unavailable: "地区不可用",
    unverified: "无商店记录",
  };
  const classifications = {
    available: "在架",
    region_unavailable: "地区不可用",
    suspected_missing: "商店未检出",
    unverified: "暂无依据",
  };
  const sources = {
    store: "商店观察",
    corroborated: "商店与导入一致",
    reference: "导入记录",
    adjustment: "明细修正",
    unknown: "—",
  };
  const permissionNames = {
    ok: "已采集",
    pending: "待采集",
    running: "采集中",
    not_scheduled: "未配置",
    stale: "待刷新",
    error: "采集异常",
    paused: "已暂停",
    unsupported: "公开接口未提供",
  };
  const eventNames = {
    first_verified: "首次验证在架",
    reappeared: "重新出现",
    region_unavailable: "确认地区不可用",
  };
  const detailTaskNames = {
    not_scheduled: "尚未配置",
    paused: "已暂停",
    running: "采集中",
    retry: "等待重试",
    scheduled: "按期采集",
    pending: "排队待采",
  };
  const permissionGroupName = (name) =>
    ({
      Location: "位置",
      Contacts: "通讯录",
      Camera: "相机",
      Microphone: "麦克风",
      SMS: "短信",
      Phone: "电话",
      Storage: "存储",
      Identity: "身份",
      Other: "其他",
      Calendar: "日历",
      "Wi-Fi connection information": "无线网络",
      "Device & app history": "应用历史",
      "Photos/Media/Files": "照片与文件",
      "Device ID & call information": "设备与通话信息",
    })[name] || name;
  let selected = null,
    cached = null,
    groups = [],
    filters = { store: "all", country: "", state: "", q: "", days: 30 },
    offset = 0,
    pageSize = 50,
    tab = "overview",
    generation = 0,
    root;
  let clearCountryHints = () => {};
  const storeName = (s) => (s === "apple" ? "App Store" : "Google Play");
  const dateValue = (v) =>
    v == null ? "—" : typeof v === "number" ? stamp(v) : esc(v);
  function dialog(title, body) {
    document.querySelector("#group-dialog")?.remove();
    const el = document.createElement("dialog");
    el.id = "group-dialog";
    el.className = "group-dialog research-dialog";
    el.innerHTML = `<form method="dialog" class="picker-heading"><h2>${esc(title)}</h2><button class="secondary" aria-label="关闭">关闭</button></form>${body}`;
    document.body.append(el);
    el.showModal();
    return el;
  }
  async function busy(button, action) {
    button.disabled = true;
    try {
      await action();
    } catch (error) {
      toast(error.message);
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }
  const decimalText = (value) => {
    const [whole, part = ""] = String(value).split(".");
    const fraction = part.replace(/0+$/, "");
    return (
      whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") +
      (fraction ? "." + fraction : "")
    );
  };
  const metricLabel = (r) =>
    `${r.metric === "downloads" ? "下载量" : "商店收入"} · ${r.is_estimate ? "估算" : "实际报表"}${r.currency ? " · " + r.currency : ""}`;
  const metricCells = (rows) =>
    rows?.length
      ? rows
          .map(
            (r) =>
              `<div>${esc(metricLabel(r))}：<strong>${esc(decimalText(r.value))}</strong><small class="sub"> · 已有 ${num(r.markets)} 个应用地区、${num(r.days)} 个日期的数据</small></div>`,
          )
          .join("")
      : '<span class="sub">暂无数据</span>';
  const options = (values, selected, empty) =>
    `<option value="">${empty}</option>` +
    values
      .map(
        ([v, label]) =>
          `<option value="${esc(v)}" ${v === selected ? "selected" : ""}>${esc(label)}</option>`,
      )
      .join("");

  const compactCount = (value) => {
    const count = Number(value);
    if (count >= 1e12) return count.toExponential(1);
    return count >= 10000
      ? new Intl.NumberFormat("zh-CN", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(count)
      : num(value);
  };
  function countryCard(country) {
    const states = [
      ["available", "在架"],
      ["unverified", "无依据"],
      ["suspected_missing", "未检出"],
      ["region_unavailable", "不可用"],
    ].map(([key, label]) => ({
      key,
      label,
      count: (country.classifications || country.states)[key] || 0,
    }));
    const total = states.reduce((sum, state) => sum + state.count, 0);
    const summary = states
      .map((s) => `${classifications[s.key]} ${num(s.count)}`)
      .join("，");
    let start = 0;
    const segments = states
      .map((s) => {
        const width = total ? (s.count / total) * 1000 : 0;
        const rect = `<rect x="${start}" y="0" width="${width}" height="8" class="country-state-${s.key}"></rect>`;
        start += width;
        return rect;
      })
      .join("");
    return `<button data-group-country="${esc(country.country)}" class="content-card group-country" aria-label="${esc(countryName(country.country))}，${num(country.apps)} 款应用，${num(country.targets)} 个目标，${esc(summary)}。查看应用明细">
      <span class="country-heading"><span class="country-name"><img class="country-flag" src="/flags/${esc(country.country)}.svg" alt="" loading="lazy"><strong>${esc(countryName(country.country))}</strong></span><small>${esc(country.country.toUpperCase())}</small></span>
      <span class="country-total"><b title="${num(country.apps)}">${compactCount(country.apps)}</b><small>APP</small></span>
      <svg class="country-status-bar" viewBox="0 0 1000 8" preserveAspectRatio="none" aria-hidden="true"><rect width="1000" height="8" class="country-state-empty"/>${segments}</svg>
      <span class="country-status-counts">${states.map((s) => `<span class="country-status" title="${esc(classifications[s.key])}：${num(s.count)}"><b>${compactCount(s.count)}</b><small>${s.label}</small></span>`).join("")}</span>
      <template class="country-detail"><div class="country-hint-heading"><strong>${esc(countryName(country.country))}</strong><span>${num(country.apps)} APP · ${num(country.targets)} 个观察目标</span></div>${states.map((s) => `<div class="country-hint-row"><i class="country-state-${s.key}" aria-hidden="true"></i><span>${esc(classifications[s.key])}</span><b>${num(s.count)}</b><small>${total ? ((s.count / total) * 100).toFixed(1) + "%" : "—"}</small></div>`).join("")}<p>状态按国家 / 语言目标统计</p></template>
    </button>`;
  }

  function bindCountryHints(host) {
    const tip = document.createElement("div");
    tip.id = "group-country-hint";
    tip.className = "country-hint";
    tip.setAttribute("role", "tooltip");
    tip.setAttribute("popover", "manual");
    tip.hidden = true;
    host.append(tip);
    let active = null,
      opened = false;
    const hide = () => {
      active?.removeAttribute("aria-describedby");
      active = null;
      if (opened && tip.isConnected) tip.hidePopover();
      opened = false;
      tip.hidden = true;
    };
    const show = (card) => {
      if (!card.isConnected) return;
      hide();
      active = card;
      tip.replaceChildren(
        card.querySelector(".country-detail").content.cloneNode(true),
      );
      tip.hidden = false;
      if (tip.showPopover) {
        tip.showPopover();
        opened = true;
      }
      card.setAttribute("aria-describedby", tip.id);
      const r = card.getBoundingClientRect(),
        box = tip.getBoundingClientRect();
      tip.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - box.width - 8))}px`;
      const top =
        r.top - box.height - 8 >= 8 ? r.top - box.height - 8 : r.bottom + 8;
      tip.style.top = `${Math.max(8, Math.min(top, window.innerHeight - box.height - 8))}px`;
    };
    host.querySelectorAll(".group-country").forEach((card) => {
      card.addEventListener("pointerenter", () => show(card));
      card.addEventListener("pointerleave", () => {
        if (document.activeElement !== card) hide();
      });
      card.addEventListener("focus", () => show(card));
      card.addEventListener("blur", hide);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Escape") hide();
      });
    });
    const onScroll = () => {
      const card = active;
      const rect = card?.getBoundingClientRect();
      if (
        card &&
        document.activeElement === card &&
        rect.bottom > 0 &&
        rect.top < window.innerHeight
      )
        show(card);
      else hide();
    };
    const cleanup = () => {
      hide();
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("hashchange", cleanup);
      tip.remove();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", hide);
    window.addEventListener("hashchange", cleanup, { once: true });
    clearCountryHints = cleanup;
  }

  async function render(revision) {
    clearCountryHints();
    const listing = await api("/groups");
    groups = listing.items;
    const requested = Number(
      new URLSearchParams(location.hash.split("?")[1] || "").get("id"),
    );
    selected = groups.some((g) => g.id === requested)
      ? requested
      : groups.some((g) => g.id === selected)
        ? selected
        : groups[0]?.id;
    if (
      !mount(
        `<div class="group-workspace"><aside class="panel group-sidebar"><div class="group-sidebar-content"><div class="panel-heading"><h2>我的应用分组</h2>${can("groups", "write") ? '<button id="group-create" class="secondary">新建</button>' : ""}</div><div id="group-list"></div></div></aside><section id="group-dashboard" aria-live="polite"></section></div>`,
        revision,
      )
    )
      return;
    root = document.querySelector(".group-workspace");
    renderGroupList();
    root
      .querySelector("#group-create")
      ?.addEventListener("click", () => editGroup());
    if (selected) await load();
    else
      root.querySelector("#group-dashboard").innerHTML = panel(
        "建立第一个监控分组",
        "<p>新建分组后可批量粘贴 APP ID、商店链接或跨国家名单，也可在应用库勾选后加入分组。</p>",
      );
  }
  function renderGroupList() {
    if (!root?.isConnected) return;
    root.querySelector("#group-list").innerHTML =
      groups
        .map(
          (g) =>
            `<button class="content-card group-choice ${g.id === selected ? "active" : ""}" data-group="${g.id}"><strong>${esc(g.name)}</strong><span>${num(g.apps)} 款应用 · ${num(g.countries)} 国</span><small>${num(g.targets)} 个国家/语言观察目标</small></button>`,
        )
        .join("") || '<p class="sub">尚无分组</p>';
    root.querySelectorAll("[data-group]").forEach(
      (button) =>
        (button.onclick = () => {
          selected = Number(button.dataset.group);
          filters = {
            store: "all",
            country: "",
            state: "",
            q: "",
            days: filters.days,
          };
          offset = 0;
          tab = "overview";
          history.replaceState(null, "", `#groups?id=${selected}`);
          renderGroupList();
          load().catch((e) => toast(e.message));
        }),
    );
  }
  async function refreshGroups() {
    groups = (await api("/groups")).items;
    renderGroupList();
    await load();
  }
  function editGroup(existing) {
    const el = dialog(
      existing ? "编辑分组" : "新建应用分组",
      `<form id="group-edit" class="form-grid"><label>分组名称<input name="name" maxlength="120" required value="${esc(existing?.name || "")}" placeholder="例如：东南亚现金贷 / 重点竞品"></label><label class="full">说明<textarea name="description" maxlength="2000">${esc(existing?.description || "")}</textarea></label><button>保存分组</button></form>`,
    );
    el.querySelector("form#group-edit").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      busy(form.querySelector("button"), async () => {
        const value = {
          name: form.elements.name.value,
          description: form.elements.description.value,
          ...(existing ? { revision: existing.revision } : {}),
        };
        const saved = await api(
          existing ? `/groups/${existing.id}` : "/groups",
          value,
          existing ? "PUT" : "POST",
        );
        if (!el.isConnected) return;
        el.close();
        selected = saved.id;
        offset = 0;
        history.replaceState(null, "", `#groups?id=${selected}`);
        await refreshGroups();
      });
    };
  }
  function addForm() {
    const id = selected;
    const el = dialog(
      "批量加入 APP",
      `<form id="group-add" class="form-grid"><label>默认商店<select name="store"><option value="google">Google Play</option><option value="apple">App Store</option></select></label><label>默认国家（逗号分隔）<input name="countries" value="${esc(filters.country || "us")}" placeholder="th,vn,id"></label><label>请求语言<input name="language" value="auto"></label><label class="full">APP ID / 商店链接 / CSV 名单<textarea name="text" rows="9" required placeholder="每行一个 ID 或官方商店链接；或跨商店格式：&#10;google,com.example.app,th,auto&#10;apple,123456789,vn,auto"></textarea></label><p class="sub full">同一 APP 可加入多个组；按商店、国家和语言去重。新增目标进入详情与 Android 权限采集队列，已有任务的暂停状态与周期保持不变。</p><button>加入分组</button></form>`,
    );
    el.querySelector("#group-add").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      busy(form.querySelector("button"), async () => {
        const value = Object.fromEntries(
          ["store", "countries", "language", "text"].map((k) => [
            k,
            form.elements[k].value,
          ]),
        );
        const result = await api(`/groups/${id}/members`, value);
        if (!el.isConnected) return;
        el.close();
        toast(`新增 ${result.added} 个目标，已有 ${result.existing} 个`);
        if (selected === id) await refreshGroups();
      });
    };
  }
  async function addSelected(members) {
    if (!members.length) return;
    const listing = await api("/groups");
    const el = dialog(
      "加入应用分组",
      `<form id="group-selected" class="form-grid"><p class="full">已选 ${members.length} 个应用目标，按所选国家和请求语言加入。</p><label>已有分组<select name="group_id">${options(
        listing.items.map((g) => [String(g.id), g.name]),
        "",
        "选择已有分组",
      )}</select></label><label>或新建分组<input name="new_name" maxlength="120" placeholder="填写新分组名称"></label><button>加入并查看看板</button></form>`,
    );
    el.querySelector("#group-selected").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      busy(form.querySelector("button"), async () => {
        let id = Number(form.elements.group_id.value);
        if (form.elements.new_name.value.trim())
          id = (await api("/groups", { name: form.elements.new_name.value }))
            .id;
        if (!id) throw new Error("请选择分组或填写新名称");
        await api(`/groups/${id}/members`, { members });
        if (!el.isConnected) return;
        el.close();
        selected = id;
        location.hash = `groups?id=${id}`;
      });
    };
  }
  function collectForm() {
    const id = selected;
    const el = dialog(
      "配置本组采集",
      `<form id="group-collect" class="form-grid"><p class="full">范围：${filters.store === "all" ? "全部商店" : storeName(filters.store)} · ${esc(filters.country || "全部国家")}。启用所选类型并安排复核，遵守共享限速。</p><div class="full group-check-options">${[
        ["details", "详情 / 上下架 / 评分"],
        ["permissions", "Android 商店声明权限"],
        ["charts", "免费 / 付费 / 畅销总榜"],
        ["reviews", "近期评论"],
        ["keywords", "关键词排名"],
      ]
        .map(
          ([v, n]) =>
            `<label><input name="${v}" type="checkbox" ${["details", "permissions"].includes(v) ? "checked" : ""}>${n}</label>`,
        )
        .join(
          "",
        )}</div><label class="full">要追踪的关键词（每行一个）<textarea name="words" rows="4" placeholder="选择关键词采集时填写；按组内各国家/语言分别查询"></textarea></label><p class="sub full">商店权限清单不代表用户实际授予；App Store 公开接口未提供同类权限清单。榜单和关键词仅覆盖接口实际返回的范围。下载和收入通过授权指标或报表补充。</p><button>启用并安排采集</button></form>`,
    );
    el.querySelector("#group-collect").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      busy(form.querySelector("button"), async () => {
        const result = await api(`/groups/${id}/collect`, {
          store: filters.store,
          country: filters.country,
          kinds: [
            "details",
            "permissions",
            "charts",
            "reviews",
            "keywords",
          ].filter((k) => form.elements[k].checked),
          keywords: form.elements.words.value
            .split(/\r?\n/)
            .map((v) => v.trim())
            .filter(Boolean),
        });
        if (!el.isConnected) return;
        el.close();
        toast(
          `${result.note} 共 ${Object.values(result.queued).reduce((a, b) => a + b, 0)} 个目标。`,
        );
        if (selected === id) await load();
      });
    };
  }
  function controlForm(data) {
    const id = data.group.id,
      scope = { store: filters.store, country: filters.country };
    const el = dialog(
      "批量启停详情与权限",
      `<form id="group-control" class="form-grid"><p class="full">操作范围：${esc(data.group.name)} · ${scope.store === "all" ? "全部商店" : storeName(scope.store)} · ${esc(scope.country || "全部国家")}，共 ${num(data.scope_targets ?? data.stats.targets)} 个观察目标。此范围包含列表搜索和状态筛选以外的成员。</p><p class="callout full">详情与 Android 权限任务会被多个分组共享；本次操作也会影响其他分组中的同一目标。历史数据保留。关键词、榜单和评论任务在采集中心独立管理。</p><label>操作<select name="action"><option value="pause">暂停详情与权限采集</option><option value="resume">恢复详情与权限采集</option></select></label><button type="submit">暂停详情与权限采集</button></form>`,
    );
    const form = el.querySelector("#group-control");
    form.elements.action.onchange = () => {
      form.querySelector("[type=submit]").textContent =
        form.elements.action.selectedOptions[0].textContent;
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      busy(form.querySelector("[type=submit]"), async () => {
        const result = await api(`/groups/${id}/monitoring/control`, {
          ...scope,
          action: form.elements.action.value,
        });
        if (!el.isConnected) return;
        el.close();
        toast(result.note);
        if (selected === id) await load();
      });
    };
  }
  function importMetrics() {
    const id = selected;
    const el = dialog(
      "导入下载与收入指标",
      `<form id="group-metrics" class="form-grid"><p class="full">仅导入本组已有应用及国家。每行是一天的区域指标；实际报表与估算、不同币种分别保存。</p><p class="sub full">CSV 列：store,app_id,country,date,downloads,revenue,currency,is_estimate<br>日期使用 YYYY-MM-DD，is_estimate 使用 true/false；revenue 填写商店付费下载、内购和订阅净收入，并提供币种；未取得的值留空，真实零值填写 0。</p><label class="full">UTF-8 CSV（最大 3.5MB）<input name="file" type="file" accept=".csv,text/csv" required></label><button>校验并导入</button></form>`,
    );
    el.querySelector("#group-metrics").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      busy(form.querySelector("button"), async () => {
        const file = form.elements.file.files[0];
        if (!file || file.size > 3500000)
          throw new Error("请选择不超过 3.5MB 的 CSV");
        const result = await api(`/groups/${id}/metrics`, {
          csv: await file.text(),
        });
        if (!el.isConnected) return;
        el.close();
        toast(`已保存 ${result.facts} 条指标`);
        if (selected === id) await load();
      });
    };
  }
  const developerSources = {
    store: "商店详情",
    watchlist: "导入名单",
    seed: "公开种子快照",
  };
  const detailTable = createAppDetailTable(ctx);
  function memberTable(data) {
    const writable = can("groups", "write");
    return `<div class="group-table-tools"><span class="member-permission-key">✓ 已声明　× 未声明　- 暂无数据或不适用</span><div class="actions"><span id="group-selection-count" aria-live="polite"></span>${writable ? '<button id="group-import-metrics" class="secondary small">导入指标</button><button id="group-remove" class="secondary small" disabled>移出本组</button>' : ""}</div></div>${detailTable(data, { writable, id: "member" })}<div class="group-table-footer"><span>共 ${num(data.total)} 条 · ${data.total ? data.offset + 1 : 0}–${data.offset + data.items.length}</span><div class="actions">${pagination({ id: "group-pages", offset, limit: data.limit, total: data.total, hasMore: data.has_more, prevId: "group-prev", nextId: "group-next", sizes: [25, 50, 100, 200] })}</div></div>`;
  }
  function bindCrosshair(grid) {
    const columns = new Map();
    const bands = new Map();
    for (const cell of grid.querySelectorAll("[data-col]")) {
      const key = cell.dataset.col;
      if (!columns.has(key)) columns.set(key, []);
      columns.get(key).push(cell);
      if (cell.tagName === "TH" && cell.hasAttribute("headers")) {
        bands.set(key, grid.querySelector("#" + cell.getAttribute("headers")));
      }
    }

    let hovered = null,
      focused = null,
      pinned = null,
      current = null;
    function mark(cell, active) {
      if (!cell) return;
      const key = cell.dataset.col;
      cell
        .closest("[data-member-row]")
        ?.classList.toggle("crosshair-row", active);
      columns
        .get(key)
        .forEach((el) => el.classList.toggle("crosshair-col", active));
      bands.get(key)?.classList.toggle("crosshair-band", active);
      cell.classList.toggle("crosshair-cell", active);
    }
    function paint() {
      const cell = hovered || focused || pinned;
      if (cell === current) return;
      mark(current, false);
      mark(cell, true);
      current = cell;
    }
    const findCell = (node) =>
      node?.closest?.("td[data-col], th[data-col]") || null;
    grid.addEventListener("pointerover", (event) => {
      hovered = findCell(event.target);
      paint();
    });
    grid.addEventListener("pointerleave", () => {
      hovered = null;
      paint();
    });
    grid.addEventListener("focusin", (event) => {
      hovered = null;
      focused = findCell(event.target);
      paint();
    });
    grid.addEventListener("focusout", (event) => {
      focused = grid.contains(event.relatedTarget)
        ? findCell(event.relatedTarget)
        : null;
      paint();
    });
    grid.addEventListener("click", (event) => {
      const cell = findCell(event.target);
      if (
        cell?.tagName !== "TD" ||
        event.target.closest("button, input, select, a, label")
      )
        return;
      pinned?.classList.remove("crosshair-pin");
      pinned = pinned === cell ? null : cell;
      pinned?.classList.add("crosshair-pin");
      // Keep subsequent Escape inside this grid, including after a touch tap.
      grid.parentElement.focus({ preventScroll: true });
      paint();
    });
    grid.parentElement.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !pinned) return;
      pinned.classList.remove("crosshair-pin");
      pinned = null;
      paint();
    });
  }
  function bindMemberGrid(host, data) {
    const grid = host.querySelector(".group-member-table");
    if (!grid) return;
    bindCrosshair(grid);
    grid.querySelectorAll("[data-adjust-row]").forEach(
      (button) =>
        (button.onclick = () => {
          const tr = button.closest("tr"),
            row = data.items.find((r) => r.id === Number(tr.dataset.memberRow));
          const status = tr.querySelector('[data-col="state"]');
          const saved = new Map();
          function replace(cell, html) {
            saved.set(cell, cell.innerHTML);
            cell.innerHTML = html;
          }
          replace(
            status,
            `<select data-edit-status aria-label="分组状态">${options(
              [
                ["available", "在架"],
                ["region_unavailable", "地区不可用"],
              ],
              row.status_override || "",
              "自动判定",
            )}</select>`,
          );
          if (row.store === "google")
            tr.querySelectorAll("[data-permission]").forEach((mark) => {
              const name = mark.dataset.permission,
                current = (row.permission_overrides || {})[name];
              replace(
                mark.closest("td"),
                `<select data-edit-permission="${esc(name)}" aria-label="${esc(permissionGroupName(name))}">${options(
                  [
                    ["true", "✓ 已声明"],
                    ["false", "× 未声明"],
                  ],
                  current == null ? "" : String(current),
                  "随采集",
                )}</select>`,
              );
            });
          const action = button.parentElement;
          button.hidden = true;
          const controls = document.createElement("span");
          controls.className = "row-edit-actions";
          controls.innerHTML =
            '<button class="link" data-save-row>保存</button><button class="link" data-cancel-row>取消</button>';
          action.append(controls);
          tr.classList.add("editing");
          const otherEditors = [
            ...grid.querySelectorAll("[data-adjust-row]"),
          ].filter((other) => other !== button);
          otherEditors.forEach((other) => {
            other.disabled = true;
          });
          controls.querySelector("[data-cancel-row]").onclick = () => {
            for (const [cell, html] of saved) cell.innerHTML = html;
            controls.remove();
            button.hidden = false;
            tr.classList.remove("editing");
            otherEditors.forEach((other) => {
              other.disabled = false;
            });
          };
          controls.querySelector("[data-save-row]").onclick = (event) =>
            busy(event.currentTarget, async () => {
              const permission_overrides = {};
              tr.querySelectorAll("[data-edit-permission]").forEach((el) => {
                if (el.value !== "")
                  permission_overrides[el.dataset.editPermission] =
                    el.value === "true";
              });
              const inputs = tr.querySelectorAll("select, [data-cancel-row]");
              inputs.forEach((input) => {
                input.disabled = true;
              });
              try {
                await api(
                  `/groups/${data.group.id}/members/${row.id}/adjustment`,
                  {
                    revision: row.adjustment_revision || 0,
                    status_override:
                      status.querySelector("select").value || null,
                    permission_overrides,
                  },
                  "PUT",
                );
              } finally {
                inputs.forEach((input) => {
                  input.disabled = false;
                });
              }
              toast("本行调整已保存");
              if (tr.isConnected && selected === data.group.id) await load();
            });
          status.querySelector("select").focus();
        }),
    );
  }
  async function load(reuse = false) {
    const ticket = ++generation,
      id = selected,
      host = root?.querySelector("#group-dashboard");
    if (!host) return;
    clearCountryHints();
    const query =
      `/groups/${id}/dashboard?` +
      qs({
        ...filters,
        state: filters.state === "error" ? "error" : "",
        classification: filters.state === "error" ? "" : filters.state,
        offset,
        limit: pageSize,
      });
    let data;
    if (reuse && cached?.query === query) data = cached.data;
    else {
      host.innerHTML = panel(
        "正在读取分组看板",
        '<p class="sub">汇总国家分布、应用状态和指标覆盖…</p>',
      );
      try {
        data = await api(query);
      } catch (error) {
        if (host.isConnected && ticket === generation) {
          host.innerHTML = panel(
            "暂未读取看板",
            `<p>${esc(error.message)}</p><button id="group-load-retry" class="secondary">重试</button>`,
          );
          host.querySelector("#group-load-retry").onclick = () =>
            load().catch((e) => toast(e.message));
        }
        throw error;
      }
    }
    if (!host.isConnected || ticket !== generation || selected !== id) return;
    cached = { query, data };
    const s = data.stats,
      counts = s.classifications || s.states,
      ev = data.event_counts;
    const badge = (name, value, note) => metric(name, value, note);
    host.innerHTML = `<div class="panel group-title"><div><h2>${esc(data.group.name)}</h2><p class="sub">${esc(data.group.description || "跨商店、跨国家的应用监控组合")}</p></div><div class="toolbar">${can("groups", "write") ? '<button id="group-edit-button" class="secondary">编辑分组</button>' : ""}${can("groups", "write") && can("watchlist", "write") ? '<button id="group-add-button">批量加入 APP</button>' : ""}${can("groups", "write") && can("collection", "write") ? '<button id="group-collect-button" class="secondary">配置采集</button>' : ""}<button id="group-reload" class="secondary">刷新</button></div></div>
      ${filterBar({
        id: "group-filter",
        resetValues: { days: 30 },
        className: "group-filters",
        submit: "查看",
        values: filters,
        fields: [
          { name: "store", label: "商店", type: "store" },
          {
            name: "country",
            label: "国家",
            type: "country",
            options: [
              ["", "全部国家"],
              ...data.countries.map((c) => [c.country, countryName(c.country)]),
            ],
          },
          {
            name: "days",
            label: "期间",
            type: "select",
            options: [7, 30, 90, 365].map((n) => [n, `近 ${n} 天`]),
          },
          ...(tab === "overview"
            ? []
            : [
                {
                  name: "state",
                  label: "列表状态",
                  type: "panel",
                  options: [
                    ["", "全部状态"],
                    ...Object.entries(classifications),
                    ["error", "采集异常"],
                  ],
                },
                {
                  name: "q",
                  label: "搜索列表",
                  wide: true,
                  placeholder: "应用名称 / ID",
                  options: data.items.map((r) => [
                    r.app_id,
                    r.name || r.app_id,
                  ]),
                },
              ]),
        ],
      })}
      <div class="group-tabs" role="tablist">${[
        ["overview", "分组总览"],
        ["matrix", "应用明细"],
      ]
        .map(
          ([v, n]) =>
            `<button role="tab" aria-selected="${tab === v}" class="${tab === v ? "active" : ""}" data-group-tab="${v}">${n}</button>`,
        )
        .join("")}</div>
      <div id="group-tab-content">${
        tab === "overview"
          ? `<div class="cards group-kpis">${badge("分组应用", s.apps, `${num(s.targets)} 个国家 / 语言目标`)}${badge("覆盖国家", s.countries, "当前商店与国家范围")}${badge("在架", counts.available || 0, "按当前分组判定")}${badge("暂无依据", counts.unverified || 0, "等待首份有效数据")}${badge("商店未检出", counts.suspected_missing || 0, "最近一次查询未返回应用")}${badge("地区不可用", counts.region_unavailable || 0, "当前国家的分组判定")}</div>
          <div class="group-health"><span>48 小时内取得详情：${num(s.fresh)} / ${num(s.targets)}</span><span>采集异常：${num(s.errors)}</span><span>${data.collection.paused ? "采集已全局暂停" : data.collection.cooldown_seconds ? `距离下次可请求：${data.collection.cooldown_seconds} 秒` : "采集开关已启用"}</span><span>看板更新：${stamp(data.observed_at)}</span></div>
          ${panel(
            "各国家应用分布",
            `<p class="sub">悬停或聚焦卡片查看状态分布，点击进入应用明细。</p><div class="group-countries">${data.countries
              .filter((c) => !filters.country || c.country === filters.country)
              .map(countryCard)
              .join("")}</div>`,
          )}
        <div class="cards">${badge("首次验证在架", ev.first_verified || 0, "所选期间去重应用×国家，不等于新发布")}${badge("重新出现", ev.reappeared || 0, "有历史证据的恢复可用")}${badge("确认地区不可用", ev.region_unavailable || 0, "按缺失确认策略产生事件")}${badge("新增关注目标", s.new_members, `近 ${filters.days} 天加入，不等于新上架`)}</div>
        <div class="grid2">${Object.entries(eventNames)
          .map(([kind, label]) =>
            panel(
              label + "趋势（UTC）",
              chart(
                data.events
                  .filter((e) => e.kind === kind)
                  .map((e) => ({
                    at: e.day,
                    value: e.count,
                    note: label + " · 去重应用×国家",
                  })),
              ),
            ),
          )
          .join(
            "",
          )}${panel("数据覆盖", `<p>评分已取得 ${s.ratings_known}/${s.targets} 个目标。</p><p>有效关键词命中 ${s.keyword_apps} 个目标，榜单命中 ${s.chart_apps} 个目标。</p><p>权限已有数据 ${s.permissions_known ?? s.permission_states.ok ?? 0} 个目标；待采 ${s.permission_states.pending || 0}，异常 ${s.permission_states.error || 0}，公开接口未提供 ${s.permission_states.unsupported || 0}。</p>${metricCells(data.money)}<p class="sub">未取得的指标保持缺失，不把累计安装量当作日下载。</p>`)}</div>`
          : `<section class="panel group-members-panel"><div class="member-panel-heading"><h2>应用明细</h2><span class="sub">近 ${filters.days} 天 · ${filters.store === "all" ? "全部商店" : storeName(filters.store)} · ${filters.country ? esc(countryName(filters.country)) : "全部国家"}</span></div><p class="sub">${can("groups", "write") ? "点击应用名称查看档案，使用「调整」修正本组记录。" : "点击应用名称查看档案。"}</p>${memberTable(data)}<details class="member-data-notes"><summary>指标口径</summary><p>权限为商店声明，不代表用户实际授予；App Store 未提供同类公开清单。排名仅包含当前有效采样，未命中与暂无样本分别展示。累计安装下限不等于期间下载量；导入参考与实测上下架状态分开显示。</p></details></section>`
      }</div><p class="sub">${esc(data.scope)} 事件和经营指标按商店、国家及期间汇总，不随列表搜索或状态筛选改变。</p>`;
    bindMemberGrid(host, data);
    if (tab === "overview") bindCountryHints(host);
    host
      .querySelector("#group-edit-button")
      ?.addEventListener("click", () => editGroup(data.group));
    host.querySelector("#group-add-button")?.addEventListener("click", addForm);
    host
      .querySelector("#group-collect-button")
      ?.addEventListener("click", collectForm);
    if (can("groups", "write") && can("collection", "write")) {
      const button = document.createElement("button");
      button.className = "secondary";
      button.textContent = "启停详情 / 权限";
      button.id = "group-control-button";
      host.querySelector("#group-reload").before(button);
      button.onclick = () => controlForm(data);
    }
    host.querySelector("#group-reload").onclick = () =>
      load().catch((e) => toast(e.message));
    bindFilterBar(
      host.querySelector("#group-filter"),
      async (values) => {
        filters = {
          store: values.store,
          country: values.country,
          state: values.state || "",
          q: values.q || "",
          days: Number(values.days),
        };
        offset = 0;
        await load();
      },
      toast,
    );
    host.querySelectorAll("[data-group-tab]").forEach(
      (b) =>
        (b.onclick = () => {
          tab = b.dataset.groupTab;
          if (tab === "overview") {
            filters.state = "";
            filters.q = "";
            offset = 0;
          }
          load(true).catch((e) => toast(e.message));
        }),
    );
    host.querySelectorAll("[data-group-country]").forEach(
      (b) =>
        (b.onclick = () => {
          filters.country = b.dataset.groupCountry;
          filters.state = "";
          filters.q = "";
          tab = "matrix";
          offset = 0;
          load().catch((e) => toast(e.message));
        }),
    );
    bindPagination(host, "group-pages", (start, size) => {
      offset = start;
      pageSize = size;
      return load();
    });
    host
      .querySelectorAll("[data-group-member]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            openMember(Number(b.dataset.groupMember)).catch((e) =>
              toast(e.message),
            )),
      );
    const syncSelection = () => {
      const checks = [...host.querySelectorAll("[data-group-check]")];
      const selectedCount = checks.filter((c) => c.checked).length;
      const remove = host.querySelector("#group-remove");
      if (remove) remove.disabled = !selectedCount;
      const selectPage = host.querySelector("#group-select-page");
      if (selectPage) {
        selectPage.checked =
          checks.length > 0 && selectedCount === checks.length;
        selectPage.indeterminate =
          selectedCount > 0 && selectedCount < checks.length;
      }
      const caption = host.querySelector("#group-selection-count");
      if (caption)
        caption.textContent = selectedCount
          ? ` · 已选 ${selectedCount} 条`
          : "";
    };
    host
      .querySelectorAll("[data-group-check]")
      .forEach((c) => (c.onchange = syncSelection));
    host
      .querySelector("#group-select-page")
      ?.addEventListener("change", (event) => {
        host
          .querySelectorAll("[data-group-check]")
          .forEach((c) => (c.checked = event.target.checked));
        syncSelection();
      });
    host.querySelector("#group-remove")?.addEventListener("click", (event) =>
      busy(event.currentTarget, async () => {
        const ids = Array.from(
          host.querySelectorAll("[data-group-check]:checked"),
        ).map((c) => Number(c.dataset.groupCheck));
        const result = await api(`/groups/${id}/members/remove`, { ids });
        toast(result.note);
        offset = 0;
        await refreshGroups();
      }),
    );
    host
      .querySelector("#group-import-metrics")
      ?.addEventListener("click", importMetrics);
  }
  async function openMember(memberId) {
    const id = selected,
      days = filters.days;
    const el = dialog("应用监控档案", "<p>正在读取该应用的国家观察与指标…</p>");
    const r = await api(`/groups/${id}/members/${memberId}?` + qs({ days }));
    if (!el.isConnected) return;
    const m = r.member,
      p = r.listing?.payload || {};
    const moneySeries = {};
    for (const f of r.metrics) {
      const key = metricLabel(f);
      (moneySeries[key] ||= []).push({
        at: Date.parse(f.day) / 1000,
        value: Number(f.value),
        note: key,
      });
    }
    const sections = {
      overview: `<div class="cards">${panel("分组状态", `<strong>${esc(classifications[r.classification || r.listing?.state || "unverified"])}</strong><p class="sub">${esc(sources[r.classification_source || "unknown"])}</p>`)}${metric("评分", p.rating, "商店最近一次观察")}${metric("评分数", p.ratings, "累计公开评分数量")}${metric("权限声明项", r.permission?.permission_count, permissionNames[r.permission_state])}</div><dl class="detail-facts">${[
        ["商店实测", stateNames[r.listing?.state || "unverified"]],
        ["应用 ID", m.app_id],
        ["国家 / 语言", m.country.toUpperCase() + " / " + m.language],
        ["版本", p.version],
        ["开发者名称", m.developer || p.developer],
        ["名称来源", developerSources[m.developer_source]],
        ["开发者 ID", m.developer_id || p.developer_id],
        ["隐私政策", p.privacy_url],
        ["开发者网站", p.developer_website || p.developer_url],
        ["开发者邮箱", p.developer_email],
        ["系统要求", p.minimum_os],
        ["年龄分级", p.age_rating || p.content_rating],
        [
          "支持语言",
          Array.isArray(p.languages) ? p.languages.join(" / ") : p.languages,
        ],

        ["ID 来源", developerSources[m.developer_id_source]],
        ["关注分类", r.watch?.group_name],
        [
          "详情监控",
          r.watch ? (r.watch.enabled ? "已启用" : "已暂停") : "尚未配置",
        ],
        ["详情复查周期", r.watch ? r.watch.refresh_hours + " 小时" : null],
        ["最近检查", stamp(r.listing?.checked_at)],
        ["已计入缺失次数", r.listing?.misses],
        ["最近详情采集问题", r.job?.last_error],
        ["商店发布日期", p.released],
        ["商店更新时间", p.updated],
        [
          "商店售价",
          p.price == null ? null : num(p.price) + " " + (p.currency || ""),
        ],
        ["商店累计安装观察（全球）", p.installs_reported],
        [
          "累计安装下限",
          p.installs == null ? null : num(p.installs) + "+（全球口径）",
        ],
        [
          "导入参考",
          r.reference
            ? { on: "参考在架", off: "参考不在架", error: "参考异常" }[
                r.reference.state
              ] +
              " · " +
              r.reference.observed_text
            : null,
        ],
      ]
        .map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v ?? "—")}</dd></div>`)
        .join(
          "",
        )}</dl><p>${esc(p.summary || "")}</p><details><summary>应用描述</summary><p class="review-text">${esc(p.description || "尚未采集")}</p></details>`,
      permissions: `${
        Object.keys(r.adjustment?.permission_overrides || {}).length
          ? `<p class="group-permission-adjustments"><strong>本组修正</strong> ${Object.entries(
              r.adjustment.permission_overrides,
            )
              .map(
                ([name, declared]) =>
                  `${esc(permissionGroupName(name))} ${declared ? "✓" : "×"}`,
              )
              .join("　")}</p>`
          : ""
      }<p class="callout">${esc(permissionNames[r.permission_state])}${r.permission?.last_error ? " · " + esc(r.permission.last_error) : ""}</p><p class="sub">商店声明的权限，说明按英文接口取得；不代表用户实际授权。最近成功：${stamp(r.permission?.success_at)}</p>${
        r.permission?.payload != null
          ? Object.entries(r.permission.payload)
              .map(([name, items]) =>
                panel(
                  esc(permissionGroupName(name)),
                  `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`,
                ),
              )
              .join("") || "<p>有效响应未列出权限项。</p>"
          : "<p>暂无可用的权限清单。</p>"
      }${table(
        ["尝试时间", "结果", "问题"],
        r.permission_history.map((h) => [
          stamp(h.observed_at),
          esc(h.outcome),
          esc(h.error || "—"),
        ]),
      )}`,
      rankings:
        panel(
          "关键词排名",
          table(
            ["关键词", "排名", "采集时间", "状态"],
            r.keywords.map((k) => [
              `<button class="link" data-rank-kind="keyword" data-rank-target="${k.target_id}">${esc(k.keyword)}</button>`,
              num(k.position),
              stamp(k.observed_at),
              k.fresh
                ? k.position == null
                  ? "本次未命中"
                  : "有效样本"
                : "旧样本 / 待刷新",
            ]),
          ),
        ) +
        panel(
          "排行榜",
          table(
            ["榜类 / 分类", "排名", "采集时间", "状态"],
            r.charts.map((c) => [
              `<button class="link" data-rank-kind="chart" data-rank-target="${c.target_id}">${esc(c.collection + " / " + (c.category || "总榜"))}</button>`,
              num(c.position),
              stamp(c.observed_at),
              c.fresh
                ? c.position == null
                  ? "未进入返回范围"
                  : "有效样本"
                : "旧样本 / 待刷新",
            ]),
          ),
        ) +
        '<p class="sub">每类最多显示1000个目标；点击关键词或榜单可查看所选期间最近500次采样趋势。</p><div id="group-rank-history"></div>',
      metrics: `<p class="sub">下载与商店净收入按国家、日期、实际/估算、币种分别展示。收入范围为付费下载、内购和订阅；累计安装量不换算成日下载。</p>${
        Object.entries(moneySeries)
          .map(([name, points]) => panel(esc(name), chart(points)))
          .join("") ||
        '<p class="empty">尚无下载 / 收入数据，可通过分组看板导入授权指标。</p>'
      }${panel(
        "评分变化",
        chart(
          [...r.snapshots]
            .reverse()
            .map((s) => ({
              at: s.observed_at,
              value: s.payload.rating ?? null,
            })),
          false,
          stamp,
          { label: "评分", domain: [0, 5], precision: 2 },
        ),
      )}${table(
        ["日期", "指标", "数值", "币种", "口径"],
        r.metrics.map((f) => [
          esc(f.day),
          f.metric === "downloads" ? "下载" : "收入",
          esc(f.value),
          esc(f.currency || "—"),
          f.is_estimate ? "估算" : "实际报表",
        ]),
      )}`,
      events: table(
        ["时间", "事件", "变更"],
        r.events.map((e) => [
          stamp(e.observed_at),
          esc(eventNames[e.kind] || e.kind),
          `<details><summary>查看</summary><pre>${esc(e.changes)}</pre></details>`,
        ]),
      ),
      reviews: table(
        ["评分", "时间", "评论"],
        r.reviews.map((v) => [
          num(v.rating),
          dateValue(v.published_at),
          `<strong>${esc(v.title || "")}</strong><p>${esc(v.content)}</p>`,
        ]),
      ),
    };
    sections.rankings += panel(
      "导入参考关键词报告",
      `<p class="sub">保留报告原始日期和口径，不计入当前有效采样命中；此处可查看本市场最近100份报告，不受上方期间筛选限制。</p>${table(
        ["报告日期", "词表 / 行数", "操作"],
        (r.reports || []).map((report) => [
          stamp(report.observed_at),
          (report.summary || [])
            .map(
              (s) =>
                esc(s.sheet) + " · " + num(s.imported_keyword_rows ?? s.rows),
            )
            .join("<br>"),
          `<button class="secondary" data-reference-report="${report.id}">查看报告词表</button>`,
        ]),
      )}<div id="group-report-content"></div>`,
    );
    if (p.store_fields)
      sections.overview += `<details class="member-data-notes"><summary>其他已采集商店字段</summary>${table(
        ["字段", "内容"],
        Object.entries(p.store_fields)
          .filter(([, v]) => v != null)
          .map(([k, v]) => [
            esc(k),
            esc(typeof v === "object" ? JSON.stringify(v) : v),
          ]),
      )}</details>`;
    el.classList.add("member-detail-dialog");
    el.innerHTML = `<form method="dialog" class="picker-heading"><div><h2>${esc(m.name || m.app_id)}</h2><p class="sub">${storeName(m.store)} · ${esc(m.country.toUpperCase())} / ${esc(m.language)} · 近 ${days} 天</p></div><button class="secondary" aria-label="关闭">关闭</button></form><div class="group-tabs">${[
      ["overview", "资料与状态"],
      ["permissions", "权限"],
      ["rankings", "关键词 / 榜单"],
      ["metrics", "下载 / 收入 / 评分"],
      ["events", "上下架与变动"],
      ["reviews", "评论"],
    ]
      .map(([v, n]) => `<button data-member-tab="${v}">${n}</button>`)
      .join(
        "",
      )}</div>${(r.markets || []).length > 1 ? `<label>切换本组内的应用市场<select id="group-member-market">${r.markets.map((v) => `<option value="${v.id}" ${v.id === memberId ? "selected" : ""}>${esc(countryName(v.country))} / ${esc(v.language)} · ${esc(stateNames[v.state || "unverified"])}</option>`).join("")}</select></label>` : ""}${can("groups", "write") && can("collection", "write") ? '<button id="group-member-refresh" class="secondary">复核当前应用的详情 / 权限</button>' : ""}<div id="group-member-content"></div>${can("apps") ? `<a class="button secondary" href="#app/${encodeURIComponent(m.store)}/${encodeURIComponent(m.app_id)}?country=${encodeURIComponent(m.country)}&language=${encodeURIComponent(m.language)}">打开完整应用详情</a>` : ""}`;
    el.querySelector("#group-member-market")?.addEventListener(
      "change",
      (event) =>
        openMember(Number(event.target.value)).catch((error) =>
          toast(error.message),
        ),
    );
    el.querySelector("#group-member-refresh")?.addEventListener(
      "click",
      (event) =>
        busy(event.currentTarget, async () => {
          const result = await api(`/groups/${id}/collect`, {
            member_id: memberId,
            kinds: ["details", "permissions"],
          });
          toast(result.note);
        }),
    );
    let reportRevision = 0;
    async function showReport(reportId, sheet = null, reportOffset = 0) {
      sheet ||=
        (r.reports || [])
          .find((report) => report.id === reportId)
          ?.summary.find((s) =>
            ["ASO关键词", "ASA竞价词", "谷歌付费关键词"].includes(s.sheet),
          )?.sheet || "ASO关键词";
      const host = el.querySelector("#group-report-content"),
        ticket = ++reportRevision;
      if (!host) return;
      host.innerHTML = "<p>正在读取参考词表…</p>";
      const data = await api(
        `/groups/${id}/members/${memberId}/report?` +
          qs({ report_id: reportId, sheet, offset: reportOffset }),
      );
      if (!host.isConnected || ticket !== reportRevision) return;
      host.innerHTML = `<label>词表<select id="group-report-sheet">${["ASO关键词", "ASA竞价词", "谷歌付费关键词"].map((s) => `<option ${s === sheet ? "selected" : ""}>${s}</option>`).join("")}</select></label><p>共 ${num(data.total)} 行 · 导入参考</p>${table(
        ["选择", "关键词", "报告排名", "报告流行度", "报告搜索指数"],
        data.items.map((row, i) => [
          `<input type="checkbox" data-report-word="${i}" aria-label="选择 ${esc(row.keyword)}">`,
          esc(row.keyword),
          num(row.position),
          esc(row.popularity ?? "—"),
          esc(row.search_index ?? "—"),
        ]),
      )}<div class="toolbar">${pagination({ id: "group-report-pages", offset: reportOffset, limit: data.limit, total: data.total, prevId: "group-report-prev", nextId: "group-report-next" })}${can("groups", "write") && can("collection", "write") ? '<button id="group-report-track" disabled>追踪所选关键词</button>' : ""}</div>`;
      host.querySelector("#group-report-sheet").onchange = (event) =>
        showReport(reportId, event.target.value, 0).catch((error) =>
          toast(error.message),
        );
      bindPagination(host, "group-report-pages", (start) =>
        showReport(reportId, sheet, start),
      );
      host.querySelectorAll("[data-report-word]").forEach(
        (c) =>
          (c.onchange = () => {
            const button = host.querySelector("#group-report-track");
            if (button)
              button.disabled = !host.querySelector(
                "[data-report-word]:checked",
              );
          }),
      );
      host
        .querySelector("#group-report-track")
        ?.addEventListener("click", (event) =>
          busy(event.currentTarget, async () => {
            const keywords = Array.from(
              host.querySelectorAll("[data-report-word]:checked"),
            ).map((c) => data.items[Number(c.dataset.reportWord)].keyword);
            const result = await api(`/groups/${id}/collect`, {
              member_id: memberId,
              kinds: ["keywords"],
              keywords,
            });
            toast(result.note);
          }),
        );
    }
    let rankRevision = 0;
    const show = (name) => {
      el.querySelector("#group-member-content").innerHTML = sections[name];
      el.querySelectorAll("[data-member-tab]").forEach((b) =>
        b.classList.toggle("active", b.dataset.memberTab === name),
      );
      el.querySelectorAll("[data-reference-report]").forEach(
        (button) =>
          (button.onclick = () =>
            showReport(Number(button.dataset.referenceReport)).catch((error) =>
              toast(error.message),
            )),
      );
      el.querySelectorAll("[data-rank-target]").forEach(
        (button) =>
          (button.onclick = () =>
            busy(button, async () => {
              const ticket = ++rankRevision,
                host = el.querySelector("#group-rank-history");
              host.innerHTML = "<p>正在读取排名历史…</p>";
              const result = await api(
                `/groups/${id}/members/${memberId}/rank-history?` +
                  qs({
                    kind: button.dataset.rankKind,
                    target_id: button.dataset.rankTarget,
                    days,
                  }),
              );
              if (host.isConnected && ticket === rankRevision)
                host.innerHTML = panel(
                  esc(result.label),
                  chart(result.points, true),
                );
            })),
      );
    };
    show("overview");
    el.querySelectorAll("[data-member-tab]").forEach(
      (b) => (b.onclick = () => show(b.dataset.memberTab)),
    );
  }
  return { render, addSelected };
}
