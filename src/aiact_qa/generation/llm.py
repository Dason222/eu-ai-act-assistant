"""Claude client (Anthropic Python SDK) with streaming, effort control and refusal fallbacks."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """Generation failed (network, auth, rate limit) - the caller degrades to extractive mode."""


@dataclass
class LLMResult:
    text: str = ""
    model: str = ""
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


def credentials_available() -> bool:
    """True if the SDK can authenticate (API key, auth token or an `ant auth login` profile)."""
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    return os.path.isdir(os.path.expanduser("~/.config/anthropic"))


def _usage(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: getattr(usage, k) for k in keys if getattr(usage, k, None) is not None}


class ClaudeLLM:
    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        max_tokens: int = 16000,
        timeout_s: float = 120.0,
        server_side_fallbacks: bool = True,
        client: Any = None,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(timeout=timeout_s, max_retries=2)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.server_side_fallbacks = server_side_fallbacks

    def _params(self, system: list[dict] | str, messages: list[dict], effort: str | None) -> dict:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": effort or self.effort},
        }
        if self.server_side_fallbacks:
            # a declined request is re-run on Anthropic's recommended fallback model, server-side
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _wrap(self, exc: Exception) -> LLMError:
        a = self._anthropic
        if isinstance(exc, a.AuthenticationError):
            return LLMError("LLM authentication failed - check ANTHROPIC_API_KEY")
        if isinstance(exc, a.RateLimitError):
            return LLMError("LLM rate limit reached - retry shortly")
        if isinstance(exc, a.APIStatusError):
            return LLMError(f"LLM API error {exc.status_code}")
        if isinstance(exc, a.APIConnectionError):
            return LLMError("cannot reach the LLM API")
        return LLMError(str(exc))

    def stream(
        self, system: list[dict] | str, messages: list[dict], result: LLMResult, effort: str | None = None
    ) -> Iterator[str]:
        """Yield answer text deltas; ``result`` is filled with the final text, usage and stop reason."""
        try:
            with self.client.beta.messages.stream(**self._params(system, messages, effort)) as stream:
                for text in stream.text_stream:
                    result.text += text
                    yield text
                final = stream.get_final_message()
        except self._anthropic.APIError as exc:
            raise self._wrap(exc) from exc
        result.model = getattr(final, "model", self.model)
        result.stop_reason = getattr(final, "stop_reason", None)
        result.usage = _usage(final)

    def complete(self, system: list[dict] | str, messages: list[dict], effort: str | None = None) -> LLMResult:
        result = LLMResult()
        for _ in self.stream(system, messages, result, effort=effort):
            pass
        return result
