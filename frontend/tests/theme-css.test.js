import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { Window } from "happy-dom";
import { applyTheme, DEFAULT_THEME, PRESETS } from "../src/theme.js";

test("production CSS keeps shared components themed after chunk extraction", async () => {
  // The multi-page build hoists shared theme CSS before the main entry CSS.
  // Test the emitted HTML order: source-only theme tests missed this regression.
  const result = await build({
    root: fileURLToPath(new URL("..", import.meta.url)),
    logLevel: "silent",
    build: { write: false },
  });
  const assets = new Map(result.output.map((asset) => [asset.fileName, asset]));
  const html = assets.get("index.html").source;
  const styles = [
    ...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g),
  ].map((match) => assets.get(match[1].replace(/^\//, "")).source);
  assert.ok(styles.length, "built page must include stylesheets");
  const win = new Window();
  try {
    for (const css of styles) {
      const style = win.document.createElement("style");
      style.textContent = css;
      win.document.head.append(style);
    }
    win.document.body.innerHTML = `
      <aside class="app-sidebar"><div class="nav-label">导航</div>
        <nav id="navigation"><a href="#overview">工作台</a><a class="active" href="#apps">应用库</a></nav>
      </aside>
      <main><div class="metric"><span class="value">514,381</span></div>
        <a id="test-link" href="#apps">应用库</a><button class="link">查看</button>
        <dialog open>详情</dialog><div id="toast">已保存</div></main>`;
    const computed = (selector) =>
      win.getComputedStyle(win.document.querySelector(selector));
    const color = (value) => {
      const probe = win.document.createElement("span");
      probe.style.color = value;
      win.document.body.append(probe);
      const resolved = win.getComputedStyle(probe).color;
      probe.remove();
      return resolved;
    };
    for (const colorScheme of ["LIGHT", "DARK"])
      for (const sidebarTheme of ["LIGHT", "DARK"])
        for (const palette of [
          ...PRESETS.map(([accentPreset]) => ({ accentPreset })),
          { accentMode: "CUSTOM_SOLID", customSolidColor: "#ffffee" },
          {
            accentMode: "CUSTOM_GRADIENT",
            customGradientColors: ["#17654b", "#e869a2"],
          },
        ]) {
          applyTheme(
            { ...DEFAULT_THEME, ...palette, colorScheme, sidebarTheme },
            win,
          );
          assert.equal(
            computed(".metric").borderTopColor,
            color("var(--accent)"),
            "metric accent",
          );
          assert.equal(
            computed(".metric").backgroundColor,
            color("var(--surface)"),
            "metric surface",
          );
          assert.equal(
            computed("#test-link").color,
            color("var(--accent-text)"),
            "readable link",
          );
          assert.equal(
            computed("button.link").color,
            color("var(--accent-text)"),
            "readable link button",
          );
          assert.equal(
            computed("#navigation a").color,
            color("var(--nav-text)"),
            "navigation text",
          );
          assert.equal(
            computed(".nav-label").color,
            color("var(--nav-muted)"),
            "navigation label",
          );
          assert.ok(
            computed("#navigation a.active").boxShadow.includes(
              color("var(--on-brand)"),
            ),
            "active navigation marker",
          );
          assert.equal(
            computed("#toast").backgroundColor,
            color("var(--ink)"),
            "toast surface",
          );
          assert.equal(
            computed("#toast").color,
            color("var(--surface)"),
            "toast text",
          );
          assert.equal(
            computed("dialog").backgroundColor,
            color("var(--surface)"),
            "dialog surface",
          );
        }
  } finally {
    await win.happyDOM.close();
  }
});
