import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";
import {
  createSelectors,
  matchesCountry,
  matchesCategory,
} from "../src/selectors.js";

const catalog = JSON.parse(
  await readFile(
    new URL("../../src/appmon/store_catalog.json", import.meta.url),
    "utf8",
  ),
);
const appleMarkets = JSON.parse(
  await readFile(
    new URL("../../src/appmon/apple_markets.json", import.meta.url),
    "utf8",
  ),
);
catalog.countries = catalog.countries.map((c) => ({
  ...c,
  apple_supported: c.code in appleMarkets,
}));
const tick = () => new Promise((r) => setTimeout(r, 10));
function setup() {
  const win = new Window({ url: "http://localhost:5173" });
  win.document.body.innerHTML =
    '<input id="global-country" value="us" type="hidden"><button id="country-trigger"><span id="country-value"></span></button><div id="fixture"></div>';
  let store = "google";
  const api = async (path) =>
    path === "/catalog"
      ? catalog
      : {
          counts: { FINANCE: 20 },
          total: 25,
          apps: 20,
          unknown: 5,
          observed_at: 100,
        };
  const selector = createSelectors({
    api,
    win,
    getMarket: () => ({
      store,
      country: win.document.querySelector("#global-country").value,
    }),
  });
  return { win, selector, setStore: (v) => (store = v) };
}

test("country search accepts Chinese, English and ISO codes", () => {
  const china = catalog.countries.find((c) => c.code === "cn");
  const hongkong = catalog.countries.find((c) => c.code === "hk");
  assert.ok(matchesCountry(china, "中国"));
  assert.ok(matchesCountry(china, "CHN"));
  assert.ok(matchesCountry(china, "china"));
  assert.ok(matchesCountry(hongkong, "hongkong"));
  assert.ok(matchesCountry(hongkong, "香港"));
  assert.equal(catalog.countries.length, 249);
});

test("country picker filters Asia subregions and commits the selected ISO value", async () => {
  const { win, selector } = setup();
  try {
    await selector.ready();
    win.document.querySelector("#country-trigger").click();
    await tick();
    win.document.querySelector('[data-region="southeast_asia"]').click();
    const asia = win.document
      .querySelector('[data-region="southeast_asia"]')
      .closest("section");
    assert.ok(asia.querySelector('[data-country-code="th"]'));
    assert.equal(asia.querySelector('[data-country-code="jp"]'), null);
    const input = win.document.querySelector("#country-picker input");
    input.value = "泰国";
    input.dispatchEvent(new win.Event("input"));
    assert.equal(
      win.document.querySelectorAll("[data-country-code]").length,
      1,
    );
    win.document.querySelector('[data-country-code="th"]').click();
    assert.equal(win.document.querySelector("#global-country").value, "th");
    assert.match(
      win.document.querySelector("#country-value").textContent,
      /泰国/,
    );
    assert.equal(win.document.querySelector("#country-picker").open, false);
  } finally {
    await win.happyDOM.close();
  }
});

test("unsupported Apple market stays disabled and no US fallback is written", async () => {
  const { win, selector, setStore } = setup();
  try {
    setStore("apple");
    await selector.ready();
    win.document.querySelector("#country-trigger").click();
    await tick();
    const unsupported = catalog.countries.find((c) => !c.apple_supported);
    const option = win.document.querySelector(
      '[data-country-code="' + unsupported.code + '"]',
    );
    assert.equal(option.disabled, true);
    option.click();
    assert.equal(win.document.querySelector("#global-country").value, "us");
  } finally {
    await win.happyDOM.close();
  }
});

test("store-specific category grid submits official IDs and removes legacy chart choices", async () => {
  const { win, selector, setStore } = setup();
  try {
    await selector.ready();
    win.document.querySelector("#fixture").innerHTML = selector.categoryField(
      "google",
      "",
      "category-fixture",
      "chart",
    );
    win.document.querySelector(".category-trigger").click();
    await tick();
    assert.ok(win.document.querySelector('[data-select-category="FINANCE"]'));
    assert.equal(
      win.document.querySelector('[data-select-category="6015"]'),
      null,
    );
    win.document.querySelector('[data-select-category="FINANCE"]').click();
    assert.equal(
      win.document.querySelector("#category-fixture").value,
      "FINANCE",
    );
    setStore("apple");
    win.document.querySelector("#fixture").innerHTML = selector.categoryField(
      "apple",
      "",
      "category-fixture",
      "chart",
    );
    win.document.querySelector(".category-trigger").click();
    await tick();
    assert.ok(win.document.querySelector('[data-select-category="6015"]'));
    assert.equal(
      win.document.querySelector('[data-select-category="6022"]'),
      null,
    );
    win.document.querySelector('[data-group="games"]').click();
    assert.ok(win.document.querySelector('[data-select-category="7003"]'));
    win.document.querySelector('[data-select-category="7003"]').click();
    assert.equal(win.document.querySelector("#category-fixture").value, "7003");
    assert.match(
      win.document.querySelector("#category-fixture-caption").textContent,
      /休闲/,
    );
  } finally {
    await win.happyDOM.close();
  }
});

test("category sidebar shows exact counts and separates unclassified records", async () => {
  const { win, selector } = setup();
  try {
    await selector.ready();
    win.document.querySelector("#fixture").innerHTML =
      selector.categorySidebar("google");
    let selected;
    await selector.mountSidebar("google", "", (v) => (selected = v));
    assert.match(
      win.document.querySelector('[data-pool-category="FINANCE"]').textContent,
      /20/,
    );
    assert.match(
      win.document.querySelector('[data-pool-category="__unknown__"]')
        .textContent,
      /5/,
    );
    win.document.querySelector('[data-pool-category="FINANCE"]').click();
    assert.equal(selected, "FINANCE");
    const input = win.document.querySelector("#category-tree-search");
    input.value = "finance";
    input.dispatchEvent(new win.Event("input"));
    assert.equal(
      win.document.querySelectorAll("[data-pool-category]").length,
      1,
    );
  } finally {
    await win.happyDOM.close();
  }
});

test("offline font contains the requested variable SemiBold instance and license", async () => {
  const font = await readFile(
    new URL("../public/fonts/font.ttf", import.meta.url),
  );
  let fvar;
  for (let i = 0; i < font.readUInt16BE(4); i++) {
    const p = 12 + i * 16;
    if (font.toString("ascii", p, p + 4) === "fvar")
      fvar = font.readUInt32BE(p + 8);
  }
  assert.ok(fvar);
  const axisOffset = font.readUInt16BE(fvar + 4),
    axisCount = font.readUInt16BE(fvar + 8),
    axisSize = font.readUInt16BE(fvar + 10),
    instanceCount = font.readUInt16BE(fvar + 12),
    instanceSize = font.readUInt16BE(fvar + 14);
  const tags = [];
  for (let i = 0; i < axisCount; i++)
    tags.push(
      font.toString(
        "ascii",
        fvar + axisOffset + i * axisSize,
        fvar + axisOffset + i * axisSize + 4,
      ),
    );
  assert.deepEqual(tags, ["wght", "BEVL"]);
  let found = false;
  for (let i = 0; i < instanceCount; i++) {
    const p = fvar + axisOffset + axisCount * axisSize + i * instanceSize;
    const weights = tags.map((_, a) => font.readInt32BE(p + 4 + a * 4) / 65536);
    if (weights[0] === 600 && weights[1] === 80) found = true;
  }
  assert.ok(found);
  assert.match(
    await readFile(
      new URL(
        "../public/fonts/AlimamaFangYuanTiVF-LICENSE.txt",
        import.meta.url,
      ),
      "utf8",
    ),
    /嵌入式使用/,
  );
  const woff = await readFile(
    new URL("../public/fonts/font.woff2", import.meta.url),
  );
  assert.equal(woff.toString("ascii", 0, 4), "wOF2");
});
