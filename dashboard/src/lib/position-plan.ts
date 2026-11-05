// The agent's own plan for a name it holds.
//
// The question the product could not answer
// -----------------------------------------
//
// Asked why a name is held, what the agent expects of it, and when it
// means to sell, every surface answered with numbers. The journal row
// carries `exit_trigger` — a condition, in English — and nothing else.
// No horizon, no valuation, and the agent's own account of the thesis
// only as the English sentence the LLM wrote.
//
// All of it already exists. core/llm/memo_log.py writes one memo per
// candidate under data/memos/<agent>/<day>.json, and the five agents
// with an analyst layer fill a structured model: a valuation per
// share, a margin of safety, a holding period, the triggers that would
// reverse the decision, and — this is the part nothing read — a thesis
// written twice, `thesis_en` and `thesis_he`, the Hebrew one a faithful
// translation in the agent's own voice rather than a phrase table's
// approximation of it. Measured on the live tree: 82 of the 240 held
// positions have a memo, and every one of those 82 has `thesis_he`.
//
// Five schemas, five dialects
// ---------------------------
//
// The models were written per playbook and do not agree with each
// other, so reading them is the work:
//
//   holding period   Fisher writes `_years` as a STRING range ("10+",
//                    "5-10", "3-5"); Buffett writes `_years` as an int;
//                    Marks, Lynch and Klarman write `_months` as an int.
//   valuation        Buffett is explicit — `_usd_per_share`. Lynch,
//                    Fisher and Klarman write `_usd`, which is also per
//                    share: checked against the `current_price_usd` in
//                    the same memo, every value sits within a few
//                    multiples of the share price, never at company
//                    scale. Marks writes none at all, which matches its
//                    doctrine: no Marks code computes a per-share value.
//   exit triggers    `exit_triggers` (Lynch, Buffett), `reverse_triggers`
//                    (Fisher, Klarman), `scaling_out_plan` (Marks,
//                    Klarman).
//
// What is deliberately not shown
// ------------------------------
//
// Seven of Lynch's thirty memos record an `intrinsic_value_estimate_usd`
// equal to `current_price_usd` to the cent, with a margin of safety of
// 0.0. That is not a valuation the agent reached; it is the price
// echoed back. Printing it as a target would be the same class of
// mistake as the action log calling a valuation a waiting price, so a
// value that equals the memo's own recorded price is treated as absent.

import type { BrainMemo } from "@/lib/types";
import type { Locale } from "@/lib/i18n";

/** A holding horizon, in whichever shape the memo recorded it. */
export type Horizon =
  | { kind: "years"; years: number }
  | { kind: "months"; months: number }
  /** Fisher's enum, e.g. "10+" or "5-10". Kept verbatim; it is a range
   *  and collapsing it to a number would invent precision. */
  | { kind: "range"; range: string };

export interface PositionPlan {
  /** The agent's thesis in the reader's language, as the memo wrote
   *  it — not a translation of the English by this codebase. */
  thesis: string | null;
  /** Whether `thesis` is the memo's Hebrew field. The page says so,
   *  because "the agent wrote this" and "we translated this" are
   *  different claims. */
  thesisIsNative: boolean;
  horizon: Horizon | null;
  /** Per-share valuation, where the memo actually reached one. */
  intrinsicValue: number | null;
  marginOfSafetyPct: number | null;
  /** What the memo says would end the position. */
  exitTriggers: string[];
  /** Whether those triggers are in the reader's own language.
   *
   *  The thesis is bilingual in every model; the trigger lists are
   *  not — `exit_triggers`, `reverse_triggers` and the scaling-out
   *  plan are English-only in all five schemas. They are one-off
   *  sentences written fresh per company (71 distinct strings across
   *  71 held positions), so no phrase table can reach them, and the
   *  page says so rather than letting English sit on a Hebrew surface
   *  unannounced. The Hebrew field names are read for when the
   *  analyst layer starts writing them. */
  exitTriggersAreNative: boolean;
}

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() !== "" ? v.trim() : null;

const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];

/** The trim levels Marks and Klarman record as a plan rather than a list. */
function scalingOut(v: unknown): string[] {
  if (typeof v !== "object" || v === null) return [];
  const plan: Record<string, unknown> = v as Record<string, unknown>;
  const levels = plan.trim_at_price_levels;
  if (!Array.isArray(levels)) return [];
  return levels
    .map((x) => num(x))
    .filter((x): x is number => x !== null)
    .map((p) => `trim at ${p.toFixed(2)}`);
}

function horizonFrom(memo: Record<string, unknown>): Horizon | null {
  const years = memo.expected_holding_period_years;
  // Fisher's range enum first: it is a string, so the numeric branch
  // would read it as absent.
  const range = str(years);
  if (range !== null) return { kind: "range", range };
  const y = num(years);
  if (y !== null && y > 0) return { kind: "years", years: y };
  const m = num(memo.expected_holding_period_months);
  if (m !== null && m > 0) return { kind: "months", months: m };
  return null;
}

function valuationFrom(memo: Record<string, unknown>): number | null {
  const iv =
    num(memo.intrinsic_value_estimate_usd_per_share) ??
    num(memo.intrinsic_value_estimate_usd);
  if (iv === null || iv <= 0) return null;
  // A value equal to the memo's own price is the price, not an
  // estimate. See the note above.
  const price =
    num(memo.current_price_usd) ?? num(memo.current_price) ?? num(memo.price);
  if (price !== null && Math.abs(iv - price) < 0.01) return null;
  return iv;
}

/** Read one memo's plan, or null where it holds none of it. */
export function planFromMemo(
  memo: Record<string, unknown>,
  locale: Locale,
): PositionPlan | null {
  const he = str(memo.thesis_he);
  const en = str(memo.thesis_en);
  const wantsHebrew = locale === "he";
  const thesis = wantsHebrew ? (he ?? en) : (en ?? he);

  const english = [
    ...strList(memo.exit_triggers),
    ...strList(memo.reverse_triggers),
    ...scalingOut(memo.scaling_out_plan),
  ];
  // Not written by any agent yet. Read anyway, so that the day the
  // analyst layer fills them the page needs no second change.
  const native = wantsHebrew
    ? [...strList(memo.exit_triggers_he), ...strList(memo.reverse_triggers_he)]
    : [];

  const plan: PositionPlan = {
    thesis,
    thesisIsNative: wantsHebrew ? he !== null : en !== null,
    horizon: horizonFrom(memo),
    intrinsicValue: valuationFrom(memo),
    marginOfSafetyPct: num(memo.margin_of_safety_pct),
    exitTriggers: native.length > 0 ? native : english,
    exitTriggersAreNative: !wantsHebrew || native.length > 0,
  };

  // Nothing worth a section. A margin of safety on its own is a number
  // with no claim attached to it, so it does not count as a plan.
  if (
    plan.thesis === null &&
    plan.horizon === null &&
    plan.intrinsicValue === null &&
    plan.exitTriggers.length === 0
  ) {
    return null;
  }
  return plan;
}

/**
 * The newest memo an agent wrote about a name.
 *
 * `loadBrainMemos` returns newest first, but it also caps at 400
 * records, so this makes no assumption about order and compares
 * timestamps itself.
 */
export function latestMemoFor(
  memos: BrainMemo[],
  agent: string,
  ticker: string,
): BrainMemo | null {
  let best: BrainMemo | null = null;
  for (const m of memos) {
    if (m.agent !== agent || m.ticker !== ticker) continue;
    if (best === null || (m.timestamp ?? "") > (best.timestamp ?? "")) best = m;
  }
  return best;
}

/** The plan for one held name, or null where the analyst layer has not
 *  reached it — which is 158 of the 240 held positions today. */
export function planFor(
  memos: BrainMemo[],
  agent: string,
  ticker: string,
  locale: Locale,
): PositionPlan | null {
  const memo = latestMemoFor(memos, agent, ticker);
  return memo ? planFromMemo(memo.memo, locale) : null;
}
