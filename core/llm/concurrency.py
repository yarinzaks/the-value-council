"""Asking the analyst layer about many candidates at once.

Why this exists
~~~~~~~~~~~~~~~

Measured on the live API, one memo takes about fifteen seconds. Across
the five hybrid agents a run asks for roughly ninety-eight of them, so
asked one after another the layer adds some twenty-four minutes to a
scan that already needs more than two hours of the three it has. The
observed range of that scan is thirty-four minutes to a hundred and
twenty-one, so there is no margin to spend.

Nothing here is clever: the calls are network-bound, so threads are the
right tool and a small pool is enough. Four workers turn twenty-four
minutes into six.

What it guarantees
~~~~~~~~~~~~~~~~~~

Results come back in the order the candidates went in, never in the
order they finished. A ranking is an ordered thing and the caller walks
it against its own list; an order that depended on which call the API
answered first would make a run unreproducible for no reason.

A failure is returned, not raised. One company whose memo could not be
produced must not cost the other ninety-seven — the existing callers
already treat an analyzer failure as "keep the quantitative verdict",
and that is only possible if the failure arrives as a value.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

from core.logger import get_logger

logger = get_logger("core.llm.concurrency")

#: How many memos to have in flight at once.
#:
#: Four rather than more. The client spaces its own calls and the API
#: has its own limits, and the gain from here is already the difference
#: between twenty-four minutes and six — past that the saving is small
#: and the chance of tripping a rate limit is not.
DEFAULT_WORKERS: int = 4


def analyze_all[CandidateT, ResultT](
    candidates: Sequence[CandidateT],
    ask: Callable[[CandidateT], ResultT],
    *,
    workers: int = DEFAULT_WORKERS,
) -> list[tuple[CandidateT, ResultT | Exception]]:
    """Run ``ask`` over every candidate, in input order, failures included.

    Returns one pair per candidate: the candidate itself, and either the
    result or the exception the call raised. Nothing is retried here —
    the client already retries the statuses worth retrying, and a second
    attempt at a refused schema would only cost the same refusal twice.
    """
    if not candidates:
        return []
    if len(candidates) == 1 or workers <= 1:
        # One call needs no pool, and a pool of one is slower than not
        # having one. Keeping this path exact also means a caller can
        # turn concurrency off and get identical behaviour.
        return [(c, _attempt(ask, c)) for c in candidates]

    with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as pool:
        # `map` preserves input order and yields lazily, which is what
        # keeps the ordering guarantee without a sort afterwards.
        outcomes = list(pool.map(lambda c: _attempt(ask, c), candidates))
    return list(zip(candidates, outcomes, strict=True))


def _attempt[CandidateT, ResultT](
    ask: Callable[[CandidateT], ResultT], candidate: CandidateT
) -> ResultT | Exception:
    try:
        return ask(candidate)
    except Exception as exc:  # noqa: BLE001 — returned to the caller, not swallowed
        return exc


__all__ = ["DEFAULT_WORKERS", "analyze_all"]
