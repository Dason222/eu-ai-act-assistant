"""Runtime configuration, read from environment variables (prefix ``AIACT_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Canonical identifiers of the regulation covered by the prototype.
CELEX = "32024R1689"
ELI_URL = "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng"
# Document-only HTML rendition of the Official Journal publication (same element ids as the ELI page).
OJ_HTML_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689"
REGULATION_NAME = "Regulation (EU) 2024/1689 (Artificial Intelligence Act)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIACT_", env_file=".env", extra="ignore")

    # --- Knowledge base -------------------------------------------------------------------------
    source_url: str = OJ_HTML_URL
    data_dir: Path = PROJECT_ROOT / "data"

    # --- Retrieval ------------------------------------------------------------------------------
    # Local ONNX embedding model (fastembed). Set to "none" to run lexical-only retrieval.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Optional cross-encoder re-ranker (fastembed), e.g. "Xenova/ms-marco-MiniLM-L-12-v2". Empty = off.
    reranker_model: str = ""
    model_cache_dir: Path | None = None
    top_k: int = 8                      # chunks passed to the LLM
    candidate_k: int = 40               # candidates per retriever before fusion
    max_context_words: int = 2600       # budget for the excerpts sent to the LLM
    # Below this best dense similarity (and with no explicit legal reference in the question) the
    # question is treated as out of scope and answered without calling the LLM.
    min_relevance: float = 0.55

    # --- Generation (provider-agnostic, see generation/providers.py) --------------------------------
    # anthropic | openai | azure_openai | mistral | ollama | openai_compatible | extractive
    llm_provider: str = "anthropic"
    llm_model: str = ""                 # empty = provider default (Claude: claude-opus-5-5)
    llm_base_url: str = ""              # override for ollama / openai_compatible endpoints
    llm_api_key: str = ""               # key for openai_compatible endpoints
    llm_effort: str = "medium"          # Claude effort: low | medium | high | xhigh | max
    llm_rewrite_effort: str = "low"     # effort for the short query-rewriting call
    llm_max_tokens: int = 1500
    llm_timeout_s: float = 120.0
    llm_server_side_fallbacks: bool = True

    # --- Service ----------------------------------------------------------------------------------
    api_key: str = ""                   # if set, the REST API requires header "X-API-Key"
    max_question_chars: int = 2000
    max_history_turns: int = 3
    max_history_chars_per_turn: int = 1000
    allowed_providers: str = "anthropic,extractive"
    allowed_models: str = "claude-sonnet-5-5,claude-haiku-5-5"
    daily_budget_usd: float = 2.0
    reserved_cost_per_llm_request_usd: float = 0.06
    daily_request_limit: int = 500
    daily_actor_request_limit: int = 40
    log_level: str = "INFO"

    @property
    def raw_html_path(self) -> Path:
        return self.data_dir / "raw" / "ai_act_L_202401689_EN.html"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def feedback_path(self) -> Path:
        return self.data_dir / "feedback" / "feedback.jsonl"

    @property
    def eval_dir(self) -> Path:
        return self.data_dir / "eval"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
