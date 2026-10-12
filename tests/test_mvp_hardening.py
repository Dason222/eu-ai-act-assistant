"""MVP security and evidence-regression tests."""
from pathlib import Path

import pytest

from aiact_qa.api import client_identity
from aiact_qa.budget import BudgetExceeded, UsageBudget
from aiact_qa.config import Settings
from aiact_qa.generation.citations import extract_citations
from aiact_qa.pipeline import QAEngine
from aiact_qa.retrieval.hybrid import (
    HybridRetriever,
    definition_subject,
    expand_query,
    is_timing_question,
    strip_act_name,
)
from aiact_qa.schemas import Source


def test_global_budget_across_instances(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, daily_request_limit=3,
                        daily_actor_request_limit=2, daily_budget_usd=0.12,
                        reserved_cost_per_llm_request_usd=0.06)
    db = tmp_path / "shared.sqlite3"
    first, second = UsageBudget(db, settings), UsageBudget(db, settings)
    first.consume("a", billable=True)
    second.consume("b", billable=True)
    with pytest.raises(BudgetExceeded):
        first.consume("c", billable=True)
    first.consume("a", billable=False)
    with pytest.raises(BudgetExceeded):
        second.consume("a", billable=False)


def test_exact_paragraph_required():
    source = Source(1, "art_5#2", "art_5", "article", "Article 5(2)",
                    "Prohibited practices", "Paragraph 2", "https://example.test/#art_5")
    result = extract_citations("See [Article 5(2)] and [Article 5(1)].", [source])
    assert [(x.text, x.verified) for x in result] == [
        ("Article 5(2)", True), ("Article 5(1)", False)
    ]


def test_definition_matching(regulation, chunks):
    retriever = HybridRetriever(chunks, dense=None)
    result = retriever.retrieve("What is an AI system under the AI Act?", k=8)
    assert result.hits
    assert result.hits[0].chunk.label == "Article 3(1)"


def test_glossary_matches_whole_words_only():
    assert "penalties" not in expand_query("Define provider")  # substring "fine"
    assert "filter job applications" in expand_query("Is a CV-screening tool high-risk?")
    assert "filter job applications" in expand_query("Can AI rank résumés?")


def test_lay_wording_reaches_annex_iii(chunks):
    retriever = HybridRetriever(chunks, dense=None)
    hits = retriever.retrieve("Is a CV-screening tool a high-risk AI system?", k=5).hits
    assert "anx_III" in {h.chunk.provision_id for h in hits}


def test_definition_boost_only_for_definition_questions(chunks):
    assert definition_subject("What is a provider according to the regulation?") == "provider"
    assert definition_subject("What does 'deep fake' mean?") == "deep fake"
    assert definition_subject("What are the obligations of providers of high-risk AI systems?") == ""
    retriever = HybridRetriever(chunks, dense=None)
    hits = retriever.retrieve("What are the obligations of providers of high-risk AI systems?", k=8).hits
    assert all("exact_definition" not in h.retrievers for h in hits)


def test_client_identity_prefers_proxy_header():
    assert client_identity({"x-real-ip": "203.0.113.7"}, "172.18.0.2") == "ip-203.0.113.7"
    assert client_identity({}, "172.18.0.2") == "ip-172.18.0.2"


def test_local_callers_bypass_quota(tmp_path: Path, regulation, chunks):
    settings = Settings(data_dir=tmp_path, daily_actor_request_limit=1, embedding_model="none")
    engine = QAEngine(regulation, chunks, HybridRetriever(chunks, dense=None), None, settings)
    for _ in range(3):
        engine.ask("Which AI practices are prohibited?")  # identity=None: CLI / eval / notebook
    engine.ask("Which AI practices are prohibited?", identity="visitor")
    with pytest.raises(BudgetExceeded):
        engine.ask("Which AI practices are prohibited?", identity="visitor")


def test_paragraph_ranges_and_whole_articles_verify():
    def src(label, pid):
        return Source(1, pid, pid, "article", label, "", "", "https://example.test/#" + pid)
    sources = [src("Article 50(4)-(6)", "art_50"), src("Article 18", "art_18"), src("Article 5(1)(a)-(b)", "art_5")]
    cited = extract_citations("[Article 50(5)] [Article 18(1)(a)] [Article 5(1)(b)] [Article 50(7)] [Article 5(2)]",
                              sources)
    assert [(c.text, c.verified) for c in cited] == [
        ("Article 50(5)", True), ("Article 18(1)(a)", True), ("Article 5(1)(b)", True),
        ("Article 50(7)", False), ("Article 5(2)", False),
    ]


def test_citations_link_to_the_provision():
    from aiact_qa.generation.citations import link_citations
    from aiact_qa.pipeline import tidy

    def src(label, pid):
        return Source(1, pid, pid, "article", label, "", "", "https://eur-lex.example/eng#" + pid)
    sources = [src("Article 5(1)(f)", "art_5"), src("Article 50(3)", "art_50"), src("Annex III, point 4", "anx_III")]
    text = "Prohibited [Article 5(1)(f)]; see [Article 50(3)] and [Annex III, point 4(a)].\n\n* \n"
    cited = extract_citations(text, sources)
    assert [c.url.split("#")[1] for c in cited] == ["art_5", "art_50", "anx_III"]
    linked = link_citations(tidy(text), cited)
    assert "[Article 5(1)(f)](https://eur-lex.example/eng#art_5)" in linked
    assert "[Annex III, point 4(a)](https://eur-lex.example/eng#anx_III)" in linked
    assert not linked.rstrip().endswith("*")
    assert tidy("- real point\n* \n- another") == "- real point\n- another"


def test_timing_questions_retrieve_article_113(chunks):
    assert is_timing_question("When do the bans on prohibited AI practices apply?")
    assert is_timing_question("What is the application date for GPAI obligations?")
    assert not is_timing_question("When is an AI system high-risk?")
    retriever = HybridRetriever(chunks, dense=None)
    hits = retriever.retrieve("When do the bans on prohibited AI practices apply?", k=8).hits
    assert hits[0].chunk.provision_id == "art_113"


def test_non_existent_provisions_lose_their_brackets():
    from aiact_qa.generation.prompts import unbracket_missing

    text = "I cannot cite [Article 999]; see [Article 5(1)(a)] and [Annex XV], not [Article 5, Article 999]."
    assert unbracket_missing(text) == (
        "I cannot cite Article 999; see [Article 5(1)(a)] and Annex XV, not [Article 5, Article 999]."
    )
    assert unbracket_missing("[OUT_OF_SCOPE]") == "[OUT_OF_SCOPE]"


def test_act_name_does_not_steer_retrieval(chunks):
    query = "What is a deployer under the EU AI Act (Regulation (EU) 2024/1689)?"
    assert strip_act_name(query) == "What is a deployer?"
    assert strip_act_name("What is the AI Act?") == "What is the AI Act?"
    retriever = HybridRetriever(chunks, dense=None)
    hits = retriever.retrieve("What is a deployer under Regulation (EU) 2024/1689 (the EU AI Act)?", k=8).hits
    assert hits[0].chunk.label == "Article 3(4)"


def test_act_name_stripping_is_linear_time():
    import time

    for hostile in (" " * 2000 + "x", " (" * 1000, " under (" * 250, " the eu" * 280):
        start = time.perf_counter()
        strip_act_name(hostile)
        assert time.perf_counter() - start < 0.05
