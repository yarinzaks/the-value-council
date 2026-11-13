/**
 * Were the filings behind the latest decisions stale? (audit FUND-2)
 *
 * Every live decision from about May 2026 read fundamentals frozen at
 * 2026-04-28, and no page said so. The open scan now writes, on every
 * book's snapshot, the newest filing it read for any universe member
 * (`fundamentals_newest_filed`) and whether that was over 120 days old
 * (`fundamentals_stale`). The run trades either way; this is how the
 * site tells a reader it did so on old numbers.
 *
 * Kept free of node:fs so it can be tested and imported anywhere.
 */
import type { DailySnapshot } from "./types";

type Measured = Pick<DailySnapshot, "date" | "fundamentals_stale" | "fundamentals_newest_filed">;

export interface FundamentalsStaleness {
  /** The scan date that recorded it. */
  date: string;
  /** Newest filing that scan read; null when it read none at all. */
  newestFiled: string | null;
}

/**
 * From each book's latest snapshot: stale when the most recent scan that
 * measured freshness said so, otherwise null. Snapshots that never
 * measured (older files, the close mark) are ignored.
 */
export function fundamentalsStaleness(
  latest: readonly (Measured | null)[],
): FundamentalsStaleness | null {
  const measured = latest.filter(
    (s): s is Measured => s !== null && typeof s.fundamentals_stale === "boolean",
  );
  if (measured.length === 0) return null;
  const newestDate = measured.reduce((acc, s) => (s.date > acc ? s.date : acc), "");
  const stale = measured.find((s) => s.date === newestDate && s.fundamentals_stale === true);
  if (!stale) return null;
  return { date: stale.date, newestFiled: stale.fundamentals_newest_filed ?? null };
}
