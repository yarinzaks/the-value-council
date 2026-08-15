"""The engine end to end, with the two orderings that matter pinned.

The rank must be computed over the whole universe rather than the
passers, and Gate D must be paid for only on the names that already
cleared A to C. Both are correctness properties dressed as
optimisations, and both are asserted directly here.
"""

from __future__ import annotations

from datetime import date, timedelta

from agents.council.assemble import Assembled
from agents.council.pipeline import run_selection
from agents.council.rank import RankInputs
from agents.council.screen import FilingFlags, Financials
from agents.council.universe import UniverseInputs

AS_OF = date(2026, 8, 14)


def company(
    ticker: str,
    *,
    cheap: bool = True,
    tradeable: bool = True,
    mechanical: bool = True,
    momentum: float = 0.20,
    sic: int = 3500,
) -> Assembled:
    """A company that clears everything, tunable per test."""
    universe = UniverseInputs(
        ticker=ticker,
        major_us_listing=tradeable,
        files_10k=True,
        quarters_of_fundamentals=20,
        latest_filing=AS_OF - timedelta(days=45),
        price=25.0,
        median_dollar_volume_63d=2_000_000.0,
        sic=sic,
        market_cap=800_000_000.0 if mechanical else 900_000_000_000.0,
        in_sp500=not mechanical,
    )
    financials = Financials(
        ticker=ticker,
        market_cap=1_000.0,
        enterprise_value=700.0 if cheap else 90_000.0,
        ebit_ttm=100.0,
        cfo_ttm=120.0,
        fcf_ttm=90.0,
        net_income_ttm=100.0,
        total_assets=2_000.0,
        tangible_book=400.0 if cheap else 1.0,
        net_cash=300.0 if cheap else 0.0,
        net_debt=-300.0 if cheap else 0.0,
        shares_cagr_3y=0.0,
    )
    rank = RankInputs(
        ticker=ticker,
        ebit_to_ev=0.15 if cheap else 0.001,
        fcf_to_ev=0.12 if cheap else 0.001,
        net_cash_to_market_cap=0.30 if cheap else 0.0,
        roic=0.15,
        f_score=7,
        momentum_12_1=momentum,
        sic2=sic // 100,
    )
    return Assembled(
        ticker=ticker, universe=universe, financials=financials, rank=rank
    )


def clean_gate_d(tickers, as_of, *, opinions=None):
    return {
        t: FilingFlags(
            ticker=t,
            restatement_8k_402=False,
            going_concern=False,
            material_weakness=False,
            late_filing=False,
        )
        for t in tickers
    }


class TestHappyPath:
    def test_a_cheap_clean_universe_produces_a_basket(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(6)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert len(s.basket) == 6
        assert s.weights
        assert all(w > 0 for w in s.weights.values())

    def test_the_weights_are_equal(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert len(set(s.weights.values())) == 1

    def test_an_expensive_universe_holds_cash(self) -> None:
        """The doctrine's stated normal outcome, not a failure."""
        rows = [company(f"T{i:02d}", cheap=False) for i in range(6)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert s.weights == {}
        assert s.basket == []

    def test_an_empty_roster_is_not_an_error(self) -> None:
        s = run_selection([], AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert s.weights == {}


class TestOrdering:
    """The two orderings that are correctness, not performance."""

    def test_the_rank_covers_the_whole_universe_not_just_the_passers(
        self,
    ) -> None:
        rows = [company("CHEAP")] + [
            company(f"RICH{i}", cheap=False) for i in range(9)
        ]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        # Every name is ranked; only one clears the screen.
        assert len(s.ranked) == 10
        assert s.provisional == ["CHEAP"]

    def test_gate_d_is_only_paid_for_on_survivors(self) -> None:
        asked: list[list[str]] = []

        def spy(tickers, as_of, *, opinions=None):
            asked.append(list(tickers))
            return clean_gate_d(tickers, as_of)

        rows = [company("CHEAP")] + [
            company(f"RICH{i}", cheap=False) for i in range(20)
        ]
        run_selection(rows, AS_OF, risk_on_dials=4, gate_d=spy)
        assert asked == [["CHEAP"]]

    def test_gate_d_is_not_called_at_all_when_nothing_survives(self) -> None:
        calls: list[object] = []

        def spy(tickers, as_of, *, opinions=None):
            calls.append(tickers)
            return {}

        rows = [company(f"RICH{i}", cheap=False) for i in range(5)]
        run_selection(rows, AS_OF, risk_on_dials=4, gate_d=spy)
        assert calls == []


class TestGateD:
    def test_a_flagged_filer_is_dropped_after_clearing_a_to_c(self) -> None:
        def dirty(tickers, as_of, *, opinions=None):
            return {
                t: FilingFlags(
                    ticker=t,
                    restatement_8k_402=(t == "BAD"),
                    going_concern=False,
                    material_weakness=False,
                    late_filing=False,
                )
                for t in tickers
            }

        rows = [company("GOOD", sic=3500), company("BAD", sic=3600)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=dirty)
        assert set(s.provisional) == {"BAD", "GOOD"}
        assert [b.ticker for b in s.basket] == ["GOOD"]

    def test_an_unreadable_filer_is_dropped(self) -> None:
        """UNKNOWN fails Gate D."""

        def unknown(tickers, as_of, *, opinions=None):
            return {t: FilingFlags(ticker=t) for t in tickers}

        rows = [company("T01")]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=unknown)
        assert s.basket == []


class TestUniverseRules:
    def test_an_otc_name_never_reaches_the_screen(self) -> None:
        rows = [company("OTC", tradeable=False), company("OK", sic=3600)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert "OTC" not in s.provisional
        assert "OK" in s.provisional

    def test_an_index_member_is_ranked_but_not_bought(self) -> None:
        """Tradeable for the Council, off limits to the machine."""
        rows = [company("BIG", mechanical=False), company("SMALL", sic=3600)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert {r.ticker for r in s.ranked} == {"BIG", "SMALL"}
        assert "BIG" not in s.provisional
        assert [b.ticker for b in s.basket] == ["SMALL"]


class TestRegimeAndBreaker:
    def test_a_risk_off_dial_stops_new_entries(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(rows, AS_OF, risk_on_dials=1, gate_d=clean_gate_d)
        assert s.weights == {}
        assert "no mechanical entries" in s.note

    def test_a_risk_off_dial_keeps_what_is_held(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(
            rows, AS_OF, risk_on_dials=1, held=["T01"], gate_d=clean_gate_d
        )
        assert set(s.weights) == {"T01"}

    def test_half_size_at_two_dials(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        full = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        half = run_selection(rows, AS_OF, risk_on_dials=2, gate_d=clean_gate_d)
        assert max(half.weights.values()) < max(full.weights.values())

    def test_the_breaker_stops_entries_but_keeps_holdings(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(
            rows,
            AS_OF,
            risk_on_dials=4,
            entries_blocked=True,
            held=["T02"],
            gate_d=clean_gate_d,
        )
        assert set(s.weights) == {"T02"}
        assert "circuit breaker" in s.note

    def test_an_unreadable_dial_takes_the_tightest_row(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(rows, AS_OF, risk_on_dials=None, gate_d=clean_gate_d)
        assert s.weights == {}


class TestKnifeGuardAndSectorCap:
    def test_the_bottom_momentum_decile_is_not_bought(self) -> None:
        rows = [
            company(f"T{i:02d}", momentum=float(i), sic=3500 + i * 100)
            for i in range(20)
        ]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert "T00" not in s.weights

    def test_the_sector_cap_binds(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500) for i in range(12)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        assert len(s.basket) == 5


class TestSummary:
    def test_it_reports_the_counts_a_reader_needs(self) -> None:
        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(3)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d)
        text = s.summary()
        assert "tradeable" in text and "basket" in text


class TestRankBuffer:
    """E8: bought into the top 20, held until rank 40.

    Without the buffer a name drifting to rank 21 is sold and re-bought
    when it drifts back, which is pure cost — the buffer exists to stop
    names oscillating around the boundary from generating turnover.
    """

    @staticmethod
    def _graded(n: int) -> list[Assembled]:
        # Distinct sectors so the sector cap never binds, and a value
        # gradient so the rank order is exactly T00, T01, ... T29.
        rows = []
        for i in range(n):
            row = company(f"T{i:02d}", sic=100 * (i + 3))
            row.rank.__dict__["ebit_to_ev"] = 1.0 - i / 100.0
            rows.append(row)
        return rows

    def test_a_held_name_outside_the_top_twenty_is_kept(self) -> None:
        rows = self._graded(30)
        s = run_selection(
            rows, AS_OF, risk_on_dials=4, held=["T24"], gate_d=clean_gate_d,
            basket_size=20,
        )
        assert "T24" in s.weights

    def test_a_held_name_past_the_buffer_is_dropped(self) -> None:
        rows = self._graded(60)
        s = run_selection(
            rows, AS_OF, risk_on_dials=4, held=["T55"], gate_d=clean_gate_d,
            basket_size=20,
        )
        assert "T55" not in s.weights

    def test_an_unheld_name_outside_the_top_twenty_is_not_bought(self) -> None:
        """The buffer holds; it does not buy."""
        rows = self._graded(30)
        s = run_selection(
            rows, AS_OF, risk_on_dials=4, gate_d=clean_gate_d, basket_size=20
        )
        assert "T24" not in s.weights

    def test_the_buffer_does_not_duplicate_a_name_already_bought(self) -> None:
        rows = self._graded(30)
        s = run_selection(
            rows, AS_OF, risk_on_dials=4, held=["T01"], gate_d=clean_gate_d,
            basket_size=20,
        )
        assert [b.ticker for b in s.basket].count("T01") == 1


class TestGateDOutage:
    """An outage at the SEC fails the gate, not the agent.

    Section 2's rule is that a gate which cannot be computed fails. A
    Gate D that raises all the way out ends the run with an error and no
    marks, which is strictly worse than buying nothing: the book stops
    being valued because a screen could not be completed.
    """

    def test_an_outage_buys_nothing_rather_than_raising(self) -> None:
        def broken(tickers, as_of, *, opinions=None):
            raise RuntimeError("HTTP Error 500: Internal Server Error")

        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=broken)
        assert s.weights == {}
        assert "Gate D unavailable" in s.note

    def test_an_outage_does_not_disturb_what_is_held(self) -> None:
        def broken(tickers, as_of, *, opinions=None):
            raise RuntimeError("HTTP Error 500")

        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(
            rows, AS_OF, risk_on_dials=4, held=["T01"], gate_d=broken
        )
        assert set(s.weights) == {"T01"}

    def test_the_names_that_cleared_a_to_c_are_still_recorded(self) -> None:
        """The run log must show the screen worked and only D failed."""

        def broken(tickers, as_of, *, opinions=None):
            raise RuntimeError("HTTP Error 500")

        rows = [company(f"T{i:02d}", sic=3500 + i * 100) for i in range(4)]
        s = run_selection(rows, AS_OF, risk_on_dials=4, gate_d=broken)
        assert len(s.provisional) == 4
