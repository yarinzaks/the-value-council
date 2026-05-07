"""Bilingual rationale builders.

Each strategy produces typed score objects with metric values that
need translating into a short human-readable explanation. We keep two
variants (English + Hebrew) on every position and watchlist entry so
the dashboard can render either without round-trip.

These functions are deliberately mechanical — they format the numbers
the strategy already computed; they do not call an LLM. Free-text
narrative summaries belong in a separate (slower, more expensive) path.
"""

from __future__ import annotations

from typing import Any


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if abs(v) < 10 else f"{v:.1f}%"


def fmt_ratio(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


# --------------------------------------------------------------------------
# Greenblatt
# --------------------------------------------------------------------------
def greenblatt_why(score: Any) -> tuple[str, str]:
    """``MagicFormulaScore`` → (en, he)."""
    en = (
        f"EY {fmt_pct(score.earnings_yield)}, ROC {fmt_pct(score.return_on_capital)}. "
        f"Magic Formula combined rank #{score.combined_rank}."
    )
    he = (
        f"EY {fmt_pct(score.earnings_yield)}, ROC {fmt_pct(score.return_on_capital)}. "
        f"דירוג משולב #{score.combined_rank} בפורמולה הקסומה."
    )
    return en, he


# --------------------------------------------------------------------------
# Schloss
# --------------------------------------------------------------------------
def schloss_why(score: Any) -> tuple[str, str]:
    en = (
        f"P/B {fmt_ratio(score.pb_ratio)}, D/E {fmt_ratio(score.debt_to_equity)}. "
        f"Trades below tangible book — Schloss deep-value bargain."
    )
    he = (
        f"P/B {fmt_ratio(score.pb_ratio)}, D/E {fmt_ratio(score.debt_to_equity)}. "
        f"נסחרת מתחת להון העצמי המוחשי — מציאה ערך עמוק לפי שלוס."
    )
    return en, he


# --------------------------------------------------------------------------
# Graham — has two modes (Net-Net + Defensive)
# --------------------------------------------------------------------------
def graham_net_net_why(score: Any) -> tuple[str, str]:
    en = (
        f"P/NCAV {fmt_ratio(score.p_ncav)} (≤ ⅔), "
        f"NCAV/share ${fmt_ratio(score.ncav_per_share)}. "
        f"Classic Graham Net-Net."
    )
    he = (
        f"P/NCAV {fmt_ratio(score.p_ncav)} (≤ ⅔), "
        f"NCAV למניה ${fmt_ratio(score.ncav_per_share)}. "
        f"Net-Net קלאסי לפי גראהם."
    )
    return en, he


def graham_defensive_why(score: Any) -> tuple[str, str]:
    en = (
        f"P/E {fmt_ratio(score.pe)}, P/B {fmt_ratio(score.pb)}, "
        f"current ratio {fmt_ratio(score.current_ratio)}. "
        f"Graham Defensive Investor (Intelligent Investor Ch.14)."
    )
    he = (
        f"P/E {fmt_ratio(score.pe)}, P/B {fmt_ratio(score.pb)}, "
        f"יחס שוטף {fmt_ratio(score.current_ratio)}. "
        f"\"המשקיע ההגנתי\" של גראהם (פרק 14)."
    )
    return en, he


# --------------------------------------------------------------------------
# Dreman
# --------------------------------------------------------------------------
def dreman_why(score: Any) -> tuple[str, str]:
    metric_names_en: list[str] = []
    metric_names_he: list[str] = []
    pe_q, pcf_q, pb_q, yld_q = score.qualifying_flags
    if pe_q:
        metric_names_en.append("P/E")
        metric_names_he.append("P/E")
    if pcf_q:
        metric_names_en.append("P/CF")
        metric_names_he.append("P/CF")
    if pb_q:
        metric_names_en.append("P/B")
        metric_names_he.append("P/B")
    if yld_q:
        metric_names_en.append("yield")
        metric_names_he.append("תשואה")

    en = (
        f"Bottom-quintile on {score.qualifying_metrics}/4 ({', '.join(metric_names_en)}). "
        f"Dreman contrarian, composite rank {score.composite_rank:.3f}."
    )
    he = (
        f"רבעון תחתון על {score.qualifying_metrics}/4 ({', '.join(metric_names_he)}). "
        f"ניגודי לפי דרמן, דירוג משולב {score.composite_rank:.3f}."
    )
    return en, he


# --------------------------------------------------------------------------
# Watchlist rationales — one per agent, slightly different wording
# --------------------------------------------------------------------------
def greenblatt_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"EY {fmt_pct(score.earnings_yield)}, ROC {fmt_pct(score.return_on_capital)}. "
        f"Currently rank #{score.combined_rank}; watching for top-30 entry."
    )
    he = (
        f"EY {fmt_pct(score.earnings_yield)}, ROC {fmt_pct(score.return_on_capital)}. "
        f"דירוג נוכחי #{score.combined_rank}; מעקב לכניסה לטופ-30."
    )
    return en, he


def schloss_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"P/B {fmt_ratio(score.pb_ratio)}; close to deep-value threshold. "
        f"Watching for further price decline."
    )
    he = (
        f"P/B {fmt_ratio(score.pb_ratio)}; קרוב לסף ערך עמוק. "
        f"מעקב לירידת מחיר נוספת."
    )
    return en, he


def graham_watch_why(score: Any) -> tuple[str, str]:
    # Score may be a NetNet or Defensive — duck-type on attribute presence.
    if hasattr(score, "p_ncav"):
        en = (
            f"P/NCAV {fmt_ratio(score.p_ncav)}; outside ⅔ threshold. "
            f"Watching for price decline."
        )
        he = (
            f"P/NCAV {fmt_ratio(score.p_ncav)}; מעל סף ⅔. "
            f"מעקב לירידת מחיר."
        )
    else:
        en = (
            f"P/E {fmt_ratio(score.pe)}, P/B {fmt_ratio(score.pb)}; "
            f"near Defensive thresholds — watching."
        )
        he = (
            f"P/E {fmt_ratio(score.pe)}, P/B {fmt_ratio(score.pb)}; "
            f"קרוב לסף המשקיע ההגנתי — במעקב."
        )
    return en, he


def dreman_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"Qualifies on {score.qualifying_metrics}/4 metrics, "
        f"composite rank {score.composite_rank:.3f}; just below top-25."
    )
    he = (
        f"מתאים ל־{score.qualifying_metrics}/4 מדדים, "
        f"דירוג משולב {score.composite_rank:.3f}; מתחת לטופ-25."
    )
    return en, he


# --------------------------------------------------------------------------
# Hybrid agents (Neff, Buffett, Lynch, Marks, Klarman, Fisher)
# --------------------------------------------------------------------------
def neff_why(score: Any) -> tuple[str, str]:
    """``NeffScore`` → bilingual rationale.

    Soft-scoring version: lead with the composite total_score (max 70)
    that drives ranking, with TR/PE as a supporting metric and the
    signature inputs (P/E, growth, yield) for context.
    """
    total = getattr(score, "total_score", None)
    score_str = f"{total:.0f}/70" if total is not None else "—"
    en = (
        f"Neff score {score_str}: P/E {fmt_ratio(score.pe)}, "
        f"EPS growth {score.eps_growth_pct:.1f}%, "
        f"yield {score.dividend_yield_pct:.1f}%, "
        f"TR/PE {score.total_return_pe:.2f}."
    )
    he = (
        f"ניקוד נף {score_str}: P/E {fmt_ratio(score.pe)}, "
        f"צמיחת EPS {score.eps_growth_pct:.1f}%, "
        f"תשואה {score.dividend_yield_pct:.1f}%, "
        f"TR/PE {score.total_return_pe:.2f}."
    )
    return en, he


def neff_watch_why(score: Any) -> tuple[str, str]:
    total = getattr(score, "total_score", None)
    score_str = f"{total:.0f}/70" if total is not None else "—"
    en = (
        f"Neff score {score_str}; just below the cohort cut. "
        f"Watching for ranking to improve."
    )
    he = (
        f"ניקוד נף {score_str}; מעט מתחת לקבוצת הקנייה. "
        f"מעקב לשיפור בדירוג."
    )
    return en, he


def buffett_why(score: Any) -> tuple[str, str]:
    """``BuffettScore`` → bilingual rationale."""
    en = (
        f"MoS {score.margin_of_safety_pct:.1f}% (intrinsic "
        f"${fmt_ratio(score.intrinsic_value_per_share)}). "
        f"5y ROE {score.avg_roe_5yr_pct:.1f}%, D/E "
        f"{fmt_ratio(score.debt_to_equity)}."
    )
    he = (
        f"מרווח ביטחון {score.margin_of_safety_pct:.1f}% (שווי פנימי "
        f"${fmt_ratio(score.intrinsic_value_per_share)}). "
        f"ROE 5 שנתי {score.avg_roe_5yr_pct:.1f}%, D/E "
        f"{fmt_ratio(score.debt_to_equity)}."
    )
    return en, he


def buffett_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"Quality passes (5y ROE {score.avg_roe_5yr_pct:.1f}%, D/E "
        f"{fmt_ratio(score.debt_to_equity)}); waiting for price to fall "
        f"into 15%+ MoS zone."
    )
    he = (
        f"איכות עוברת (ROE 5 שנתי {score.avg_roe_5yr_pct:.1f}%, D/E "
        f"{fmt_ratio(score.debt_to_equity)}); ממתין לירידת מחיר לאזור "
        f"מרווח ביטחון 15%+."
    )
    return en, he


def lynch_why(score: Any) -> tuple[str, str]:
    """``LynchScore`` → bilingual rationale."""
    en = (
        f"{score.lynch_category}: PEG {score.peg:.2f}, 5y EPS CAGR "
        f"{score.growth_rate_5yr_pct:.1f}%, P/E "
        f"{fmt_ratio(score.pe)}."
    )
    he = (
        f"{score.lynch_category}: PEG {score.peg:.2f}, צמיחת EPS 5 שנתית "
        f"{score.growth_rate_5yr_pct:.1f}%, P/E "
        f"{fmt_ratio(score.pe)}."
    )
    return en, he


def lynch_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"{score.lynch_category}: PEG {score.peg:.2f}; just outside the buy "
        f"zone for this category."
    )
    he = (
        f"{score.lynch_category}: PEG {score.peg:.2f}; מחוץ לאזור הקנייה "
        f"של הקטגוריה."
    )
    return en, he


def marks_why(score: Any) -> tuple[str, str]:
    """``MarksScore`` → bilingual rationale (cycle-aware)."""
    en = (
        f"Posture {score.posture_at_score}: earnings yield "
        f"{score.earnings_yield_pct:.1f}%, FCF yield "
        f"{score.fcf_yield_pct:.1f}%, D/E "
        f"{fmt_ratio(score.debt_to_equity)}. "
        f"Cycle-adjusted score {score.total_score:.1f}."
    )
    he = (
        f"מיצוב {score.posture_at_score}: תשואת רווח "
        f"{score.earnings_yield_pct:.1f}%, תשואת FCF "
        f"{score.fcf_yield_pct:.1f}%, D/E "
        f"{fmt_ratio(score.debt_to_equity)}. "
        f"ניקוד מותאם מחזור {score.total_score:.1f}."
    )
    return en, he


def marks_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"Score {score.total_score:.1f}; below the {score.posture_at_score} "
        f"posture's deployment threshold."
    )
    he = (
        f"ניקוד {score.total_score:.1f}; מתחת לסף הפריסה של מיצוב "
        f"{score.posture_at_score}."
    )
    return en, he


def klarman_why(score: Any) -> tuple[str, str]:
    """``KlarmanScore`` → bilingual rationale."""
    en = (
        f"MoS {score.margin_of_safety_pct:.1f}% to conservative DCF "
        f"(intrinsic ${fmt_ratio(score.intrinsic_value_per_share)}). "
        f"5y avg FCF ${score.avg_fcf_usd / 1e6:.0f}M, D/E "
        f"{fmt_ratio(score.debt_to_equity)}."
    )
    he = (
        f"מרווח ביטחון {score.margin_of_safety_pct:.1f}% מ-DCF שמרני "
        f"(שווי פנימי ${fmt_ratio(score.intrinsic_value_per_share)}). "
        f"FCF ממוצע 5 שנתי ${score.avg_fcf_usd / 1e6:.0f}מ׳, D/E "
        f"{fmt_ratio(score.debt_to_equity)}."
    )
    return en, he


def klarman_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"Conservative DCF MoS {score.margin_of_safety_pct:.1f}%; just "
        f"below 30% floor — watching for further price decline."
    )
    he = (
        f"מרווח ביטחון לפי DCF שמרני {score.margin_of_safety_pct:.1f}%; "
        f"מתחת לסף 30% — מעקב לירידת מחיר נוספת."
    )
    return en, he


def fisher_why(score: Any) -> tuple[str, str]:
    """``FisherScore`` → bilingual rationale (tier + 5-pt quant)."""
    qs = score.quality_score
    margin = qs.operating_margin_pct
    margin_str = f"{margin:.1f}%" if margin is not None else "—"
    rev_g = qs.revenue_cagr_5yr_pct
    rev_g_str = f"{rev_g:.1f}%" if rev_g is not None else "—"
    en = (
        f"Tier {score.tier} ({score.quality_points}/5 points): "
        f"P/E {fmt_ratio(score.pe)}, op margin {margin_str}, "
        f"5y revenue CAGR {rev_g_str}."
    )
    he = (
        f"קבוצה {score.tier} ({score.quality_points}/5 נקודות): "
        f"P/E {fmt_ratio(score.pe)}, מרווח תפעולי {margin_str}, "
        f"צמיחת הכנסות 5 שנתית {rev_g_str}."
    )
    return en, he


def fisher_watch_why(score: Any) -> tuple[str, str]:
    en = (
        f"Tier {score.tier} ({score.quality_points}/5); below the "
        f"current portfolio cut by P/E ranking."
    )
    he = (
        f"קבוצה {score.tier} ({score.quality_points}/5); מתחת לחתך "
        f"התיק הנוכחי בדירוג P/E."
    )
    return en, he


__all__ = [
    "buffett_watch_why",
    "buffett_why",
    "dreman_watch_why",
    "dreman_why",
    "fisher_watch_why",
    "fisher_why",
    "fmt_pct",
    "fmt_ratio",
    "graham_defensive_why",
    "graham_net_net_why",
    "graham_watch_why",
    "greenblatt_watch_why",
    "greenblatt_why",
    "klarman_watch_why",
    "klarman_why",
    "lynch_watch_why",
    "lynch_why",
    "marks_watch_why",
    "marks_why",
    "neff_watch_why",
    "neff_why",
    "schloss_watch_why",
    "schloss_why",
]
