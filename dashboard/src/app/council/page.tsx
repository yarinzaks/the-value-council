// The Council — a doctrine-driven agent, shown on its own terms.
//
// It is deliberately not on the leaderboard. The eleven agents there are
// scored on the same five-year backtest and their CAGRs mean the same
// thing as each other. This one reads filings and reasons; it cannot be
// replayed over history without the model knowing how the history came
// out, so a number in that column would look comparable and would not
// be. What it shows instead is what it is actually doing: the risk dial,
// the limits, the filings on what it holds, and the punch card.

import { Card, EmptyState, Money, PageTitle } from "@/components/Cards";
import { loadCouncilState } from "@/lib/data";
import { getServerI18n } from "@/lib/locale-server";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

const STANCE_STYLE: Record<string, string> = {
  risk_on: "text-green-700 dark:text-green-400",
  risk_off: "text-red-700 dark:text-red-400",
  unknown: "text-muted",
};

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300",
  investigate:
    "bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300",
  note: "bg-council-100 dark:bg-council-800 text-council-700 dark:text-council-300",
};

const LIMIT_STYLE: Record<string, string> = {
  pass: "text-green-700 dark:text-green-400",
  breach: "text-red-700 dark:text-red-400",
  unknown: "text-amber-700 dark:text-amber-400",
};

function pct(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export default async function CouncilPage() {
  const { locale } = getServerI18n();
  const he = locale === "he";
  const state = await loadCouncilState();

  if (!state) {
    return (
      <>
        <PageTitle
          title={he ? "המועצה" : "The Council"}
          subtitle={
            he
              ? "סוכן שפועל לפי דוקטרינה כתובה, לא לפי מסנן"
              : "An agent answering to a written doctrine, not a screen"
          }
        />
        <EmptyState>
          {he
            ? "טרם רצה. הריצה הראשונה תפרסם את מצבו."
            : "It has not run yet. The first run publishes its state."}
        </EmptyState>
      </>
    );
  }

  const { journal, regime } = state;

  return (
    <>
      <PageTitle
        title={he ? "המועצה" : "The Council"}
        subtitle={
          he
            ? "סוכן שפועל לפי דוקטרינה כתובה, לא לפי מסנן"
            : "An agent answering to a written doctrine, not a screen"
        }
      />

      {/* What it is, stated before any number — the page is otherwise
          easy to read as a twelfth agent, which it is not. */}
      <Card className="mb-4">
        <p className="text-sm leading-relaxed text-council-700 dark:text-council-300">
          {he
            ? "הוא מציע, אדם מאשר. הוא צפוי להחזיק כלום במשך תקופות ארוכות — 0 עד 2 פוזיציות בשנה — וזו לא תקלה אלא האסטרטגיה. אין לו מספר בטבלת הליגה כי אי אפשר להריץ אותו על ההיסטוריה בלי שהמודל יידע איך היא נגמרה."
            : "It proposes; a person approves. It expects to hold nothing for long stretches — 0 to 2 positions a year — and that is the strategy rather than a fault. It has no place on the leaderboard because it cannot be replayed over history without the model knowing how that history came out."}
        </p>
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Card>
          <div className="text-xs text-muted">{he ? "שווי" : "NAV"}</div>
          <div className="text-xl font-semibold">
            <Money value={state.nav} />
          </div>
          <div className="text-xs text-muted">
            {state.positions} {he ? "החזקות" : "positions"}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">{he ? "מזומן" : "Cash"}</div>
          <div className="text-xl font-semibold">{pct(state.cash_weight)}</div>
          <div className="text-xs text-muted">
            {he ? "רצפה 5%" : "floor 5%"}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">
            {he ? "ירידה מהשיא" : "Drawdown"}
          </div>
          <div className="text-xl font-semibold">
            {pct(state.drawdown_from_peak)}
          </div>
          <div className="text-xs text-muted">
            {state.circuit_breaker
              ? he
                ? "מפסק פעיל"
                : "breaker tripped"
              : he
                ? "מפסק ב-25%-"
                : "breaker at -25%"}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">
            {he ? "כרטיס אגרוף" : "Punch card"}
          </div>
          <div className="text-xl font-semibold">
            {journal.punch_card.remaining}/{journal.punch_card.total}
          </div>
          <div className="text-xs text-muted">
            {he ? "לכל החיים" : "for its lifetime"}
          </div>
        </Card>
      </div>

      {regime && (
        <Card className="mb-4">
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-sm font-semibold">
              {he ? "חוגת המשטר" : "Regime dial"}
            </h3>
            <span className="text-sm">
              <span className="font-semibold">{regime.risk_on_count}</span>
              <span className="text-muted">/4 {he ? "סיכון-פתוח" : "risk-on"}</span>
            </span>
          </div>
          <div className="space-y-1.5">
            {regime.signals.map((s) => (
              <div
                key={s.series}
                className="flex items-baseline gap-3 text-xs border-b border-council-100 dark:border-council-800 last:border-b-0 pb-1.5"
              >
                <span className="font-mono w-32 shrink-0">{s.series}</span>
                <span
                  className={`w-16 shrink-0 font-medium ${STANCE_STYLE[s.stance] ?? ""}`}
                >
                  {s.stance}
                </span>
                <span className="text-muted">{s.reason}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mb-4">
        <h3 className="text-sm font-semibold mb-3">
          {he ? "מגבלות חלק 4" : "Part 4 limits"}
        </h3>
        {state.limits.length === 0 ? (
          <p className="text-xs text-muted">
            {he
              ? "אין החזקות, אז אין מה למדוד."
              : "Nothing held, so nothing to measure."}
          </p>
        ) : (
          <div className="space-y-1">
            {state.limits.map((l) => (
              <div key={l.limit} className="flex items-baseline gap-3 text-xs">
                <span
                  className={`w-16 shrink-0 font-medium ${LIMIT_STYLE[l.state] ?? ""}`}
                >
                  {l.state}
                </span>
                <span className="font-mono flex-1 truncate">{l.limit}</span>
                <span className="tabular text-muted">
                  {l.observed === null ? "—" : pct(l.observed)}
                </span>
                {l.note && <span className="text-muted">{l.note}</span>}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <h3 className="text-sm font-semibold mb-3">
          {he ? "הגשות על ההחזקות" : "Filings on what is held"}
        </h3>
        {state.filings_flagged.length === 0 ? (
          <p className="text-xs text-muted">
            {he
              ? "אין הגשות מסומנות. זו התוצאה הרגילה."
              : "Nothing flagged. That is the normal outcome."}
          </p>
        ) : (
          <div className="space-y-1.5">
            {state.filings_flagged.map((f, i) => (
              <div
                key={`${f.ticker}-${f.code}-${f.filed}-${i}`}
                className="flex items-baseline gap-2 text-xs"
              >
                <span className="tabular text-muted w-20 shrink-0">
                  {f.filed}
                </span>
                <span className="font-mono w-14 shrink-0">{f.ticker}</span>
                <span
                  className={`px-1.5 py-0.5 rounded shrink-0 ${SEVERITY_STYLE[f.severity] ?? ""}`}
                >
                  {f.code}
                </span>
                <span className="text-muted">{f.meaning}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <p className="mt-4 text-[11px] text-muted">
        {he ? "עודכן" : "Updated"} {state.updated} · {he ? "ריצה" : "run"}{" "}
        {state.run}
      </p>
    </>
  );
}
