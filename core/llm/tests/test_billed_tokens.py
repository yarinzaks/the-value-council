"""What a call was billed, against what the log used to record.

The API reports cached input in its own fields rather than folding it
into ``input_tokens``, so a record of a call that sent a playbook as
cacheable reference material carried the per-candidate remainder and
nothing else. Measured on a real call: 31 input, 107 output, 14,879
read from cache — a page summing the first two showed 138 against a
true 14,910.
"""

from __future__ import annotations

from core.llm.memo_log import MemoRecord


def a_record(**kw: object) -> MemoRecord:
    base: dict[str, object] = {
        "agent": "warren_buffett",
        "ticker": "HOG",
        "schema": "BuffettMemo",
        "timestamp": "2026-10-31T12:25:23+00:00",
        "model": "claude-opus-5-5",
    }
    base.update(kw)
    return MemoRecord(**base)  # type: ignore[arg-type]


class TestBilledInputTokens:
    def test_the_real_call_that_exposed_this(self) -> None:
        record = a_record(input_tokens=31, output_tokens=107, cache_read_tokens=14_879)

        assert record.billed_input_tokens == 14_910
        assert record.input_tokens == 31, "the uncached remainder is still itself"

    def test_a_cache_write_counts_too(self) -> None:
        """The first candidate of a run pays to write what the rest read."""
        record = a_record(input_tokens=31, cache_write_tokens=14_622)

        assert record.billed_input_tokens == 14_653

    def test_a_call_with_no_cache_is_just_its_input(self) -> None:
        assert a_record(input_tokens=1_189).billed_input_tokens == 1_189

    def test_a_record_from_before_the_fields_existed_still_answers(self) -> None:
        """Every memo already on disk lacks them."""
        assert a_record(input_tokens=30, output_tokens=23).billed_input_tokens == 30

    def test_a_record_with_nothing_recorded_is_zero_not_an_error(self) -> None:
        assert a_record().billed_input_tokens == 0


class TestTheRecordStillRoundTrips:
    def test_the_new_fields_survive_to_dict(self) -> None:
        payload = a_record(cache_read_tokens=14_879).to_dict()

        assert payload["cache_read_tokens"] == 14_879
        assert payload["cache_write_tokens"] is None

    def test_an_old_payload_still_loads(self) -> None:
        old = {
            "agent": "warren_buffett",
            "ticker": "KO",
            "schema": "BuffettMemo",
            "timestamp": "2026-10-30T15:34:00+00:00",
            "model": "claude-opus-5-5",
            "input_tokens": 1_189,
            "output_tokens": 102,
        }

        assert MemoRecord(**old).billed_input_tokens == 1_189
