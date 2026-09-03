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
      // Every entry inferred rather than recorded. Shown differently on
      // the page, because a reconstruction presented as a record would
      // look complete and be approximate.
      ledger_is_reconstructed:
        ledger.length > 0 &&
        ledger.every((r) => r.source === "reconstructed"),
    },
    contributors,
  };
}
