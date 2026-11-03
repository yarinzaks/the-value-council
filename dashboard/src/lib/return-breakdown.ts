// Splitting a book's return into parts that add back up to its NAV.
//
// Graham showed +24.55% while four of his five open positions were
// losing money. Both true, and together unreadable: nothing said that
// the open book was down $704 while closed trades had made $3,239.
//
// The identity is
//
//     NAV = initial + realized + unrealized + dividends - costs
//
// Every term but `realized` is readable off the book, so realized is
// solved for rather than summed from the ledger. That ordering is the
// point: summing the ledger would understate the figure by exactly the
// history the ledger is missing, and the parts would stop adding up to
// a NAV the reader can check for themselves.

import type { LedgerRow } from "@/lib/data";
import type { LivePortfolio } from "@/lib/types";
import type { Contributor, ReturnParts } from "@/components/ReturnBreakdown";

export function realizedByTicker(ledger: LedgerRow[]): Contributor[] {
  const totals = new Map<string, number>();
  for (const row of ledger) {
    if (!row.realized_pnl_usd) continue;
    totals.set(
      row.ticker,
      (totals.get(row.ticker) ?? 0) + row.realized_pnl_usd,
    );
  }
  return [...totals.entries()]
    .map(([ticker, realized_usd]) => ({
      ticker,
      realized_usd: Math.round(realized_usd * 100) / 100,
    }))
    .sort((a, b) => b.realized_usd - a.realized_usd);
}

/**
 * The share of the closed result that carries a stock's name, or
 * `null` where the question has no percentage answer.
 *
 * Dividing regardless returns a number, and the number is nonsense:
 * where the ledger sums to more than the book's own realized figure
 * the card read "192% of closed gains named", and the remainder note
 * under it read "−92%". A loss divided by a loss, or a gain divided by
 * a loss, is just as misleading. None of those is a share of anything,
 * so none of them gets printed.
 */
export function namedShare(parts: {
  realized: number;
  attributed_total: number;
}): number | null {
  const { realized, attributed_total } = parts;
  if (realized <= 0) return null;
  if (attributed_total < 0 || attributed_total > realized) return null;
  return attributed_total / realized;
}

/**
 * Which way the remainder runs, or `null` where it is too small to
 * say anything about.
 *
 * This was the sign of `unattributed`, and the sign only means
 * "over-attributed" when the book closed a gain. Under a closed loss
 * the two sentences swap: market_core named nothing at all against a
 * -$102 result and was told its named trades overshot the book by
 * $102, while walter_schloss, whose names sum to -$306 against a
 * -$12 book, was told $294 "cannot be traced to a stock".
 *
 * What the reader is being told is whether the ledger accounts for
 * more of the closed result than the book itself reports, so that is
 * what gets measured — magnitudes, not signs. An empty ledger falls
 * out of it: nothing named cannot overshoot anything.
 */
export function remainderDirection(parts: {
  realized: number;
  attributed_total: number;
  unattributed: number;
}): "untraced" | "over" | null {
  // Under a dollar either way is rounding, and a note about it would
  // read as a finding.
  if (Math.abs(parts.unattributed) < 1) return null;
  return Math.abs(parts.attributed_total) > Math.abs(parts.realized)
    ? "over"
    : "untraced";
}

export function buildReturnParts(
  book: LivePortfolio,
  ledger: LedgerRow[],
): { parts: ReturnParts; contributors: Contributor[] } {
  const costBasis = (book.positions ?? []).reduce(
    (sum, p) => sum + p.shares * p.entry_price,
    0,
  );
  const unrealized = (book.invested ?? 0) - costBasis;
  const realized =
    (book.total_nav ?? 0) -
    (book.initial_cash ?? 0) -
    unrealized -
    (book.cumulative_dividends ?? 0) +
    (book.cumulative_costs ?? 0);

  const contributors = realizedByTicker(ledger);
  const attributed = contributors.reduce((n, c) => n + c.realized_usd, 0);

  return {
    parts: {
      initial_cash: book.initial_cash ?? 0,
      realized,
      unrealized,
      dividends: book.cumulative_dividends ?? 0,
      costs: book.cumulative_costs ?? 0,
      nav: book.total_nav ?? 0,
      attributed_total: attributed,
      unattributed: realized - attributed,
      // How many of the named amounts were inferred rather than
      // recorded, because a reconstruction presented as a record would
      // look complete and be approximate.
      //
      // This was a boolean gated on `every`, so one caught-up row
      // silenced the warning for the whole book: Neff was 87 of 94
      // reconstructed and said nothing, and the note reached 3 of the
      // 13 agents. A count cannot be switched off by a single row.
      reconstructed_rows: ledger.filter((r) => r.source === "reconstructed")
        .length,
      ledger_rows: ledger.length,
    },
    contributors,
  };
}
