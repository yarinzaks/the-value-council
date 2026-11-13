// The positions an agent used to hold, from the ledger it sold them in.
//
// The performance chart could draw the book and the names it holds
// now. A name it sold was gone from the page the day it left, so the
// question a reader asks first about a sale — was it right, what did
// the price do after we left — had no answer anywhere. The ledger has
// the sale, and since the entry fields shipped it has the position's
// beginning too; data/prices has the series past the exit.
//
// What a row here claims and does not claim: the entry and exit prices
// are the ledger's. An `executed` row is the fill; a `reconstructed`
// row is the last mark the book carried, and the page says which. The
// "since exit" figure is the published close against the ledger's
// exit price, so a reconstructed row's is an estimate on both ends.

import type { SeriesPoint } from "./compare-series";

export interface LedgerSale {
  date: string;
  ticker: string;
  side: string;
  shares: number;
  price: number;
  realized_pnl_usd: number;
  source?: string;
  entry_date?: string;
  entry_price?: number;
}

export interface OpenLine {
  ticker: string;
  entry_date: string;
}

export interface ClosedPosition {
  /** `${ticker}@${exitDate}` — a name sold and bought back closes twice. */
  key: string;
  ticker: string;
  entryDate: string | null;
  entryPrice: number | null;
  exitDate: string;
  /** Share-weighted across the sales that closed the line. */
  exitPrice: number;
  shares: number;
  realizedPnlUsd: number;
  /** Entry to exit, on the ledger's two prices. Null without an entry. */
  heldPct: number | null;
  /** Calendar days from entry to exit. Null without an entry. */
  heldDays: number | null;
  /** The newest published close against the exit price. Null when the
   *  series does not reach past the exit, which is what "delisted" or
   *  "no longer followed" looks like from here. */
  sinceExitPct: number | null;
  sinceExitDate: string | null;
  /** The index over the same two windows, for the same reader. */
  spyHeldPct: number | null;
  spySinceExitPct: number | null;
  /** True when any sale that closed the line was reconstructed from
   *  committed books rather than recorded at execution. */
  reconstructed: boolean;
}

function closeOnOrBefore(points: readonly SeriesPoint[], day: string): SeriesPoint | null {
  let best: SeriesPoint | null = null;
  for (const p of points) {
    if (p.d <= day && (best === null || p.d > best.d)) best = p;
  }
  return best;
}

function last(points: readonly SeriesPoint[]): SeriesPoint | null {
  let best: SeriesPoint | null = null;
  for (const p of points) if (best === null || p.d > best.d) best = p;
  return best;
}

function pct(from: number | null | undefined, to: number | null | undefined): number | null {
  if (from === null || from === undefined || to === null || to === undefined) return null;
  if (!Number.isFinite(from) || !Number.isFinite(to) || from <= 0) return null;
  return Math.round((to / from - 1) * 1e6) / 1e4;
}

function daysBetween(from: string, to: string): number {
  return Math.round(
    (Date.parse(to + "T00:00:00Z") - Date.parse(from + "T00:00:00Z")) / 86_400_000,
  );
}

/** Whether a sale ended the line or only trimmed it.
 *
 *  A ledger row does not say. The book does: a name still held whose
 *  entry is on or before the sale was trimmed and kept; a name not held,
 *  or held again from a later entry, was closed. */
function endedTheLine(sale: LedgerSale, open: ReadonlyMap<string, string>): boolean {
  const entry = open.get(sale.ticker.toUpperCase());
  if (entry === undefined) return true;
  return entry > sale.date;
}

/**
 * The closed positions, newest exit first.
 *
 * Sales of one name that share an entry date are one closing — the
 * runner can sell a line in two pieces on consecutive days — and are
 * folded into one row at the share-weighted exit price. A sale with no
 * entry fields (every row written before 2026-11-13) still gets a row,
 * with its entry unknown rather than guessed.
 */
export function buildClosedPositions(
  ledger: readonly LedgerSale[],
  openLines: readonly OpenLine[],
  series: Readonly<Record<string, readonly SeriesPoint[]>>,
): ClosedPosition[] {
  const open = new Map(openLines.map((l) => [l.ticker.toUpperCase(), l.entry_date]));
  const spy = series.SPY ?? [];

  // Group the closing sales by (ticker, entry). Sales without an entry
  // group by (ticker, exit date) so two unknown-entry sales on different
  // days stay apart.
  const groups = new Map<string, LedgerSale[]>();
  for (const sale of ledger) {
    if (sale.side.toUpperCase() !== "SELL") continue;
    if (!Number.isFinite(sale.price) || sale.price <= 0 || !(sale.shares > 0)) continue;
    if (!endedTheLine(sale, open)) continue;
    const ticker = sale.ticker.toUpperCase();
    const groupKey = sale.entry_date ? `${ticker}#${sale.entry_date}` : `${ticker}#?${sale.date}`;
    const bucket = groups.get(groupKey);
    if (bucket) bucket.push(sale);
    else groups.set(groupKey, [sale]);
  }

  const out: ClosedPosition[] = [];
  for (const sales of groups.values()) {
    const ticker = sales[0].ticker.toUpperCase();
    const shares = sales.reduce((n, s) => n + s.shares, 0);
    const exitPrice = sales.reduce((v, s) => v + s.shares * s.price, 0) / shares;
    const exitDate = sales.map((s) => s.date).sort().at(-1) as string;
    const entryDate = sales[0].entry_date ?? null;
    const entryPrice =
      typeof sales[0].entry_price === "number" && Number.isFinite(sales[0].entry_price)
        ? sales[0].entry_price
        : null;
    const points = series[ticker] ?? [];
    const newest = last(points);
    const reaches = newest !== null && newest.d > exitDate;
    const spyEntry = entryDate ? closeOnOrBefore(spy, entryDate) : null;
    const spyExit = closeOnOrBefore(spy, exitDate);
    const spyNow = reaches ? closeOnOrBefore(spy, newest.d) : null;
    out.push({
      key: `${ticker}@${exitDate}`,
      ticker,
      entryDate,
      entryPrice,
      exitDate,
      exitPrice,
      shares,
      realizedPnlUsd: sales.reduce((v, s) => v + (s.realized_pnl_usd ?? 0), 0),
      heldPct: pct(entryPrice, exitPrice),
      heldDays: entryDate ? daysBetween(entryDate, exitDate) : null,
      sinceExitPct: reaches ? pct(exitPrice, newest.c) : null,
      sinceExitDate: reaches ? newest.d : null,
      spyHeldPct: pct(spyEntry?.c, spyExit?.c),
      spySinceExitPct: pct(spyExit?.c, spyNow?.c),
      reconstructed: sales.some((s) => s.source === "reconstructed"),
    });
  }
  return out.sort((a, b) => b.exitDate.localeCompare(a.exitDate) || a.ticker.localeCompare(b.ticker));
}

/** The tickers a page must load a series for to draw the closed
 *  positions: every name the ledger sold. */
export function soldTickers(ledger: readonly LedgerSale[]): string[] {
  return [...new Set(
    ledger.filter((s) => s.side.toUpperCase() === "SELL").map((s) => s.ticker.toUpperCase()),
  )].sort();
}
