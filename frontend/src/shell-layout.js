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
  const toggle = doc.querySelector("#mobile-nav-toggle"),
    navigation = doc.querySelector("#navigation"),
    sidebar = doc.querySelector(".app-sidebar");
  if (!toggle || !navigation || !sidebar) return;
  const mobile = win.matchMedia("(max-width: 750px)");
  let open = false;
  const showNavigation = (value, restoreFocus = false) => {
    open = mobile.matches && value;
    root.dataset.mobileNavOpen = String(open);
    navigation.hidden = mobile.matches && !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "关闭导航菜单" : "打开导航菜单");
    if (restoreFocus) toggle.focus();
  };
  showNavigation(false);
  toggle.addEventListener("click", () => showNavigation(!open));
  toggle.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      showNavigation(true);
      navigation
        .querySelector('[aria-current="page"], a:not([hidden])')
        ?.focus();
    }
  });
  navigation.addEventListener("click", (event) => {
    if (event.target.closest("a") && mobile.matches)
      showNavigation(false, true);
  });
  doc.addEventListener("keydown", (event) => {
    if (open && event.key === "Escape") {
      event.preventDefault();
      showNavigation(false, true);
    }
  });
  doc.addEventListener("click", (event) => {
    if (open && !sidebar.contains(event.target)) showNavigation(false);
  });
  doc.addEventListener("focusin", (event) => {
    if (open && !sidebar.contains(event.target)) showNavigation(false);
  });
  mobile.addEventListener("change", () => showNavigation(false));
  win.addEventListener("hashchange", () => showNavigation(false));
}
