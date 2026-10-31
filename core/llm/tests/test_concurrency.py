"""Many memos at once, in the order they were asked for.

One memo takes about fifteen seconds on the live API, and a run across
the five hybrid agents asks for roughly ninety-eight. End to end that
is twenty-four minutes added to a scan that already needs more than two
hours of the three it has, and whose own duration has ranged from
thirty-four minutes to a hundred and twenty-one. Four workers make it
six.

Two properties carry the weight. Order, because a ranking is an ordered
thing and a run whose output depended on which call the API answered
first would not be reproducible. And failure as a value, because the
callers already treat an analyzer failure as "keep the quantitative
verdict" — which is only possible if one company's failure does not
raise through the other ninety-seven.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.llm.concurrency import DEFAULT_WORKERS, analyze_all


class TestOrder:
    def test_results_come_back_in_input_order(self) -> None:
        """Even when the slowest candidate is first."""
        delays = {"A": 0.05, "B": 0.0, "C": 0.0, "D": 0.0}

        def ask(name: str) -> str:
            time.sleep(delays[name])
            return name.lower()

        out = analyze_all(["A", "B", "C", "D"], ask)

        assert [c for c, _ in out] == ["A", "B", "C", "D"]
        assert [r for _, r in out] == ["a", "b", "c", "d"]

    def test_each_result_is_paired_with_its_own_candidate(self) -> None:
        out = analyze_all([1, 2, 3], lambda n: n * 10)

        assert out == [(1, 10), (2, 20), (3, 30)]

    def test_an_empty_list_is_no_calls_and_no_pool(self) -> None:
        calls: list[object] = []

        assert analyze_all([], lambda c: calls.append(c)) == []
        assert calls == []


class TestFailureIsAValue:
    def test_a_raising_candidate_comes_back_as_its_exception(self) -> None:
        boom = RuntimeError("the analyzer refused")

        def ask(name: str) -> str:
            if name == "B":
                raise boom
            return name

        out = dict(analyze_all(["A", "B", "C"], ask))

        assert out["B"] is boom
        assert out["A"] == "A" and out["C"] == "C"

    def test_one_failure_does_not_cost_the_others(self) -> None:
        """The whole reason this returns rather than raises."""

        def ask(n: int) -> int:
            if n == 0:
                raise ValueError("first one")
            return n

        out = analyze_all(list(range(20)), ask)

        assert isinstance(out[0][1], ValueError)
        assert [r for _, r in out[1:]] == list(range(1, 20))

    def test_every_candidate_is_attempted(self) -> None:
        seen: set[int] = set()
        lock = threading.Lock()

        def ask(n: int) -> int:
            with lock:
                seen.add(n)
            raise RuntimeError("all of them fail")

        analyze_all(list(range(30)), ask)

        assert seen == set(range(30))


class TestConcurrency:
    def test_work_actually_overlaps(self) -> None:
        """Otherwise this is an elaborate for-loop."""
        started = threading.Barrier(DEFAULT_WORKERS, timeout=5)

        def ask(_n: int) -> str:
            # Returns only once DEFAULT_WORKERS calls are in flight, so
            # it cannot pass unless they genuinely overlap.
            started.wait()
            return "ok"

        out = analyze_all(list(range(DEFAULT_WORKERS)), ask)

        assert [r for _, r in out] == ["ok"] * DEFAULT_WORKERS

    def test_the_pool_is_never_wider_than_the_work(self) -> None:
        live, peak = 0, 0
        lock = threading.Lock()

        def ask(_n: int) -> None:
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.01)
            with lock:
                live -= 1

        analyze_all([1, 2], ask, workers=16)

        assert peak <= 2

    @pytest.mark.parametrize("workers", [0, 1])
    def test_turning_it_off_still_runs_everything(self, workers: int) -> None:
        out = analyze_all([1, 2, 3], lambda n: n + 1, workers=workers)

        assert out == [(1, 2), (2, 3), (3, 4)]

    def test_a_single_candidate_needs_no_pool(self) -> None:
        """A pool of one is slower than not having one."""
        where: list[str] = []

        def ask(_n: int) -> None:
            where.append(threading.current_thread().name)

        analyze_all([1], ask)

        assert where == [threading.current_thread().name]
