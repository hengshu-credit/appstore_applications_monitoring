// Only visible app cells are resolved; no full-pool icon scan or remote image proxy.
export function createAppIconManager({ api, getMarket, win = window }) {
  const doc = win.document,
    records = new Map(),
    observed = new WeakSet();
  let timer = null,
    poll = null,
    busy = false;
  const dataFor = (node) => {
    const m = getMarket(node.dataset.iconStore);
    return {
      store: node.dataset.iconStore,
      app_id: node.dataset.iconApp,
      country: node.dataset.iconCountry || m.country,
      language: node.dataset.iconLanguage || m.language,
      source_url: node.dataset.iconSource || "",
      name: node.dataset.iconName || node.dataset.iconApp,
    };
  };
  const keyFor = (v) => [v.store, v.app_id, v.country, v.language].join("\0");
  function apply(record) {
    for (const node of record.nodes) {
      if (!node.isConnected) {
        record.nodes.delete(node);
        continue;
      }
      const result = record.result,
        image = node.querySelector("img"),
        fallback = node.querySelector(".appicon-placeholder"),
        button = node.querySelector("[data-icon-refresh]");
      node.setAttribute("aria-busy", String(!!result?.pending));
      if (result?.url?.startsWith("/api/icons/")) {
        if (image.getAttribute("src") !== result.url) {
          image.hidden = true;
          fallback.hidden = false;
          image.src = result.url;
        }
      }
      button.disabled = !!result?.pending;
      button.title = result?.pending
        ? "图标正在采集"
        : result?.status === "paused"
          ? "恢复并补采图标"
          : result?.url
            ? "重新采集图标"
            : "图标缺失，点击采集";
      button.setAttribute("aria-label", button.title);
      node.dataset.iconStatus = result?.status || "pending";
    }
  }
  function schedule() {
    win.clearTimeout(timer);
    timer = win.setTimeout(flush, 100);
  }
  async function flush() {
    if (busy) return;
    const candidates = [];
    for (const record of records.values()) {
      for (const n of record.nodes)
        if (!n.isConnected) {
          record.nodes.delete(n);
          observer?.unobserve(n);
        }
      if (
        [...record.nodes].some((n) => n.dataset.iconVisible === "true") &&
        (record.dirty || record.result?.pending)
      )
        candidates.push(record);
    }
    if (!candidates.length) return;
    busy = true;
    try {
      for (let offset = 0; offset < candidates.length; offset += 50) {
        const batch = candidates.slice(offset, offset + 50);
        const result = await api("/icons/resolve", {
          items: batch.map((r) => r.data),
        });
        for (let i = 0; i < batch.length; i++) {
          const record = batch[i];
          record.result = result.items[i];
          record.dirty = false;
          record.checkedAt = Date.now();
          apply(record);
        }
      }
    } catch {
      for (const record of candidates) {
        record.dirty = false;
        record.result = { status: "error", pending: false };
        apply(record);
      }
    } finally {
      busy = false;
      win.clearTimeout(poll);
      if (
        [...records.values()].some(
          (r) =>
            r.result?.pending &&
            [...r.nodes].some(
              (n) => n.isConnected && n.dataset.iconVisible === "true",
            ),
        )
      )
        poll = win.setTimeout(flush, 3000);
    }
  }
  const observer = win.IntersectionObserver
    ? new win.IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            entry.target.dataset.iconVisible = String(entry.isIntersecting);
            if (entry.isIntersecting) {
              const record = records.get(keyFor(dataFor(entry.target)));
              if (
                record &&
                !record.result?.pending &&
                (record.result?.status !== "ready" ||
                  Date.now() - record.checkedAt > 30000)
              )
                record.dirty = true;
            }
          }
          schedule();
        },
        { rootMargin: "150px" },
      )
    : null;
  function scan() {
    for (const node of doc.querySelectorAll(
      "[data-icon-store][data-icon-app]",
    )) {
      if (observed.has(node)) continue;
      observed.add(node);
      const data = dataFor(node),
        key = keyFor(data);
      let record = records.get(key);
      if (!record) {
        record = { data, nodes: new Set(), dirty: true, checkedAt: 0 };
        records.set(key, record);
      }
      record.nodes.add(node);
      apply(record);
      const image = node.querySelector("img"),
        fallback = node.querySelector(".appicon-placeholder");
      image.addEventListener("load", () => {
        image.hidden = false;
        fallback.hidden = true;
      });
      image.addEventListener("error", () => {
        image.hidden = true;
        fallback.hidden = false;
        node.dataset.iconStatus = "error";
      });
      if (observer) observer.observe(node);
      else node.dataset.iconVisible = "true";
    }
    schedule();
  }
  const mutation = new win.MutationObserver(scan);
  mutation.observe(doc.body, { childList: true, subtree: true });
  doc.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-icon-refresh]");
    if (!button) return;
    event.preventDefault();
    const node = button.closest("[data-icon-store]"),
      record = records.get(keyFor(dataFor(node)));
    if (!record) return;
    button.disabled = true;
    try {
      const result = await api("/icons/resolve", {
        items: [record.data],
        force: true,
      });
      record.result = result.items[0];
      record.dirty = false;
      apply(record);
      schedule();
    } catch {
      button.disabled = false;
      button.title = "请求失败，点击重试";
    }
  });
  scan();
  return {
    scan,
    destroy() {
      mutation.disconnect();
      observer?.disconnect();
      win.clearTimeout(timer);
      win.clearTimeout(poll);
    },
  };
}
