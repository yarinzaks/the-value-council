"""The analyst has to be told what the book already holds.

Every memo on disk says WATCH (88 of 90) or REJECT (2). Not one says
BUY, HOLD, TRIM or SELL, although all five schemas allow them — and 78
of the 82 memos matching a live holding were written after the
purchase, up to three months after, while the book held the shares.
What they say on names the book owns: "so I watch from the side, with
no position, waiting for proof" (howard_marks/COLL, 16 shares); "so I
wait and observe and do not force a purchase" (howard_marks/GTM, 111
shares).

The model was not wrong, it was not told. Each of the five system
prompts promises it "the agent's current portfolio" and each of the
five call sites passed ``{"as_of": ...}`` — the date, and nothing else.
The holdings were a parameter of the very method that called the
analyzer.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import date

import pytest

from core.llm.held_context import HELD_POSITION_RUBRIC, portfolio_state_for

AS_OF = date(2026, 10, 31)


@dataclass
class Held:
    """The shape of ``core.backtest.strategy_runner.HeldPosition``."""

    ticker: str
    shares: float
    entry_price: float
    entry_date: date
    current_price: float

    def days_held_at(self, as_of: date) -> int:
        return max(0, (as_of - self.entry_date).days)

    @property
    def return_pct(self) -> float | None:
        if self.entry_price <= 0 or self.current_price <= 0:
            return None
        return (self.current_price / self.entry_price - 1.0) * 100.0


#: howard_marks/GTM, the worst of the real cases: 111 shares bought
#: 2026-08-07, and a memo dated 2026-10-31 saying it holds none.
GTM = Held("GTM", 111.0, 3.2134, date(2026, 8, 7), 3.0512)


class TestWhatTheAnalystIsTold:
    def test_a_held_name_is_reported_as_held(self) -> None:
        state = portfolio_state_for("GTM", AS_OF, {"GTM": GTM})
        pos = state["position"]
        assert pos["known"] is True
        assert pos["held"] is True
        assert pos["shares"] == 111.0
        assert pos["entry_price"] == pytest.approx(3.2134)
        assert pos["entry_date"] == "2026-08-07"
        assert pos["current_price"] == pytest.approx(3.0512)
        assert pos["days_held"] == 85
        assert pos["unrealised_return_pct"] == pytest.approx(-5.05, abs=0.01)
        assert state["names_held"] == 1
        assert state["as_of"] == "2026-10-31"

    def test_a_name_the_book_does_not_hold_says_so(self) -> None:
        state = portfolio_state_for("OTHER", AS_OF, {"GTM": GTM})
        assert state["position"] == {"known": True, "held": False}
        # Still reported, because "I hold 1 name and not this one" is a
        # different fact from "I hold nothing".
        assert state["names_held"] == 1

    def test_no_book_at_all_is_unknown_and_not_absent(self) -> None:
        """A backtest sweep has no live book.

        Reporting that as ``held: false`` would tell the model something
        untrue — it does not know — and a memo asserting "I hold no
        position" is exactly what this change exists to stop.
        """
        state = portfolio_state_for("GTM", AS_OF, None)
        assert state["position"] == {"known": False}
        assert "held" not in state["position"]
        assert "names_held" not in state

    def test_an_empty_book_is_known_and_empty(self) -> None:
        state = portfolio_state_for("GTM", AS_OF, {})
        assert state["position"] == {"known": True, "held": False}
        assert state["names_held"] == 0

    def test_the_ticker_matches_case_insensitively(self) -> None:
        assert portfolio_state_for("gtm", AS_OF, {"GTM": GTM})["position"]["held"]

    def test_a_same_day_purchase_is_zero_days_not_negative(self) -> None:
        fresh = Held("X", 1.0, 10.0, AS_OF, 10.0)
        assert portfolio_state_for("X", AS_OF, {"X": fresh})["position"]["days_held"] == 0

    def test_an_unusable_basis_reports_no_return_rather_than_a_number(self) -> None:
        broken = Held("X", 1.0, 0.0, date(2026, 1, 1), 10.0)
        pos = portfolio_state_for("X", AS_OF, {"X": broken})["position"]
        assert pos["unrealised_return_pct"] is None
        assert pos["held"] is True

    def test_a_strategy_may_add_its_own_fields(self) -> None:
        """Marks sends its market posture and always did."""
        state = portfolio_state_for("GTM", AS_OF, {"GTM": GTM}, posture="defensive")
        assert state["posture"] == "defensive"
        assert state["position"]["held"] is True

    def test_an_extra_field_may_not_overwrite_the_position(self) -> None:
        with pytest.raises(ValueError, match="position"):
            portfolio_state_for("GTM", AS_OF, {"GTM": GTM}, position="nonsense")
        with pytest.raises(ValueError, match="names_held"):
            portfolio_state_for("GTM", AS_OF, {"GTM": GTM}, names_held=99)
        # `as_of` is positional, so the language refuses this one before
        # the function body runs. Asserted so that making it
        # keyword-only later does not quietly open the hole.
        with pytest.raises(TypeError):
            portfolio_state_for("GTM", AS_OF, None, as_of="yesterday")


class TestTheRubric:
    def test_it_names_the_field_the_block_actually_carries(self) -> None:
        state = portfolio_state_for("GTM", AS_OF, {"GTM": GTM})
        assert "portfolio_state.position" in HELD_POSITION_RUBRIC
        for key in ("held", "known"):
            assert key in state["position"]
            assert f"`{key}:" in HELD_POSITION_RUBRIC or key in HELD_POSITION_RUBRIC

    def test_it_forbids_the_sentence_the_memos_actually_wrote(self) -> None:
        assert "watching from the sidelines" in HELD_POSITION_RUBRIC
        assert "WATCH is wrong for a name you hold" in HELD_POSITION_RUBRIC

    def test_it_does_not_tell_the_model_which_way_to_decide(self) -> None:
        """The bug was the missing question, not a missing verdict.

        A rubric that said "a held name is a HOLD" would replace one
        false memo with another.
        """
        lowered = HELD_POSITION_RUBRIC.lower()
        for pushy in ("you should buy", "you must hold", "always hold", "prefer buy"):
            assert pushy not in lowered


# ---- the drift guard -----------------------------------------------------
#
# Five strategies and five prompts. One of them regressing, or a sixth
# agent arriving without this, puts the product back where it was: a
# memo about a position the book holds, written as though it held none.
# Source inspection rather than five harnesses, in the shape of the
# mode-mapping guard in core/live/tests/test_notify.py.

STRATEGIES = [
    "agents.fisher.quality_growth",
    "agents.klarman.margin_of_safety",
    "agents.buffett.wonderful_business",
    "agents.lynch.garp",
    "agents.marks.cycle_value",
]

ANALYSTS = [
    "agents.fisher.scuttlebutt",
    "agents.klarman.downside",
    "agents.buffett.moat_analyzer",
    "agents.lynch.category_classifier",
    "agents.marks.second_level",
]


@pytest.mark.parametrize("module_name", STRATEGIES)
def test_every_strategy_sends_the_book_to_its_analyst(module_name: str) -> None:
    import importlib

    source = inspect.getsource(importlib.import_module(module_name))
    assert "portfolio_state_for(" in source, (
        f"{module_name} builds its own portfolio_state; it has to go through "
        "core.llm.held_context so the five cannot drift"
    )
    assert 'portfolio_state={"as_of"' not in source, (
        f"{module_name} still sends the date alone — the bug this guards"
    )
    # The holdings have to reach the call, not just the module.
    assert "held=held" in source, f"{module_name} does not thread `held` through"


@pytest.mark.parametrize("module_name", ANALYSTS)
def test_every_analyst_prompt_carries_the_rubric(module_name: str) -> None:
    import importlib

    module = importlib.import_module(module_name)
    prompts = [
        value
        for name, value in vars(module).items()
        if name.endswith("_SYSTEM_PROMPT") and isinstance(value, str)
    ]
    assert prompts, f"{module_name} has no system prompt to check"
    for prompt in prompts:
        assert HELD_POSITION_RUBRIC in prompt, (
            f"{module_name}'s system prompt promises the model the portfolio "
            "and does not tell it what a held position means"
        )
