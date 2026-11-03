import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  selectedStores,
  effectiveLanguage,
  autoCaption,
  languageChoices,
} from "../src/market-scope.js";
const prefs = JSON.parse(
  await readFile(
    new URL("../../src/appmon/market_locales.json", import.meta.url),
    "utf8",
  ),
);
test("automatic locale follows country and store while explicit English stays fixed", () => {
  assert.equal(effectiveLanguage(prefs, "google", "kz", "auto"), "kk");
  assert.equal(effectiveLanguage(prefs, "apple", "kz", "auto"), "ru");
  assert.equal(effectiveLanguage(prefs, "google", "th", "auto"), "th");
  assert.equal(effectiveLanguage(prefs, "apple", "th", "en"), "en");
  assert.equal(effectiveLanguage(prefs, "all", "kz", "auto"), "auto");
  assert.deepEqual(selectedStores("all"), ["google", "apple"]);
  assert.match(autoCaption(prefs, "all", "kz"), /哈萨克语.*俄语/);
  assert.equal(
    languageChoices(prefs, "apple", "kz", [
      ["ru", "俄语"],
      ["en", "英语"],
    ]).filter(([code]) => code === "ru").length,
    1,
  );
});
