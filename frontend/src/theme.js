// Independent implementation of the Tianshu theme contract and token workflow.
export const PRESETS = [
  ["THEME_BLUE", "主题蓝", "#2639E9"],
  ["LIQUID_PURPLE", "凝液紫", "#873FF2"],
  ["PEACH_PINK", "桃粉红", "#FF6B9D"],
  ["NEON_PINK", "霓虹粉", "#E14ECA"],
  ["MIDNIGHT_PURPLE", "深夜紫", "#19103B"],
  ["INDIGO_PURPLE", "靛青紫", "#5B3DF5"],
  ["NEBULA_BLUE", "星云蓝", "#536DFE"],
  ["CRYSTAL_BLUE", "晶石蓝", "#2F80ED"],
  ["THEME_BLUE_GRADIENT", "主题蓝渐变", "#2639E9", "#536DFE"],
  ["LIQUID_PURPLE_GRADIENT", "凝液紫渐变", "#19103B", "#873FF2"],
  ["PEACH_PINK_GRADIENT", "桃粉渐变", "#873FF2", "#E14ECA"],
  ["NEBULA_GRADIENT", "星云渐变", "#536DFE", "#E14ECA"],
];
export const DEFAULT_THEME = Object.freeze({
  schemaVersion: 1,
  colorScheme: "LIGHT",
  accentMode: "PRESET",
  accentPreset: "THEME_BLUE",
  customSolidColor: "#2639E9",
  customGradientColors: ["#2639E9", "#873FF2"],
  customGradientType: "LINEAR",
  customGradientAngle: 135,
  navigationLayout: "LEFT",
  sidebarTheme: "DARK",
  contentWidth: "FLUID",
  tableScrollMode: "AUTO",
  fixedSidebar: true,
  colorWeak: false,
});
export const THEME_KEY = "appmon.theme.v1";
const hex = (value) =>
  typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
export function normalizeTheme(value) {
  const fallback = () => ({
    ...DEFAULT_THEME,
    customGradientColors: [...DEFAULT_THEME.customGradientColors],
  });
  if (!value || value.schemaVersion !== 1) return fallback();
  const c = { ...fallback(), ...value };
  for (const [key, options] of Object.entries({
    colorScheme: ["LIGHT", "DARK", "SYSTEM"],
    accentMode: ["PRESET", "CUSTOM_SOLID", "CUSTOM_GRADIENT"],
    navigationLayout: ["LEFT", "TOP"],
    sidebarTheme: ["LIGHT", "DARK"],
    contentWidth: ["FLUID", "FIXED"],
    tableScrollMode: ["AUTO", "FIXED"],
    customGradientType: ["LINEAR", "RADIAL"],
  }))
    if (!options.includes(c[key])) return fallback();
  if (
    !PRESETS.some((p) => p[0] === c.accentPreset) ||
    !hex(c.customSolidColor) ||
    !Array.isArray(c.customGradientColors) ||
    ![2, 3].includes(c.customGradientColors.length) ||
    !c.customGradientColors.every(hex) ||
    !Number.isInteger(c.customGradientAngle) ||
    c.customGradientAngle < 0 ||
    c.customGradientAngle > 360 ||
    typeof c.fixedSidebar !== "boolean" ||
    typeof c.colorWeak !== "boolean"
  )
    return fallback();
  return Object.fromEntries(Object.keys(DEFAULT_THEME).map((k) => [k, c[k]]));
}
const rgb = (color) =>
  [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
const mix = (a, b, weight) =>
  "#" +
  rgb(a)
    .map((v, i) =>
      Math.round(v * (1 - weight) + rgb(b)[i] * weight)
        .toString(16)
        .padStart(2, "0"),
    )
    .join("");
const luminance = (color) =>
  rgb(color)
    .map((x) => x / 255)
    .map((x) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
    .reduce((n, x, i) => n + x * [0.2126, 0.7152, 0.0722][i], 0);
export const contrast = (a, b) =>
  (Math.max(luminance(a), luminance(b)) + 0.05) /
  (Math.min(luminance(a), luminance(b)) + 0.05);
export const foreground = (color) =>
  contrast(color, "#ffffff") >= contrast(color, "#111827")
    ? "#ffffff"
    : "#111827";
function readableAccent(color, surface) {
  let output = color;
  const destination = luminance(surface) > 0.4 ? "#111827" : "#ffffff";
  for (let step = 0; step < 20 && contrast(output, surface) < 4.5; step++)
    output = mix(output, destination, 0.12);
  return output;
}
export function resolveTheme(value, systemDark = false) {
  const c = normalizeTheme(value),
    dark =
      c.colorScheme === "DARK" || (c.colorScheme === "SYSTEM" && systemDark);
  const preset = PRESETS.find((p) => p[0] === c.accentPreset);
  const colors =
    c.accentMode === "CUSTOM_SOLID"
      ? [c.customSolidColor]
      : c.accentMode === "CUSTOM_GRADIENT"
        ? c.customGradientColors
        : preset.slice(2);
  const surface = dark ? "#151d31" : "#ffffff";
  const background =
    colors.length === 1
      ? colors[0]
      : (c.customGradientType === "RADIAL"
          ? "radial-gradient(circle, "
          : "linear-gradient(" + c.customGradientAngle + "deg, ") +
        colors.join(", ") +
        ")";
  const samples = colors.flatMap((color, i) =>
    i === colors.length - 1
      ? [color]
      : Array.from({ length: 11 }, (_, j) => mix(color, colors[i + 1], j / 10)),
  );
  const whiteContrast = Math.min(...samples.map((s) => contrast(s, "#ffffff"))),
    blackContrast = Math.min(...samples.map((s) => contrast(s, "#111827")));
  const onBrand = whiteContrast >= blackContrast ? "#ffffff" : "#111827";
  const safeBackground =
    Math.max(whiteContrast, blackContrast) >= 4.5
      ? background
      : `linear-gradient(#00000099,#00000099), ${background}`;
  return {
    config: c,
    dark,
    accent: colors[0],
    accentText: readableAccent(colors[0], surface),
    background: safeBackground,
    onAccent: foreground(colors[0]),
    onBrand:
      Math.max(whiteContrast, blackContrast) >= 4.5 ? onBrand : "#ffffff",
  };
}
export function applyTheme(value, win = window) {
  const state = resolveTheme(
      value,
      win.matchMedia("(prefers-color-scheme: dark)").matches,
    ),
    root = win.document.documentElement,
    c = state.config;
  Object.assign(root.dataset, {
    theme: state.dark ? "dark" : "light",
    sidebar: c.sidebarTheme.toLowerCase(),
    navigation: c.navigationLayout.toLowerCase(),
    width: c.contentWidth.toLowerCase(),
    tableScroll: c.tableScrollMode.toLowerCase(),
    fixedSidebar: String(c.fixedSidebar),
    colorWeak: String(c.colorWeak),
  });
  for (const [key, value] of Object.entries({
    "--accent": state.accent,
    "--accent-text": state.accentText,
    "--brand-background": state.background,
    "--on-accent": state.onAccent,
    "--on-brand": state.onBrand,
    "--chart-accent": state.accentText,
    "--radius": "0px",
  }))
    root.style.setProperty(key, value);
  win.dispatchEvent(new win.CustomEvent("appmon-theme-change", { detail: c }));
  return c;
}
export function initTheme(win = window) {
  const doc = win.document;
  let config;
  try {
    config = normalizeTheme(JSON.parse(win.localStorage.getItem(THEME_KEY)));
  } catch {
    config = normalizeTheme(null);
  }
  let committed = config;
  const preview = (value) => {
    config = applyTheme(value, win);
    return config;
  };
  const save = (value) => {
    committed = preview(value);
    try {
      win.localStorage.setItem(THEME_KEY, JSON.stringify(config));
    } catch {}
    return config;
  };
  applyTheme(config, win);
  win
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => applyTheme(config, win));
  win.addEventListener("storage", (e) => {
    if (e.key === THEME_KEY) {
      try {
        committed = normalizeTheme(JSON.parse(e.newValue));
      } catch {
        committed = normalizeTheme(null);
      }
      preview(committed);
    }
  });
  const drawer = doc.createElement("dialog");
  drawer.id = "theme-settings";
  drawer.setAttribute("aria-labelledby", "theme-title");
  drawer.innerHTML = `<form method="dialog" class="drawer-head"><h2 id="theme-title">主题设置</h2><button class="secondary small" aria-label="取消主题设置">取消</button></form><p class="sub">修改即时预览，保存后保留在当前浏览器。矩形控件统一使用直角。</p>
    <form id="theme-form"><div class="form-grid"><label>显示模式<select name="colorScheme"><option value="LIGHT">浅色</option><option value="DARK">深色</option><option value="SYSTEM">跟随系统</option></select></label><label>主题色模式<select name="accentMode"><option value="PRESET">预设主题</option><option value="CUSTOM_SOLID">自定义纯色</option><option value="CUSTOM_GRADIENT">自定义渐变</option></select></label><label class="full">主题预设<select name="accentPreset">${PRESETS.map((p) => `<option value="${p[0]}">${p[1]}</option>`).join("")}</select></label><div class="full theme-swatches" aria-label="预设主题"></div>
    <label>自定义纯色<input name="customSolidColor" type="color"></label><label>渐变类型<select name="customGradientType"><option value="LINEAR">线性</option><option value="RADIAL">径向</option></select></label><label class="full">渐变颜色（2–3 个 HEX 色值，逗号分隔）<input name="gradientText" placeholder="#2639E9,#873FF2"></label><label>渐变角度<input name="customGradientAngle" type="number" min="0" max="360" step="1"></label>
    <label>导航布局<select name="navigationLayout"><option value="LEFT">左侧导航</option><option value="TOP">顶部导航</option></select></label><label>导航配色<select name="sidebarTheme"><option value="DARK">深色</option><option value="LIGHT">浅色</option></select></label><label>内容宽度<select name="contentWidth"><option value="FLUID">自适应</option><option value="FIXED">居中定宽</option></select></label><label>表格滚动<select name="tableScrollMode"><option value="AUTO">随页面滚动</option><option value="FIXED">固定表头 / 内部滚动</option></select></label><label class="check-label"><input type="checkbox" name="fixedSidebar">固定导航</label><label class="check-label"><input type="checkbox" name="colorWeak">色弱辅助</label></div><p id="theme-error" role="alert"></p><div class="toolbar"><button type="submit">保存主题</button><button type="button" class="secondary" id="reset-theme">恢复默认</button></div></form>`;
  doc.body.append(drawer);
  const form = drawer.querySelector("#theme-form"),
    error = drawer.querySelector("#theme-error");
  function sync() {
    for (const [key, v] of Object.entries(config)) {
      const field = form.elements.namedItem(key);
      if (field) {
        if (field.type === "checkbox") field.checked = v;
        else field.value = v;
      }
    }
    form.elements.namedItem("gradientText").value =
      config.customGradientColors.join(",");
  }
  function read() {
    const data = Object.fromEntries(new win.FormData(form));
    data.schemaVersion = 1;
    data.customGradientColors = data.gradientText
      .split(",")
      .map((s) => s.trim());
    data.customGradientAngle = Number(data.customGradientAngle);
    data.fixedSidebar = form.elements.namedItem("fixedSidebar").checked;
    data.colorWeak = form.elements.namedItem("colorWeak").checked;
    delete data.gradientText;
    if (
      ![2, 3].includes(data.customGradientColors.length) ||
      !data.customGradientColors.every(hex)
    ) {
      error.textContent = "请填写 2–3 个有效 HEX 色值，例如 #2639E9。";
      return false;
    }
    if (
      !Number.isInteger(data.customGradientAngle) ||
      data.customGradientAngle < 0 ||
      data.customGradientAngle > 360
    ) {
      error.textContent = "渐变角度必须是 0–360 的整数。";
      return false;
    }
    error.textContent = "";
    preview(data);
    sync();
    return true;
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (read()) {
      save(config);
      drawer.close();
    }
  });
  form.addEventListener("change", (event) => {
    if (event.target.name !== "gradientText") read();
  });
  drawer.querySelector("#reset-theme").addEventListener("click", () => {
    preview(DEFAULT_THEME);
    sync();
    error.textContent = "";
  });
  for (const p of PRESETS) {
    const button = doc.createElement("button");
    button.type = "button";
    button.className = "theme-swatch";
    button.title = p[1];
    button.setAttribute("aria-label", p[1]);
    button.style.background =
      p.length > 3 ? `linear-gradient(135deg,${p[2]},${p[3]})` : p[2];
    button.addEventListener("click", () => {
      preview({ ...config, accentMode: "PRESET", accentPreset: p[0] });
      sync();
    });
    drawer.querySelector(".theme-swatches").append(button);
  }
  drawer.addEventListener("close", () => preview(committed));
  doc.querySelector("#open-theme").addEventListener("click", () => {
    sync();
    drawer.showModal();
  });
  return { get: () => config, set: save };
}
