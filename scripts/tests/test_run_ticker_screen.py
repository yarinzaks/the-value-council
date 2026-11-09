"""One ticker through Graham's screen, and said in the reader's language.

Asked "what do the agents say about XMTR", the assistant could only
answer that none of them had said anything — true, and not the
question. This is the other question: what would Graham say if he
looked. It is answerable for nothing, because his doctrine is a set of
absolute thresholds rather than a rank.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agents.graham.playbook_audit import AuditResult
from core.live.rtl import strip_marks
from scripts.run_ticker_screen import (
    RELATIVE_AGENTS,
    TickerScreenError,
    _criterion,
    local_price,
    render,
    screen,
)

#: XMTR as the real screen scored it on 2026-11-09, at $109.73.
XMTR = AuditResult(
    ticker="XMTR",
    price=109.73,
    industry="Industrials/Metal Fabrications",
    status="REJECT_ANTI_PATTERN",
    metrics={"pb": 18.364, "eps_3y_avg": -1.298, "dividend_streak_years": 0},
    defensive_failures=(
        "not 10 years of positive earnings",
        "dividend streak 0y",
        "P/E (3y avg) > 15",
        "P/B > 1.5 and P/E x P/B > 22.5",
    ),
    enterprising_failures=("loss in the last 5 years", "no current dividend"),
    net_net_failures=("price > 2/3 NCAV", "TTM EPS <= 0"),
    anti_patterns=("#2 never paid a dividend", "#6 intangibles > 50% of equity"),
)


class TestTheCriteriaReachHebrew:
    """The failure strings are the playbook's, and templated.

    Every one of them goes through the shared phrase table, which is
    why a Hebrew verdict does not end up listing its reasons in
    English — the defect this codebase has been chasing across the
    dashboard, the notifier and the chat context.
    """

    EVERY_STRING = (
        *XMTR.defensive_failures,
        *XMTR.enterprising_failures,
        *XMTR.net_net_failures,
        *XMTR.anti_patterns,
        "earnings below 4 years ago",
        "price >= 1.2x NTA and P/E >= 10",
        "10y earnings growth n/a < 33%",
        "current ratio < 2",
        "current ratio < 1.5",
        "long-term debt > working capital",
        "debt > 110% of net current assets",
        "no annual net income",
        "no current assets (unclassified balance sheet)",
        "#1 no 10 years of positive earnings",
        "#3 market cap < $50M",
        "#8 debt exceeds book value",
        "#10 excluded sector",
    )

    @pytest.mark.parametrize("text", EVERY_STRING)
    def test_each_one_is_rendered_in_hebrew(self, text: str) -> None:
        out = strip_marks(_criterion(text, he=True))
        assert out != text, f"{text!r} has no Hebrew"
        # No English word survives. The ratio abbreviations are Latin by
        # convention and stay, so the check is on words, not characters.
        import re

        leftover = re.sub(
            r"\b(P/E|P/B|P/NCAV|NCAV|NTA|EPS|TTM|XBRL|D/E|ROE|n/a|x)\b", "", out
        )
        assert not re.search(r"[A-Za-z]{3,}", leftover), out

    def test_the_english_reader_gets_the_english(self) -> None:
        assert _criterion("no current dividend", he=False) == "no current dividend"

    def test_a_string_the_table_cannot_render_keeps_its_english(self) -> None:
        """A criterion added to the playbook and not yet to the table.

        Half a sentence in each language is worse than one whole
        sentence in the wrong one.
        """
        novel = "some criterion nobody has translated yet"
        assert _criterion(novel, he=True) == novel

    def test_the_numbers_are_isolated_for_a_right_to_left_line(self) -> None:
        from core.live.rtl import LRI

        assert LRI in _criterion("dividend streak 0y", he=True)
        assert "0" in strip_marks(_criterion("dividend streak 0y", he=True))


class TestTheVerdictReads:
    def test_it_names_the_status_the_ticker_and_the_price(self) -> None:
        out = strip_marks(render(XMTR, "2026-11-09", locale="he"))
        assert "XMTR" in out
        assert "נפסלת על אנטי-דפוס" in out
        assert "109.73" in out
        assert "2026-11-09" in out

    def test_it_lists_what_failed_under_each_category(self) -> None:
        out = strip_marks(render(XMTR, "live", locale="he"))
        assert "המשקיע ההגנתי" in out
        assert "המשקיע היזם" in out
        assert "נט-נט" in out
        assert "אין דיבידנד שוטף" in out

    def test_a_passing_name_says_what_it_passed(self) -> None:
        passing = replace(
            XMTR,
            status="BUY",
            defensive_failures=(),
            enterprising_failures=(),
            net_net_failures=(),
            anti_patterns=(),
            qualifying=("Defensive",),
        )
        out = strip_marks(render(passing, "live", locale="he"))
        assert "עוברת את הסינון" in out
        assert "המשקיע ההגנתי" in out
        assert "נכשלת" not in out

    def test_it_always_says_which_agents_it_did_not_ask(self) -> None:
        """The honest half.

        Greenblatt ranks earnings yield against the other 3,594 names,
        Dreman wants the bottom fifth of the market's P/E, Neff
        compares against the market's average. A rank cannot be
        computed from one company, and a verdict invented for them
        would be the same mistake as telling a reader a position has a
        sell target when its row names none.
        """
        for index, locale in ((0, "en"), (1, "he")):
            out = strip_marks(render(XMTR, "live", locale=locale))
            for key, names in RELATIVE_AGENTS.items():
                assert names[index] in out, f"{key} is not disclaimed in {locale}"

    def test_the_english_verdict_is_english(self) -> None:
        out = render(XMTR, "live", locale="en")
        assert "through Benjamin Graham's screen" in out
        assert "fails Defensive:" in out
        assert "no current dividend" in out


class TestTheInputs:
    def test_a_ticker_with_no_filings_is_refused_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Empty:
            def tickers(self):
                return []

        monkeypatch.setattr("scripts.run_ticker_screen.EdgarCache", lambda: Empty())
        with pytest.raises(TickerScreenError, match="EDGAR cache"):
            screen("NOSUCH", session=object())  # type: ignore[arg-type]

    def test_an_empty_ticker_is_refused(self) -> None:
        with pytest.raises(TickerScreenError, match="no ticker"):
            screen("   ", session=object())  # type: ignore[arg-type]

    def test_the_local_price_store_is_read_before_the_network(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "prices").mkdir()
        (tmp_path / "prices" / "HOG.json").write_text(
            json.dumps({"points": [{"d": "2026-11-01", "c": 30.0}, {"d": "2026-11-04", "c": 34.36}]})
        )
        monkeypatch.setattr("scripts.run_ticker_screen.data_root", lambda: tmp_path)
        assert local_price("HOG") == (34.36, "2026-11-04")

    def test_a_missing_or_broken_price_file_is_not_a_crash(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "prices").mkdir()
        (tmp_path / "prices" / "BAD.json").write_text("{not json")
        (tmp_path / "prices" / "EMPTY.json").write_text(json.dumps({"points": []}))
        monkeypatch.setattr("scripts.run_ticker_screen.data_root", lambda: tmp_path)
        assert local_price("GONE") is None
        assert local_price("BAD") is None
        assert local_price("EMPTY") is None


def test_every_status_the_playbook_can_return_has_hebrew() -> None:
    """The drift guard.

    A status added to the audit and not here renders as the raw enum —
    "REJECT_NO_CATEGORY" on a Hebrew page, which is how the first
    version of this file shipped.
    """
    import typing

    from agents.graham.playbook_audit import Status
    from scripts.run_ticker_screen import _STATUS_HE

    assert set(typing.get_args(Status)) == set(_STATUS_HE)
