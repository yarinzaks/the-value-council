// Put the book, its holdings and the indices on one axis.
//
// The chart could draw the portfolio's NAV and nothing else, so the
// obvious question — did this agent beat the market, and which name
// carried it — had no answer on the page. The pieces were all here:
// the daily snapshots hold NAV, and data/prices/<TICKER>.json holds a
// year of closes for every held name. They just could not be drawn
// together, because $12,087 of NAV and $18.52 of Smithfield share no
// scale.
//
// Rebasing solves that. Every series starts at 0% on the first day of
// the window and the axis reads in percent, so a line above another
// means exactly what it looks like. It is also the only form that
// works for a name sold mid-window: the position is gone, the price
// series is not, and "what did it do after we sold" stays answerable.
//
// Two alignment problems are handled here rather than in the chart.
// A price series carries trading days, while snapshots carry the days
// the runner ran, and the two sets are not equal — a holiday the
// scheduler mistook for a session is in one and not the other. And a
// name bought last week has no closes for the start of a 1M window.
// Missing days carry the last known close forward; days before a
// series begins are null, which Recharts draws as a line that starts
// late rather than as a line that starts at zero.

export interface SeriesPoint {
  d: string;
  c: number;
}

/** One rebased row per date, keyed by series id. */
export interface CompareRow {
  date: string;
  [key: string]: number | string | null;
}

/** Percent change from the first value each series has in the window. */
/** How long a close may stand in for days that have none.
 *
 *  A market shuts for weekends and holidays, so the last close is the
 *  right value for a few days after it. It is not the right value for
 *  two months. Without a bound, a holding whose feed stopped — eight
 *  of them had, on the store this was written against — rebased to a
 *  dead-flat 0.00%, which reads as "this share did not move" when the
 *  truth is "nobody has published a price since August". Those are
 *  opposite claims and the chart drew them the same.
 *
 *  Nine days covers the longest run of shut sessions a US exchange
 *  produces (a Thursday holiday, the Friday after it, and the weekend
 *  on either side) with room to spare, and is far short of the
 *  gap a stopped feed opens.
 */
export const MAX_CARRY_DAYS = 9;

function daysBetween(from: string, to: string): number {
  return Math.round(
    (Date.parse(to + "T00:00:00Z") - Date.parse(from + "T00:00:00Z")) / 86_400_000,
  );
}

export function rebase(
  dates: readonly string[],
  points: readonly SeriesPoint[],
  maxCarryDays: number = MAX_CARRY_DAYS,
): (number | null)[] {
  if (dates.length === 0) return [];

  // Walk both in order, carrying the last close forward. The points
  // are sorted by date in the published files; sorting a copy here
  // makes that a property of this function rather than of the file.
  const sorted = [...points].sort((a, b) => a.d.localeCompare(b.d));
  const out: (number | null)[] = [];
  let i = 0;
  let last: number | null = null;
  let lastDate: string | null = null;
  let base: number | null = null;

  for (const date of dates) {
    while (i < sorted.length && sorted[i].d <= date) {
      last = sorted[i].c;
      lastDate = sorted[i].d;
      i += 1;
    }
    // Past the carry limit the series has nothing to say about this
    // day, and saying nothing is the honest answer.
    if (lastDate !== null && daysBetween(lastDate, date) > maxCarryDays) {
      out.push(null);
      continue;
    }
    if (last === null || last === 0) {
      // Before this series starts — or a zero close, which cannot be
      // a base and would make every later point infinite.
      out.push(null);
      continue;
    }
    if (base === null) base = last;
    // Rounded because this is a display series and binary floats are
    // not: 110/100 lands on 10.000000000000009, which a tooltip would
    // print and an equality test would trip over. Four places is
    // one ten-thousandth of a percent — far past anything a chart,
    // or a reader, can tell apart.
    out.push(Math.round((last / base - 1) * 1e6) / 1e4);
  }
  return out;
}

/**
 * Rows for the chart: one per date, a rebased percentage per selected
 * series.
 *
 * `selected` is honoured in order, and a key with no points is left
 * out rather than drawn flat — an absent series and a series that did
 * not move must not look the same.
 */
export function buildCompareRows(
  dates: readonly string[],
  series: Readonly<Record<string, readonly SeriesPoint[]>>,
  selected: readonly string[],
  maxCarryDays: number = MAX_CARRY_DAYS,
): CompareRow[] {
  const rows: CompareRow[] = dates.map((date) => ({ date }));

  for (const key of selected) {
    const points = series[key];
    if (!points || points.length === 0) continue;
    const values = rebase(dates, points, maxCarryDays);
    // All-null means the series exists but has nothing inside the
    // window; drawing its key would put an empty entry in the legend.
    if (values.every((v) => v === null)) continue;
    values.forEach((v, i) => {
      rows[i][key] = v;
    });
  }

  return rows;
}

/** Which of `selected` actually produced a line, in the given order. */
export function drawableKeys(
  dates: readonly string[],
  series: Readonly<Record<string, readonly SeriesPoint[]>>,
  selected: readonly string[],
  maxCarryDays: number = MAX_CARRY_DAYS,
): string[] {
  return selected.filter((key) => {
    const points = series[key];
    if (!points || points.length === 0) return false;
    return rebase(dates, points, maxCarryDays).some((v) => v !== null);
  });
}
