// A standing warning on every page that shows backtest returns.
//
// Why this is not optional
// ------------------------
//
// The stored runs report figures no value strategy produces. Benjamin
// Graham — whose entire method is buying at a discount with a margin of
// safety — shows a 3,029% total return over five years, a 98.97% CAGR
// and a best year of +476%. The heatmap carries +447% alpha for one
// agent-year and +243% for another.
//
// Those are not results, they are symptoms, and this session identified
// the causes:
//
//   * The full-market universe is not survivorship-bias-free. Of ten
//     large caps acquired or failed while filing 10-Ks, none appears at
//     any historical date. The missing names are the ones that failed,
//     so returns are overstated in one direction.
//   * get_price_on carried a price forward without limit. 307 of 395
//     tickers have a gap over five days, median worst 367, maximum
//     4,021 — and a stale price that suddenly catches up reads as a
//     gain that never happened.
//   * Concept chains resolved by order rather than recency, serving
//     revenue a median seven fiscal years stale on a quarter of the
//     universe.
//
// Every stored run predates those fixes. Presenting the numbers without
// saying so would be the same failure as the docstring that claimed the
// universe was bias-free: technically it is what the file contains,
// and it is not true.
//
// The windows are also not comparable. One agent is measured over
// 2022-12-30 to 2024-12-31 against an S&P of 25.47%; another over
// 2019-12-30 to 2024-12-31 against 14.49%. Ranking them in one table
// sorts partly by who caught a better two years.

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
