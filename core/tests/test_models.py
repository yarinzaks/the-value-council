"""Unit tests for the data models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.data.models import (
    Fundamentals,
    NewsItem,
    Quote,
    StockSnapshot,
)


class TestQuote:
    def test_minimal_construction(self) -> None:
        q = Quote(ticker="AAPL", price=150.0)
        assert q.ticker == "AAPL"
        assert q.currency == "USD"
        assert q.timestamp is not None

    def test_round_trip_json(self) -> None:
        q = Quote(ticker="AAPL", price=150.0, volume=1_000_000)
        restored = Quote.model_validate_json(q.model_dump_json())
        assert restored == q


class TestFundamentals:
    def test_all_optional_fields_default_none(self) -> None:
        f = Fundamentals(ticker="AAPL")
        assert f.pe_ratio is None
        assert f.book_value_per_share is None

    def test_negative_metrics_allowed(self) -> None:
        # Real markets have negative earnings — the model must allow it.
        f = Fundamentals(ticker="LOSER", eps=-2.5, net_income=-1_000_000)
        assert f.eps == -2.5


class TestNewsItem:
    def test_sentiment_bounds(self) -> None:
        with pytest.raises(ValidationError):
            NewsItem(
                title="x",
                url="https://example.com",
                published_at=datetime.now(UTC),
                source="test",
                sentiment=1.5,
            )

    def test_sentiment_within_range(self) -> None:
        item = NewsItem(
            title="x",
            url="https://example.com",
            published_at=datetime.now(UTC),
            source="test",
            sentiment=0.5,
        )
        assert item.sentiment == 0.5


class TestStockSnapshot:
    def test_empty_snapshot(self) -> None:
        s = StockSnapshot(ticker="AAPL")
        assert s.quote is None
        assert s.fundamentals is None
        assert s.news == []
        assert s.sources == []

    def test_full_snapshot_round_trip(self) -> None:
        s = StockSnapshot(
            ticker="AAPL",
            quote=Quote(ticker="AAPL", price=150.0),
            fundamentals=Fundamentals(ticker="AAPL", pe_ratio=25.0),
            sources=["yfinance", "fmp"],
        )
        restored = StockSnapshot.model_validate_json(s.model_dump_json())
        assert restored.ticker == "AAPL"
        assert restored.quote is not None and restored.quote.price == 150.0
        assert restored.fundamentals is not None and restored.fundamentals.pe_ratio == 25.0
        assert "yfinance" in restored.sources
