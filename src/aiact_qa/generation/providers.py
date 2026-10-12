"""LLM provider catalogue and factory - the generation layer is vendor-independent.

* ``anthropic``          Claude (Anthropic SDK): streaming, refusal fallbacks, prompt caching
* ``openai``             OpenAI / ChatGPT models
* ``azure_openai``       Azure OpenAI deployments (EU regions available)
* ``mistral``            Mistral AI La Plateforme - European provider (France)
* ``ollama``             self-hosted open-weights models (Mistral, EuroLLM, Teuken, Salamandra, Llama...)
                         via Ollama - or vLLM/TGI by changing the base URL; nothing leaves your servers
* ``openai_compatible``  any OpenAI-compatible endpoint, e.g. EU clouds (Scaleway Generative APIs,
                         OVHcloud AI Endpoints, IONOS AI Model Hub) or an internal LLM gateway
* ``extractive``         no LLM: the best excerpts are returned verbatim

Every provider implements ``stream(system, messages, result, effort)`` and ``complete(...)``
(see ``ClaudeLLM``), so the pipeline, the API and the evaluation are provider-agnostic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from aiact_qa.config import Settings
from aiact_qa.generation.llm import ClaudeLLM, LLMError, LLMResult, credentials_available

log = logging.getLogger(__name__)


class LLM(Protocol):
    provider: str
    model: str

    def stream(
        self, system: list[dict] | str, messages: list[dict], result: LLMResult, effort: str | None = None
    ) -> Iterator[str]: ...

    def complete(self, system: list[dict] | str, messages: list[dict], effort: str | None = None) -> LLMResult: ...


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    default_model: str
    api_key_env: str | None      # environment variable holding the credential
    base_url: str | None = None  # OpenAI-compatible endpoint
    hosting: str = ""            # where inference runs / data residency note


PROVIDERS: dict[str, ProviderInfo] = {
    p.id: p
    for p in [
        ProviderInfo("anthropic", "Anthropic Claude", "claude-sonnet-5-5", "ANTHROPIC_API_KEY",
                     hosting="Anthropic API (also via AWS Bedrock, Google Vertex AI, Microsoft Foundry)"),
        ProviderInfo("openai", "OpenAI (ChatGPT models)", "gpt-5", "OPENAI_API_KEY",
                     hosting="OpenAI API"),
        ProviderInfo("azure_openai", "Azure OpenAI", "", "AZURE_OPENAI_API_KEY",
                     hosting="Azure region of your deployment (EU regions available)"),
        ProviderInfo("mistral", "Mistral AI (EU)", "mistral-large-latest", "MISTRAL_API_KEY",
                     base_url="https://api.mistral.ai/v1", hosting="Mistral AI, EU (France)"),
        ProviderInfo("ollama", "Self-hosted open-weights model (Ollama / vLLM)", "mistral", None,
                     base_url="http://localhost:11434/v1", hosting="your own server - full data control"),
        ProviderInfo("openai_compatible", "OpenAI-compatible endpoint (EU clouds, LLM gateway)", "",
                     "AIACT_LLM_API_KEY", hosting="as configured, e.g. Scaleway / OVHcloud / IONOS (EU)"),
        ProviderInfo("extractive", "No LLM (extractive excerpts)", "", None, hosting="local"),
    ]
}


def _is_configured(info: ProviderInfo, settings: Settings) -> bool:
    if info.id == "extractive":
        return True
    if info.id == "anthropic":
        return credentials_available()
    if info.id == "azure_openai":
        return bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))
    if info.id == "ollama":
        return True  # assumed reachable; a failing call degrades to extractive mode
    if info.id == "openai_compatible":
        return bool(settings.llm_base_url)
    return bool(info.api_key_env and os.getenv(info.api_key_env))


def available_providers(settings: Settings) -> list[dict[str, Any]]:
    out = []
    for info in PROVIDERS.values():
        item = asdict(info)
        item.pop("api_key_env")
        if info.id == "azure_openai":
            item["default_model"] = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        item["configured"] = _is_configured(info, settings)
        out.append(item)
    return out


class OpenAICompatibleLLM:
    """Chat Completions client for OpenAI, Azure OpenAI, Mistral, Ollama/vLLM and EU clouds."""

    def __init__(self, provider: str, model: str, client: Any, max_tokens: int = 4096) -> None:
        import openai

        self._openai = openai
        self.provider = provider
        self.model = model
        self.client = client
        self.max_tokens = max_tokens

    @staticmethod
    def _system_text(system: list[dict] | str) -> str:
        return system if isinstance(system, str) else "\n\n".join(b["text"] for b in system)

    def _wrap(self, exc: Exception) -> LLMError:
        o = self._openai
        if isinstance(exc, o.AuthenticationError):
            return LLMError(f"{self.provider}: authentication failed - check the API key")
        if isinstance(exc, o.RateLimitError):
            return LLMError(f"{self.provider}: rate limit reached - retry shortly")
        if isinstance(exc, o.APIStatusError):
            return LLMError(f"{self.provider}: API error {exc.status_code}")
        if isinstance(exc, o.APIConnectionError):
            return LLMError(f"{self.provider}: cannot reach the endpoint")
        return LLMError(f"{self.provider}: {exc}")

    def stream(
        self, system: list[dict] | str, messages: list[dict], result: LLMResult, effort: str | None = None
    ) -> Iterator[str]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": self._system_text(system)}, *messages],
            "stream": True,
        }
        if self.provider in {"openai", "azure_openai"}:
            # newer OpenAI models take max_completion_tokens; usage arrives in the last chunk
            params["max_completion_tokens"] = self.max_tokens
            params["stream_options"] = {"include_usage": True}
        else:
            params["max_tokens"] = self.max_tokens
        try:
            for chunk in self.client.chat.completions.create(**params):
                if getattr(chunk, "usage", None):
                    result.usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    }
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                text = getattr(choice.delta, "content", None)
                if text:
                    result.text += text
                    yield text
                if choice.finish_reason:
                    result.stop_reason = {"length": "max_tokens", "content_filter": "refusal"}.get(
                        choice.finish_reason, choice.finish_reason
                    )
                result.model = getattr(chunk, "model", None) or self.model
        except self._openai.APIError as exc:
            raise self._wrap(exc) from exc

    def complete(self, system: list[dict] | str, messages: list[dict], effort: str | None = None) -> LLMResult:
        result = LLMResult()
        for _ in self.stream(system, messages, result, effort):
            pass
        return result


def make_llm(settings: Settings, provider: str | None = None, model: str | None = None) -> LLM | None:
    """Build the client for ``provider`` (default: settings). Returns None for extractive mode."""
    provider = (provider or settings.llm_provider or "anthropic").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown LLM provider '{provider}'; choose one of {', '.join(PROVIDERS)}")
    info = PROVIDERS[provider]
    if provider == "extractive":
        return None
    if not _is_configured(info, settings):
        raise ValueError(f"LLM provider '{provider}' is not configured (missing credentials or base URL)")
    model = model or settings.llm_model or info.default_model

    if provider == "anthropic":
        llm = ClaudeLLM(
            model=model,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
            timeout_s=settings.llm_timeout_s,
            server_side_fallbacks=settings.llm_server_side_fallbacks,
        )
        llm.provider = "anthropic"
        return llm

    import openai

    timeout = settings.llm_timeout_s
    if provider == "azure_openai":
        model = model or os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        client = openai.AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("OPENAI_API_VERSION", "2024-10-21"),
            timeout=timeout,
        )
    else:
        base_url = settings.llm_base_url or info.base_url
        api_key = os.getenv(info.api_key_env) if info.api_key_env else None
        client = openai.OpenAI(
            api_key=api_key or settings.llm_api_key or "not-needed",
            base_url=base_url,
            timeout=timeout,
        )
    if not model:
        raise ValueError(f"no model configured for provider '{provider}' (set AIACT_LLM_MODEL)")
    return OpenAICompatibleLLM(provider, model, client, max_tokens=min(settings.llm_max_tokens, 8192))
