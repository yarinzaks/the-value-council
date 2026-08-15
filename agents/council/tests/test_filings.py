"""Gate D must never read an unreadable filer as a clean one.

Every fetch is injected, so nothing here touches the network. The
property that matters most is the failure direction: a phrase index that
could not be built raises rather than returning an empty set, because an
empty set means "no company has going-concern doubt", which would turn
this gate into decoration.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agents.council.filings import (
    LATE_FILING_LOOKBACK_DAYS,
    RESTATEMENT_LOOKBACK_DAYS,
    FullTextUnavailableError,
    OpinionIndex,
    ciks_using_phrase,
    gate_d_flags,
)
from agents.council.screen import Outcome, gate_d

AS_OF = date(2026, 8, 14)


def page(ciks: list[str], total: int) -> dict:
    return {
        "hits": {
            "total": {"value": total},
            "hits": [{"_source": {"ciks": [c]}} for c in ciks],
        }
    }


class TestFullTextIndex:
    def test_it_collects_ciks(self) -> None:
        def fetch(phrase, forms, start, end, offset):
            return page(["0000320193", "0000789019"], total=2) if offset == 0 else page([], 2)

        found = ciks_using_phrase("x", start=date(2025, 1, 1), end=AS_OF, fetch=fetch)
        assert found == {"0000320193", "0000789019"}

    def test_it_pages_until_the_total_is_reached(self) -> None:
        calls: list[int] = []

        def fetch(phrase, forms, start, end, offset):
            calls.append(offset)
            if offset == 0:
                return page(["0000000001", "0000000002"], total=4)
            if offset == 2:
                return page(["0000000003", "0000000004"], total=4)
            return page([], total=4)

        found = ciks_using_phrase("x", start=date(2025, 1, 1), end=AS_OF, fetch=fetch)
        assert len(found) == 4
        assert calls == [0, 2]

    def test_ciks_are_zero_padded_the_way_edgar_spells_them(self) -> None:
        def fetch(*a):
            return page(["320193"], total=1)

        found = ciks_using_phrase("x", start=date(2025, 1, 1), end=AS_OF, fetch=fetch)
        assert found == {"0000320193"}

    def test_a_failed_fetch_raises_rather_than_returning_nothing(self) -> None:
        """An empty set would read as 'no company has this problem'."""

        def fetch(*a):
            raise OSError("network down")

        with pytest.raises(FullTextUnavailableError):
            ciks_using_phrase("x", start=date(2025, 1, 1), end=AS_OF, fetch=fetch)

    def test_a_phrase_matching_everything_raises(self) -> None:
        def fetch(*a):
            return page(["0000000001"], total=999_999)

        with pytest.raises(FullTextUnavailableError, match="too common"):
            ciks_using_phrase("x", start=date(2025, 1, 1), end=AS_OF, fetch=fetch)

    def test_no_matches_is_an_empty_set_not_an_error(self) -> None:
        def fetch(*a):
            return page([], total=0)

        assert ciks_using_phrase("x", start=date(2025, 1, 1), end=AS_OF, fetch=fetch) == set()


class TestOpinionIndex:
    def test_both_going_concern_phrasings_are_collected(self) -> None:
        seen: list[str] = []

        def fetch(phrase, forms, start, end, offset):
            seen.append(phrase)
            if offset:
                return page([], total=1)
            if "its ability" in phrase:
                return page(["0000000001"], total=1)
            if "our ability" in phrase:
                return page(["0000000002"], total=1)
            return page(["0000000003"], total=1)

        index = OpinionIndex.build(AS_OF, fetch=fetch)
        assert index.going_concern == {"0000000001", "0000000002"}
        assert index.material_weakness == {"0000000003"}
        assert len(seen) >= 3


def submissions(forms: list[tuple[str, date, str]]) -> dict:
    return {
        "filings": {
            "recent": {
                "form": [f for f, _, _ in forms],
                "filingDate": [d.isoformat() for _, d, _ in forms],
                "items": [i for _, _, i in forms],
            }
        }
    }


EMPTY_INDEX = OpinionIndex(going_concern=set(), material_weakness=set())


class TestGateDFlags:
    @staticmethod
    def _resolve(ticker: str) -> int | None:
        return {"GOOD": 1, "BAD": 2, "NOCIK": None}.get(ticker, 3)

    def test_a_clean_filer_passes_the_gate(self) -> None:
        flags = gate_d_flags(
            ["GOOD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions([("10-K", AS_OF - timedelta(days=60), "")]),
            resolve_cik=self._resolve,
        )
        assert gate_d(flags["GOOD"]).outcome is Outcome.PASS

    def test_a_restatement_inside_two_years_is_flagged(self) -> None:
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions(
                [("8-K", AS_OF - timedelta(days=400), "4.02,2.02")]
            ),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].restatement_8k_402 is True
        assert gate_d(flags["BAD"]).outcome is Outcome.FAIL

    def test_a_restatement_older_than_two_years_is_not(self) -> None:
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions(
                [
                    (
                        "8-K",
                        AS_OF - timedelta(days=RESTATEMENT_LOOKBACK_DAYS + 30),
                        "4.02",
                    )
                ]
            ),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].restatement_8k_402 is False

    def test_another_8k_item_does_not_flag(self) -> None:
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions([("8-K", AS_OF - timedelta(days=5), "2.02")]),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].restatement_8k_402 is False

    def test_a_late_filing_inside_a_year_is_flagged(self) -> None:
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions(
                [("NT 10-K", AS_OF - timedelta(days=100), "")]
            ),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].late_filing is True

    def test_a_late_filing_older_than_a_year_is_not(self) -> None:
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions(
                [
                    (
                        "NT 10-Q",
                        AS_OF - timedelta(days=LATE_FILING_LOOKBACK_DAYS + 30),
                        "",
                    )
                ]
            ),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].late_filing is False

    def test_nothing_filed_after_as_of_is_read(self) -> None:
        """Point-in-time: tomorrow's restatement is not knowable today."""
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: submissions(
                [("8-K", AS_OF + timedelta(days=1), "4.02")]
            ),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].restatement_8k_402 is False

    def test_the_opinion_index_supplies_going_concern(self) -> None:
        index = OpinionIndex(going_concern={"0000000002"}, material_weakness=set())
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=index,
            fetch=lambda cik: submissions([("10-K", AS_OF, "")]),
            resolve_cik=self._resolve,
        )
        assert flags["BAD"].going_concern is True
        assert gate_d(flags["BAD"]).outcome is Outcome.FAIL

    def test_a_material_weakness_disqualifies(self) -> None:
        index = OpinionIndex(going_concern=set(), material_weakness={"0000000002"})
        flags = gate_d_flags(
            ["BAD"],
            AS_OF,
            opinions=index,
            fetch=lambda cik: submissions([("10-K", AS_OF, "")]),
            resolve_cik=self._resolve,
        )
        assert gate_d(flags["BAD"]).outcome is Outcome.FAIL

    def test_an_unresolvable_ticker_fails_the_gate(self) -> None:
        flags = gate_d_flags(
            ["NOCIK"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: None,
            resolve_cik=self._resolve,
        )
        assert gate_d(flags["NOCIK"]).outcome is Outcome.UNKNOWN

    def test_unreadable_submissions_leave_the_gate_unknown(self) -> None:
        """The audit phrases are known; the item codes are not."""
        flags = gate_d_flags(
            ["GOOD"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=lambda cik: None,
            resolve_cik=self._resolve,
        )
        assert flags["GOOD"].going_concern is False
        assert flags["GOOD"].restatement_8k_402 is None
        assert gate_d(flags["GOOD"]).outcome is Outcome.UNKNOWN

    def test_it_runs_one_fetch_per_candidate(self) -> None:
        """The cost is set by candidates, not by universe size."""
        calls: list[int] = []

        def fetch(cik: int):
            calls.append(cik)
            return submissions([("10-K", AS_OF, "")])

        gate_d_flags(
            ["A", "B", "C"],
            AS_OF,
            opinions=EMPTY_INDEX,
            fetch=fetch,
            resolve_cik=self._resolve,
        )
        assert len(calls) == 3

    def test_an_empty_candidate_list_costs_nothing(self) -> None:
        assert gate_d_flags([], AS_OF, opinions=EMPTY_INDEX) == {}
