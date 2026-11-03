// The subject line and a comparison line must never share a colour.
//
// Benjamin Graham's brand colour is #059669, which is also the fourth
// entry of the light palette, so with all six holdings selected the
// book and one of its own positions drew in the same green — two
// lines claiming to be the same series.

import test from "node:test";
import assert from "node:assert/strict";

import { seriesPalette, seriesStyle } from "../src/lib/series-palette.ts";

const LIGHT = { dark: false };
const DARK = { dark: true };

test("the palette is all-distinct in both themes", () => {
  for (const theme of [LIGHT, DARK]) {
    const p = seriesPalette(theme);
    assert.equal(new Set(p).size, p.length);
  }
});

test("an excluded colour is dropped", () => {
  const p = seriesPalette(LIGHT, "#059669");
  assert.ok(!p.includes("#059669"), p.join(","));
  assert.equal(p.length, 9);
});

test("exclusion ignores case and surrounding space", () => {
  assert.equal(seriesPalette(LIGHT, " #059669 ").length, 9);
  assert.equal(seriesPalette(LIGHT, "#059669".toUpperCase()).length, 9);
});

test("a colour that is not in the palette removes nothing", () => {
  assert.equal(seriesPalette(LIGHT, "#123456").length, 10);
});

test("no brand colour can empty the palette", () => {
  for (const theme of [LIGHT, DARK]) {
    for (const c of seriesPalette(theme)) {
      assert.ok(seriesPalette(theme, c).length >= 9);
    }
  }
});

test("the palette holds ten colours, so a nine-name book plus two indices fits", () => {
  for (const theme of [LIGHT, DARK]) {
    assert.equal(seriesPalette(theme).length, 10);
    assert.equal(new Set(seriesPalette(theme)).size, 10);
  }
});

test("no two lines on one chart ever look alike", () => {
  // Colour alone runs out; past the palette the dash changes.
  const palette = seriesPalette(LIGHT, "#059669"); // 9 left
  const seen = new Set();
  for (let i = 0; i < 25; i++) {
    const s = seriesStyle(i, palette);
    const key = `${s.stroke}|${s.strokeDasharray ?? "solid"}`;
    assert.ok(!seen.has(key), `line ${i} repeats ${key}`);
    seen.add(key);
  }
});

test("the first lap is solid", () => {
  const palette = seriesPalette(LIGHT);
  for (let i = 0; i < palette.length; i++) {
    assert.equal(seriesStyle(i, palette).strokeDasharray, undefined);
    assert.equal(seriesStyle(i, palette).stroke, palette[i]);
  }
});

test("a wrapped line is dashed, not a silent duplicate", () => {
  const palette = seriesPalette(LIGHT);
  const first = seriesStyle(0, palette);
  const wrapped = seriesStyle(palette.length, palette);
  assert.equal(wrapped.stroke, first.stroke);
  assert.ok(wrapped.strokeDasharray, "wrapped line must differ somehow");
});
