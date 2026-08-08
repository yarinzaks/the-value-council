// A standing warning on every page that shows backtest returns.
//
// Why it still stands after the re-run
// ------------------------------------
//
// The 2026-08-07 campaign re-ran all ten agents through the corrected
// pipeline, and three of the four defects this banner used to describe
// are gone:
//
//   * A price is no longer carried forward past MAX_CARRY_FORWARD_DAYS,
//     so a stale quote that suddenly catches up can no longer read as a
//     gain that never happened.
//   * Concept chains resolve by recency, not by the order concepts
//     appear in, so revenue is no longer served years stale.
//   * Every agent runs core.backtest.validation_window. Schloss used to
//     start in 2022 and Greenblatt in 2022-12, which meant the
//     leaderboard was partly ranking windows.
//
// The size of the correction is the argument for having said so:
// Greenblatt went from a 112.13% CAGR to 14.60%, Klarman from a 1,365%
// total return to 106.58%, Lynch from 61.95% CAGR to 11.60%. The ten
// now span 9.70% to 21.61% against an S&P that returned 14.49% — a
// spread you can argue with, which the old one was not.
//
// What is NOT fixed, and why this component stays:
//
//   * FullMarketUniverse is not survivorship-bias-free. Its roster is
//     the SEC's list of currently registered issuers, so a company
//     acquired or wound up before the prefetch ran is absent at every
//     historical date. Of ten large caps acquired or failed while
//     filing 10-Ks, none appears at any historical date. The missing
//     names are disproportionately the ones that failed, so the bias
//     runs in one direction: up.
//
// Presenting the numbers without saying so would be the same failure as
// the docstring that once claimed the universe was bias-free —
// technically it is what the file contains, and it is not true.

import { Card } from "@/components/Cards";

export function BacktestCaveat({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <Card className="mb-4 border-amber-300 dark:border-amber-700/60 bg-amber-50/60 dark:bg-amber-950/20">
      <div className="flex gap-3">
        <span aria-hidden className="text-amber-600 dark:text-amber-400">
          ⚠
        </span>
        <div>
          <div className="text-sm font-semibold text-amber-900 dark:text-amber-200">
            {title}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-amber-900/80 dark:text-amber-200/80">
            {body}
          </p>
        </div>
      </div>
    </Card>
  );
}
