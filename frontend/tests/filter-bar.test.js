import { test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
import {
  filterBar,
  bindFilterBar,
  initFilterBars,
  setFilterCatalog,
} from "../src/filter-bar.js";

test("configured search keeps typed text, commits picked identities and submits Enter once", async () => {
  const win = new Window(),
    doc = win.document,
    applied = [];
  try {
    initFilterBars({ doc, win });
    doc.body.innerHTML = filterBar({
      id: "sample",
      fields: [
        {
          name: "q",
          label: "应用",
          options: [["com.loan.one", "Loan One"]],
          matchKey: "q_match",
          pickedMode: "id",
        },
        {
          name: "rating",
          type: "number",
          label: "最低评分",
          min: 0,
          max: 5,
          step: "any",
          options: [3, 4, 5],
        },
      ],
    });
    const form = doc.querySelector("form"),
      input = form.elements.q;
    bindFilterBar(form, (v) => applied.push(v));
    const type = (value) => {
      input.value = value;
      input.dispatchEvent(new win.Event("input", { bubbles: true }));
    };
    const key = (name, el = input) =>
      el.dispatchEvent(
        new win.KeyboardEvent("keydown", {
          key: name,
          bubbles: true,
          cancelable: true,
        }),
      );
    input.focus();
    type("Loan");
    key("Enter");
    await Promise.resolve();
    assert.deepEqual(
      applied.map((x) => [x.q, x.q_match]),
      [["Loan", "contains"]],
    );
    type("One");
    key("ArrowDown");
    key("Enter");
    await Promise.resolve();
    assert.deepEqual(
      [applied.at(-1).q, applied.at(-1).q_match],
      ["com.loan.one", "id"],
    );
    type("unlisted");
    form.elements.rating.value = "4.25";
    key("Enter", form.elements.rating);
    await Promise.resolve();
    assert.deepEqual(
      [applied.at(-1).q, applied.at(-1).q_match, applied.at(-1).rating],
      ["unlisted", "contains", "4.25"],
    );
    type("One");
    key("Escape");
    assert.equal(input.getAttribute("aria-expanded"), "false");
  } finally {
    await win.happyDOM.close();
  }
});

test("market selectors bind their own form, allow clearing and search localized country and language options", async () => {
  const win = new Window(),
    doc = win.document;
  try {
    setFilterCatalog({
      countries: [{ code: "th", name: "泰国", english: "Thailand" }],
      locales: { languages: { th: "泰语" }, countries: {} },
    });
    initFilterBars({ doc, win });
    doc.body.innerHTML =
      '<input id="global-country" value="us">' +
      filterBar({
        id: "tasks",
        fields: [
          { name: "country", label: "国家", type: "country" },
          { name: "language", label: "语言", type: "language" },
          {
            name: "state",
            label: "状态",
            type: "panel",
            options: [
              ["", "全部"],
              ["paused", "暂停"],
            ],
          },
        ],
      });
    const pick = (name, query, label) => {
      const root = doc.querySelector("form").elements[name].parentElement;
      root.querySelector(".filter-trigger").click();
      const search = root.querySelector("input[type=search]");
      if (search) {
        search.value = query;
        search.dispatchEvent(new win.Event("input", { bubbles: true }));
      }
      Array.from(root.querySelectorAll("[role=option]"))
        .find((e) => e.textContent.includes(label))
        .click();
    };
    pick("country", "Thai", "泰国");
    pick("language", "th", "泰语");
    pick("state", "", "暂停");
    assert.deepEqual(
      Object.fromEntries(new win.FormData(doc.querySelector("form"))),
      { country: "th", language: "th", state: "paused" },
    );
    pick("country", "", "全部国家");
    assert.equal(doc.querySelector("form").elements.country.value, "");
    assert.equal(doc.querySelector("#global-country").value, "us");
  } finally {
    await win.happyDOM.close();
  }
});

test("one click reset clears typed filters and chosen selectors before submitting", async () => {
  const win = new Window(),
    doc = win.document,
    calls = [];
  try {
    initFilterBars({ doc, win });
    doc.body.innerHTML = filterBar({
      id: "reset",
      fields: [
        { name: "q", label: "应用", matchKey: "q_match" },
        { name: "store", label: "商店", type: "store" },
        {
          name: "state",
          label: "状态",
          type: "panel",
          options: [
            ["", "全部"],
            ["off", "下架"],
          ],
        },
      ],
      values: { q: "old", q_match: "exact", store: "apple", state: "off" },
    });
    bindFilterBar(doc.querySelector("form"), (v) => calls.push(v));
    doc.querySelector("[data-filter-reset-button]").click();
    await Promise.resolve();
    assert.deepEqual(calls[0], {
      q: "",
      q_match: "contains",
      store: "all",
      state: "",
    });
    assert.match(
      doc.querySelector("[data-filter-type=store] .filter-caption").textContent,
      /全部商店/,
    );
  } finally {
    await win.happyDOM.close();
  }
});
