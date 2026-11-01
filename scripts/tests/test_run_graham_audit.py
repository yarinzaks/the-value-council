"""Unit tests for the Graham audit script's input parsers."""

from __future__ import annotations

import pytest
import requests
from pytest_mock import MockerFixture

from agents.graham.playbook_audit import AuditResult, MarketRates
from scripts.run_graham_audit import (
    companyfacts_to_frame,
    fetch_and_audit,
    parse_fred_latest,
    parse_nasdaq_quotes,
)

RATES = MarketRates(aaa_corporate_pct=6.0, treasury_20y_pct=5.0)


class TestParseNasdaqQuotes:
    def test_parses_price_and_industry(self) -> None:
        payload = {
            "data": {
                "rows": [
                    {
                        "symbol": "ingr",
                        "lastsale": "$1,094.25",
                        "sector": "Consumer Staples",
                        "industry": "Packaged Foods",
                    },
                    {"symbol": "BAD", "lastsale": "NA"},
                    {"symbol": "ZERO", "lastsale": "$0.00"},
                ]
            }
        }
        assert parse_nasdaq_quotes(payload) == {
            "INGR": (1094.25, "Consumer Staples/Packaged Foods")
        }

    def test_wrong_shape_raises(self) -> None:
        with pytest.raises(ValueError, match=r"data\.rows"):
            parse_nasdaq_quotes({"rows": []})

    def test_no_usable_quotes_raises(self) -> None:
        with pytest.raises(ValueError, match="no usable quotes"):
            parse_nasdaq_quotes({"data": {"rows": [{"symbol": "X", "lastsale": "NA"}]}})


class TestParseFredLatest:
    def test_skips_missing_observations(self) -> None:
        text = "observation_date,DGS20\n2026-10-26,5.31\n2026-10-27,5.34\n2026-10-28,.\n"
        assert parse_fred_latest(text, "DGS20") == 5.34

    def test_empty_series_raises(self) -> None:
        with pytest.raises(ValueError, match="no observations"):
            parse_fred_latest("observation_date,AAA\n", "AAA")


class TestCompanyfactsToFrame:
    def test_flattens_into_cache_layout(self) -> None:
        payload = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "val": 5,
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "filed": "2026-02-10",
                                    "form": "10-K",
                                }
                            ]
                        }
                    }
                }
            }
        }
        frame = companyfacts_to_frame(payload)
        assert len(frame) == 1
        row = frame.iloc[0]
        assert row["concept"] == "NetIncomeLoss"
        assert row["value"] == 5
        assert str(row["period_end"].date()) == "2025-12-31"

    def test_missing_facts_raises(self) -> None:
        with pytest.raises(ValueError, match="no 'facts'"):
            companyfacts_to_frame({"cik": 1})


class TestFetchAndAudit:
    """A bad SEC response becomes a recorded reason, never a crashed run."""

    def _nominee(self) -> AuditResult:
        return AuditResult(ticker="X", price=10.0, industry="I/I", status="WATCH")

    def _session(
        self, mocker: MockerFixture, *, payload: object = None, exc: Exception | None = None
    ) -> requests.Session:
        session = mocker.Mock(spec=requests.Session)
        if exc is not None:
            session.get.side_effect = exc
        else:
            resp = mocker.Mock()
            resp.json.return_value = payload
            session.get.return_value = resp
        return session

    def test_payload_without_facts_is_reported(self, mocker: MockerFixture) -> None:
        session = self._session(mocker, payload={"cik": 1})
        out = fetch_and_audit(session, self._nominee(), 1, RATES)
        assert isinstance(out, str)
        assert "no 'facts'" in out

    def test_network_error_is_reported(self, mocker: MockerFixture) -> None:
        session = self._session(mocker, exc=requests.ConnectionError("down"))
        out = fetch_and_audit(session, self._nominee(), 1, RATES)
        assert out == "ConnectionError: down"

    def test_valid_payload_is_audited(self, mocker: MockerFixture) -> None:
        session = self._session(mocker, payload={"facts": {}})
        out = fetch_and_audit(session, self._nominee(), 1, RATES)
        assert isinstance(out, AuditResult)
        assert out.status == "INSUFFICIENT_DATA"
