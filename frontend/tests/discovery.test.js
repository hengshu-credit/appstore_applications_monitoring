import { test } from "node:test";
import assert from "node:assert/strict";
import { csvText } from "../src/discovery.js";

test("ranking CSV preserves unicode, missing values and quotes and neutralizes spreadsheet formulas", () => {
  const text = csvText(
    ["关键词", "位置"],
    [
      ["借款", null],
      ['a,"b"', 3],
      ['=HYPERLINK("evil")', 1],
      ["  +SUM(1)", 2],
    ],
  );
  assert.ok(text.startsWith("\ufeff"));
  assert.ok(text.includes('"借款",""'));
  assert.ok(text.includes('"a,""b""",3'.replace(",3", ',"3"')));
  assert.ok(text.includes("'=HYPERLINK"));
  assert.ok(text.includes("'  +SUM"));
});
