"""Sampling is decided per call: pinned where output is compared, free where it is prose.

**History.** This file began as ``test_grounded_requests_do_not_sample.py``. On 2026-08-26, in
``cdd-sow-research``, two runs of one identical case minutes apart returned different scores,
because the shared request builder defaulted to ``temperature=0.2`` and every grounded call
sampled. The response then was to pin the request TYPE's default to 0.0, so a call site that
omitted the parameter could not sample.

**What changed (owner decision, 2026-09-23).** A blanket pin is the wrong default in both
directions. It pins prose nobody compares, and a model that rejects the parameter outright
(Opus 5, Fable 5) cannot be called at all while every request carries one. So the type now
defaults to ``None``, which SENDS NO TEMPERATURE, and the decision moved to the call site:

* **pinned (0.0)**: output that is extracted, classified, scored or compared. Here that is the
  two ``classify`` methods, whose label is matched against a fixed set.
* **free (None, omitted on the wire)**: drafting, narration, summaries, explanations, judges.
  Here that is both narrators (the review summary and the green-claim narrative, prose over an
  outcome the deterministic engines already decided) and the ADK agent's config.

**Temperature 0 is not a promise of determinism, and nothing here asserts one.** A hosted model
can still vary across batching and model revisions.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit.localmodel import LocalModelClient, LocalModelSettings
from tests.conftest import LOOPBACK_PEER, _settings
from tests.fixtures.fake_genai import FakeGenaiClient, install_fake_genai

from marketing_compliance_gate.adapters.gcp.gemini_llm import GeminiLLMAdapter
from marketing_compliance_gate.adapters.live.llm import LocalModelLLMAdapter
from marketing_compliance_gate.adapters.local.llm import LocalDeterministicLLMAdapter
from marketing_compliance_gate.agent import root_agent
from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps, security
from marketing_compliance_gate.api.deps import make_substantiation_service
from marketing_compliance_gate.config import Container
from marketing_compliance_gate.domain.identity import Principal
from marketing_compliance_gate.domain.models import (
    AssetType,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    Market,
    MarketingAsset,
    Vertical,
)


class _Recording(LocalDeterministicLLMAdapter):
    """The real offline narrator, recording every request a service hands it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[LlmRequest] = []

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return super().generate(request)


@pytest.fixture
def recording() -> tuple[Container, _Recording]:
    container = Container(_settings("local"))
    narrator = _Recording(container.settings)
    container.__dict__["llm"] = narrator
    return container, narrator


def test_the_request_type_sends_no_temperature_unless_a_call_site_pins_one() -> None:
    assert LlmRequest.__dataclass_fields__["temperature"].default is None


def test_the_review_summary_is_free(
    recording: tuple[Container, _Recording], monkeypatch: pytest.MonkeyPatch
) -> None:
    container, narrator = recording
    for module in (deps, security, app_module):
        monkeypatch.setattr(module, "get_container", lambda: container)
    reply = TestClient(app_module.app, client=LOOPBACK_PEER).post(
        "/v1/review",
        json={
            "asset": {
                "asset_type": "creative",
                "title": "Submitted asset",
                "body": "Get guaranteed returns of 4.10% with zero risk-free worry!",
                "market": "SG",
                "vertical": "banking",
            }
        },
    )
    assert reply.status_code == 200, reply.text
    assert narrator.requests, "the review narrated nothing, so this would prove nothing"
    assert [r.temperature for r in narrator.requests] == [None] * len(narrator.requests)


def test_the_green_claim_narrative_is_free(recording: tuple[Container, _Recording]) -> None:
    container, narrator = recording
    make_substantiation_service(container).assess(
        MarketingAsset(
            id="camp-green-au-001",
            asset_type=AssetType.CAMPAIGN,
            title="Green campaign",
            body="Bank with a carbon neutral balance sheet. Offset details on request.",
            market=Market.AU,
            vertical=Vertical.BANKING,
            fields={"substantiation_ref": "dms://example.test/pack-1"},
        ),
        Principal(subject="demo.reviewer@brand.example", tenant="demo-brand", source="test"),
        as_of=date(2026, 8, 5),
    )
    assert narrator.requests, "the assessment narrated nothing, so this would prove nothing"
    assert [r.temperature for r in narrator.requests] == [None] * len(narrator.requests)


def _gemini(monkeypatch: pytest.MonkeyPatch) -> tuple[GeminiLLMAdapter, FakeGenaiClient]:
    install_fake_genai(monkeypatch)
    adapter = GeminiLLMAdapter(_settings("gcp"))
    fake = FakeGenaiClient()
    adapter._client = fake
    return adapter, fake


def test_gemini_omits_a_free_temperature_and_sends_a_pinned_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, fake = _gemini(monkeypatch)
    request = LlmRequest(messages=(LlmMessage(role="user", content="Summarise."),))
    adapter.generate(request)
    adapter.generate(LlmRequest(messages=request.messages, temperature=0.0))
    adapter.classify("text", ["retail", "banking"])
    free, pinned, label = (config for _, config in fake.calls)
    assert "temperature" not in free, "a free call sent a temperature; Opus 5 would refuse it"
    assert pinned["temperature"] == 0.0
    assert label["temperature"] == 0.0, "classification must stay pinned"


class _Transport:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        self.bodies.append(json.loads(body))
        reply = {"model": "m", "choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        return json.dumps(reply).encode()


def test_the_live_adapter_passes_a_free_temperature_through_as_absent() -> None:
    transport = _Transport()
    client = LocalModelClient(LocalModelSettings(url="http://127.0.0.1:1/x"), transport=transport)
    adapter = LocalModelLLMAdapter(_settings("live"), client=client)
    adapter.generate(LlmRequest(messages=(LlmMessage(role="user", content="Summarise."),)))
    adapter.classify("text", ["retail", "banking"])
    assert "temperature" not in transport.bodies[0]
    assert transport.bodies[1]["temperature"] == 0.0


def test_the_agent_config_does_not_pin_a_temperature() -> None:
    """Read from source: building the ADK agent needs the SDK, which the gate never installs."""
    tree = ast.parse(inspect.getsource(root_agent))
    configs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "GenerateContentConfig"
    ]
    assert configs, "the agent builds no generation config, so this would prove nothing"
    for call in configs:
        assert "temperature" not in {kw.arg for kw in call.keywords}
