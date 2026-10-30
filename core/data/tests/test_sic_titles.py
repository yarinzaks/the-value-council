"""Industry titles: the SEC's description of what a company does.

The bundle is reference data, so most of these are assertions about the
bundle itself rather than about logic. That is deliberate — the failure
mode worth catching is a stale or truncated file, which no amount of
testing the lookup would reveal.
"""

from __future__ import annotations

import json

import pytest

from core.data.sic_codes import (
    TITLES_PATH,
    _titlecase,
    industry_title,
    load_sic_map,
    load_sic_titles,
    sic_for,
    sic_title,
)

# ------------------------------------------------------------- the bundle


def test_the_bundle_exists_and_is_populated():
    titles = load_sic_titles()
    # 444 at the time of writing. A floor rather than an equality: the
    # SEC may add a code, and that should not fail the suite.
    assert len(titles) > 400, f"only {len(titles)} titles — bundle looks truncated"


def test_every_code_in_use_has_a_title():
    """The property that makes the label worth showing at all.

    Checked when the bundle was built: all 397 distinct codes across the
    cached tickers were present. If this fails, a company will show no
    industry and the cause is a stale bundle, not an unusual company.
    """
    titles = load_sic_titles()
    used = {str(v) for v in load_sic_map().values() if v is not None}
    missing = sorted(used - set(titles))
    assert not missing, f"{len(missing)} codes in use have no title: {missing[:10]}"


def test_the_bundle_is_sorted_and_stringly_keyed():
    # Sorted so a refresh produces a reviewable diff rather than a
    # reshuffle; string keys because JSON has no integer keys and a
    # silent int/str mismatch would make every lookup miss.
    raw = json.loads(TITLES_PATH.read_text())
    assert list(raw) == sorted(raw)
    assert all(isinstance(k, str) for k in raw)


# -------------------------------------------------------------- the lookup


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        (3751, "MOTORCYCLES"),
        (2011, "MEAT PACKING"),
        (2040, "GRAIN MILL"),
        (6021, "COMMERCIAL BANKS"),
    ],
)
def test_sic_title_returns_the_sec_wording_verbatim(code, fragment):
    title = sic_title(code)
    assert title is not None
    assert fragment in title
    assert title == title.upper(), "the verbatim form should not be case-folded"


def test_sic_title_accepts_an_int_or_a_string():
    assert sic_title(3751) == sic_title("3751")


def test_sic_title_is_none_for_an_unknown_or_missing_code():
    assert sic_title(None) is None
    assert sic_title(9999) is None


def test_industry_title_describes_a_real_company():
    # The case that motivated this: a sector bucket calls Harley-Davidson
    # "manufacturing", which tells a reader nothing.
    assert industry_title("HOG") == "Motorcycles, Bicycles & Parts"


def test_industry_title_separates_companies_the_sector_bucket_merges():
    hog = industry_title("HOG")
    ingr = industry_title("INGR")
    assert hog and ingr
    assert hog != ingr


def test_industry_title_is_none_for_an_unknown_ticker():
    assert industry_title("NOTATICKER") is None


def test_industry_title_is_case_insensitive_on_the_ticker():
    assert industry_title("hog") == industry_title("HOG")


def test_every_graham_holding_has_a_label():
    # The notifier names the industry on every message; a holding without
    # one would print a gap.
    for ticker in ("SEB", "UVV", "SFD", "HOG", "INGR", "CRI", "UFPI"):
        assert industry_title(ticker), f"{ticker} has no industry label"
        assert sic_for(ticker) is not None


# ------------------------------------------------------------- the casing


def test_an_apostrophe_does_not_start_a_new_word():
    # str.title() gives "Women'S Clothing Stores".
    assert _titlecase("WOMEN'S CLOTHING STORES") == "Women's Clothing Stores"


def test_a_hyphen_does_start_a_new_word():
    # Two words; "Wholesale-farm" reads as a typo.
    assert _titlecase("WHOLESALE-FARM PRODUCT RAW MATERIALS") == (
        "Wholesale-Farm Product Raw Materials"
    )


def test_an_ampersand_separated_word_is_capitalised():
    assert _titlecase("OIL & GAS FIELD SERVICES") == "Oil & Gas Field Services"


def test_casing_preserves_punctuation_and_length():
    for raw in load_sic_titles().values():
        cased = _titlecase(raw)
        assert len(cased) == len(raw)
        assert cased.upper() == raw.upper()
