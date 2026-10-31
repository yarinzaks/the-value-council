"""Building the analyst layer, or deciding there is not one.

The five hybrid agents each take an optional analyzer. Constructing one
needs three things that can all be absent — an API key, a playbook file
and a working client — and none of their absences is a fault: a
checkout without a key is the normal state of a fresh clone, and a run
that cannot reach the layer must still trade on its quantitative screen
exactly as it did before there was one.

So this returns ``None`` per agent rather than raising, and says in the
log which it did. The alternative, a runner that refuses to start
because the optional half of one of twelve agents is unavailable, would
make the layer a dependency of the books rather than an addition to
them.

One client for all five, deliberately. It carries the spacing between
calls and the SDK's connection pool, and five of them would space
against five separate clocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.llm.claude_client import ClaudeClient, LLMError
from core.llm.mode import LLMMode
from core.logger import get_logger

logger = get_logger("core.llm.wiring")

#: Where each hybrid agent's playbook lives, relative to the repository
#: root. Nine to twelve thousand tokens each, and the thing the cached
#: system block carries.
AGENTS_ROOT = Path(__file__).resolve().parents[2] / "agents"


@dataclass(frozen=True)
class AnalystLayer:
    """The analyzers that could be built, and the mode they run in."""

    mode: LLMMode
    client: ClaudeClient | None
    #: Agent directory name -> the playbook text, for those that loaded.
    playbooks: dict[str, str]

    @property
    def available(self) -> bool:
        return self.client is not None and bool(self.playbooks)

    def build(self, agent: str, factory: Any) -> Any | None:
        """One analyzer, or ``None`` when this agent cannot have one.

        ``factory`` is the analyzer class; it is passed in rather than
        imported here so this module stays free of the five agent
        packages, which already import a good deal of the core.
        """
        if self.client is None:
            return None
        playbook = self.playbooks.get(agent)
        if playbook is None:
            return None
        return factory(client=self.client, playbook=playbook)


def _read_playbook(agent: str) -> str | None:
    path = AGENTS_ROOT / agent / "playbook.md"
    try:
        text = path.read_text()
    except OSError as exc:
        logger.warning(f"{agent}: no playbook to analyse from ({exc})")
        return None
    if not text.strip():
        logger.warning(f"{agent}: the playbook is empty")
        return None
    return text


def build_analyst_layer(
    agents: tuple[str, ...],
    *,
    mode: LLMMode = LLMMode.OFF,
) -> AnalystLayer:
    """Assemble the layer, degrading to nothing rather than failing.

    ``OFF`` short-circuits before the client is constructed, so a run
    that does not want the layer never touches the key or the network.
    """
    if mode is LLMMode.OFF:
        return AnalystLayer(mode=mode, client=None, playbooks={})

    try:
        client = ClaudeClient()
    except LLMError as exc:
        # The usual cause is no key, which is the normal state of a
        # checkout rather than a fault. The books trade on their screens.
        logger.warning(f"the analyst layer is unavailable ({exc}); running without it")
        return AnalystLayer(mode=LLMMode.OFF, client=None, playbooks={})

    playbooks = {}
    for agent in agents:
        text = _read_playbook(agent)
        if text is not None:
            playbooks[agent] = text

    if not playbooks:
        logger.warning("no playbooks loaded; the analyst layer has nothing to read")
        return AnalystLayer(mode=LLMMode.OFF, client=None, playbooks={})

    logger.info(
        f"analyst layer {mode.value} on {client.model_name} "
        f"for {len(playbooks)}/{len(agents)} agents"
    )
    return AnalystLayer(mode=mode, client=client, playbooks=playbooks)


__all__ = ["AGENTS_ROOT", "AnalystLayer", "build_analyst_layer"]
