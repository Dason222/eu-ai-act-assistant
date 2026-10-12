"""Retrieval, citations, providers and the engine (lexical-only; LLM clients are mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiact_qa.config import Settings
from aiact_qa.generation.citations import extract_citations
from aiact_qa.generation.llm import ClaudeLLM, LLMResult
from aiact_qa.generation.providers import OpenAICompatibleLLM, available_providers, make_llm
from aiact_qa.pipeline import QAEngine, looks_english
from aiact_qa.retrieval.bm25 import BM25Index, tokenize
from aiact_qa.retrieval.hybrid import HybridRetriever
from aiact_qa.retrieval.references import find_references, int_to_roman, roman_to_int
from aiact_qa.schemas import Source


def test_reference_resolver():
    refs = find_references("Compare Article 6(2), Art. 5 and Articles 9 to 11 with Annex 3 and recital 27")
    assert [(r.provision_id, r.paragraph) for r in refs] == [
        ("art_6", "2"), ("art_5", None), ("art_9", None), ("art_10", None), ("art_11", None),
        ("anx_III", None), ("rct_27", None)]
    assert find_references("Article 999") == []
    assert roman_to_int("XIII") == 13 and int_to_roman(9) == "IX"


def test_bm25_ranks_exact_terms():
    index = BM25Index(["prohibited practices social scoring", "transparency chatbot disclosure", "fines"])
    assert index.search("social scoring", 1)[0][0] == 0
    assert tokenize("The providers shall") == ["provid"]


@pytest.fixture(scope="module")
def retriever(chunks):
    return HybridRetriever(chunks, dense=None)


def test_lexical_retrieval_and_reference_priority(retriever):
    hits = retriever.retrieve("fines for prohibited AI practices", k=8).hits
    assert "art_99" in [h.chunk.provision_id for h in hits]
    result = retriever.retrieve("What does Article 26 require?", k=8)
    assert result.hits[0].chunk.provision_id == "art_26"
    assert "reference" in result.hits[0].retrievers


def test_citation_validation():
    sources = [Source(1, "art_5#1.1", "art_5", "article", "Article 5(1)(a)-(b)", "", "", "https://x/eng#005.001")]
    citations = extract_citations("Banned [Article 5(1)(a)]. Fines [Article 99(3)]. See [1].", sources)
    assert [(c.provision_id, c.verified) for c in citations] == [("art_5", True), ("art_99", False)]
    assert citations[0].url == "https://x/eng#art_5"


def test_language_heuristic():
    assert looks_english("What is a deployer?")
    assert not looks_english("Kas yra diegėjas pagal DI aktą?")


def _settings(tmp_path, **kwargs) -> Settings:
    return Settings(embedding_model="none", llm_provider="extractive", data_dir=tmp_path, **kwargs)


def test_engine_extractive_and_out_of_scope(regulation, chunks, tmp_path):
    engine = QAEngine(regulation, chunks, HybridRetriever(chunks, dense=None), None, _settings(tmp_path))
    answer = engine.ask("Which AI practices are prohibited?")
    assert answer.mode == "extractive" and answer.citations and all(c.verified for c in answer.citations)
    events = list(engine.ask_stream("What does Article 50 require?"))
    assert [e["type"] for e in events][0] == "sources" and events[-1]["type"] == "done"
    with pytest.raises(ValueError):
        engine.ask("x" * 5000)


class FakeLLM:
    provider, model = "fake", "fake-1"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def stream(self, system, messages, result: LLMResult, effort=None):
        self.calls.append({"system": system, "messages": messages, "effort": effort})
        text = "Social scoring is prohibited [Article 5(1)(c)] and fines apply [Article 99(3)]."
        result.text, result.model, result.stop_reason = text, self.model, "end_turn"
        yield text

    def complete(self, system, messages, effort=None):
        self.calls.append({"system": system, "messages": messages, "effort": effort})
        return LLMResult(text="What obligations apply to deployers of high-risk AI systems?")


def test_engine_with_llm_condenses_and_validates(regulation, chunks, tmp_path):
    llm = FakeLLM()
    engine = QAEngine(regulation, chunks, HybridRetriever(chunks, dense=None), llm, _settings(tmp_path))
    history = [{"role": "user", "content": "What must providers do?"}, {"role": "assistant", "content": "..."}]
    answer = engine.ask("and deployers?", history=history)
    assert answer.standalone_question.startswith("What obligations apply to deployers")
    assert llm.calls[0]["effort"] == "low"  # rewrite call
    prompt = llm.calls[1]["messages"][-1]["content"]
    assert "<excerpts>" in prompt and "<question>\nand deployers?\n</question>" in prompt
    assert llm.calls[1]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Table of contents" in llm.calls[1]["system"][0]["text"]
    # Unsupported model claims are withheld rather than shown as validated answers.
    assert answer.mode == "extractive"
    assert any("LLM answer withheld" in w for w in answer.warnings)


def test_claude_request_parameters():
    captured = {}

    class Stream:
        text_stream = iter(["Hello"])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            usage = SimpleNamespace(input_tokens=10, output_tokens=2, cache_read_input_tokens=5,
                                    cache_creation_input_tokens=0)
            return SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn", usage=usage)

    def stream(**params):
        captured.update(params)
        return Stream()

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    llm = ClaudeLLM(client=client)
    result = llm.complete("system", [{"role": "user", "content": "hi"}])
    assert result.text == "Hello" and result.usage["cache_read_input_tokens"] == 5
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_config"] == {"effort": "medium"}
    assert captured["fallbacks"] == "default" and captured["betas"] == ["server-side-fallback-2026-07-01"]


def test_openai_compatible_streaming():
    def chunk(text=None, finish=None, usage=None):
        choices = [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)] if text or finish \
            else []
        return SimpleNamespace(choices=choices, usage=usage, model="mistral-large-latest")

    captured = {}

    def create(**params):
        captured.update(params)
        return iter([chunk("Grounded "), chunk("answer", finish="stop"),
                     chunk(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=3))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = OpenAICompatibleLLM("mistral", "mistral-large-latest", client)
    result = llm.complete([{"type": "text", "text": "SYS"}], [{"role": "user", "content": "q"}])
    assert result.text == "Grounded answer" and result.stop_reason == "stop"
    assert result.usage == {"input_tokens": 100, "output_tokens": 3}
    assert captured["messages"][0] == {"role": "system", "content": "SYS"}
    assert "max_tokens" in captured and "stream_options" not in captured


def test_provider_catalogue(tmp_path, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    settings = _settings(tmp_path)
    ids = {p["id"]: p for p in available_providers(settings)}
    assert {"anthropic", "openai", "azure_openai", "mistral", "ollama", "openai_compatible", "extractive"} <= set(ids)
    assert ids["extractive"]["configured"] and not ids["mistral"]["configured"]
    assert make_llm(settings, provider="extractive") is None
    with pytest.raises(ValueError):
        make_llm(settings, provider="mistral")
    with pytest.raises(ValueError):
        make_llm(settings, provider="nope")
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    llm = make_llm(settings, provider="mistral")
    assert llm.model == "mistral-large-latest" and str(llm.client.base_url).startswith("https://api.mistral.ai")
    # the configured model belongs to the configured provider, never to another one
    other = settings.model_copy(update={"llm_provider": "openai", "llm_model": "gpt-5.4-mini"})
    assert make_llm(other, provider="mistral").model == "mistral-large-latest"
