"use client";

// One look for every chart on the site.
//
// Recharts draws its own chrome from defaults that know nothing about
// the page: the grid is a mid grey that disappears on white and glares
// on the dark theme, and the tooltip is a white box with black text,
// which on the dark theme is a slab of daylight dropped over the
// series the reader was following. Each chart had been patching some
// of that inline, differently, so the six of them agreed on nothing.
//
// Everything here is theme-aware because the theme is a class on
// <html> that Recharts cannot see — these are SVG presentation
// attributes, which do not read CSS custom properties, so the value
// has to be resolved in JS and handed over.

import { useUI } from "./Providers";

export interface ChartTheme {
  dark: boolean;
  /** The grid: present enough to measure against, never enough to read. */
  grid: string;
  /** Axis lines and ticks. */
  axis: string;
  /** Axis labels — the muted token, resolved. */
  tick: string;
  /** The benchmark series: deliberately colourless beside an agent's line. */
  benchmark: string;
  /** The two P&L colours, resolved.
   *
   * They are the same pairs the --gain / --loss tokens carry, and they
   * exist here for the same reason those tokens do: #16a34a is 3.28:1
   * on white, which is below AA, and a chart's reference-line label is
   * text like any other. The charts had that value hard-coded. */
  gain: string;
  loss: string;
  /** What a dot sits on, so its halo matches the surface under it. */
  surface: string;
}

export function useChartTheme(): ChartTheme {
  const { theme } = useUI();
  const dark = theme === "dark";
  return {
    dark,
    grid: dark ? "rgba(255,255,255,0.07)" : "rgba(10,16,32,0.07)",
    axis: dark ? "rgba(255,255,255,0.15)" : "rgba(10,16,32,0.12)",
    // council-400 on dark, council-500 on light — the same two values
    // the --muted token carries, for the same contrast reason.
    tick: dark ? "#7089a9" : "#516c8e",
    benchmark: dark ? "#64748b" : "#94a3b8",
    gain: dark ? "#22c55e" : "#15803d",
    loss: dark ? "#f87171" : "#dc2626",
    surface: dark ? "#11182a" : "#ffffff",
  };
}

/** Axis props every chart shares. Spread, don't retype. */
export function axisProps(t: ChartTheme) {
  return {
    tick: { fontSize: 11, fill: t.tick },
    tickLine: false,
    axisLine: { stroke: t.axis },
  } as const;
}

export interface TooltipEntry {
  name?: string;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  /** The whole row this datum came from — charts that print a second
   *  figure beside the value (a percent next to a NAV) need it. */
  payload?: Record<string, unknown>;
}

/** Narrow one field of a tooltip row to a finite number. */
export function rowNumber(
  entry: TooltipEntry,
  key: string,
): number | null {
  const v = entry.payload?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * The tooltip, as a surface rather than a box.
 *
 * It floats free of the page, so it takes the deepest elevation and a
 * solid background: a translucent panel over a chart would put the
 * series it is explaining directly behind the number, which is the one
 * place legibility cannot be traded for depth.
 *
 * Recharts clones this element with `active`, `payload` and `label`.
 */
export function ChartTooltip({
  active,
  payload,
  label,
  formatValue,
  formatName,
  formatLabel,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  /** Render one datum. Receives the raw numeric value. */
  formatValue?: (value: number, entry: TooltipEntry) => string;
  /** Series name — charts keyed by slug pass the human label here. */
  formatName?: (entry: TooltipEntry) => string;
  formatLabel?: (label: string | number) => string;
}) {
  if (!active || !payload || payload.length === 0) return null;

  const heading =
    label === undefined || label === null
      ? null
      : formatLabel
        ? formatLabel(label)
        : String(label);

  return (
    <div
      dir="ltr"
      className="rounded-xl border border-council-200 bg-white px-3 py-2 shadow-elev-3 dark:border-council-700 dark:bg-council-900"
    >
      {heading && (
        <div className="mb-1.5 text-[11px] font-medium text-muted">
          {heading}
        </div>
      )}
      <ul className="space-y-1">
        {payload.map((entry, i) => {
          const raw = entry.value;
          const numeric = typeof raw === "number" ? raw : Number(raw);
          const text =
            formatValue && Number.isFinite(numeric)
              ? formatValue(numeric, entry)
              : String(raw ?? "—");
          const name = formatName ? formatName(entry) : (entry.name ?? "");
          return (
            <li
              key={`${entry.dataKey ?? i}`}
              className="flex items-center gap-2 text-xs"
            >
              {entry.color && (
                <span
                  className="inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: entry.color }}
                />
              )}
              {name && (
                <span className="text-council-700 dark:text-council-300">
                  {name}
                </span>
              )}
              <span className="tabular ms-auto font-medium text-council-900 dark:text-council-100">
                {text}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
