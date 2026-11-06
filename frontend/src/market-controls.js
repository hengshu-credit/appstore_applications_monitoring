import { icon, storeLogo } from "./icons.js";
import { autoCaption, languageChoices, toggleStore } from "./market-scope.js";
export const languages = [
  ["en", "英语"],
  ["zh-cn", "简体中文"],
  ["zh-tw", "繁体中文"],
  ["th", "泰语"],
  ["vi", "越南语"],
  ["fil", "菲律宾语"],
  ["id", "印度尼西亚语"],
  ["ms", "马来语"],
  ["ja", "日语"],
  ["ko", "韩语"],
  ["hi", "印地语"],
  ["ar", "阿拉伯语"],
  ["es", "西班牙语"],
  ["pt-br", "葡萄牙语（巴西）"],
  ["pt", "葡萄牙语"],
  ["fr", "法语"],
  ["de", "德语"],
  ["it", "意大利语"],
  ["ru", "俄语"],
  ["tr", "土耳其语"],
  ["bn", "孟加拉语"],
  ["ur", "乌尔都语"],
  ["ta", "泰米尔语"],
  ["te", "泰卢固语"],
  ["nl", "荷兰语"],
  ["pl", "波兰语"],
  ["uk", "乌克兰语"],
  ["sv", "瑞典语"],
  ["da", "丹麦语"],
  ["fi", "芬兰语"],
  ["no", "挪威语"],
  ["he", "希伯来语"],
  ["el", "希腊语"],
  ["ro", "罗马尼亚语"],
  ["hu", "匈牙利语"],
  ["cs", "捷克语"],
  ["sk", "斯洛伐克语"],
  ["sw", "斯瓦希里语"],
  ["en-us", "英语（美国）"],
  ["en-gb", "英语（英国）"],
  ["en-ph", "英语（菲律宾）"],
];
const esc = (v) =>
  String(v).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export function initMarketControls(win = window) {
  const doc = win.document;
  const controllers = [];
  let preferences;
  for (const kind of ["store", "language"]) {
    const input = doc.querySelector(`#global-${kind}`),
      trigger = doc.querySelector(`#${kind}-trigger`);
    if (!input || !trigger) continue;
    const menu = doc.querySelector(`#${kind}-menu`);
    const choicesFor = () =>
      kind === "store"
        ? [
            ["google", "Google Play"],
            ["apple", "App Store"],
          ]
        : languageChoices(
            preferences,
            doc.querySelector("#global-store").value,
            doc.querySelector("#global-country").value,
            languages,
          );
    function label(code, name) {
      return kind === "store"
        ? `${code === "all" ? `<span class="store-logo-stack">${storeLogo("google")}${storeLogo("apple")}</span>` : storeLogo(code)}<span>${name}</span>`
        : `<span>${esc(name)}</span><small>${esc(code)}</small>`;
    }
    function sync() {
      const choices = choicesFor();
      const selected = (kind === "store" && input.value === "all"
        ? ["all", "2 个商店"]
        : choices.find((v) => v[0] === input.value)) || [
        input.value,
        "自定义语言",
      ];
      trigger.querySelector(".market-value").innerHTML =
        input.value === "auto"
          ? esc(
              autoCaption(
                preferences,
                doc.querySelector("#global-store").value,
                doc.querySelector("#global-country").value,
              ),
            )
          : label(...selected);
      trigger.title = trigger.textContent.trim();
    }
    function close(focus = false) {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (focus) trigger.focus();
    }
    function render(query = "") {
      const choices = choicesFor();
      let rows = choices.filter(([code, name]) =>
        (code + " " + name).toLowerCase().includes(query.toLowerCase()),
      );
      if (
        kind === "language" &&
        /^[a-z]{2,3}(?:-[a-z]{2}|-[a-z]{4})?(?:-[a-z]{2})?$/.test(query) &&
        (!!preferences?.languages?.[query.split("-")[0]] ||
          languages.some(([c]) => c === query.split("-")[0])) &&
        !["kz", "us", "und", "zxx"].includes(query) &&
        !choices.some((v) => v[0] === query)
      )
        rows = [...rows, [query, "使用此语言代码"]];
      menu.querySelector('[role="listbox"]').innerHTML =
        rows
          .map(
            ([code, name]) =>
              `<button type="button" role="option" aria-selected="${code === input.value || (kind === "store" && input.value === "all")}" data-value="${esc(code)}">${label(code, name)}${kind === "store" ? '<span class="choice-check" aria-hidden="true">✓</span>' : ""}</button>`,
          )
          .join("") || '<p class="sub">没有匹配语言，可输入语言代码</p>';
    }
    trigger.addEventListener("click", () => {
      const opening = menu.hidden;
      controllers.forEach((c) => c.close());
      if (!opening) return;
      render();
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      const search = menu.querySelector("input");
      if (search) {
        search.value = "";
        search.focus();
      } else menu.querySelector('[aria-selected="true"]')?.focus();
    });
    menu
      .querySelector("input")
      ?.addEventListener("input", (e) =>
        render(e.target.value.trim().toLowerCase()),
      );
    menu.addEventListener("click", (e) => {
      const option = e.target.closest("[data-value]");
      if (!option) return;
      if (kind === "store") {
        // The canonical "all" value represents both checked stores.
        if (input.value === option.dataset.value) return;
        input.value = toggleStore(input.value, option.dataset.value);
      } else input.value = option.dataset.value;
      sync();
      if (kind === "store") {
        menu
          .querySelectorAll('[role="option"]')
          .forEach((row) =>
            row.setAttribute(
              "aria-selected",
              String(
                input.value === "all" || input.value === row.dataset.value,
              ),
            ),
          );
      } else close(true);
      input.dispatchEvent(new win.Event("change", { bubbles: true }));
    });
    menu.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        close(true);
        e.preventDefault();
      }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
        const options = [...menu.querySelectorAll('[role="option"]')];
        let index = options.indexOf(doc.activeElement);
        index =
          e.key === "Home"
            ? 0
            : e.key === "End"
              ? options.length - 1
              : (index + (e.key === "ArrowDown" ? 1 : -1) + options.length) %
                options.length;
        options[index]?.focus();
        e.preventDefault();
      }
    });
    trigger.addEventListener("keydown", (e) => {
      if (["ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        if (menu.hidden) trigger.click();
      }
    });
    doc.addEventListener("click", (e) => {
      if (!e.composedPath().includes(trigger.parentElement)) close();
    });
    trigger.parentElement.addEventListener("focusout", () => {
      win.setTimeout(() => {
        if (!trigger.parentElement.contains(doc.activeElement)) close();
      }, 0);
    });
    input.addEventListener("change", sync);
    sync();
    controllers.push({ sync, close });
  }
  doc.querySelector("#refresh-page").innerHTML = icon("refresh");
  doc.querySelector("#open-theme").innerHTML =
    icon("settings") + "<span>主题设置</span>";
  const countryArrow = doc.querySelector("#country-trigger > :last-child");
  if (countryArrow)
    countryArrow.outerHTML = icon("chevron").replace(
      'class="ui-icon"',
      'class="ui-icon chevron"',
    );
  return {
    sync: () => controllers.forEach((c) => c.sync()),
    setPreferences(value) {
      preferences = value;
      controllers.forEach((c) => c.sync());
    },
  };
}
