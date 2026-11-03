// The subject line and a comparison line must never share a colour.
//
// Benjamin Graham's brand colour is #059669, which is also the fourth
// entry of the light palette, so with all six holdings selected the
// book and one of its own positions drew in the same green — two
// lines claiming to be the same series.

import test from "node:test";
import assert from "node:assert/strict";

import {
  contrast,
  distinctCapacity,
  readableOn,
  seriesPalette,
  seriesStyle,
} from "../src/lib/series-palette.ts";

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

test("no two lines on one chart look alike, up to the stated capacity", () => {
  // An earlier version dashed every lap past the first the same way,
  // which only moved the collision from line 11 to line 31. John
  // Neff holds 34 names, so 36 lines is reachable with one click.
  for (const exclude of [undefined, "#059669"]) {
    const palette = seriesPalette(LIGHT, exclude);
    const seen = new Set();
    for (let i = 0; i < distinctCapacity(palette); i++) {
      const s = seriesStyle(i, palette);
      const key = `${s.stroke}|${s.strokeDasharray ?? "solid"}`;
      assert.ok(!seen.has(key), `line ${i} repeats ${key} (exclude=${exclude})`);
      seen.add(key);
    }
  }
});

test("capacity covers the widest book here", () => {
  // 34 holdings + SPY + QQQ.
  assert.ok(distinctCapacity(seriesPalette(LIGHT, "#059669")) >= 36);
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
  for (let lap = 1; lap < 4; lap++) {
    const wrapped = seriesStyle(palette.length * lap, palette);
    assert.equal(wrapped.stroke, first.stroke);
    assert.ok(wrapped.strokeDasharray, `lap ${lap} must differ somehow`);
  }
});

test("every chip label clears 4.5:1 on its own colour", () => {
  // The palette's own comment claims only the 3:1 a line needs; a
  // chip puts text on it. #0284c7 is 4.10:1 against white and
  // #059669 is 3.77:1, so white alone is not enough.
  for (const theme of [LIGHT, DARK]) {
    for (const colour of seriesPalette(theme)) {
      const ink = readableOn(colour);
      const ratio = contrast(colour, ink);
      assert.ok(ratio >= 4.5, `${colour} on ${ink} is ${ratio.toFixed(2)}:1`);
    }
  }
});

test("the two known failures pick dark ink", () => {
  assert.equal(readableOn("#0284c7"), "#0a1020");
  assert.equal(readableOn("#059669"), "#0a1020");
});

test("a dark colour still takes white", () => {
  assert.equal(readableOn("#7c3aed"), "#ffffff");
});
