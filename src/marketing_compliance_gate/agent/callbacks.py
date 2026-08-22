"""Model-boundary callbacks: defense-in-depth guardrail + audit (P-11, P-07).

The domain service already screens INPUT and OUTPUT and audits inside its pipeline (SPEC §5).
These ADK callbacks add a **second, independent line of defence at the model boundary**: every
prompt that reaches the LLM and every response that leaves it is, once more,

  1. **screened** (Model Armor via :class:`GuardrailPort`) for prompt injection, jailbreak,
     sensitive-data leakage and RAI categories, and
  2. **audited** (Cloud Logging locked WORM bucket via :class:`AuditSinkPort`) with a record at
     agent turn end (P-07).

D6 reviews marketing assets against a rule set and handles no customer PII (C2/C3/C4 n/a in the
practices audit), so there is no PII-redaction callback here. The guardrail's own
``sanitized_text`` is still honoured, so any content the policy chooses to mask never reaches
the model or the audit record.

The callbacks are built from a :class:`~marketing_compliance_gate.config.Container`, so the active
profile decides whether these are real Model Armor / Cloud Logging calls or on-prem placeholders.

Span privacy: ADK can attach message content to trace spans. :func:`configure_span_privacy`
sets ``ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`` (idempotent; never overrides an operator who
has already pinned it). ADK imports are done lazily inside the factory / callbacks so this
module imports without ADK installed (SPEC §4).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..config import Container
from ..domain.models import (
    AuditEvent,
    Decision,
    Direction,
    GuardrailVerdict,
    utcnow,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models import LlmRequest, LlmResponse

SPAN_CONTENT_ENV = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"

_LAST_PROMPT_KEY = "_mkt_last_prompt"
_LAST_RESPONSE_KEY = "_mkt_last_response"
_BLOCKED_KEY = "_mkt_turn_blocked"
_RESOURCE = "marketing-compliance-gate"
_DEFAULT_ACTOR = "compliance-governance-agent"


def configure_span_privacy() -> None:
    """Ensure message content is never captured into trace spans.

    Idempotent and non-destructive: only sets the flag if the operator has not already pinned
    it. Pairs with the Cloud Trace adapter's content-capture-off setting (SPEC §3).
    """
    os.environ.setdefault(SPAN_CONTENT_ENV, "false")


def _screen(container: Container, text: str, direction: Direction) -> tuple[str, GuardrailVerdict]:
    """Guardrail-screen ``text``; return (safe_text, verdict).

    The verdict's ``sanitized_text`` (if any) replaces the original, so masked content never
    reaches the model or the audit record.
    """
    verdict = container.guardrail.screen(text, direction)
    safe_text = verdict.sanitized_text if verdict.sanitized_text is not None else text
    return safe_text, verdict


def _content_to_text(content: Any) -> str:
    """Best-effort flatten of an ADK ``types.Content`` (or text) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = getattr(content, "parts", None)
    if parts is None:
        return str(content)
    chunks: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _set_state(callback_context: Any, key: str, value: Any) -> None:
    state = getattr(callback_context, "state", None)
    if state is None:
        return
    with contextlib.suppress(Exception):  # pragma: no cover - extremely defensive
        state[key] = value


def _get_state(callback_context: Any, key: str, default: Any = None) -> Any:
    state = getattr(callback_context, "state", None)
    if state is None:
        return default
    try:
        return state.get(key, default)
    except Exception:  # pragma: no cover - extremely defensive
        return default


def build_callbacks(container: Container) -> dict[str, Callable[..., Any]]:
    """Build the before/after-model and after-agent callbacks bound to ``container``.

    Returns a dict with keys ``before_model_callback``, ``after_model_callback`` and
    ``after_agent_callback`` ready to attach to an ADK ``LlmAgent``. ``google.adk`` is imported
    lazily here so the module is import-safe without ADK installed (SPEC §4).
    """
    from google.adk.models import LlmResponse
    from google.genai import types

    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Guardrail the outbound prompt; short-circuit if blocked."""
        prompt_text = _request_text(llm_request)
        safe_text, verdict = _screen(container, prompt_text, Direction.INPUT)
        _set_state(callback_context, _LAST_PROMPT_KEY, safe_text)

        if not verdict.allowed:
            _set_state(callback_context, _BLOCKED_KEY, True)
            reason = verdict.reason or "Request blocked by input guardrail policy."
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=reason)]))
        return None

    def after_model_callback(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Guardrail the model response; replace text if blocked / sanitised."""
        response_text = _content_to_text(getattr(llm_response, "content", None))
        safe_text, verdict = _screen(container, response_text, Direction.OUTPUT)
        _set_state(callback_context, _LAST_RESPONSE_KEY, safe_text)

        if not verdict.allowed:
            _set_state(callback_context, _BLOCKED_KEY, True)
            reason = verdict.reason or "Response withheld by output guardrail policy."
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=reason)]))
        if safe_text != response_text:
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=safe_text)])
            )
        return None

    def after_agent_callback(callback_context: CallbackContext) -> types.Content | None:
        """Write one WORM audit record for the agent turn (P-07)."""
        blocked = bool(_get_state(callback_context, _BLOCKED_KEY, False))
        event = AuditEvent(
            action="review",
            actor=_actor(callback_context),
            decision=Decision.BLOCKED if blocked else Decision.ALLOWED,
            prompt=_get_state(callback_context, _LAST_PROMPT_KEY, ""),
            response=_get_state(callback_context, _LAST_RESPONSE_KEY, ""),
            resource=_RESOURCE,
            trace_id=_trace_id(callback_context),
            timestamp=utcnow(),
            metadata={"layer": "model-boundary"},
        )
        container.audit.record(event)
        return None

    return {
        "before_model_callback": before_model_callback,
        "after_model_callback": after_model_callback,
        "after_agent_callback": after_agent_callback,
    }


def _request_text(llm_request: Any) -> str:
    """Flatten the user-visible text of an ADK ``LlmRequest`` to a single string."""
    contents = getattr(llm_request, "contents", None) or []
    chunks: list[str] = []
    for content in contents:
        if getattr(content, "role", None) == "model":
            continue
        chunks.append(_content_to_text(content))
    return "\n".join(c for c in chunks if c)


def _actor(callback_context: Any) -> str:
    actor = _get_state(callback_context, "actor", None)
    if actor:
        return str(actor)
    user_id = getattr(callback_context, "user_id", None)
    return str(user_id) if user_id else _DEFAULT_ACTOR


def _trace_id(callback_context: Any) -> str | None:
    invocation_id = getattr(callback_context, "invocation_id", None)
    return str(invocation_id) if invocation_id else None
