"""The audit trail's own guarantees.

Three things matter here. A record that is written must come back
whole, because the whole point is that the journal's three-field
summary was not enough. A second record on the same day must not
overwrite the first. And the mapping from memo class to agent must
stay complete, or a sixth analyst's reasoning quietly files itself
under a name nobody looks at.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel

from core.llm.memo_log import (
    AGENT_BY_SCHEMA,
    MemoLog,
    MemoLogError,
    MemoRecord,
    agent_for,
    digest,
)

DAY = date(2026, 10, 30)


def a_record(
    *, agent="philip_fisher", ticker="AAPL", schema="FisherMemo", memo=None, **kw
) -> MemoRecord:
    return MemoRecord(
        agent=agent,
        ticker=ticker,
        schema=schema,
        timestamp=f"{DAY.isoformat()}T12:00:00+00:00",
        model="claude-opus-5-5",
        memo=memo if memo is not None else {"decision": "BUY", "tier": "Tier 1"},
        **kw,
    )


@pytest.fixture
def log(tmp_path: Path) -> MemoLog:
    return MemoLog(root=tmp_path)


class TestItKeepsTheWholeThing:
    def test_a_record_comes_back_as_it_went_in(self, log: MemoLog) -> None:
        rich = {
            "decision": "BUY",
            "tier_classification": "Tier 1",
            "fifteen_points_score": {"point_1_market_potential": "PASS"},
            "concerns": ["cyclical end market"],
            "integrity_check_passed": True,
        }
        log.log(a_record(memo=rich))

        got = log.read_day("philip_fisher", DAY)

        assert len(got) == 1
        assert got[0].memo == rich, "the journal's three fields are not enough"
        assert got[0].ticker == "AAPL"
        assert got[0].model == "claude-opus-5-5"

    def test_the_cost_fields_survive(self, log: MemoLog) -> None:
        log.log(a_record(input_tokens=12_000, output_tokens=640, prompt_chars=31_200))

        got = log.read_day("philip_fisher", DAY)[0]

        assert got.input_tokens == 12_000
        assert got.output_tokens == 640
        assert got.prompt_chars == 31_200

    def test_a_second_record_appends_rather_than_replaces(self, log: MemoLog) -> None:
        log.log(a_record(ticker="AAPL"))
        log.log(a_record(ticker="MSFT"))

        got = log.read_day("philip_fisher", DAY)

        assert [r.ticker for r in got] == ["AAPL", "MSFT"]

    def test_an_unknown_day_is_empty_not_an_error(self, log: MemoLog) -> None:
        assert log.read_day("philip_fisher", date(1999, 1, 1)) == []

    def test_reading_everything_is_ordered_oldest_first(self, log: MemoLog) -> None:
        for day, ticker in (("2026-10-28", "OLD"), ("2026-10-30", "NEW")):
            log.log(
                MemoRecord(
                    agent="howard_marks",
                    ticker=ticker,
                    schema="MarksMemo",
                    timestamp=f"{day}T09:00:00+00:00",
                    model="m",
                )
            )

        assert [r.ticker for r in log.read_all_for_agent("howard_marks")] == [
            "OLD",
            "NEW",
        ]

    def test_which_books_have_reasoning(self, log: MemoLog) -> None:
        log.log(a_record())
        log.log(a_record(agent="seth_klarman", schema="KlarmanMemo"))

        assert log.agents() == ["philip_fisher", "seth_klarman"]

    def test_no_books_yet(self, tmp_path: Path) -> None:
        assert MemoLog(root=tmp_path / "nothing_here").agents() == []


class TestItSurvivesABadFile:
    def test_a_corrupt_file_does_not_lose_todays_record(self, log: MemoLog) -> None:
        """The decision journal makes the same choice.

        Today's reasoning is worth more than yesterday's unreadable
        bytes, and refusing to write would lose both.
        """
        path = log.path_for("philip_fisher", DAY)
        path.write_text("{ not json at all")

        log.log(a_record(ticker="RESCUED"))

        got = log.read_day("philip_fisher", DAY)
        assert [r.ticker for r in got] == ["RESCUED"]

    def test_a_file_that_is_not_a_list_is_an_error_on_read(self, log: MemoLog) -> None:
        path = log.path_for("philip_fisher", DAY)
        path.write_text(json.dumps({"not": "a list"}))

        with pytest.raises(MemoLogError, match="not a list"):
            log.read_day("philip_fisher", DAY)

    def test_nothing_is_left_behind_after_a_write(self, log: MemoLog) -> None:
        """The write is a temp file plus a rename, not an in-place edit."""
        log.log(a_record())

        leftovers = list((log.root / "philip_fisher").glob("*.tmp"))
        assert leftovers == []

    def test_an_unknown_field_in_an_old_file_is_ignored(self, log: MemoLog) -> None:
        """A record written by a later version must still be readable."""
        path = log.path_for("philip_fisher", DAY)
        path.write_text(
            json.dumps([{**a_record().to_dict(), "a_field_from_the_future": 1}])
        )

        assert log.read_day("philip_fisher", DAY)[0].ticker == "AAPL"


class TestTheAgentIsAPathSegment:
    @pytest.mark.parametrize(
        "bad", ["../escape", "with space", "UPPER", "", "a/b", "dots.dots"]
    )
    def test_a_value_that_is_not_a_slug_is_refused(
        self, log: MemoLog, bad: str
    ) -> None:
        with pytest.raises(MemoLogError, match="not an agent slug"):
            log.path_for(bad, DAY)

    def test_a_bad_timestamp_is_refused(self, log: MemoLog) -> None:
        with pytest.raises(MemoLogError, match="invalid timestamp"):
            log.log(
                MemoRecord(
                    agent="philip_fisher",
                    ticker="AAPL",
                    schema="FisherMemo",
                    timestamp="not a date",
                    model="m",
                )
            )


class TestTheMappingStaysComplete:
    def test_every_analyst_memo_class_has_a_home(self) -> None:
        """The drift guard.

        A sixth analyst whose memo class is not in the mapping still
        gets recorded, but under a derived name nobody would think to
        look at. This fails the moment such a class appears.
        """
        import importlib

        modules = [
            "agents.fisher.scuttlebutt",
            "agents.marks.second_level",
            "agents.klarman.downside",
            "agents.buffett.moat_analyzer",
            "agents.lynch.category_classifier",
        ]
        found: set[str] = set()
        for name in modules:
            mod = importlib.import_module(name)
            for attr, obj in vars(mod).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseModel)
                    and attr.endswith("Memo")
                ):
                    found.add(attr)

        assert found, "found no memo classes at all — the scan is broken"
        missing = found - set(AGENT_BY_SCHEMA)
        assert not missing, f"memo classes with no agent mapped: {sorted(missing)}"

    def test_the_mapped_slugs_are_real_books(self) -> None:
        """Not a plausible-looking name — the adapter's own `name`."""
        from core.live.agent_adapter import (
            BuffettLive,
            FisherLive,
            KlarmanLive,
            LynchLive,
            MarksLive,
        )

        real = {
            cls.name
            for cls in (FisherLive, MarksLive, KlarmanLive, BuffettLive, LynchLive)
        }
        mapped = {v for k, v in AGENT_BY_SCHEMA.items() if k != "InvestmentMemo"}

        assert mapped == real, f"mapped {sorted(mapped)} but the books are {sorted(real)}"

    @pytest.mark.parametrize(
        "schema,expected",
        [
            ("FisherMemo", "philip_fisher"),
            ("BuffettMemo", "warren_buffett"),
            ("InvestmentMemo", "connectivity_probe"),
        ],
    )
    def test_agent_for_resolves_the_known_ones(
        self, schema: str, expected: str
    ) -> None:
        assert agent_for(schema) == expected

    def test_an_unmapped_class_still_gets_a_findable_name(self) -> None:
        assert agent_for("SomethingNewMemo") == "something_new_memo"

    def test_the_probe_never_lands_in_a_book(self) -> None:
        """A connectivity check is not an investment opinion."""
        assert AGENT_BY_SCHEMA["InvestmentMemo"] not in {
            v for k, v in AGENT_BY_SCHEMA.items() if k != "InvestmentMemo"
        }


def test_the_digest_identifies_a_prompt_without_keeping_it() -> None:
    long_playbook = "Graham's playbook, " * 2000

    d = digest(long_playbook)

    assert len(d) == 64
    assert long_playbook[:40] not in d
    assert digest(long_playbook) == d, "same text, same digest"
    assert digest(long_playbook + "!") != d
