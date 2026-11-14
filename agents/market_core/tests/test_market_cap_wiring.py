"""Every agent's market cap goes through the one split-aware helper.

Audit FUND-5: eleven agents each carried their own
``price * fin.shares_outstanding``. Fixing the helper fixes nothing if a
copy survives, so this pins each agent's ``_market_cap`` to the
split-adjusted answer on NVDA 2024-06-05, and one ``select`` to prove
the screen's floor sees that answer rather than the old $306bn.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agents.buffett.wonderful_business import WarrenBuffett
from agents.composite.composite import FactorComposite
from agents.dreman.contrarian import DavidDreman
from agents.fisher.quality_growth import PhilipFisher
from agents.graham.net_net import BenjaminGraham
from agents.greenblatt.magic_formula import MagicFormula
from agents.klarman.margin_of_safety import SethKlarman
from agents.lynch.garp import PeterLynch
from agents.market_core.tests.test_shares import (
    NVDA_COVER,
    NVDA_PRICE_2024_06_05,
    _seed,
)
from agents.marks.cycle_value import HowardMarks
from agents.neff.total_return import JohnNeff
from agents.schloss.deep_value import WalterSchloss
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache

AS_OF = date(2024, 6, 5)
#: The answer: May 2024 10-Q cover count, restated for the 10-for-1.
EXPECTED = NVDA_PRICE_2024_06_05 * 24.6e9

AGENTS: dict[str, Callable[[EdgarCache], Any]] = {
    "greenblatt": lambda c: MagicFormula(edgar_cache=c),
    "schloss": lambda c: WalterSchloss(edgar_cache=c),
    "graham": lambda c: BenjaminGraham(edgar_cache=c),
    "composite": lambda c: FactorComposite(price_loader=object(), edgar_cache=c),  # type: ignore[arg-type]
    "dreman": lambda c: DavidDreman(edgar_cache=c),
    "neff": lambda c: JohnNeff(edgar_cache=c),
    "buffett": lambda c: WarrenBuffett(edgar_cache=c),
    "lynch": lambda c: PeterLynch(edgar_cache=c),
    "marks": lambda c: HowardMarks(edgar_cache=c),
    "klarman": lambda c: SethKlarman(edgar_cache=c),
    "fisher": lambda c: PhilipFisher(edgar_cache=c),
}


def _fin(shares: float | None = 2.5e9, **fields: Any) -> PointInTimeFinancials:
    """NVDA as the PIT loader returned it: the 10-K's pre-split count."""
    return PointInTimeFinancials(
        ticker="NVDA",
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker="NVDA",
            cik=None,
            form_type="10-K",
            filing_date=date(2024, 2, 21),
            period_of_report=date(2024, 1, 28),
            accession_number="acc-2024-02-21",
        ),
        shares_outstanding=shares,
        **fields,
    )


@pytest.fixture
def cache(tmp_path: Path) -> EdgarCache:
    c = EdgarCache(cache_dir=tmp_path)
    _seed(c, "NVDA", NVDA_COVER)
    return c


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_market_cap_is_split_adjusted(agent: str, cache: EdgarCache) -> None:
    strategy = AGENTS[agent](cache)
    mcap = strategy._market_cap("NVDA", AS_OF, _fin(), NVDA_PRICE_2024_06_05)
    assert mcap == pytest.approx(EXPECTED, rel=1e-9)


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_no_financials_still_means_no_market_cap(agent: str, cache: EdgarCache) -> None:
    strategy = AGENTS[agent](cache)
    assert strategy._market_cap("NVDA", AS_OF, None, NVDA_PRICE_2024_06_05) is None


class _Prices:
    def get(self, ticker: str) -> float | None:
        return NVDA_PRICE_2024_06_05 if ticker == "NVDA" else None


class _Fundamentals:
    def __init__(self, fin: PointInTimeFinancials) -> None:
        self._fin = fin

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._fin if ticker == "NVDA" else None


def test_a_trillion_dollar_floor_admits_nvda(cache: EdgarCache) -> None:
    # $306bn would fail a $1trn floor; $3.01trn passes it. The screen
    # must be reading the helper's number, with the rebalance's own date.
    fin = _fin(
        operating_income=33e9,
        current_assets=67e9,
        current_liabilities=15e9,
        ppe_net=4e9,
        cash_and_equivalents=7e9,
        total_debt=9e9,
        sic_code="3674",
    )
    strategy = MagicFormula(edgar_cache=cache, min_market_cap=1e12)
    strategy.select(AS_OF, ["NVDA"], _Prices(), _Fundamentals(fin))  # type: ignore[arg-type]
    assert strategy.selection_history[-1].candidates_after_filters == 1
