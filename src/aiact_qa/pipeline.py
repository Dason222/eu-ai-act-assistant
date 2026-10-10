"""QAEngine - the single entry point used by the API, the UI, the CLI and the evaluation.

Flow per question:
  1. guardrails on the input (length, empty)
  2. follow-up / non-English questions are rewritten into a standalone English query (LLM, cheap)
  3. hybrid retrieval: explicit legal references + BM25 + dense embeddings, fused with RRF
  4. relevance gate: clearly off-topic questions are answered without calling the LLM
  5. grounded generation with Claude (streamed), citing provisions as [Article 6(2)]
  6. citation validation against the retrieved sources + deep links to EUR-Lex
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from aiact_qa.config import Settings, get_settings
from aiact_qa.models import Provision
from aiact_qa.schemas import Answer, Source

# History format accepted everywhere: [{"role": "user" | "assistant", "content": "..."}]
History = list[dict[str, str]]


class QAEngine:
    """Contract (implementation in progress; signatures are stable)."""

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> QAEngine:
        """Load the persisted index (building it on first use) and the configured LLM client."""
        raise NotImplementedError

    def ask(self, question: str, history: History | None = None, top_k: int | None = None) -> Answer:
        """Answer a question (blocking)."""
        raise NotImplementedError

    def ask_stream(
        self, question: str, history: History | None = None, top_k: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Answer a question, streaming events:

        {"type": "sources", "sources": [Source.to_dict(), ...], "standalone_question": str}
        {"type": "delta", "text": str}                      # answer text as it is generated
        {"type": "done", "answer": Answer.to_dict()}        # final, with validated citations
        """
        raise NotImplementedError

    def search(self, query: str, k: int = 8) -> list[Source]:
        """Retrieval only (no LLM)."""
        raise NotImplementedError

    def get_provision(self, provision_id: str) -> Provision | None:
        """Full text of an article / recital / annex by id ("art_5", "rct_27", "anx_III")."""
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        """Index and LLM configuration summary for /health."""
        raise NotImplementedError


def get_engine(settings: Settings | None = None) -> QAEngine:
    return QAEngine.from_settings(settings or get_settings())
