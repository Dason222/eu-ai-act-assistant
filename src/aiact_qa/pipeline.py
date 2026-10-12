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

import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from aiact_qa.budget import UsageBudget
from aiact_qa.config import Settings, get_settings
from aiact_qa.generation import prompts
from aiact_qa.generation.citations import extract_citations, unverified_warning
from aiact_qa.generation.llm import LLMError, LLMResult
from aiact_qa.generation.providers import LLM, available_providers, make_llm
from aiact_qa.ingestion.build import build_knowledge_base, load_processed, processed_is_fresh
from aiact_qa.models import Chunk, Provision, Regulation
from aiact_qa.retrieval.dense import DenseIndex
from aiact_qa.retrieval.hybrid import HybridRetriever, RetrievalResult
from aiact_qa.schemas import Answer, Source

log = logging.getLogger(__name__)

# History format accepted everywhere: [{"role": "user" | "assistant", "content": "..."}]
History = list[dict[str, str]]

_ENGLISH_HINTS = re.compile(r"\b(the|what|which|who|how|is|are|does|do|can|when|of|and|for|to)\b", re.I)


def looks_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    non_ascii = sum(1 for c in letters if not c.isascii()) / len(letters)
    return non_ascii < 0.1 and bool(_ENGLISH_HINTS.search(text))


class QAEngine:
    def __init__(
        self,
        regulation: Regulation,
        chunks: list[Chunk],
        retriever: HybridRetriever,
        llm: LLM | None,
        settings: Settings,
    ) -> None:
        self.regulation = regulation
        self.chunks = chunks
        self.retriever = retriever
        self.llm = llm
        self.settings = settings
        self._provisions = regulation.by_id()
        self._system = prompts.system_blocks(regulation)
        self._variants: dict[tuple[str, str], QAEngine] = {}
        self.budget = UsageBudget(settings.feedback_path.parent / "usage_budget.sqlite3", settings)

    # ------------------------------------------------------------------------------------------ #
    @classmethod
    def from_settings(cls, settings: Settings | None = None, llm: LLM | None = None) -> QAEngine:
        """Load the persisted index (building it on first use) and the configured LLM client."""
        settings = settings or get_settings()
        if not processed_is_fresh(settings):
            build_knowledge_base(settings)
        regulation, chunks = load_processed(settings.processed_dir)

        dense = None
        if settings.embedding_model.lower() != "none":
            dense = DenseIndex.load(settings.index_dir, settings.embedding_model, settings.model_cache_dir)
            if dense is None or not dense.matches(chunks):
                build_knowledge_base(settings)
                regulation, chunks = load_processed(settings.processed_dir)
                dense = DenseIndex.load(settings.index_dir, settings.embedding_model, settings.model_cache_dir)
        retriever = HybridRetriever(
            chunks, dense, reranker_model=settings.reranker_model, cache_dir=settings.model_cache_dir
        )

        if llm is None:
            try:
                llm = make_llm(settings)
            except ValueError as exc:
                log.warning("%s - running in extractive mode (no LLM)", exc)
        return cls(regulation, chunks, retriever, llm, settings)

    def for_provider(self, provider: str | None = None, model: str | None = None) -> QAEngine:
        """Same index, different LLM (e.g. compare Claude, GPT and Mistral on one question)."""
        if not provider and not model:
            return self
        key = ((provider or self.settings.llm_provider).lower(), model or "")
        allowed_providers = {p.strip() for p in self.settings.allowed_providers.split(",")}
        allowed_models = {m.strip() for m in self.settings.allowed_models.split(",")}
        if key[0] not in allowed_providers:
            raise ValueError("The selected LLM provider is not enabled for this deployment")
        if key[1] and key[1] not in allowed_models:
            raise ValueError("The selected LLM model is not enabled for this deployment")
        if key not in self._variants:
            llm = make_llm(self.settings, provider=key[0], model=model)
            self._variants[key] = QAEngine(self.regulation, self.chunks, self.retriever, llm, self.settings)
        return self._variants[key]

    def available_providers(self) -> list[dict[str, Any]]:
        return available_providers(self.settings)

    # ------------------------------------------------------------------------------------------ #
    def stats(self) -> dict[str, Any]:
        return {
            "regulation": "Regulation (EU) 2024/1689 (AI Act)",
            "source": self.regulation.source_url,
            "source_sha256": self.regulation.source_sha256,
            "articles": len(self.regulation.articles),
            "recitals": len(self.regulation.recitals),
            "annexes": len(self.regulation.annexes),
            "chunks": len(self.chunks),
            "retrieval": self.retriever.mode,
            "embedding_model": self.settings.embedding_model if self.retriever.dense else None,
            "reranker": self.settings.reranker_model or None,
            "llm": {
                "mode": "llm" if self.llm else "extractive",
                "provider": self.llm.provider if self.llm else "extractive",
                "model": self.llm.model if self.llm else None,
            },
        }

    def get_provision(self, provision_id: str) -> Provision | None:
        return self._provisions.get(provision_id)

    # ------------------------------------------------------------------------------------------ #
    def _to_sources(self, result: RetrievalResult, top_k: int) -> list[Source]:
        sources: list[Source] = []
        words = 0
        for hit in result.hits[:top_k]:
            n_words = hit.chunk.word_count
            if sources and words + n_words > self.settings.max_context_words:
                break
            words += n_words
            c = hit.chunk
            sources.append(
                Source(
                    n=len(sources) + 1, chunk_id=c.id, provision_id=c.provision_id, kind=c.kind,
                    label=c.label, title=c.title, text=c.text, url=c.url,
                    score=round(hit.score, 5), retrievers=list(hit.retrievers),
                )
            )
        return sources

    def search(self, query: str, k: int = 8) -> list[Source]:
        result = self.retriever.retrieve(query, k=k, candidate_k=self.settings.candidate_k)
        return self._to_sources(result, k)

    def _standalone_question(self, question: str, history: History) -> str:
        needs_rewrite = bool(history) or not looks_english(question)
        if not needs_rewrite:
            return question
        if self.llm is None:
            last_user = next((t["content"] for t in reversed(history) if t["role"] == "user"), "")
            return f"{last_user} {question}".strip()
        try:
            result = self.llm.complete(
                prompts.CONDENSE_INSTRUCTIONS,
                prompts.condense_messages(question, history),
                effort=self.settings.llm_rewrite_effort,
            )
            rewritten = result.text.strip().strip('"')
            return rewritten if rewritten and not result.refused else question
        except LLMError as exc:
            log.warning("query rewriting failed (%s); using the raw question", exc)
            return question

    def _extractive_answer(self, sources: list[Source], reason: str) -> str:
        lines = [f"_{reason} The most relevant provisions of the AI Act are quoted below._", ""]
        for s in sources[:4]:
            excerpt = " ".join(s.text.split()[:90])
            more = " …" if len(s.text.split()) > 90 else ""
            heading = f"**{s.label}**" + (f" - {s.title}" if s.title else "")
            lines += [heading, f"> {excerpt}{more} [{s.label}]", ""]
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------------------------------ #
    def ask_stream(
        self, question: str, history: History | None = None, top_k: int | None = None,
        identity: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        question = (question or "").strip()
        if not question:
            raise ValueError("question must not be empty")
        if len(question) > self.settings.max_question_chars:
            raise ValueError(f"question longer than {self.settings.max_question_chars} characters")
        history = [
            {"role": t["role"], "content": t["content"][:self.settings.max_history_chars_per_turn]}
            for t in (history or []) if t.get("role") in {"user", "assistant"} and isinstance(t.get("content"), str)
        ][-2 * self.settings.max_history_turns:]
        top_k = min(top_k or self.settings.top_k, 20)
        # Reserve before the rewrite call: even rewriting consumes billable LLM tokens.
        # identity=None marks a trusted local caller (CLI, notebook, evaluation): no quota.
        if identity is not None:
            self.budget.consume(identity, billable=self.llm is not None)

        standalone = self._standalone_question(question, history)
        result = self.retriever.retrieve(standalone, k=top_k, candidate_k=self.settings.candidate_k)
        off_topic = (
            not result.references
            and result.best_dense_similarity is not None
            and result.best_dense_similarity < self.settings.min_relevance
        )
        sources = [] if off_topic else self._to_sources(result, top_k)
        yield {"type": "sources", "sources": [s.to_dict() for s in sources], "standalone_question": standalone}

        answer = Answer(question=question, answer="", sources=sources, standalone_question=standalone)
        if off_topic:
            answer.mode, answer.answer = "out_of_scope", prompts.OUT_OF_SCOPE_ANSWER
        elif self.llm is None:
            answer.mode = "extractive"
            answer.answer = self._extractive_answer(sources, "No LLM is configured (extractive mode).")
        else:
            # History is only used by the query condenser; untrusted historic assistant turns
            # must never become authoritative conversation instructions to the answer model.
            messages = [{"role": "user", "content": prompts.user_message(question, sources)}]
            llm_result = LLMResult()
            try:
                # Buffer model output; streaming unvalidated claims would leak before checks.
                for _ in self.llm.stream(self._system, messages, llm_result):
                    pass
                answer.answer, answer.model, answer.usage = llm_result.text, llm_result.model, llm_result.usage
                if llm_result.refused:
                    answer.mode = "refused"
                    answer.answer = answer.answer or "The model declined to answer this request."
                if llm_result.stop_reason == "max_tokens":
                    answer.warnings.append("The answer was truncated (max_tokens reached).")
            except LLMError as exc:
                log.error("generation failed: %s", exc)
                answer.mode = "extractive"
                answer.warnings.append(f"LLM unavailable ({exc}); showing the retrieved provisions instead.")
                answer.answer = self._extractive_answer(sources, "The language model is unavailable.")
        answer.citations = extract_citations(answer.answer, answer.sources)
        if answer.mode == "llm":
            missing = not answer.citations and not any(
                p in answer.answer.lower() for p in ("cannot determine", "not enough information",
                    "do not contain", "don't contain", "insufficient information")
            )
            warning = unverified_warning(answer.citations)
            if missing or warning or not answer.answer.strip():
                answer.warnings.append(
                    "LLM answer withheld: missing, unsupported or unverified citations. "
                    "Displaying official excerpts instead."
                )
                answer.mode = "extractive"
                answer.answer = self._extractive_answer(sources, "Answer validation did not pass.")
                answer.citations = extract_citations(answer.answer, answer.sources)
        if warning := unverified_warning(answer.citations):
            answer.warnings.append(warning)
        # The only user-visible delta is the post-validation answer.
        yield {"type": "delta", "text": answer.answer}
        answer.latency_ms = int((time.perf_counter() - started) * 1000)
        yield {"type": "done", "answer": answer.to_dict()}

    def ask(
        self, question: str, history: History | None = None, top_k: int | None = None,
        identity: str | None = None,
    ) -> Answer:
        final: dict[str, Any] | None = None
        for event in self.ask_stream(question, history, top_k, identity):
            if event["type"] == "done":
                final = event["answer"]
        assert final is not None
        return Answer.from_dict(final)


def get_engine(settings: Settings | None = None) -> QAEngine:
    return QAEngine.from_settings(settings or get_settings())
