"""The cooling-off rule, for the machine as well as the Council.

§7 gives the statistical rebalance one sentence that is easy to skim
past: *"execute the statistical rebalance from a list published at least
one run earlier — the cooling-off rule holds even for the machine."*

Part 7 states the rule for people — *"nothing is bought in the run in
which it is identified, or the run in which it is read"* — and gives the
reason: it costs almost nothing in expected return and removes an entire
class of error. A mechanical sleeve that ranks and buys in one breath
has that class of error back, and the fact that a formula produced the
list does not make the list any less freshly-minted.

So the rank is computed and written on the monthly close, and the
quarterly council executes whatever was written *before today*. The
whole of this module exists to make "before today" checkable.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agents.council.published_list import (
    PublishedList,
    executable_list,
    latest_published,
    publish,
)


def _list(published_on: date, tickers: tuple[str, ...] = ("AAA", "BBB")) -> PublishedList:
    return PublishedList(
        published_on=published_on,
        as_of=published_on,
        tickers=tickers,
        weights={t: round(1 / len(tickers), 4) for t in tickers},
        note="test",
    )


class TestPublishing:
    def test_a_published_list_round_trips(self, tmp_path: Path) -> None:
        original = _list(date(2026, 8, 3))
        publish(original, directory=tmp_path)
        loaded = latest_published(tmp_path, before=date(2026, 8, 10))
        assert loaded == original

    def test_it_is_written_under_its_publication_date(self, tmp_path: Path) -> None:
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        assert (tmp_path / "2026-08-03.json").exists()

    def test_republishing_the_same_day_replaces_it(self, tmp_path: Path) -> None:
        """A re-run of the monthly close is a correction, not a second list."""
        publish(_list(date(2026, 8, 3), ("AAA",)), directory=tmp_path)
        publish(_list(date(2026, 8, 3), ("ZZZ",)), directory=tmp_path)
        loaded = latest_published(tmp_path, before=date(2026, 8, 4))
        assert loaded is not None
        assert loaded.tickers == ("ZZZ",)

    def test_the_file_is_readable_json(self, tmp_path: Path) -> None:
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        data = json.loads((tmp_path / "2026-08-03.json").read_text())
        assert data["tickers"] == ["AAA", "BBB"]
        assert data["published_on"] == "2026-08-03"


class TestLatestPublished:
    def test_nothing_published_is_none(self, tmp_path: Path) -> None:
        assert latest_published(tmp_path, before=date(2026, 8, 10)) is None

    def test_a_missing_directory_is_none_not_an_error(self, tmp_path: Path) -> None:
        assert latest_published(tmp_path / "nope", before=date(2026, 8, 10)) is None

    def test_the_most_recent_before_the_bound_wins(self, tmp_path: Path) -> None:
        for d in (date(2026, 5, 4), date(2026, 8, 3), date(2026, 11, 2)):
            publish(_list(d, (f"T{d.month}",)), directory=tmp_path)
        loaded = latest_published(tmp_path, before=date(2026, 9, 1))
        assert loaded is not None
        assert loaded.published_on == date(2026, 8, 3)

    def test_the_bound_is_strict(self, tmp_path: Path) -> None:
        """Published today is not "published at least one run earlier"."""
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        assert latest_published(tmp_path, before=date(2026, 8, 3)) is None

    def test_an_unreadable_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """One corrupt artefact must not strand the whole rebalance."""
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        (tmp_path / "2026-08-04.json").write_text("{ not json")
        loaded = latest_published(tmp_path, before=date(2026, 8, 10))
        assert loaded is not None
        assert loaded.published_on == date(2026, 8, 3)

    def test_a_file_whose_name_is_not_a_date_is_ignored(self, tmp_path: Path) -> None:
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        (tmp_path / "notes.json").write_text("{}")
        loaded = latest_published(tmp_path, before=date(2026, 8, 10))
        assert loaded is not None
        assert loaded.published_on == date(2026, 8, 3)


class TestExecutableList:
    """The gate the rebalance actually calls."""

    def test_a_list_from_a_previous_run_is_executable(self, tmp_path: Path) -> None:
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        got = executable_list(tmp_path, as_of=date(2026, 8, 10))
        assert got is not None
        assert got.tickers == ("AAA", "BBB")

    def test_a_list_published_today_is_not(self, tmp_path: Path) -> None:
        """The one rule this module exists for."""
        publish(_list(date(2026, 8, 3)), directory=tmp_path)
        assert executable_list(tmp_path, as_of=date(2026, 8, 3)) is None

    def test_no_list_at_all_is_not_an_error(self, tmp_path: Path) -> None:
        """A quarter with nothing published simply does not rebalance.

        Refusing to trade is the correct failure here. The alternative —
        ranking now and executing now — is exactly the cooling-off
        breach the rule forbids, and it would arrive dressed as
        robustness.
        """
        assert executable_list(tmp_path, as_of=date(2026, 8, 3)) is None

    def test_a_stale_list_is_still_executable(self, tmp_path: Path) -> None:
        """Staleness is the point: the list is meant to have aged.

        §7 asks for "at least one run earlier", not "recent". A list
        published at the start of the quarter and executed at the
        rebalance is the intended shape, not a degraded one.
        """
        publish(_list(date(2026, 5, 4)), directory=tmp_path)
        got = executable_list(tmp_path, as_of=date(2026, 8, 3))
        assert got is not None
        assert got.published_on == date(2026, 5, 4)


class TestPublishedListValue:
    def test_weights_and_tickers_agree(self) -> None:
        with pytest.raises(ValueError, match="weights"):
            PublishedList(
                published_on=date(2026, 8, 3),
                as_of=date(2026, 8, 3),
                tickers=("AAA", "BBB"),
                weights={"AAA": 1.0},
                note="",
            )

    def test_an_empty_list_is_allowed(self) -> None:
        """A quarter in which the screen qualifies nobody is a result.

        It has to be publishable, or the only way to record "nothing
        qualified" would be to publish nothing — which reads as "the
        run never happened".
        """
        empty = PublishedList(
            published_on=date(2026, 8, 3),
            as_of=date(2026, 8, 3),
            tickers=(),
            weights={},
            note="nothing cleared the gates",
        )
        assert empty.tickers == ()
