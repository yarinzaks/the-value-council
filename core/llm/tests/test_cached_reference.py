"""Reference material rides in a cached system block, candidates do not.

An agent's playbook is nine to twelve thousand tokens and identical for
every candidate in a run — eight calls for Buffett, thirty for Lynch.
Carrying it afresh each time was most of what a shadow run across the
five hybrid agents cost.

The cache matches on an exact prefix, so the whole saving rests on one
property: nothing that varies per candidate may be inside the cached
block. A single ticker leaking in turns every call after the first into
a cache miss *and* a cache write, which costs more than sending it
plainly would have.
"""

from __future__ import annotations

import pytest

from core.llm.claude_client import CACHE_CONTROL, _system_blocks


class TestWithoutReferenceNothingChanges:
    def test_none_sends_the_plain_string(self) -> None:
        """A caller that never passes one sends what it always sent."""
        assert _system_blocks("SYSTEM", None) == "SYSTEM"

    def test_an_empty_reference_is_the_same_as_none(self) -> None:
        # An empty cached block would be a breakpoint around nothing.
        assert _system_blocks("SYSTEM", "") == "SYSTEM"


class TestWithReference:
    @pytest.fixture
    def blocks(self) -> list[dict[str, object]]:
        return _system_blocks("SYSTEM", "PLAYBOOK")

    def test_the_stable_material_comes_first(self, blocks: list) -> None:
        """The cache matches a prefix, so order is the whole mechanism."""
        assert blocks[0]["text"] == "SYSTEM"
        assert blocks[1]["text"] == "PLAYBOOK"

    def test_only_the_reference_is_marked_cacheable(self, blocks: list) -> None:
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == dict(CACHE_CONTROL)

    def test_every_block_is_text(self, blocks: list) -> None:
        assert [b["type"] for b in blocks] == ["text", "text"]

    def test_the_marker_is_not_shared_between_calls(self) -> None:
        """A dict handed out by reference is one a caller can mutate."""
        first = _system_blocks("S", "P")[1]["cache_control"]
        first["type"] = "tampered"

        assert _system_blocks("S", "P")[1]["cache_control"] == {"type": "ephemeral"}


class TestTheSplitIsWhatPaysForIt:
    def test_the_candidate_never_reaches_the_cached_block(self) -> None:
        """Measured the other way round: this is the failure mode.

        If per-candidate text were appended to the reference, every call
        after the first would both miss the cache and pay to write a new
        one — strictly worse than not caching at all.
        """
        blocks = _system_blocks("SYSTEM", "PLAYBOOK")

        assert "ZZZZ" not in str(blocks)

    def test_reference_is_keyword_only_on_the_client(self) -> None:
        """So it cannot be passed positionally where prompt belongs."""
        import inspect

        from core.llm.claude_client import ClaudeClient

        param = inspect.signature(ClaudeClient.complete_memo).parameters["reference"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
