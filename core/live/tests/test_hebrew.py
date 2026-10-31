"""What the Hebrew layers must and must not translate."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.live.hebrew_industry import BY_CODE, BY_GROUP, industry_he
from core.live.hebrew_terms import TERMS, translate

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- criteria
class TestTheCriteriaReadInHebrew:
    @pytest.mark.parametrize(
        ("english", "hebrew"),
        [
            ("positive trailing net income", "רווח נקי חיובי בשנה החולפת"),
            ("current ratio=2.10 (≥ 2.0)", "יחס שוטף=2.10 (≥ 2.0)"),
            ("Fast Grower", "צומחת מהר"),
            ("quality 5/5", "איכות 5/5"),
            ("left today's target list", "לא נמצאת ברשימת היעד של היום"),
            ("yield=12.0% (top quintile)", "תשואה=12.0% (בחמישון העליון)"),
            ("5yr avg FCF $217,127,200", "FCF ממוצע ל-5 שנים: $217,127,200"),
            ("5-yr avg ROE 15.9%", "ROE ממוצע ל-5 שנים: 15.9%"),
            ("OE 5yr avg $1,200,000", "רווחי בעלים ממוצע ל-5 שנים: $1,200,000"),
            ("3yr EPS CAGR 12.4%", "צמיחת EPS שנתית ל-3 שנים: 12.4%"),
        ],
    )
    def test_a_known_criterion(self, english: str, hebrew: str) -> None:
        assert translate(english) == hebrew

    @pytest.mark.parametrize(
        "ratio",
        ["P/E=11.23 (≤ 15.0)", "P/B=1.11 (≤ 1.5)", "D/E=0.94 (≤ 1.0)",
         "ROE=18.2%", "PEG 0.84", "PEGY 0.61", "TR/PE=1.9", "P/NCAV=0.61 (≤ 1.0)"],
    )
    def test_a_ratio_abbreviation_is_left_alone(self, ratio: str) -> None:
        """What a Hebrew-speaking investor calls them. "מכפיל הון" would
        be translating past the reader rather than for them."""
        assert translate(ratio) == ratio

    def test_an_unknown_phrase_passes_through(self) -> None:
        assert translate("some test nobody wrote yet") == "some test nobody wrote yet"

    def test_empty_input_is_not_decorated(self) -> None:
        assert translate("") == ""

    def test_no_number_is_altered(self) -> None:
        """The table replaces words around figures, never figures."""
        for english in (
            "current ratio=2.10 (≥ 2.0)",
            "market cap $1,234,567,890",
            "5yr avg FCF $217,127,200",
            "yield=12.0%",
        ):
            before = re.findall(r"[\d,.]+", english)
            after = re.findall(r"[\d,.]+", translate(english))
            assert before == after, english

    def test_a_longer_phrase_wins_over_a_prefix_of_it(self) -> None:
        """"dividend yield" must not be matched first by "yield "."""
        assert translate("dividend yield 3.2%") == "תשואת דיבידנד 3.2%"
        assert translate("earnings yield 8.1%") == "תשואת רווח 8.1%"
        assert translate("FCF yield 6.0%") == "תשואת FCF 6.0%"

    def test_every_entry_actually_changes_its_input(self) -> None:
        """An entry whose Hebrew equals its English is dead weight and
        hides a missing translation."""
        for english, hebrew in TERMS.items():
            assert english != hebrew, english
            assert hebrew.strip(), english

    def test_the_live_journal_is_covered(self) -> None:
        """Walks the real decision journal. Every criterion it holds
        must come back with no English words left in it beyond the
        ratio abbreviations — which is how a template added by a future
        strategy shows up as a failure here rather than in a message.
        """
        journal = PROJECT_ROOT / "data" / "decisions"
        if not journal.exists():
            pytest.skip("no decision journal in this checkout")
        keep = re.compile(
            r"P/E|P/B|P/CF|P/NCAV|D/E|ROE|ROC|EPS|FCF|PEGY|PEG|TR/PE|MoS|OE|EY|nan"
        )
        untranslated: set[str] = set()
        seen = 0
        for path in sorted(journal.glob("*/*.json"))[-200:]:
            for row in json.loads(path.read_text()):
                for crit in (row.get("criteria_met") or []):
                    seen += 1
                    rest = keep.sub("", translate(str(crit)))
                    if re.search(r"[A-Za-z]{3,}", rest):
                        untranslated.add(str(crit))
        assert seen > 0, "the journal produced no criteria to check"
        assert not untranslated, f"still English: {sorted(untranslated)[:5]}"


# ---------------------------------------------------------------- industry
class TestTheIndustryReadsInHebrew:
    @pytest.mark.parametrize(
        ("code", "hebrew"),
        [
            ("2834", "תרופות"),
            ("7372", "תוכנה"),
            ("3751", "אופנועים ואופניים"),
            ("6798", 'קרן השקעות בנדל"ן (REIT)'),
            ("6022", "בנקאות מסחרית"),
            ("3674", "מוליכים למחצה"),
        ],
    )
    def test_a_four_digit_code_overrides_its_group(
        self, code: str, hebrew: str
    ) -> None:
        assert industry_he(code, "whatever the SEC called it") == hebrew

    @pytest.mark.parametrize(
        ("code", "hebrew"),
        [("2011", "עיבוד בשר ועוף"), ("2621", "נייר ומוצריו"), ("4512", "תעופה")],
    )
    def test_a_code_with_no_override_falls_to_its_group(
        self, code: str, hebrew: str
    ) -> None:
        assert industry_he(code, "ignored") == hebrew

    def test_an_unknown_group_falls_back_to_english(self) -> None:
        """A code a future bundle adds arrives readable, not blank."""
        assert industry_he("9999", "Something New") == "לא מסווג"
        assert industry_he("1199", "Something New") == "Something New"

    def test_no_code_and_no_title_leaves_the_line_out(self) -> None:
        assert industry_he(None, None) is None
        assert industry_he("", None) is None

    def test_a_short_code_is_padded_not_misread(self) -> None:
        """SIC 100 is agricultural crops — group 01, not group 10."""
        assert industry_he(100, "Agricultural Production-Crops") == "גידולים חקלאיים"
        assert industry_he(1000, "Metal Mining") == "כריית מתכות"

    def test_an_integer_code_works_like_its_string(self) -> None:
        assert industry_he(2834, None) == industry_he("2834", None)

    def test_every_ticker_in_the_bundle_gets_hebrew(self) -> None:
        """The claim the two-level mapping exists to make. A ticker with
        a SIC code must never fall through to the SEC's English."""
        bundle = PROJECT_ROOT / "data_bundled" / "company_sic.json"
        titles = PROJECT_ROOT / "data_bundled" / "sic_titles.json"
        if not bundle.exists():
            pytest.skip("no SIC bundle in this checkout")
        sic = json.loads(bundle.read_text())
        english = json.loads(titles.read_text()) if titles.exists() else {}
        fell_through: set[str] = set()
        for code in sic.values():
            if code is None or not str(code).strip().isdigit():
                continue  # no code at all; the line is simply omitted
            title = english.get(str(code))
            if industry_he(code, title) == title:
                fell_through.add(str(code))
        assert not fell_through, f"no Hebrew for SIC {sorted(fell_through)[:8]}"

    def test_every_entry_is_hebrew_and_distinct_from_the_english(self) -> None:
        for table in (BY_CODE, BY_GROUP):
            for code, hebrew in table.items():
                assert hebrew.strip(), code
                assert any("֐" <= c <= "׿" for c in hebrew), code


# ------------------------------------------------ the rationale builders
class TestEveryRationaleBuilderFinishesItsHebrew:
    """The Hebrew half of a ``*_why`` goes through the phrase table.

    Several builders compose a Hebrew sentence and interpolate an
    English label into it — Lynch's category, Marks' posture, Pabrai's
    floor reason. 71 positions across the thirteen live books carried
    one, so every page and every notification showing the Hebrew showed
    that word in English.
    """

    def test_every_exported_builder_is_wrapped(self) -> None:
        """The wrapping is applied in a loop over ``__all__``, which is
        what makes a builder added later safe by default — and what this
        test exists to keep true."""
        from core.live import why_translator

        builders = [n for n in why_translator.__all__ if n.endswith("_why")]
        assert len(builders) >= 20, "the module lost its builders"
        unwrapped = [
            n for n in builders if not hasattr(getattr(why_translator, n), "__wrapped__")
        ]
        assert unwrapped == [], f"not passed through the table: {unwrapped}"

    def test_lynch_category_arrives_in_hebrew(self) -> None:
        from core.live.why_translator import lynch_why

        class Score:
            lynch_category = "Fast Grower"
            peg = 0.07
            growth_rate_5yr_pct = 247.4
            pe = 17.93

        english, hebrew = lynch_why(Score())
        assert "צומחת מהר" in hebrew
        assert "Fast Grower" not in hebrew
        # The English half is the audit string and the English pages
        # render it as written.
        assert "Fast Grower" in english

    def test_no_live_book_carries_english_in_its_hebrew_reason(self) -> None:
        """Walks the real portfolios. The scan that found this found 71
        positions across nine distinct English fragments."""
        import json
        import re
        from pathlib import Path as _Path

        books = _Path(__file__).resolve().parents[3] / "data" / "portfolios"
        if not books.exists():
            pytest.skip("no portfolios in this checkout")
        # Latin that stays Latin: the ratio abbreviations, and the
        # composite forms the agents quote.
        keep = re.compile(
            r"P/E|P/B|P/CF|P/NCAV|D/E|ROE|ROC|EPS|FCF|PEGY|PEG|TR/PE|MoS|OE|EY"
            r"|NCAV|CAGR|DCF|EV/EBIT|EBIT|REIT|SPAC|nan"
        )
        leaks: list[str] = []
        for path in sorted(books.glob("*.json")):
            book = json.loads(path.read_text())
            for pos in book.get("positions", []):
                rendered = translate(str(pos.get("why_he") or ""))
                for found in re.findall(
                    r"[A-Za-z][A-Za-z\- ]{3,}", keep.sub("", rendered)
                ):
                    if found.strip():
                        leaks.append(f"{book['agent']}/{pos['ticker']}: {found.strip()!r}")
        assert leaks == [], f"English inside why_he: {leaks[:6]}"
