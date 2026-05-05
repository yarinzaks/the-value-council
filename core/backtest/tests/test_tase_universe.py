"""Unit tests for TASE universe scaffold."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.tase_universe import (
    STARTER_TA125_TICKERS,
    TASEUniverse,
    TASEUniverseError,
)
from core.backtest.universe_protocol import Universe


class TestStarterList:
    def test_starter_has_ta_suffix(self) -> None:
        assert all(t.endswith(".TA") for t in STARTER_TA125_TICKERS)

    def test_starter_includes_known_names(self) -> None:
        # Check some unmistakable TA-125 components
        assert "TEVA.TA" in STARTER_TA125_TICKERS
        assert "POLI.TA" in STARTER_TA125_TICKERS  # Bank Hapoalim
        assert "LUMI.TA" in STARTER_TA125_TICKERS  # Bank Leumi


class TestLoad:
    def test_falls_back_to_starter_when_no_file(self, tmp_path: Path) -> None:
        u = TASEUniverse.load(tmp_path / "nonexistent.json")
        assert len(u.tickers) == len(STARTER_TA125_TICKERS)

    def test_loads_from_json_when_present(self, tmp_path: Path) -> None:
        path = tmp_path / "tase.json"
        import json

        path.write_text(json.dumps({"tickers": ["FOO.TA", "BAR.TA"]}))
        u = TASEUniverse.load(path)
        assert u.tickers == ("FOO.TA", "BAR.TA")

    def test_disable_fallback_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TASEUniverseError):
            TASEUniverse.load(
                tmp_path / "nonexistent.json", fall_back_to_starter=False
            )


class TestSave:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "tase.json"
        u = TASEUniverse(tickers=("A.TA", "B.TA", "C.TA"), tickers_path=path)
        u.save()
        loaded = TASEUniverse.load(path)
        assert sorted(loaded.tickers) == ["A.TA", "B.TA", "C.TA"]


class TestConstituentsAt:
    def test_returns_full_list_for_any_date(self) -> None:
        u = TASEUniverse(tickers=("X.TA", "Y.TA"))
        # Documented limitation: same list every date
        assert u.constituents_at(date(2010, 1, 1)) == ["X.TA", "Y.TA"]
        assert u.constituents_at(date(2024, 12, 31)) == ["X.TA", "Y.TA"]

    def test_was_member_on(self) -> None:
        u = TASEUniverse(tickers=("TEVA.TA", "WIX.TA"))
        assert u.was_member_on("TEVA.TA", date(2020, 1, 1)) is True
        assert u.was_member_on("UNKNOWN.TA", date(2020, 1, 1)) is False


class TestProtocolCompliance:
    def test_satisfies_universe_protocol(self) -> None:
        u = TASEUniverse(tickers=STARTER_TA125_TICKERS)
        assert isinstance(u, Universe)
