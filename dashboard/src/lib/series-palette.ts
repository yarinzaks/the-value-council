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

/** Stroke colour and dash for the `i`-th comparison line.
 *
 *  Colour alone runs out. Nine holdings plus two indices is eleven
 *  lines against ten colours, and the eleventh silently became the
 *  first — two series the same colour, which is worse than a series
 *  that is hard to name. Past the end of the palette the colours
 *  repeat but the stroke becomes dashed, so no two lines on one chart
 *  ever look alike.
 */
export function seriesStyle(
  i: number,
  palette: readonly string[],
): { stroke: string; strokeDasharray?: string } {
  const n = palette.length;
  const lap = Math.floor(i / n);
  const stroke = palette[((i % n) + n) % n];
  if (lap <= 0) return { stroke };
  // "5 3" on the second lap, "2 3" on the third — distinct from each
  // other and from the dotted benchmark line in the NAV view.
  return { stroke, strokeDasharray: lap === 1 ? "5 3" : "2 3" };
}
