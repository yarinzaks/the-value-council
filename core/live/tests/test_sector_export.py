"""Tests for the per-ticker sector export.

A position list says what an agent owns; it does not say what it is
doing. Ten holdings in banks and ten spread across manufacturing,
utilities and retail are the same list length and opposite stances.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.live.portfolio import LivePortfolio, Position
from core.live.sector_export import UNKNOWN, export_sectors, sector_of


def _portfolio(agent: str, tickers: list[str]) -> LivePortfolio:
    return LivePortfolio(
        agent=agent,
        positions=[
            Position(
                ticker=t,
                shares=10.0,
                entry_price=100.0,
                entry_date="2026-05-06",
                current_price=110.0,
            )
            for t in tickers
        ],
    )


class TestSectorOf:
    def test_a_bank_is_finance(self) -> None:
        # JPM is SIC 6022; the 60-67 division is finance, insurance and
        # real estate.
        assert sector_of("JPM") == "finance"

    def test_a_utility_is_transport_utilities(self) -> None:
        # SO is SIC 4911 — the 40-49 division.
        assert sector_of("SO") == "transport_utilities"

    def test_a_manufacturer_is_manufacturing(self) -> None:
        # AAPL is SIC 3571, inside the wide 20-39 division.
        assert sector_of("AAPL") == "manufacturing"

    def test_an_unknown_ticker_is_unknown_not_bucketed(self) -> None:
        # Filing it under a large division because that division is
        # large would hide that nobody has classified it.
        assert sector_of("NOTATICKER1") == UNKNOWN

    def test_a_retired_ticker_still_resolves(self) -> None:
        # ASGN left the SEC's current ticker map when the issuer renamed
        # to EFOR, but sic_for reads the cached filings rather than that
        # map, so the sector survives. That is the right behaviour here:
        # a position still on the books belongs in the breakdown, and
        # excluding it would understate the sector it sits in. The
        # separate question of whether it should be *bought* is
        # is_currently_listed's, and it answers no.
        assert sector_of("ASGN") == sector_of("EFOR")
        assert sector_of("ASGN") != UNKNOWN


class TestExport:
    def test_it_writes_one_entry_per_held_ticker(self, tmp_path: Path) -> None:
        out = tmp_path / "sectors.json"

        n = export_sectors([_portfolio("buffett", ["JPM", "SO"])], path=out)

        assert n == 2
        stored = json.loads(out.read_text())
        assert stored == {"JPM": "finance", "SO": "transport_utilities"}

    def test_a_ticker_held_twice_appears_once(self, tmp_path: Path) -> None:
        out = tmp_path / "sectors.json"

        n = export_sectors(
            [_portfolio("a", ["JPM"]), _portfolio("b", ["JPM"])], path=out
        )

        assert n == 1

    def test_nothing_held_writes_an_empty_map(self, tmp_path: Path) -> None:
        # Not a missing file: the dashboard reads absent as "no data
        # yet", and an empty map says the run happened and found none.
        out = tmp_path / "sectors.json"

        assert export_sectors([_portfolio("a", [])], path=out) == 0
        assert json.loads(out.read_text()) == {}

    def test_unclassified_names_are_kept_not_dropped(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "sectors.json"

        export_sectors([_portfolio("a", ["JPM", "NOTATICKER1"])], path=out)

        assert json.loads(out.read_text())["NOTATICKER1"] == UNKNOWN
