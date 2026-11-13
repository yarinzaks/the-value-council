/**
 * Holdings the daily runner could not price.
 *
 * A held name with no bar for more than five sessions keeps its last
 * close as its mark — right for NAV over a halt, wrong as a silent
 * state. LEG was acquired on 2026-08-26 and sat in Klarman's book at
 * that close for 78 days with nothing on any page saying the number
 * referred to a listing that no longer existed (audit CE-1a). The runner
 * now writes `unpriced_since` on such a position; these helpers are how
 * the pages read it.
 *
 * Kept free of node:fs so the client-side positions table can import it.
 */
import type { LivePosition } from "./types";

type Flagged = Pick<LivePosition, "unpriced_since">;

/** True when the runner flagged this position as unpriced. */
export function isUnpriced(position: Flagged): boolean {
  return typeof position.unpriced_since === "string" && position.unpriced_since !== "";
}

export interface UnpricedHolding {
  ticker: string;
  /** First session with no bar, as the runner recorded it. */
  since: string;
  /** Shares × the last mark: what NAV is still counting for it. */
  value: number;
}

/** Every unpriced holding in a book, the longest-unpriced first. */
export function unpricedHoldings(
  positions: readonly Pick<LivePosition, "ticker" | "shares" | "current_price" | "unpriced_since">[],
): UnpricedHolding[] {
  return positions
    .filter(isUnpriced)
    .map((p) => ({
      ticker: p.ticker,
      since: p.unpriced_since ?? "",
      value: p.shares * p.current_price,
    }))
    .sort((a, b) => a.since.localeCompare(b.since) || a.ticker.localeCompare(b.ticker));
}
