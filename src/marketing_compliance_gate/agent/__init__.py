"""The D6 Marketing Compliance ADK agent package.

Exposes the ADK-convention ``root_agent`` (discovered by ``adk web`` / Agent Runtime) plus the
explicit factories used by the API and tests. Everything here is import-safe without ADK
installed: the ADK-heavy construction is quarantined behind lazy imports (SPEC §4), so importing
this package under the local / on-prem / test profile never requires ``google.adk``.
"""

from __future__ import annotations

from .agent_card import agent_card_document, build_agent_card
from .root_agent import build_root_agent, to_a2a_app

# NB: the ADK-convention ``root_agent`` instance lives in ``agent.root_agent`` and is NOT
# re-exported here on purpose. Re-binding the name ``root_agent`` in this package namespace
# would shadow the ``root_agent`` *submodule*, so ``from agent import root_agent`` would hand
# back the lazy proxy (and touching any attribute would eagerly build it, needing ADK).
__all__ = [
    "build_root_agent",
    "to_a2a_app",
    "build_agent_card",
    "agent_card_document",
]
