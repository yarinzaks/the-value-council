// Line colours for a comparison chart. Lives apart from ChartKit
// because it is pure data with no JSX, and a .tsx module cannot be
// loaded by the test runner.

/** Distinct line colours for a comparison chart, light and dark.
 *
 *  Ten, because a value book runs six to nine names and the chart can
 *  show all of them plus two indices. Each pair is picked to clear 3:1
 *  against its own background, and the hues are spaced so that no two
 *  neighbours are a red/green pair and every adjacent pair differs in
 *  lightness as well as hue.
 *
 *  The portfolio's own line does not come from here. It keeps the
 *  agent's brand colour and a heavier stroke, because it is the
 *  subject and the rest are the comparison.
 */
export function seriesPalette(
  t: { dark: boolean },
  exclude?: string,
): string[] {
  const all = t.dark
    ? [
        "#38bdf8", "#fbbf24", "#a78bfa", "#34d399", "#fb7185",
        "#22d3ee", "#f472b6", "#a3e635", "#fb923c", "#c4b5fd",
      ]
    : [
        "#0284c7", "#b45309", "#7c3aed", "#059669", "#be123c",
        "#0e7490", "#be185d", "#4d7c0f", "#c2410c", "#6d28d9",
      ];
  if (!exclude) return all;
  // The subject line carries the agent's brand colour, and several of
  // those are in this list — Benjamin Graham's #059669 is the light
  // palette's fourth entry. A holding drawn in the book's own colour
  // is a chart with two lines claiming to be the same series.
  const taken = exclude.trim().toLowerCase();
  const kept = all.filter((c) => c.toLowerCase() !== taken);
  return kept.length === all.length ? all : kept;
}

/** Dash patterns, one per lap through the palette.
 *
 *  Four, so ten colours carry forty distinguishable lines. The widest
 *  book here is John Neff's thirty-four names, which with the two
 *  indices is thirty-six.
 */
const DASHES: readonly (string | undefined)[] = [
  undefined,
  "6 3",
  "2 3",
  "8 3 2 3",
];

/** Stroke colour and dash for the `i`-th comparison line.
 *
 *  Colour alone runs out. Thirty-four holdings plus two indices is
 *  thirty-six lines against ten colours, and without a second
 *  dimension the eleventh silently became the first — two series the
 *  same colour, which is worse than a series that is hard to name.
 *
 *  An earlier version dashed every lap past the first identically,
 *  which only moved the collision from the 11th line to the 31st. The
 *  pattern cycles with the laps, so a repeat needs both the colour and
 *  the dash to come round: `palette.length * DASHES.length` lines.
 */
export function seriesStyle(
  i: number,
  palette: readonly string[],
): { stroke: string; strokeDasharray?: string } {
  const n = palette.length;
  const idx = ((i % n) + n) % n;
  const lap = Math.floor((((i % (n * DASHES.length)) + n * DASHES.length) %
    (n * DASHES.length)) / n);
  const dash = DASHES[lap];
  return dash === undefined
    ? { stroke: palette[idx] }
    : { stroke: palette[idx], strokeDasharray: dash };
}

/** How many lines this palette can draw before any two look alike. */
export function distinctCapacity(palette: readonly string[]): number {
  return palette.length * DASHES.length;
}

/** Relative luminance per WCAG 2.1, for a #rrggbb string. */
function luminance(hex: string): number {
  const v = hex.replace("#", "");
  const ch = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255);
  const lin = ch.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

/** Contrast ratio between two #rrggbb colours, 1 to 21. */
export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

/** Black or white, whichever is readable on `background`.
 *
 *  The palette is documented for the 3:1 that a line has to clear.
 *  A selected chip puts a label on the same colour, where the bar is
 *  4.5:1, and two light entries did not make it: #0284c7 reaches only
 *  4.10:1 against white and #059669 only 3.77:1. Choosing the better
 *  of the two text colours per chip clears it everywhere without
 *  giving up the colours that make the lines legible.
 */
export function readableOn(background: string): "#ffffff" | "#0a1020" {
  return contrast(background, "#ffffff") >= contrast(background, "#0a1020")
    ? "#ffffff"
    : "#0a1020";
}
