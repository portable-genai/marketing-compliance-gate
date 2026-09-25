"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool. Both come from response headers the kit
emits (``install_answer_provenance`` in ``api/app.py``) for whatever the model adapters NOTED as
they called. Before a request is answered the pill shows ``generator_model`` from ``/healthz``,
so that value must be the model the bound adapter calls, never one a configuration flag names
while the adapter calls another.

No call in this service attaches an online search tool, so nothing here notes a search in
production. The route is still proved to carry ``X-Search-Used`` the day one does, by binding a
narrator that notes one on the real app.

The console calls this service directly (cross-origin when standalone), so the two headers are
also proved to be listed in the CORS ``Access-Control-Expose-Headers``: a browser hides every
header that list leaves out, and the pills would stay on the configured model forever.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from hex_service_kit import provenance
from tests.conftest import LOOPBACK_PEER, _settings
from tests.fixtures.fake_genai import FakeGenaiClient, install_fake_genai

from marketing_compliance_gate.adapters.gcp.gemini_llm import GeminiLLMAdapter
from marketing_compliance_gate.adapters.local.llm import LocalDeterministicLLMAdapter
from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps, security
from marketing_compliance_gate.config import Container, ModelSettings, Settings
from marketing_compliance_gate.domain.models import LlmMessage, LlmRequest, LlmResponse

ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"

_REVIEW = {
    "asset": {
        "asset_type": "creative",
        "title": "Submitted asset",
        "body": "Get guaranteed returns of 4.10% with zero risk-free worry!",
        "market": "SG",
        "vertical": "banking",
    }
}


def _client(container: Container, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for module in (deps, security, app_module):
        monkeypatch.setattr(module, "get_container", lambda: container)
    return TestClient(app_module.app, client=LOOPBACK_PEER)


@pytest.fixture
def local_container() -> Container:
    return Container(_settings("local"))


def test_a_narrated_review_names_the_model_generator_model_reports(
    local_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under ``local`` the stub answered, and the pill names it exactly as /healthz did."""
    client = _client(local_container, monkeypatch)
    reply = client.post("/v1/review", json=_REVIEW)
    assert reply.status_code == 200, reply.text
    assert reply.headers[ANSWERED_BY] == local_container.settings.generator_model
    assert reply.headers[ANSWERED_BY] == "deterministic-offline-stub"
    assert SEARCH_USED not in reply.headers


def test_a_route_that_called_no_model_names_none(
    local_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing noted, nothing sent: the pill never invents a model nobody called."""
    reply = _client(local_container, monkeypatch).get("/healthz")
    assert reply.status_code == 200, reply.text
    assert ANSWERED_BY not in reply.headers
    assert SEARCH_USED not in reply.headers


class _SearchingNarrator(LocalDeterministicLLMAdapter):
    """The real offline narrator, plus what an adapter that searched would note as it called."""

    def generate(self, request: LlmRequest) -> LlmResponse:
        response = super().generate(request)
        provenance.note_model("fake-searching-model")
        provenance.note_search()
        return response


def test_a_call_that_searched_says_so_and_the_next_request_starts_clean(
    local_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(local_container, monkeypatch)
    local_container.__dict__["llm"] = _SearchingNarrator(local_container.settings)
    reply = client.post("/v1/review", json=_REVIEW)
    assert reply.status_code == 200, reply.text
    assert reply.headers[SEARCH_USED] == "true"
    assert "fake-searching-model" in reply.headers[ANSWERED_BY]

    local_container.__dict__["llm"] = LocalDeterministicLLMAdapter(local_container.settings)
    after = client.post("/v1/review", json=_REVIEW)
    assert SEARCH_USED not in after.headers, "one request's search leaked into the next"
    assert after.headers[ANSWERED_BY] == "deterministic-offline-stub"


def test_a_cross_origin_console_is_allowed_to_read_both_headers(
    local_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A header the CORS response does not expose is invisible to the console's fetch.

    The allowlist is resolved once, when the app module is imported, from the profile of that
    process; the gate imports it with no profile, which allows no origin. So the console's own
    dev origin is put on the REAL middleware for this test and the stack rebuilt, rather than
    trusting whichever profile happened to import the module first.
    """
    cors = [m for m in app_module.app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1, "the service has no CORS middleware to expose anything through"
    origin = app_module._DEV_ORIGINS[0]
    monkeypatch.setitem(cors[0].kwargs, "allow_origins", [origin])
    monkeypatch.setattr(app_module.app, "middleware_stack", None)
    reply = _client(local_container, monkeypatch).post(
        "/v1/review", json=_REVIEW, headers={"Origin": origin}
    )
    assert reply.status_code == 200, reply.text
    assert reply.headers.get("access-control-allow-origin") == origin
    exposed = {
        name.strip().lower()
        for value in reply.headers.get_list("access-control-expose-headers")
        for name in value.split(",")
    }
    assert {ANSWERED_BY, SEARCH_USED} <= exposed, exposed
    assert {ANSWERED_BY, SEARCH_USED} <= {
        name.lower() for name in cors[0].kwargs.get("expose_headers", ())
    }, "the CORS middleware itself does not list both headers"


def _gcp_adapter(monkeypatch: pytest.MonkeyPatch) -> tuple[GeminiLLMAdapter, FakeGenaiClient]:
    """The managed adapter on a faked client, its two model settings made DISTINCT.

    Distinct so that agreement between what was called, what was noted and what
    ``generator_model`` reports cannot come from every setting being the same string.
    """
    install_fake_genai(monkeypatch)
    settings = dataclasses.replace(
        _settings("gcp"),
        models=ModelSettings(reasoning="the-reasoning-model", triage="the-triage-model"),
    )
    adapter = GeminiLLMAdapter(settings)
    fake = FakeGenaiClient()
    adapter._client = fake
    return adapter, fake


def test_the_gemini_adapter_notes_the_model_it_called(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _gcp_adapter(monkeypatch)
    request = LlmRequest(messages=(LlmMessage(role="user", content="Summarise."),))
    with provenance.scope() as record:
        adapter.generate(request)
        adapter.generate(dataclasses.replace(request, model="an-explicit-model"))
        adapter.classify("text", ["retail", "banking"])
    called = [model for model, _ in fake.calls]
    assert called == ["the-reasoning-model", "an-explicit-model", "the-triage-model"]
    assert record.models == called, "a model answered that the pill would never name"
    assert record.search_used is False, "no call here attaches a search tool"


def test_generator_model_is_the_model_the_gemini_adapter_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The latent false banner: a flag that moved the pill but not the model that answered.

    ``generator_model`` once named ``models.hard_reasoning`` when a hard-reasoning flag was
    set, while this adapter called ``request.model or models.reasoning`` and never read the
    flag. Measured here against the call itself.
    """
    adapter, fake = _gcp_adapter(monkeypatch)
    adapter.generate(LlmRequest(messages=(LlmMessage(role="user", content="Summarise."),)))
    assert fake.calls[-1][0] == adapter._settings.generator_model == "the-reasoning-model"


def test_the_hard_reasoning_flag_does_not_exist() -> None:
    fields = {f.name for f in dataclasses.fields(ModelSettings)}
    assert "use_hard_reasoning" not in fields
    assert "hard_reasoning" not in fields, "a model nothing calls is a model a pill could name"
    repo = Path(__file__).resolve().parents[2]
    settings_file = (repo / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "hard_reasoning" not in settings_file
    for source in sorted((repo / "src").rglob("*.py")):
        assert "hard_reasoning" not in source.read_text(encoding="utf-8"), source
    assert isinstance(Settings.load("config/settings.yaml").models, ModelSettings)
