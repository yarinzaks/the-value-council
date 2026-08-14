"""The regime dial. No test here touches the network."""

from __future__ import annotations

from datetime import date, timedelta

from agents.council.regime import (
    MIN_OBSERVATIONS,
    Stance,
    credit_signal,
    curve_signal,
    read_regime,
    trend_signal,
    volatility_signal,
)

AS_OF = date(2026, 8, 14)


def _flat(value: float, *, days: int, end: date = AS_OF) -> list[tuple[date, float]]:
    return [(end - timedelta(days=i), value) for i in range(days)][::-1]


class TestCredit:
    """Risk-off needs the spread both wide and widening."""

    def test_wide_and_widening_is_risk_off(self) -> None:
        rows = _flat(3.0, days=400)
        # Ramp the last four weeks so today is both above the median and
        # higher than it was 28 days ago. A flat elevated level is not
        # "widening" — that is the tightening case below.
        rows = rows[:-30] + [
            (d, 4.0 + 0.1 * i) for i, (d, _) in enumerate(rows[-30:])
        ]
        signal = credit_signal(rows, AS_OF)
        assert signal.stance is Stance.RISK_OFF
        assert "widening" in signal.reason

    def test_wide_but_tightening_is_risk_on(self) -> None:
        """The rule that keeps it from going defensive into every rally.

        A spread above its median but coming down is a recovery. Treating
        that as risk-off sells the bottom.
        """
        rows = _flat(3.0, days=400)
        rows = rows[:-40] + [(d, 9.0) for d, _ in rows[-40:-30]]
        rows += [(AS_OF - timedelta(days=i), 6.0) for i in range(29, -1, -1)]
        assert credit_signal(rows, AS_OF).stance is Stance.RISK_ON

    def test_too_few_observations_is_unknown_not_risk_on(self) -> None:
        assert (
            credit_signal(_flat(3.0, days=MIN_OBSERVATIONS - 1), AS_OF).stance
            is Stance.UNKNOWN
        )


class TestCurve:
    def test_inverted_is_risk_off(self) -> None:
        assert curve_signal([(AS_OF, -0.35)], AS_OF).stance is Stance.RISK_OFF

    def test_positive_is_risk_on(self) -> None:
        assert curve_signal([(AS_OF, 0.76)], AS_OF).stance is Stance.RISK_ON

    def test_exactly_zero_is_not_inverted(self) -> None:
        assert curve_signal([(AS_OF, 0.0)], AS_OF).stance is Stance.RISK_ON


class TestTrend:
    def test_below_the_average_is_risk_off(self) -> None:
        rows = _flat(100.0, days=250)
        rows[-1] = (AS_OF, 50.0)
        assert trend_signal(rows, AS_OF).stance is Stance.RISK_OFF

    def test_above_the_average_is_risk_on(self) -> None:
        rows = _flat(100.0, days=250)
        rows[-1] = (AS_OF, 150.0)
        assert trend_signal(rows, AS_OF).stance is Stance.RISK_ON

    def test_short_history_is_unknown(self) -> None:
        assert trend_signal(_flat(100.0, days=50), AS_OF).stance is Stance.UNKNOWN


class TestVolatility:
    def test_above_one_and_a_half_times_median_is_risk_off(self) -> None:
        rows = _flat(15.0, days=400)
        rows[-1] = (AS_OF, 30.0)
        assert volatility_signal(rows, AS_OF).stance is Stance.RISK_OFF

    def test_at_the_median_is_risk_on(self) -> None:
        rows = _flat(15.0, days=400)
        assert volatility_signal(rows, AS_OF).stance is Stance.RISK_ON


class TestLookAhead:
    """The guard that makes a historical reading meaningful."""

    def test_observations_after_as_of_are_ignored(self) -> None:
        rows = _flat(0.5, days=400)
        # A big move that happens tomorrow must not change today's read.
        rows.append((AS_OF + timedelta(days=1), -9.0))
        assert curve_signal(rows, AS_OF).stance is Stance.RISK_ON
        assert curve_signal(rows, AS_OF).value == 0.5


class TestReadRegime:
    def test_counts_only_risk_on(self) -> None:
        def fetch(series: str) -> list[tuple[date, float]]:
            if series == "T10Y3M":
                return [(AS_OF, -1.0)]  # risk-off
            if series == "SP500":
                rows = _flat(100.0, days=250)
                rows[-1] = (AS_OF, 150.0)
                return rows  # risk-on
            return _flat(1.0, days=400)  # credit + vol both risk-on

        regime = read_regime(AS_OF, fetch=fetch)
        assert regime.risk_on_count == 3
        assert regime.unknown_count == 0

    def test_a_dead_series_is_unknown_and_does_not_count_as_risk_on(self) -> None:
        """Four series that quietly default to risk-on would be a dial
        that only ever says buy."""

        def fetch(series: str) -> list[tuple[date, float]]:
            return []

        regime = read_regime(AS_OF, fetch=fetch)
        assert regime.risk_on_count == 0
        assert regime.unknown_count == 4

    def test_serialises_for_the_dashboard(self) -> None:
        regime = read_regime(AS_OF, fetch=lambda s: [(AS_OF, 1.0)])
        d = regime.to_dict()
        assert d["as_of"] == AS_OF.isoformat()
        assert len(d["signals"]) == 4
