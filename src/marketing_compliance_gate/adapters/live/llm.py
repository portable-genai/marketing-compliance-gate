"""Live LLM adapter (LlmPort): the fleet's one local open-weight model, on the laptop.

The ``live`` profile's narrator. It delegates every call to the shared
:class:`hex_service_kit.localmodel.LocalModelClient`, which reaches an OpenAI-compatible
``/chat/completions`` server (``LOCAL_MODEL_URL``, default ``http://127.0.0.1:8001``) serving
``LOCAL_MODEL`` (default Gemma 4 31B). That client states the response schema in the prompt,
strips a markdown fence, validates the answer and asks again with the problem named, so this
adapter only maps the domain request onto chat messages and the completion back onto
:class:`LlmResponse`.

As under every profile, the model narrates the findings the deterministic rule engine and the
green-claim coverage engine already decided; it never decides whether a rule passes, whether a
claim is substantiated, or what the coverage is. Both callers treat narration as best-effort,
so the two domain errors raised here leave the deterministic fallback narrative in place.
"""

from __future__ import annotations

import json
from typing import Any

from hex_service_kit.localmodel import (
    LocalCompletion,
    LocalModelClient,
    LocalModelOutputError,
    LocalModelSettings,
    LocalModelUnavailable,
)

from ...config import Settings
from ...domain.errors import ModelOutputError, ModelUnavailableError
from ...domain.models import LlmRequest, LlmResponse, TokenUsage

#: Classification answers one label, so a short completion is enough.
_CLASSIFY_MAX_TOKENS = 16


class LocalModelLLMAdapter:
    """Narrate findings and triage labels with the local open-weight model."""

    def __init__(self, settings: Settings, *, client: LocalModelClient | None = None) -> None:
        self._settings = settings
        self._client = client or LocalModelClient(LocalModelSettings.from_env())

    # ------------------------------------------------------------------ #
    # LlmPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        messages = self._to_messages(request)
        try:
            if request.response_schema is not None:
                completion = self._client.complete_json(
                    messages,
                    schema=request.response_schema,
                    temperature=request.temperature,
                    max_tokens=request.max_output_tokens,
                )
            else:
                completion = self._client.complete(
                    messages,
                    temperature=request.temperature,
                    max_tokens=request.max_output_tokens,
                )
        except LocalModelUnavailable as exc:
            raise ModelUnavailableError(str(exc)) from exc
        except LocalModelOutputError as exc:
            raise ModelOutputError(str(exc)) from exc
        return self._to_response(completion)

    def classify(self, text: str, labels: list[str]) -> str:
        label_list = ", ".join(labels)
        prompt = (
            f"Classify the text into exactly one of these labels: {label_list}.\n"
            "Reply with the single label only, no punctuation or explanation.\n\n"
            f"Text:\n{text}"
        )
        try:
            completion = self._client.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=_CLASSIFY_MAX_TOKENS,
            )
        except LocalModelUnavailable as exc:
            raise ModelUnavailableError(str(exc)) from exc
        return _match_label(completion.text.strip(), labels)

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_messages(request: LlmRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        for message in request.messages:
            role = {"model": "assistant", "system": "system"}.get(message.role, "user")
            messages.append({"role": role, "content": message.content})
        return messages

    @staticmethod
    def _to_response(completion: LocalCompletion) -> LlmResponse:
        # LlmResponse.usage is a required TokenUsage, so a server that reports no usage (MLX
        # reports none) reads as zeros here; the kit client itself returns None, never zeros.
        usage = completion.usage or TokenUsage()
        raw: dict[str, Any] | None = None
        text = completion.text
        if isinstance(completion.data, dict):
            raw = completion.data
            # Hand the domain the validated JSON, not the fenced or prefixed original.
            text = json.dumps(completion.data)
        return LlmResponse(text=text, usage=usage, model=completion.model, raw=raw)


def _match_label(raw: str, labels: list[str]) -> str:
    """Coerce the reply to one of ``labels`` (case-insensitive), as the Gemini adapter does."""
    if not labels:
        return raw
    lowered = raw.lower()
    for label in labels:
        if label.lower() == lowered:
            return label
    for label in labels:
        if label.lower() in lowered:
            return label
    return labels[0]
