"""Unit tests for the SEC Company Facts client."""

from __future__ import annotations

from datetime import date

import pytest

from core.data.edgar_facts import (
    EdgarFactsClient,
    EdgarFactsError,
    XbrlFact,
    _parse_date,
    _parse_date_required,
)


class TestXbrlFact:
    def test_to_dict_round_trip(self) -> None:
        f = XbrlFact(
            concept="Revenues",
            namespace="us-gaap",
            unit="USD",
            value=1000.0,
            period_start=date(2020, 1, 1),
            period_end=date(2020, 12, 31),
            filed=date(2021, 2, 15),
            form="10-K",
            fiscal_year=2020,
            fiscal_period="FY",
            accession_number="0000-00-000",
        )
        d = f.to_dict()
        assert d["concept"] == "Revenues"
        assert d["period_start"] == "2020-01-01"
        assert d["period_end"] == "2020-12-31"
        assert d["filed"] == "2021-02-15"


class TestParseHelpers:
    def test_parse_date_handles_iso(self) -> None:
        assert _parse_date_required("2020-06-15") == date(2020, 6, 15)

    def test_parse_date_passes_through_date(self) -> None:
        d = date(2020, 1, 1)
        assert _parse_date_required(d) is d

    def test_parse_date_returns_none_on_empty(self) -> None:
        assert _parse_date(None) is None
        assert _parse_date("") is None


class TestParseFacts:
    def test_parses_us_gaap_revenue(self) -> None:
        client = EdgarFactsClient(user_agent="test test@example.com")
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2019-01-01",
                                    "end": "2019-12-31",
                                    "val": 1000000000,
                                    "filed": "2020-02-15",
                                    "form": "10-K",
                                    "fy": 2019,
                                    "fp": "FY",
                                    "accn": "0000320193-20-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }
        facts = client._parse_facts(payload)
        assert len(facts) == 1
        f = facts[0]
        assert f.concept == "Revenues"
        assert f.namespace == "us-gaap"
        assert f.unit == "USD"
        assert f.value == 1_000_000_000.0
        assert f.period_end == date(2019, 12, 31)
        assert f.filed == date(2020, 2, 15)
        assert f.form == "10-K"
        assert f.fiscal_year == 2019
        assert f.fiscal_period == "FY"

    def test_parses_dei_namespace(self) -> None:
        client = EdgarFactsClient(user_agent="test test@example.com")
        payload = {
            "facts": {
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {
                                    "end": "2020-09-30",
                                    "val": 16_950_000_000,
                                    "filed": "2020-10-30",
                                    "form": "10-K",
                                    "fy": 2020,
                                    "fp": "FY",
                                    "accn": "acc-1",
                                }
                            ]
                        }
                    }
                }
            }
        }
        facts = client._parse_facts(payload)
        assert len(facts) == 1
        assert facts[0].namespace == "dei"
        assert facts[0].unit == "shares"

    def test_skips_malformed_facts(self) -> None:
        client = EdgarFactsClient(user_agent="test test@example.com")
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                # missing 'end' — should be skipped
                                {"val": 1000, "filed": "2020-01-01", "form": "10-K"},
                                # valid one
                                {
                                    "end": "2020-12-31",
                                    "val": 2000,
                                    "filed": "2021-02-15",
                                    "form": "10-K",
                                    "fy": 2020,
                                    "fp": "FY",
                                    "accn": "acc-2",
                                },
                            ]
                        }
                    }
                }
            }
        }
        facts = client._parse_facts(payload)
        assert len(facts) == 1
        assert facts[0].value == 2000

    def test_handles_empty_payload(self) -> None:
        client = EdgarFactsClient(user_agent="test test@example.com")
        facts = client._parse_facts({})
        assert facts == []
        facts = client._parse_facts({"facts": {}})
        assert facts == []


class TestClientConstruction:
    def test_missing_user_agent_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate settings unavailable
        def boom() -> object:
            raise RuntimeError("no .env")
        monkeypatch.setattr("core.data.edgar_facts.get_settings", boom)
        with pytest.raises(EdgarFactsError):
            EdgarFactsClient()

    def test_explicit_user_agent_accepted(self) -> None:
        c = EdgarFactsClient(user_agent="Test test@example.com")
        assert c._user_agent == "Test test@example.com"

    def test_empty_user_agent_raises(self) -> None:
        with pytest.raises(EdgarFactsError):
            EdgarFactsClient(user_agent="")
