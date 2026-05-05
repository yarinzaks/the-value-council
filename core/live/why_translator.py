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


__all__ = [
    "dreman_watch_why",
    "dreman_why",
    "fmt_pct",
    "fmt_ratio",
    "graham_defensive_why",
    "graham_net_net_why",
    "graham_watch_why",
    "greenblatt_watch_why",
    "greenblatt_why",
    "schloss_watch_why",
    "schloss_why",
]
