"""What the analyst layer is told about a position the book already owns.

The bug this exists to end
~~~~~~~~~~~~~~~~~~~~~~~~~

Every memo on disk records ``decision: "WATCH"`` (88 of 90) or
``"REJECT"`` (2). Not one says BUY, HOLD, TRIM or SELL, although every
one of the five schemas allows them — and 78 of the 82 memos that
match a live holding were written *after* the purchase, up to three
months after, while the book held the shares.

What they say, on names the book owns:

    howard_marks/COLL,  16 shares   "so I watch from the side, with no
                                     position, waiting for proof"
    howard_marks/GTM,  111 shares   "so I wait and observe and do not
                                     force a purchase"
    howard_marks/CALM,   7 shares   "the name stays on the watchlist"

The model was not wrong. It was not told. Each of the five system
prompts promises it "(3) the agent's current portfolio", and each of
the five call sites then passed this::

    portfolio_state={"as_of": as_of.isoformat()}

— the date, and nothing else. Marks passed its market posture too. So
the analyst was handed a candidate with no indication that the book was
already long it, and answered the only question it could see: should I
buy this. Asked that, "I am watching from the side with no position" is
a correct answer and a false statement.

The holdings were never out of reach. ``select()`` takes
``held: Mapping[str, HeldPosition]`` on all five strategies, the live
runner fills it from the real book at ``runner.py`` line 1010, and
``_llm_filter`` is called from inside that same method. The parameter
was simply not threaded the last two frames.

Why a module rather than five dicts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Five strategies asking the same question of the same object is one
claim in five places, and the five would disagree within a month —
which is the reason ``notify.py`` reads the journal rather than
restating it. The shape of the answer is written once, here.

What the analyst is NOT told
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The same line ``HeldPosition`` itself draws: size, basis, age and mark
for the name under analysis, and a count of how many names the book
holds. No cash, no weights, no other position's basis. A doctrine needs
"how long have I held this, at what price, and how has it done" to
express an exit; it has no business reaching into what the runner owns.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol, runtime_checkable

__all__ = ["HeldLike", "portfolio_state_for"]


@runtime_checkable
class HeldLike(Protocol):
    """The part of ``HeldPosition`` this module reads.

    A Protocol rather than an import: ``HeldPosition`` lives in
    ``core.backtest.strategy_runner``, which the agents already import
    alongside this module, and binding the two together buys nothing
    but a cycle to trip over later.
    """

    ticker: str
    shares: float
    entry_price: float
    entry_date: date
    current_price: float

    def days_held_at(self, as_of: date) -> int: ...

    @property
    def return_pct(self) -> float | None: ...


def portfolio_state_for(
    ticker: str,
    as_of: date,
    held: Mapping[str, HeldLike] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """The ``portfolio_state`` block for one candidate's memo.

    ``held`` is the mapping ``select()`` received. ``None`` means the
    caller genuinely has no book — a backtest sweep, or a strategy
    invoked outside the live runner — and is reported as unknown rather
    than as "not held", because those are different facts and the model
    should not be told the second when the first is true.

    ``extra`` carries whatever a strategy adds of its own; Marks sends
    its market posture. Keys cannot collide with the ones below.

    >>> portfolio_state_for("GTM", date(2026, 10, 31), None)["position"]
    {'known': False}
    """
    state: dict[str, Any] = {"as_of": as_of.isoformat()}

    if held is None:
        # Say "I cannot see the book", not "the book is empty".
        state["position"] = {"known": False}
    else:
        pos = held.get(ticker) or held.get(ticker.upper())
        if pos is None:
            state["position"] = {"known": True, "held": False}
        else:
            state["position"] = {
                "known": True,
                "held": True,
                "shares": round(float(pos.shares), 4),
                "entry_price": round(float(pos.entry_price), 4),
                "entry_date": pos.entry_date.isoformat(),
                "current_price": round(float(pos.current_price), 4),
                "days_held": pos.days_held_at(as_of),
                # None where the basis is unusable; the property
                # already decides that and this does not second-guess it.
                "unrealised_return_pct": (
                    None if pos.return_pct is None else round(pos.return_pct, 2)
                ),
            }
        state["names_held"] = len(held)

    for key, value in extra.items():
        if key in state:
            raise ValueError(f"{key!r} would overwrite the portfolio state")
        state[key] = value
    return state


#: The paragraph every analyst prompt needs once it is handed a real
#: position. Written once so the five cannot drift, and worded against
#: what the block above actually contains.
#:
#: It does not tell the model which verdict to reach. It tells it which
#: question it is being asked, which is the thing that was missing: a
#: held name is a hold-or-sell question, and every one of the five
#: schemas already allows HOLD, TRIM and a sell.
HELD_POSITION_RUBRIC = """\

## The position you already have in this name

`portfolio_state.position` tells you whether this book already owns the
name. Read it before you decide anything, and let it set the question
you are answering:

- `held: true` — you own it. The question is whether to KEEP it, add to
  it, trim it, or sell it, NOT whether to open it. Your thesis must
  read as the view of someone who owns the shares, and must not say you
  are watching from the sidelines, waiting for proof, or holding no
  position; you are long. Use the size, basis, age and unrealised
  return given there. A decision of WATCH is wrong for a name you hold:
  choose HOLD, ADD, TRIM or the sell verdict your schema allows.
- `held: false` — you do not own it, and this is the buy-or-wait
  question. WATCH and REJECT are available as they always were.
- `known: false` — you cannot see the book on this call. Say what you
  would do on the evidence in front of you and do not assert either way
  whether you hold the name.
"""
