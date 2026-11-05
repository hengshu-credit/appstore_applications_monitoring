export function initShellLayout(doc = document, win = window) {
  const root = doc.documentElement;
  const button = doc.querySelector("#sidebar-toggle");
  const key = "appmon-sidebar-collapsed";
  let collapsed = false;
  try {
    collapsed = win.localStorage.getItem(key) === "true";
  } catch {}
  function apply() {
    root.dataset.sidebarCollapsed = String(collapsed);
    const label = collapsed ? "展开侧边栏" : "收起侧边栏";
    button.setAttribute("aria-expanded", String(!collapsed));
    button.setAttribute("aria-label", label);
    button.title = label;
  }
  apply();
  button.addEventListener("click", () => {
    collapsed = !collapsed;
    apply();
    try {
      win.localStorage.setItem(key, String(collapsed));
    } catch {}
  });
}
