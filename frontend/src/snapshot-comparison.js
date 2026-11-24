const labels = {
  version: "版本", release_notes: "开发者更新说明", name: "应用名称", summary: "简短描述",
  description: "应用描述", icon: "应用图标", screenshots: "商店截图", updated: "商店更新时间",
  released: "首次发布", developer: "开发者", developer_id: "开发者 ID", category: "分类",
  rating: "商店评分", ratings: "评分数", reviews: "评论数", installs: "安装量下限",
  installs_reported: "安装量", price: "价格", currency: "币种", minimum_os: "最低系统版本",
  languages: "支持语言", size_bytes: "应用大小（字节）", rating_distribution: "星级分布",
  age_rating: "年龄分级", has_ads: "含广告", has_iap: "含应用内购",
};

function publishedNote(payload) {
  const fields = payload?.store_fields;
  const raw = [payload?.release_notes, fields?.releaseNotes, fields?.recentChanges, fields?.recentChangesHTML]
    .find((value) => typeof value === "string" && value.trim());
  if (!raw) return "";
  if (!/<(?:br|p|div|li|b|i|strong)\b|&(?:#\d+|#x[\da-f]+|\w+);/i.test(raw)) return raw.trim();
  const template = document.createElement("template");
  template.innerHTML = raw.replace(/<br\s*\/?\s*>|<\/(?:p|div|li)>/gi, "\n");
  return template.content.textContent.trim();
}

// Every candidate belongs to the same store, app, country and language as this detail response.
export function versionNote(record, data) {
  const text = publishedNote(record?.payload);
  if (text) return { text };
  const version = record?.payload?.version;
  if (!version) return null;
  const candidates = data.snapshots.filter((s) => s !== record && s.payload?.version === version);
  if (data.listing?.payload?.version === version && data.listing !== record)
    candidates.push({ payload: data.listing.payload, observed_at: data.listing.last_success });
  const at = record.observed_at ?? record.last_success ?? 0;
  candidates.sort((a, b) => Math.abs(a.observed_at - at) - Math.abs(b.observed_at - at));
  for (const candidate of candidates) {
    const note = publishedNote(candidate.payload);
    if (note) return { text: note, supplemental: true, at: candidate.observed_at };
  }
  return null;
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, stableValue(value[k])]));
  return value;
}

function compareFields(left, right, raw = false) {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  const ordered = raw ? [...keys].sort() : [...new Set([...Object.keys(labels), ...keys])];
  const rows = [];
  for (const key of ordered) {
    if (!keys.has(key) || (!raw && key === "store_fields")) continue;
    const normalize = (v) => {
      if (v == null || v === "") return null;
      if (!raw && key === "screenshots" && Array.isArray(v)) return [...new Set(v)];
      return stableValue(v);
    };
    const before = normalize(left[key]), after = normalize(right[key]);
    if (before === null && after === null) continue;
    const kind = JSON.stringify(before) === JSON.stringify(after) ? "一致" :
      before === null ? "新增" : after === null ? "移除" : "修改";
    rows.push({ key, before, after, kind });
  }
  return rows;
}

export function mountSnapshotComparison(host, data, { esc, stamp, snapshotMedia }) {
  const { snapshots } = data;
  if (!snapshots.length) {
    host.innerHTML = '<p class="sub">暂无已保存的快照。</p>';
    return;
  }
  const caption = (s) => `${s.payload?.version || "版本未知"} · ${stamp(s.observed_at)}`;
  const options = snapshots.map((s, index) => `<option value="${index}">${esc(caption(s))}</option>`).join("");
  const multiple = snapshots.length > 1;
  const selector = (name, label) => `<label>${label}<select name="${name}" aria-label="${label}">${options}</select></label>`;
  host.innerHTML = `<div class="snapshot-selectors">${multiple ? selector("base", "左侧 · 基准快照") : ""}${selector("target", multiple ? "右侧 · 对比快照" : "已保存快照")}</div>
    <div class="snapshot-previews"></div>
    ${multiple ? '<div class="snapshot-diff-tools"><strong aria-live="polite"></strong><label><input type="checkbox" checked>只看差异</label></div>' : '<p class="sub">当前市场仅有一份快照。</p>'}
    <div class="snapshot-diff-fields"></div>`;
  const base = host.querySelector('[name="base"]'), target = host.querySelector('[name="target"]');
  const onlyChanges = host.querySelector('input[type="checkbox"]');
  if (base) {
    const version = snapshots[0].payload?.version;
    const previous = snapshots.findIndex((s, i) => i > 0 && s.payload?.version && s.payload.version !== version);
    base.value = String(version && previous > 0 ? previous : 1);
  }
  function preview(s) {
    const note = versionNote(s, data);
    return `<section class="snapshot-preview"><h3>${esc(caption(s))}</h3>
      <h4>开发者更新说明</h4>${note ? `<div class="snapshot-note">${esc(note.text)}</div>${note.supplemental ? `<small class="sub">取自同版本的其他商店记录${note.at ? " · " + esc(stamp(note.at)) : ""}</small>` : ""}` : '<p class="sub">此市场未采集到该版本的开发者更新说明。</p>'}
      ${snapshotMedia(s.payload || {})}</section>`;
  }
  function valueCell(value, side, kind, key) {
    if (value === null) return '<div class="snapshot-value sub">—</div>';
    let text = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
    if (["updated", "released"].includes(key) && typeof value === "number") text = stamp(value);
    const change = kind === "一致" ? "" : side === "left" ? " removed-value" : " added-value";
    return `<div class="snapshot-value${change}">${esc(text)}</div>`;
  }
  function rowHtml(row, raw) {
    const { key, before, after, kind } = row;
    return `<article class="snapshot-diff-row"><header><strong>${esc(raw ? key : labels[key] || key)}</strong><span>${kind}</span></header>
      <div class="snapshot-value-pair">${valueCell(before, "left", kind, key)}${valueCell(after, "right", kind, key)}</div></article>`;
  }
  function renderFields() {
    if (!multiple) return;
    const same = base.value === target.value;
    const left = snapshots[base.value].payload || {}, right = snapshots[target.value].payload || {};
    const rows = compareFields(left, right);
    const originals = compareFields(left.store_fields || {}, right.store_fields || {}, true);
    const changes = rows.filter((r) => r.kind !== "一致");
    const rawChanges = originals.filter((r) => r.kind !== "一致");
    host.querySelector(".snapshot-diff-tools strong").textContent = same ? "已选择同一快照" :
      changes.length || rawChanges.length ? `资料字段差异 ${changes.length} 项 · 原始字段差异 ${rawChanges.length} 项` : "两份快照的资料一致";
    const shown = onlyChanges.checked ? changes : rows;
    const shownRaw = onlyChanges.checked ? rawChanges : originals;
    host.querySelector(".snapshot-diff-fields").innerHTML = shown.map((r) => rowHtml(r, false)).join("") +
      (shownRaw.length ? `<details class="snapshot-raw"><summary>商店原始字段（${shownRaw.length} 项）</summary></details>` : "");
    const details = host.querySelector(".snapshot-raw");
    details?.addEventListener("toggle", () => {
      if (details.open && details.childElementCount === 1)
        details.insertAdjacentHTML("beforeend", shownRaw.map((r) => rowHtml(r, true)).join(""));
    });
  }
  function render() {
    const pair = multiple ? [snapshots[base.value], snapshots[target.value]] : [snapshots[0]];
    host.querySelector(".snapshot-previews").innerHTML = pair.map(preview).join("");
    renderFields();
  }
  base?.addEventListener("change", render);
  target.addEventListener("change", render);
  onlyChanges?.addEventListener("change", renderFields);
  render();
  return (from, to) => {
    if (!base) return;
    base.value = String(from);
    target.value = String(to);
    target.dispatchEvent(new Event("change", { bubbles: true }));
  };
}
