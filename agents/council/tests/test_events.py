"""The filing watch — the mechanism THR and ASGN needed and did not have."""

from __future__ import annotations

from datetime import date

from agents.council.events import (
    ITEM_MEANINGS,
    TERMINAL_FORMS,
    Severity,
    _events_from_submissions,
)

SINCE = date(2026, 6, 1)


def _submissions(*rows: tuple[str, str, str]) -> dict:
    """rows are (form, filingDate, items)."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "items": [r[2] for r in rows],
                "accessionNumber": [f"acc-{i}" for i in range(len(rows))],
            }
        }
    }


class TestTheCaseThatStartedThis:
    def test_a_completed_acquisition_is_critical(self) -> None:
        """Thermon closed on 2026-06-01 and the book carried it at a dead
        price for seventy days. Item 2.01 is that announcement."""
        doc = _submissions(("8-K", "2026-06-01", "2.01,9.01"))
        events = _events_from_submissions("THR", doc, since=SINCE)
        assert [e.code for e in events] == ["2.01"]
        assert events[0].severity is Severity.CRITICAL

    def test_a_delisting_form_is_critical(self) -> None:
        """Form 25 is the exchange striking the security. After it, the
        price series simply stops."""
        doc = _submissions(("25-NSE", "2026-06-12", ""))
        events = _events_from_submissions("THR", doc, since=SINCE)
        assert events[0].severity is Severity.CRITICAL
        assert "delist" in events[0].meaning


class TestSeverity:
    def test_non_reliance_is_the_accountants_veto(self) -> None:
        doc = _submissions(("8-K", "2026-07-01", "4.02"))
        assert (
            _events_from_submissions("X", doc, since=SINCE)[0].severity
            is Severity.CRITICAL
        )

    def test_results_of_operations_is_only_a_note(self) -> None:
        doc = _submissions(("8-K", "2026-07-01", "2.02,9.01"))
        events = _events_from_submissions("X", doc, since=SINCE)
        assert [e.severity for e in events] == [Severity.NOTE]

    def test_every_mapped_item_has_a_meaning(self) -> None:
        for code, (severity, meaning) in ITEM_MEANINGS.items():
            assert meaning, code
            assert severity in set(Severity)


class TestFiltering:
    def test_filings_before_the_cutoff_are_ignored(self) -> None:
        doc = _submissions(("8-K", "2026-01-01", "4.02"))
        assert _events_from_submissions("X", doc, since=SINCE) == []

    def test_unmapped_item_codes_are_dropped_not_guessed(self) -> None:
        doc = _submissions(("8-K", "2026-07-01", "9.01,7.01"))
        assert _events_from_submissions("X", doc, since=SINCE) == []

    def test_a_ten_q_is_not_an_event(self) -> None:
        doc = _submissions(("10-Q", "2026-07-01", ""))
        assert _events_from_submissions("X", doc, since=SINCE) == []

    def test_several_items_on_one_filing_each_report(self) -> None:
        doc = _submissions(("8-K", "2026-07-01", "4.01,2.06"))
        codes = {e.code for e in _events_from_submissions("X", doc, since=SINCE)}
        assert codes == {"4.01", "2.06"}

    def test_a_malformed_date_is_skipped_not_fatal(self) -> None:
        doc = _submissions(("8-K", "not-a-date", "4.02"))
        assert _events_from_submissions("X", doc, since=SINCE) == []

    def test_terminal_forms_are_all_reachable(self) -> None:
        for form in TERMINAL_FORMS:
            doc = _submissions((form, "2026-07-01", ""))
            events = _events_from_submissions("X", doc, since=SINCE)
            assert len(events) == 1, form
            assert events[0].severity is Severity.CRITICAL


class TestSerialisation:
    def test_event_shape(self) -> None:
        doc = _submissions(("8-K", "2026-07-01", "4.02"))
        d = _events_from_submissions("X", doc, since=SINCE)[0].to_dict()
        assert d["ticker"] == "X"
        assert d["filed"] == "2026-07-01"
        assert d["severity"] == "critical"
