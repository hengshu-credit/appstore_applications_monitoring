import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  DEFAULT_THEME,
  PRESETS,
  normalizeTheme,
  resolveTheme,
  applyTheme,
  initTheme,
  THEME_KEY,
  contrast,
} from "../src/theme.js";

test("theme validates configuration and custom colors without CSS injection", () => {
  assert.equal(PRESETS.length, 12);
  assert.equal(
    normalizeTheme({
      ...DEFAULT_THEME,
      customSolidColor: "url(https://evil.test)",
    }).accentMode,
    "PRESET",
  );
  assert.deepEqual(normalizeTheme({ schemaVersion: 99 }), DEFAULT_THEME);
  assert.equal(
    resolveTheme({ ...DEFAULT_THEME, colorScheme: "SYSTEM" }, true).dark,
    true,
  );
  assert.equal(
    resolveTheme({ ...DEFAULT_THEME, colorScheme: "SYSTEM" }, false).dark,
    false,
  );
  assert.ok(
    contrast(
      resolveTheme({
        ...DEFAULT_THEME,
        accentMode: "CUSTOM_SOLID",
        customSolidColor: "#ffffee",
      }).accentText,
      "#ffffff",
    ) >= 4.5,
  );
});

test("every preset supplies readable text in both color schemes and square corners", async () => {
  const win = new Window();
  try {
    for (const preset of PRESETS)
      for (const colorScheme of ["LIGHT", "DARK"]) {
        const config = {
          ...DEFAULT_THEME,
          accentPreset: preset[0],
          colorScheme,
        };
        const state = resolveTheme(config);
        applyTheme(config, win);
        assert.ok(
          contrast(state.accentText, state.dark ? "#151d31" : "#ffffff") >= 4.5,
        );
        assert.equal(
          win.document.documentElement.style.getPropertyValue("--radius"),
          "0px",
        );
        assert.equal(
          win.document.documentElement.dataset.theme,
          colorScheme.toLowerCase(),
        );
      }
  } finally {
    await win.happyDOM.close();
  }
});

test("theme previews cancel cleanly; saving persists; corrupted storage recovers", async () => {
  const win = new Window({ url: "http://localhost:5173" });
  win.document.body.innerHTML = '<button id="open-theme">主题设置</button>';
  win.localStorage.setItem(THEME_KEY, "broken");
  try {
    const controller = initTheme(win);
    assert.equal(controller.get().colorScheme, "LIGHT");
    win.document.querySelector("#open-theme").click();
    const scheme = win.document.querySelector("[name=colorScheme]");
    scheme.value = "DARK";
    scheme.dispatchEvent(new win.Event("change", { bubbles: true }));
    assert.equal(win.document.documentElement.dataset.theme, "dark");
    assert.equal(win.localStorage.getItem(THEME_KEY), "broken");
    win.document.querySelector("#theme-settings").close();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(win.document.documentElement.dataset.theme, "light");
    win.document.querySelector("#open-theme").click();
    scheme.value = "DARK";
    scheme.dispatchEvent(new win.Event("change", { bubbles: true }));
    win.document
      .querySelector("#theme-form")
      .dispatchEvent(
        new win.Event("submit", { bubbles: true, cancelable: true }),
      );
    assert.equal(
      JSON.parse(win.localStorage.getItem(THEME_KEY)).colorScheme,
      "DARK",
    );
    assert.equal(win.document.documentElement.dataset.theme, "dark");
  } finally {
    await win.happyDOM.close();
  }
});

test("gradient colors and direction can be configured without typing color codes", async () => {
  const win = new Window({ url: "http://localhost:5173" });
  win.document.body.innerHTML = '<button id="open-theme">Theme</button>';
  try {
    const controller = initTheme(win);
    win.document.querySelector("#open-theme").click();
    const form = win.document.querySelector("#theme-form");
    form.elements.accentMode.value = "CUSTOM_GRADIENT";
    form.elements.accentMode.dispatchEvent(
      new win.Event("change", { bubbles: true }),
    );
    assert.equal(form.elements.gradientText.type, "hidden");
    assert.equal(
      win.document.querySelector('[data-color-section="PRESET"]').hidden,
      true,
    );
    const color = win.document.querySelector('[data-gradient-index="0"]');
    color.value = "#ff8800";
    color.dispatchEvent(new win.Event("input", { bubbles: true }));
    assert.equal(controller.get().customGradientColors[0], "#ff8800");
    win.document.querySelector("#toggle-gradient-stop").click();
    assert.equal(
      win.document.querySelectorAll("[data-gradient-index]").length,
      3,
    );
    win.document.querySelector('[data-theme-angle="90"]').click();
    assert.equal(controller.get().customGradientAngle, 90);
    form.dispatchEvent(
      new win.Event("submit", { bubbles: true, cancelable: true }),
    );
    const saved = JSON.parse(win.localStorage.getItem(THEME_KEY));
    assert.equal(saved.customGradientColors.length, 3);
    assert.equal(saved.customGradientAngle, 90);
  } finally {
    await win.happyDOM.close();
  }
});

test("layout choices preview directly and preserve selection through save and cancel", async () => {
  const win = new Window({ url: "http://localhost:5173" });
  win.document.body.innerHTML = '<button id="open-theme">主题设置</button>';
  try {
    const controller = initTheme(win);
    win.document.querySelector("#open-theme").click();
    const choice = win.document.querySelector(
      '[data-theme-field="tableScrollMode"][data-theme-value="FIXED"]',
    );
    choice.click();
    assert.equal(controller.get().tableScrollMode, "FIXED");
    assert.equal(choice.getAttribute("aria-pressed"), "true");
    assert.equal(win.document.documentElement.dataset.tableScroll, "fixed");
    win.document.querySelector("#theme-settings").close();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(controller.get().tableScrollMode, "AUTO");
    win.document.querySelector("#open-theme").click();
    assert.equal(choice.getAttribute("aria-pressed"), "false");
    choice.click();
    win.document
      .querySelector(
        '[data-theme-field="navigationLayout"][data-theme-value="TOP"]',
      )
      .click();
    win.document
      .querySelector("#theme-form")
      .dispatchEvent(new win.Event("submit", { cancelable: true }));
    const saved = JSON.parse(win.localStorage.getItem(THEME_KEY));
    assert.equal(saved.tableScrollMode, "FIXED");
    assert.equal(saved.navigationLayout, "TOP");
  } finally {
    await win.happyDOM.close();
  }
});

test("chart colors follow presets and custom palettes in both modes", async () => {
  const win = new Window();
  try {
    for (const colorScheme of ["LIGHT", "DARK"]) {
      for (const value of [
        ...PRESETS.map(([accentPreset]) => ({ accentPreset })),
        { accentMode: "CUSTOM_SOLID", customSolidColor: "#ffffee" },
        {
          accentMode: "CUSTOM_GRADIENT",
          customGradientColors: ["#17654b", "#e869a2"],
        },
      ]) {
        const config = { ...DEFAULT_THEME, ...value, colorScheme };
        const state = resolveTheme(config);
        applyTheme(config, win);
        assert.equal(
          win.document.documentElement.style.getPropertyValue(
            "--chart-secondary",
          ),
          state.chartSecondary,
        );
        assert.ok(
          contrast(state.chartSecondary, state.dark ? "#151d31" : "#ffffff") >=
            4.5,
        );
      }
    }
    const blue = resolveTheme({ ...DEFAULT_THEME, accentPreset: "THEME_BLUE" });
    const pink = resolveTheme({ ...DEFAULT_THEME, accentPreset: "PEACH_PINK" });
    assert.notEqual(blue.chartSecondary, pink.chartSecondary);
  } finally {
    await win.happyDOM.close();
  }
});
