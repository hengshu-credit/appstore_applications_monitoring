const pagerNumber = (v) => Number(v).toLocaleString("zh-CN");
export function pagination({
  id,
  offset = 0,
  limit = 50,
  total,
  pages,
  hasMore,
  prevId,
  nextId,
  sizes = [],
  unit = "条",
}) {
  const page = Math.floor(offset / limit) + 1;
  const last =
    pages ?? (total == null ? null : Math.max(1, Math.ceil(total / limit)));
  const canNext = hasMore ?? (last != null && page < last);
  const numbers = [
    ...new Set(
      [
        1,
        ...Array.from({ length: 6 }, (_, i) => Math.max(1, page - 2) + i),
        last,
      ].filter(
        (n) =>
          n > 0 && (last == null ? n <= page + (canNext ? 1 : 0) : n <= last),
      ),
    ),
  ].sort((a, b) => a - b);
  const button = (n, label, attrs = "") =>
    `<button type="button" class="secondary pager-button" data-page="${n}" ${attrs}>${label}</button>`;
  return `<div class="table-pager" data-pager="${id}" data-limit="${limit}" data-current="${page}" ${last == null ? "" : `data-last="${last}"`} aria-label="表格分页">
    <span class="pager-summary">${total == null ? `第 ${pagerNumber(page)} 页` : `共 ${pagerNumber(total)} ${unit}`}</span>
    <select data-page-size aria-label="每页条数" ${sizes.length ? "" : "disabled"}>${(sizes.length ? sizes : [limit]).map((n) => `<option value="${n}" ${n === limit ? "selected" : ""}>${n}${unit}/页</option>`).join("")}</select>
    <span class="pager-controls">${button(page - 1, "‹", `id="${prevId || id + "-prev"}" aria-label="上一页" ${page <= 1 ? "disabled" : ""}`)}${numbers.map((n, i) => (i && n - numbers[i - 1] > 1 ? '<span aria-hidden="true">…</span>' : "") + button(n, pagerNumber(n), `aria-label="第 ${n} 页" ${n === page ? 'aria-current="page"' : ""}`)).join("")}${button(page + 1, "›", `id="${nextId || id + "-next"}" aria-label="下一页" ${canNext ? "" : "disabled"}`)}</span>
    <span class="pager-jump"><label>跳至<input type="number" min="1" ${last == null ? "" : `max="${last}"`} step="1" value="${page}" aria-label="跳转页码"></label><button type="button" class="secondary pager-button" data-page-jump>跳转</button></span><span class="pager-feedback" role="status"></span></div>`;
}
export function bindPagination(root, id, onPage) {
  const el = root.querySelector(`[data-pager="${id}"]`);
  if (!el) return;
  const input = el.querySelector("input"),
    page = Number(el.dataset.current),
    limit = Number(el.dataset.limit);
  let pending = false;
  const go = async (n, size = limit) => {
    if (pending || (n === page && size === limit)) return;
    if (
      !Number.isSafeInteger(n) ||
      n < 1 ||
      (el.dataset.last && n > Number(el.dataset.last))
    ) {
      input.reportValidity();
      return;
    }
    pending = true;
    el.setAttribute("aria-busy", "true");
    try {
      await onPage((n - 1) * size, size);
    } catch (e) {
      el.querySelector(".pager-feedback").textContent = e.message;
    } finally {
      pending = false;
      el.removeAttribute("aria-busy");
    }
  };
  el.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.hasAttribute("data-page-jump")) go(Number(input.value));
    else if (button.dataset.page) go(Number(button.dataset.page));
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      go(Number(input.value));
    }
  });
  el.querySelector("[data-page-size]")?.addEventListener("change", (e) =>
    go(1, Number(e.target.value)),
  );
}
