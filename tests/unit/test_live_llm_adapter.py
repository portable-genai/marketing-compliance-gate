"""The ``live`` profile: the local open-weight model behind the LLM port, proved offline.

Every call goes through the shared ``hex_service_kit.localmodel`` client with a FAKE transport,
so this runs in the normal offline suite with no model server. What is proved: the request maps
onto chat messages with its temperature unchanged, a fenced and then schema-invalid answer is
retried until it validates, the response names the model that answered, the kit's two failure
types become domain errors, a review whose narrator is down still answers with the
deterministic fallback narrative (narration is best-effort here, as it is for Gemini), and the
container builds every port under ``live``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit.localmodel import LocalModelClient, LocalModelSettings
from tests.conftest import LOOPBACK_PEER, _settings

from marketing_compliance_gate.adapters.live.llm import LocalModelLLMAdapter
from marketing_compliance_gate.config import Container
from marketing_compliance_gate.domain.errors import ModelOutputError, ModelUnavailableError
from marketing_compliance_gate.domain.models import LlmMessage, LlmRequest

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "used_rule_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "used_rule_ids"],
}
_ANSWERED_BY = "fake-org/fake-local-model"


class _FakeTransport:
    """Answers each POST with the next scripted content and records every request body."""

    def __init__(self, *answers: str, usage: dict[str, int] | None = None) -> None:
        self._answers = list(answers)
        self._usage = usage
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        self.bodies.append(json.loads(body))
        reply: dict[str, Any] = {
            "model": _ANSWERED_BY,
            "choices": [{"message": {"role": "assistant", "content": self._answers.pop(0)}}],
        }
        if self._usage is not None:
            reply["usage"] = self._usage
        return json.dumps(reply).encode()


def _refused(url: str, body: bytes | None, timeout: float) -> bytes:
    raise ConnectionRefusedError("connection refused")


def _adapter(transport: Any) -> LocalModelLLMAdapter:
    client = LocalModelClient(LocalModelSettings(url="http://127.0.0.1:1/x"), transport=transport)
    return LocalModelLLMAdapter(_settings("live"), client=client)


def _request(**overrides: Any) -> LlmRequest:
    fields: dict[str, Any] = {
        "messages": (LlmMessage(role="user", content="Summarise the findings."),),
        "system_instruction": "You narrate compliance findings.",
        "temperature": 0.2,
        "max_output_tokens": 400,
        "response_schema": _SCHEMA,
    }
    fields.update(overrides)
    return LlmRequest(**fields)


def test_a_fenced_then_invalid_answer_is_retried_until_it_validates() -> None:
    transport = _FakeTransport(
        '```json\n{"summary": "half an answer"}\n```',  # fenced, and missing `used_rule_ids`
        '{"summary": "Two rules failed.", "used_rule_ids": ["sg-banking-01"]}',
        usage={"prompt_tokens": 50, "completion_tokens": 10},
    )

    response = _adapter(transport).generate(_request())

    assert len(transport.bodies) == 2, "the invalid first answer must be asked again"
    assert "used_rule_ids" in transport.bodies[1]["messages"][-1]["content"]
    assert json.loads(response.text) == {
        "summary": "Two rules failed.",
        "used_rule_ids": ["sg-banking-01"],
    }
    assert response.model == _ANSWERED_BY
    assert (response.usage.input_tokens, response.usage.output_tokens) == (100, 20)


def test_the_request_maps_onto_chat_messages_with_its_temperature_unchanged() -> None:
    transport = _FakeTransport("A plain narrative.")

    response = _adapter(transport).generate(_request(response_schema=None))

    body = transport.bodies[0]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 400
    assert body["messages"] == [
        {"role": "system", "content": "You narrate compliance findings."},
        {"role": "user", "content": "Summarise the findings."},
    ]
    assert response.text == "A plain narrative."
    # MLX reports no usage; LlmResponse requires one, so it reads as zeros, never invented counts.
    assert (response.usage.input_tokens, response.usage.output_tokens) == (0, 0)


def test_an_answer_that_never_validates_is_a_model_output_error() -> None:
    transport = _FakeTransport("not json", "still not json", "nope")
    with pytest.raises(ModelOutputError):
        _adapter(transport).generate(_request())
    assert len(transport.bodies) == 3


def test_an_unreachable_server_is_a_model_unavailable_error_naming_the_recipe() -> None:
    with pytest.raises(ModelUnavailableError, match="mlx_vlm.server"):
        _adapter(_refused).generate(_request())


def test_classify_coerces_the_answer_to_a_label() -> None:
    transport = _FakeTransport("Label: Banking.")
    assert _adapter(transport).classify("text", ["retail", "banking"]) == "banking"
    assert transport.bodies[0]["temperature"] == 0.0


def test_the_container_builds_every_port_under_live() -> None:
    settings = _settings("live")
    container = Container(settings)
    assert settings.adapters, "no port is bound, so this test would prove nothing"
    for port in settings.adapters:
        assert getattr(container, port) is not None, port
    assert isinstance(container.llm, LocalModelLLMAdapter)


def test_the_banner_names_the_local_model_under_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODEL", _ANSWERED_BY)
    settings = _settings("live")
    assert settings.runtime == "local"
    assert settings.generator_model == _ANSWERED_BY


def test_a_review_with_the_live_model_down_keeps_the_deterministic_narrative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_compliance_gate.api import app as app_module
    from marketing_compliance_gate.api import deps, security

    container = Container(_settings("live"))
    container.__dict__["llm"] = _adapter(_refused)
    for module in (deps, security, app_module):
        monkeypatch.setattr(module, "get_container", lambda: container)
    client = TestClient(app_module.app, client=LOOPBACK_PEER)

    reply = client.post(
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
    assert reply.json()["summary"].startswith("Compliance review of creative 'Submitted asset'")
