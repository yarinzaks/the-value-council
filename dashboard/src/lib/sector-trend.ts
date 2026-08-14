// How an agent's own money in each sector actually moved.
//
// The donut beside this answers "where is the money"; a reader's next
// question is "and how did that go". A broad sector index (XLI, XLU)
// would answer a different question — how the sector at large moved,
// which is not what this investor owns. Warren Buffett holding two
// manufacturers is not holding manufacturing.
//
// So each line is a fixed-weight index of the agent's actual holdings
// in that sector, rebased to 100 at the start of the window. Weights
// are today's position values, held constant backwards.
//
// That last sentence is the honest caveat and the page states it: this
// is not the agent's realised P&L in the sector. The book changed over
// the window — names were bought and sold — and this rewinds today's
// book instead of replaying the real one. It answers "how have the
// things he now owns been moving", which is the question the donut
// raises, and it is the only one the published data can support: the
// price series are per ticker, and no per-sector history is stored.

import type { PricePoint, PriceSeries } from "./data";

/** One holding's contribution: what it is worth now, and its history. */
export interface TrendHolding {
  ticker: string;
  /** Current market value of the position, used as the fixed weight. */
  value: number;
  series: PriceSeries | null;
}

/** One line on the chart. */
export interface SectorTrend {
  key: string;
  label: string;
  /** Tickers that actually contributed — those with a usable series. */
  tickers: string[];
  /** Rebased to 100 at the first common date. */
  points: { d: string; v: number }[];
  /** Percent change across the window, for the legend. */
  changePct: number;
}

/** Dates every holding has a price for.
 *
 * The intersection rather than the union, because a basket whose
 * membership changes mid-series draws a step that no stock took. A
 * ticker listed halfway through the window costs the whole sector its
 * earlier history, which is the correct trade: a shorter honest line
 * beats a longer invented one.
 */
function commonDates(serieses: PriceSeries[]): string[] {
  if (serieses.length === 0) return [];
  let dates = new Set(serieses[0].points.map((p) => p.d));
  for (const s of serieses.slice(1)) {
    const next = new Set<string>();
    for (const p of s.points) if (dates.has(p.d)) next.add(p.d);
    dates = next;
  }
  return [...dates].sort();
}

function closesByDate(points: PricePoint[]): Map<string, number> {
  return new Map(points.map((p) => [p.d, p.c]));
}

/**
 * Build one rebased index per sector.
 *
 * Sectors are dropped rather than drawn flat when they cannot support a
 * line: no holding with a price series, or fewer than two dates every
 * holding shares. A drawn flat line reads as "this sector did nothing",
 * which is a claim; absence is not.
 */
export function buildSectorTrends(
  sectors: { key: string; label: string; holdings: TrendHolding[] }[],
): SectorTrend[] {
  // Every sector's usable window, found before any of them is rebased.
  const windows = sectors
    .map((sector) => {
      const usable = sector.holdings.filter(
        (h): h is TrendHolding & { series: PriceSeries } =>
          h.series !== null && h.series.points.length > 0 && h.value > 0,
      );
      return { sector, usable, dates: commonDates(usable.map((h) => h.series)) };
    })
    .filter((w) => w.usable.length > 0 && w.dates.length >= 2);

  if (windows.length === 0) return [];

  // One base date for all of them: the latest start any sector has.
  // Rebasing each sector to its own first date would put two lines at
  // 100 on different days and invite exactly the comparison the chart
  // exists for — a sector whose names listed recently would appear to
  // start level with one that has a year of history behind it. The cost
  // is that everyone is trimmed to the shortest window, which is the
  // right trade: the chart's whole job is comparing these lines.
  const base = windows
    .map((w) => w.dates[0])
    .reduce((latest, d) => (d > latest ? d : latest));

  const out: SectorTrend[] = [];

  for (const { sector, usable, dates: all } of windows) {
    // The base has to be a day this sector actually traded, or its 100
    // would be struck on a different day from everyone else's and the
    // comparison would be back. Every window starts on or before the
    // base by construction, so this only drops a sector with a hole on
    // that exact day — rare, and better dropped than silently shifted.
    if (!all.includes(base)) continue;
    const dates = all.filter((d) => d >= base);
    if (dates.length < 2) continue;

    const closes = usable.map((h) => closesByDate(h.series.points));
    // A zero or missing base price would divide the whole line by
    // nothing. Drop the holding rather than the sector.
    const kept = usable
      .map((h, i) => ({ h, closes: closes[i], base: closes[i].get(base) }))
      .filter((x): x is typeof x & { base: number } => !!x.base && x.base > 0);
    if (kept.length === 0) continue;

    const totalWeight = kept.reduce((s, x) => s + x.h.value, 0);
    if (totalWeight <= 0) continue;

    const points = dates.map((d) => {
      let acc = 0;
      for (const x of kept) {
        const c = x.closes.get(d);
        // Guaranteed present: d came from the intersection.
        if (c === undefined) continue;
        acc += x.h.value * (c / x.base);
      }
      return { d, v: (acc / totalWeight) * 100 };
    });

    out.push({
      key: sector.key,
      label: sector.label,
      tickers: kept.map((x) => x.h.ticker).sort(),
      points,
      changePct: points[points.length - 1].v - 100,
    });
  }

  return out;
}

/**
 * Merge the per-sector lines into the row shape recharts wants, where
 * every sector is a key on one object per date.
 *
 * Sectors can cover different windows — one may hold a recently listed
 * name — so a date missing for a sector is left undefined rather than
 * zero. recharts breaks the line there, which is the truth; a zero
 * would draw a crash to the axis.
 */
export function toChartRows(
  trends: SectorTrend[],
): Record<string, string | number>[] {
  const dates = new Set<string>();
  const byKey = new Map<string, Map<string, number>>();
  for (const t of trends) {
    const lookup = new Map<string, number>();
    for (const p of t.points) {
      dates.add(p.d);
      lookup.set(p.d, p.v);
    }
    byKey.set(t.key, lookup);
  }

  return [...dates].sort().map((d) => {
    const row: Record<string, string | number> = { d };
    for (const t of trends) {
      const v = byKey.get(t.key)?.get(d);
      if (v !== undefined) row[t.key] = Number(v.toFixed(2));
    }
    return row;
  });
}
