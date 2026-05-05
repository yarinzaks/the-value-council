"""Generic, declarative stock screener.

Each agent's playbook produces a list of :class:`Filter` objects;
:meth:`ScreenerEngine.screen` returns the subset of snapshots that pass
*every* filter (logical AND). Snapshots whose field is missing fail
their filter — we treat absent data as "criterion not met" rather than
silently passing.

Field paths use dotted notation against :class:`StockSnapshot`, e.g.::

    Filter("fundamentals.pe_ratio", "<=", 15)
    Filter("quote.price", ">", 0)
    Filter("fundamentals.dividend_yield", ">=", 0.03)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from core.data.models import StockSnapshot

FilterOp = Literal[">", "<", ">=", "<=", "==", "!=", "in", "not_in", "between"]


@dataclass(frozen=True)
class Filter:
    """A single declarative filter rule.

    ``field`` is a dotted path resolved against a :class:`StockSnapshot`.
    For ``between``, ``value`` must be a 2-tuple ``(low, high)``.
    For ``in``/``not_in``, ``value`` must be an iterable of allowed values.
    """

    field: str
    op: FilterOp
    value: Any

    def __post_init__(self) -> None:
        valid_ops: tuple[FilterOp, ...] = (
            ">", "<", ">=", "<=", "==", "!=", "in", "not_in", "between",
        )
        if self.op not in valid_ops:
            raise ValueError(f"unknown filter op {self.op!r}")
        if self.op == "between":
            try:
                low, high = self.value  # type: ignore[misc]
            except (TypeError, ValueError) as exc:
                raise ValueError("'between' requires (low, high) tuple") from exc
            if low is None or high is None or low > high:
                raise ValueError("'between' bounds must be ordered and non-None")
        if self.op in ("in", "not_in") and not hasattr(self.value, "__iter__"):
            raise ValueError(f"{self.op!r} requires an iterable value")


class ScreenerEngine:
    """Apply filter rules to stock snapshots."""

    def apply(self, snapshot: StockSnapshot, filters: list[Filter]) -> bool:
        """Return True iff ``snapshot`` passes every filter."""
        return all(self._evaluate(snapshot, f) for f in filters)

    def screen(
        self, snapshots: Iterable[StockSnapshot], filters: list[Filter]
    ) -> list[StockSnapshot]:
        """Return the subset of ``snapshots`` that pass every filter."""
        return [s for s in snapshots if self.apply(s, filters)]

    # --- Internals ----------------------------------------------------------
    def _evaluate(self, snapshot: StockSnapshot, f: Filter) -> bool:
        actual = self._resolve(snapshot, f.field)
        if actual is None:
            return False  # missing data fails the filter

        op = f.op
        v = f.value
        try:
            if op == ">":
                return actual > v
            if op == "<":
                return actual < v
            if op == ">=":
                return actual >= v
            if op == "<=":
                return actual <= v
            if op == "==":
                return actual == v
            if op == "!=":
                return actual != v
            if op == "in":
                return actual in v
            if op == "not_in":
                return actual not in v
            if op == "between":
                low, high = v
                return low <= actual <= high
        except TypeError:
            return False
        return False

    @staticmethod
    def _resolve(snapshot: StockSnapshot, dotted: str) -> Any:
        """Resolve a dotted attribute path on a snapshot; return None if missing."""
        obj: Any = snapshot
        for part in dotted.split("."):
            if obj is None:
                return None
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                obj = getattr(obj, part, None)
        return obj


__all__ = ["Filter", "FilterOp", "ScreenerEngine"]
