"""The Model Armor adapter allows ONLY a complete, clean screen, and fails closed otherwise.

The adapter speaks Model Armor's REST API, whose JSON carries each enum by its member NAME.
The verdict is allowed only when ``sanitizationResult.filterMatchState`` is ``NO_MATCH_FOUND``
AND ``sanitizationResult.invocationResult`` is ``SUCCESS``. The mapping this replaced was
``allowed = state != "MATCH_FOUND"`` (or "no per-filter findings" when the state was absent),
which allowed ``FILTER_MATCH_STATE_UNSPECIFIED``, an empty or missing ``sanitizationResult``,
and a ``PARTIAL`` or ``FAILURE`` screen, where a skipped filter reports ``NO_MATCH_FOUND``.
Padding a prompt past the prompt-injection filter's token limit got it through unscreened.

This module tests at two levels:

* **SDK-free** (always runs, including the offline gate's dev-lock ``make test``): responses
  are the REST JSON built from ``_MirrorState`` / ``_MirrorInvocation``, stdlib enums with the
  real member names and numbers, and driven through ``screen()`` with a fake HTTP client.
* **Real SDK** (runs where ``google-cloud-modelarmor`` is installed, skips on the SDK-free
  profile): responses are real ``modelarmor_v1`` messages serialized to the REST wire JSON by
  the SDK's own ``to_json``, so the field and enum spellings are the service's, not ours. The
  first of these also pins the mirror enums to the real ones, so the SDK-free half cannot drift.

Nothing touches the network: the HTTP client and the bearer token are replaced on the adapter.
"""

from __future__ import annotations

import enum
import json
from typing import Any

import httpx
import pytest

from marketing_compliance_gate.adapters.gcp.model_armor_guardrail import (
    ModelArmorGuardrailAdapter,
)
from marketing_compliance_gate.config import Settings
from marketing_compliance_gate.domain.models import Direction

TEXT = "Guaranteed 12% returns with zero risk, apply today."
DIRECTIONS = [Direction.INPUT, Direction.OUTPUT]
#: The adapter's per-call deadline, in seconds. Asserted, so a dropped deadline fails here.
DEADLINE_SECONDS = 30.0


class _MirrorState(enum.IntEnum):
    """``modelarmor_v1.FilterMatchState``'s members, by name and number."""

    FILTER_MATCH_STATE_UNSPECIFIED = 0
    NO_MATCH_FOUND = 1
    MATCH_FOUND = 2


class _MirrorInvocation(enum.IntEnum):
    """``modelarmor_v1.InvocationResult``'s members, by name and number."""

    INVOCATION_RESULT_UNSPECIFIED = 0
    SUCCESS = 1
    PARTIAL = 2
    FAILURE = 3


# --------------------------------------------------------------------------- #
# A fake HTTP client, so screen() runs end to end with no network
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._body = body
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            request = httpx.Request("POST", "https://modelarmor.example/v1")
            raise httpx.HTTPStatusError(
                f"HTTP {self._status}",
                request=request,
                response=httpx.Response(self._status, request=request),
            )

    def json(self) -> Any:
        return self._body


class _FakeClient:
    """Records each POST and answers with a canned body, status or raised error."""

    def __init__(self, body: Any = None, *, status: int = 200, error: Exception | None = None):
        self._body = body
        self._status = status
        self._error = error
        self.urls: list[str] = []
        self.timeouts: list[Any] = []

    def post(self, url: str, *, json: Any, headers: Any, timeout: Any = None) -> _FakeResponse:
        self.urls.append(url)
        self.timeouts.append(timeout)
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._body, self._status)


def _adapter(client: _FakeClient) -> ModelArmorGuardrailAdapter:
    adapter = ModelArmorGuardrailAdapter(Settings(project_id="p", profile="gcp"))
    adapter._client = client  # the mapping is under test, not the transport
    adapter._bearer_token = lambda: "test-token"  # type: ignore[method-assign]
    return adapter


def _screen(body: Any, direction: Direction = Direction.INPUT) -> Any:
    return _adapter(_FakeClient(body)).screen(TEXT, direction)


def _mirror_body(
    state: _MirrorState | None,
    invocation: _MirrorInvocation | None = _MirrorInvocation.SUCCESS,
) -> dict[str, Any]:
    """The REST JSON for a result; ``None`` omits a field, as proto3 JSON omits a default."""
    result: dict[str, Any] = {}
    if state is not None:
        result["filterMatchState"] = state.name
    if invocation is not None:
        result["invocationResult"] = invocation.name
    return {"sanitizationResult": result}


# --------------------------------------------------------------------------- #
# SDK-free: the mapping, through screen()
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_match_found_blocks_sdk_free(direction: Direction) -> None:
    verdict = _screen(_mirror_body(_MirrorState.MATCH_FOUND), direction)
    assert verdict.allowed is False
    assert verdict.findings


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_no_match_found_with_success_allows_sdk_free(direction: Direction) -> None:
    verdict = _screen(_mirror_body(_MirrorState.NO_MATCH_FOUND), direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT
    assert verdict.findings == ()


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("invocation", list(_MirrorInvocation), ids=lambda m: m.name)
def test_match_found_blocks_however_many_filters_ran_sdk_free(
    direction: Direction, invocation: _MirrorInvocation
) -> None:
    verdict = _screen(_mirror_body(_MirrorState.MATCH_FOUND, invocation), direction)
    assert verdict.allowed is False


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "invocation",
    [
        _MirrorInvocation.PARTIAL,
        _MirrorInvocation.FAILURE,
        _MirrorInvocation.INVOCATION_RESULT_UNSPECIFIED,
        None,
    ],
    ids=["PARTIAL", "FAILURE", "UNSPECIFIED", "absent"],
)
def test_no_match_from_an_incomplete_screen_blocks_sdk_free(
    direction: Direction, invocation: _MirrorInvocation | None
) -> None:
    """A skipped filter reports no match. That is not a pass: the text was not screened."""
    verdict = _screen(_mirror_body(_MirrorState.NO_MATCH_FOUND, invocation), direction)
    assert verdict.allowed is False
    assert "no complete filter decision" in verdict.reason


def test_exactly_one_combination_allows_sdk_free() -> None:
    allowed = [
        (state.name, invocation.name)
        for state in _MirrorState
        for invocation in _MirrorInvocation
        if _screen(_mirror_body(state, invocation)).allowed
    ]
    assert allowed == [("NO_MATCH_FOUND", "SUCCESS")]


@pytest.mark.parametrize(
    "body",
    [
        _mirror_body(_MirrorState.FILTER_MATCH_STATE_UNSPECIFIED),
        _mirror_body(None),
        _mirror_body(None, None),
        {"sanitizationResult": None},
        {},
        [],
        None,
        # The numeric enum form is not the REST wire form: no allow on anything but the name.
        {"sanitizationResult": {"filterMatchState": 1, "invocationResult": 1}},
        {
            "sanitizationResult": {
                "filterMatchState": "no_match_found",
                "invocationResult": "success",
            }
        },
        {"sanitizationResult": {"filterMatchState": True, "invocationResult": True}},
    ],
    ids=[
        "unspecified-state",
        "absent-state",
        "empty-result",
        "null-result",
        "missing-result",
        "non-object-body",
        "null-body",
        "numeric-enums",
        "lower-case-names",
        "boolean-fields",
    ],
)
def test_no_verdict_fails_closed_sdk_free(body: Any) -> None:
    verdict = _screen(body)
    assert verdict.allowed is False
    assert verdict.findings


def test_no_per_filter_findings_is_not_a_clean_screen_sdk_free() -> None:
    """The old fallback: no ``filterMatchState`` and no filter matches meant allowed."""
    body = {
        "sanitizationResult": {
            "filterResults": {
                "pi_and_jailbreak": {
                    "piAndJailbreakFilterResult": {
                        "executionState": "EXECUTION_SKIPPED",
                        "matchState": "NO_MATCH_FOUND",
                    }
                }
            }
        }
    }
    assert _screen(body).allowed is False


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_every_call_carries_the_deadline(direction: Direction) -> None:
    client = _FakeClient(_mirror_body(_MirrorState.NO_MATCH_FOUND))
    _adapter(client).screen(TEXT, direction)
    assert client.timeouts == [DEADLINE_SECONDS]
    verb = "sanitizeUserPrompt" if direction is Direction.INPUT else "sanitizeModelResponse"
    assert client.urls[0].endswith(f":{verb}")


@pytest.mark.parametrize(
    ("client", "error"),
    [
        (_FakeClient(_mirror_body(_MirrorState.NO_MATCH_FOUND), status=503), httpx.HTTPStatusError),
        (_FakeClient(error=httpx.ReadTimeout("deadline exceeded")), httpx.ReadTimeout),
    ],
    ids=["http-503", "deadline"],
)
def test_api_errors_propagate(client: _FakeClient, error: type[Exception]) -> None:
    """An API failure must not turn into an allow; it reaches the caller, which refuses."""
    with pytest.raises(error):
        _adapter(client).screen(TEXT, Direction.INPUT)


# --------------------------------------------------------------------------- #
# Real SDK: real modelarmor_v1 messages, serialized to the REST wire JSON
# --------------------------------------------------------------------------- #
def _ma() -> Any:
    return pytest.importorskip("google.cloud.modelarmor_v1")


def _wire(message: Any, *, integer_enums: bool = False) -> Any:
    """The REST JSON for ``message``, as the SDK's own proto3 JSON mapping writes it."""
    return json.loads(type(message).to_json(message, use_integers_for_enums=integer_enums))


def _real_response(
    direction: Direction,
    state_name: str | None,
    invocation_name: str = "SUCCESS",
    *,
    skipped: bool = False,
) -> Any:
    """A real sanitize response; ``state_name=None`` leaves ``sanitization_result`` unset.

    ``skipped`` adds the prompt-injection filter as not having run, the shape a prompt padded
    past that filter's token limit produces.
    """
    ma = _ma()
    cls = (
        ma.SanitizeUserPromptResponse
        if direction is Direction.INPUT
        else ma.SanitizeModelResponseResponse
    )
    if state_name is None:
        return cls()
    filter_results = {}
    if skipped:
        filter_results["pi_and_jailbreak"] = ma.FilterResult(
            pi_and_jailbreak_filter_result=ma.PiAndJailbreakFilterResult(
                execution_state=ma.FilterExecutionState.EXECUTION_SKIPPED,
                match_state=ma.FilterMatchState.NO_MATCH_FOUND,
            )
        )
    return cls(
        sanitization_result=ma.SanitizationResult(
            filter_match_state=ma.FilterMatchState[state_name],
            invocation_result=ma.InvocationResult[invocation_name],
            filter_results=filter_results,
        )
    )


@pytest.mark.parametrize(
    ("mirror", "real_name"),
    [(_MirrorState, "FilterMatchState"), (_MirrorInvocation, "InvocationResult")],
    ids=["FilterMatchState", "InvocationResult"],
)
def test_the_mirror_matches_the_real_enum(mirror: Any, real_name: str) -> None:
    real = getattr(_ma(), real_name)
    assert {m.name: int(m) for m in real} == {m.name: int(m) for m in mirror}


def test_the_wire_json_uses_the_field_and_enum_names_the_adapter_reads() -> None:
    result = _wire(_real_response(Direction.INPUT, "NO_MATCH_FOUND"))["sanitizationResult"]
    assert result["filterMatchState"] == "NO_MATCH_FOUND"
    assert result["invocationResult"] == "SUCCESS"
    skipped = _wire(_real_response(Direction.INPUT, "NO_MATCH_FOUND", "PARTIAL", skipped=True))
    pi = skipped["sanitizationResult"]["filterResults"]["pi_and_jailbreak"]
    assert pi["piAndJailbreakFilterResult"]["executionState"] == "EXECUTION_SKIPPED"


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_match_found_blocks(direction: Direction) -> None:
    verdict = _screen(_wire(_real_response(direction, "MATCH_FOUND")), direction)
    assert verdict.allowed is False


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_no_match_found_with_success_allows(direction: Direction) -> None:
    verdict = _screen(_wire(_real_response(direction, "NO_MATCH_FOUND")), direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "state_name",
    [None, "FILTER_MATCH_STATE_UNSPECIFIED"],
    ids=["missing-result", "unspecified-state"],
)
def test_no_verdict_fails_closed(direction: Direction, state_name: str | None) -> None:
    verdict = _screen(_wire(_real_response(direction, state_name)), direction)
    assert verdict.allowed is False


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("invocation_name", ["PARTIAL", "FAILURE", "INVOCATION_RESULT_UNSPECIFIED"])
def test_no_match_from_a_screen_where_filters_did_not_run_blocks(
    direction: Direction, invocation_name: str
) -> None:
    response = _real_response(direction, "NO_MATCH_FOUND", invocation_name, skipped=True)
    verdict = _screen(_wire(response), direction)
    assert verdict.allowed is False
    assert "no complete filter decision" in verdict.reason


def test_integer_enum_json_fails_closed() -> None:
    """The SDK's default ``to_json`` writes enums as numbers; that is not a clean-screen name."""
    body = _wire(_real_response(Direction.INPUT, "NO_MATCH_FOUND"), integer_enums=True)
    assert body["sanitizationResult"]["filterMatchState"] == 1
    assert _screen(body).allowed is False


def test_api_errors_propagate_with_the_sdk_present() -> None:
    _ma()
    client = _FakeClient(error=httpx.ConnectError("Model Armor unavailable"))
    with pytest.raises(httpx.ConnectError, match="unavailable"):
        _adapter(client).screen(TEXT, Direction.INPUT)
