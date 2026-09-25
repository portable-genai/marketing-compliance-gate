"""A stand-in for the two pieces of ``google-genai`` the Gemini adapter touches, offline.

The gate runs with no cloud SDK installed, so the managed adapter is driven here through a fake
``google.genai.types`` module and a fake client that records every ``generate_content`` call.
Nothing is asserted about the SDK itself; what is observable is what the ADAPTER hands it: the
model id it calls and the config keyword arguments it builds.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


class _Config:
    """Records the keyword arguments ``GenerateContentConfig`` was built with."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _types_module() -> ModuleType:
    types = ModuleType("google.genai.types")
    types.Content = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    types.Part = SimpleNamespace(from_text=lambda text: SimpleNamespace(text=text))  # type: ignore[attr-defined]
    types.GenerateContentConfig = _Config  # type: ignore[attr-defined]
    types.ThinkingConfig = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    types.ThinkingLevel = SimpleNamespace(LOW="LOW", HIGH="HIGH")  # type: ignore[attr-defined]
    return types


@dataclass
class FakeGenaiClient:
    """Answers every call with ``reply`` and records ``(model, config kwargs)`` per call."""

    reply: str = '{"summary": "narrated", "used_rule_ids": []}'
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def models(self) -> FakeGenaiClient:
        return self

    def generate_content(self, *, model: str, contents: Any, config: _Config) -> Any:
        self.calls.append((model, dict(config.kwargs)))
        return SimpleNamespace(text=self.reply, usage_metadata=None)


def install_fake_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``from google.genai import types`` resolve to the fake, for this test only."""
    types = _types_module()
    genai = ModuleType("google.genai")
    genai.types = types  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", sys.modules.get("google") or ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)
