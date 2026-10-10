"""REST API (with a fake engine) and the evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiact_qa.api import create_app
from aiact_qa.config import Settings
from aiact_qa.evaluation.dataset import EvalItem, load_dataset, validate_dataset
from aiact_qa.evaluation.metrics import answer_metrics, retrieval_metrics
from aiact_qa.evaluation.runner import run_evaluation
from aiact_qa.schemas import Answer, Citation, Source

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Source(1, "art_5#1.1", "art_5", "article", "Article 5(1)(a)-(b)", "Prohibited AI practices",
                "1. The following AI practices shall be prohibited", "https://x#art_5", 0.03, ["bm25"])


class FakeEngine:
    def __init__(self) -> None:
        self.provider = None

    def stats(self) -> dict:
        return {"chunks": 1, "llm": {"mode": "extractive"}}

    def available_providers(self) -> list[dict]:
        return [{"id": "extractive", "configured": True}]

    def for_provider(self, provider=None, model=None):
        if provider not in (None, "extractive", "anthropic"):
            raise ValueError(f"unknown LLM provider '{provider}'")
        self.provider = provider
        return self

    def ask(self, question, history=None, top_k=None) -> Answer:
        return Answer(question=question, answer="Prohibited [Article 5(1)(a)].", sources=[SOURCE],
                      citations=[Citation("Article 5(1)(a)", "art_5", "https://x#art_5", True)], mode="extractive")

    def ask_stream(self, question, history=None, top_k=None):
        yield {"type": "sources", "sources": [SOURCE.to_dict()], "standalone_question": question}
        yield {"type": "delta", "text": "Prohibited"}
        yield {"type": "done", "answer": self.ask(question).to_dict()}

    def search(self, query, k=8) -> list[Source]:
        return [SOURCE]

    def get_provision(self, provision_id):
        return None


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="", embedding_model="none")
    with TestClient(create_app(engine=FakeEngine(), settings=settings)) as c:
        yield c


def test_health_ask_search(client):
    assert client.get("/health").json()["status"] == "ok"
    body = client.post("/ask", json={"question": "What is prohibited?"}).json()
    assert body["citations"][0]["verified"] is True
    assert client.post("/ask", json={"question": "x" * 5000}).status_code == 422
    assert client.post("/ask", json={"question": "q", "provider": "nope"}).status_code == 400
    assert client.post("/search", json={"query": "prohibited"}).json()["results"][0]["provision_id"] == "art_5"
    assert client.get("/provisions/art_999").status_code == 404
    assert client.get("/providers").json()["providers"][0]["id"] == "extractive"


def test_stream_and_feedback(client, tmp_path):
    with client.stream("POST", "/ask/stream", json={"question": "q"}) as response:
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["sources", "delta", "done"]
    assert client.post("/feedback", json={"question": "q", "answer": "a", "rating": "up"}).status_code == 200
    record = json.loads((tmp_path / "feedback" / "feedback.jsonl").read_text().splitlines()[0])
    assert record["rating"] == "up" and "timestamp" in record


def test_api_key_enforced(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="secret", embedding_model="none")
    with TestClient(create_app(engine=FakeEngine(), settings=settings)) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/ask", json={"question": "q"}).status_code == 401
        assert c.post("/ask", json={"question": "q"}, headers={"X-API-Key": "secret"}).status_code == 200


def test_metrics():
    item = EvalItem("q1", "q", "penalties", ["art_99"], "", ["7 %"])
    assert retrieval_metrics(item, ["art_5", "art_99", "art_99"], k=2) == {
        "hit": 1.0, "recall": 1.0, "mrr": 0.5, "precision": 0.5}
    answer = Answer("q", "Up to 7% of turnover [Article 99(3)]",
                    citations=[Citation("Article 99(3)", "art_99", "u", True)])
    m = answer_metrics(item, answer)
    assert m["must_contain"] == 1.0 and m["citation_precision"] == 1.0 and m["expected_citation_recall"] == 1.0
    oos = EvalItem("q2", "weather?", "out_of_scope", [], "")
    assert answer_metrics(oos, Answer("q", "x", mode="out_of_scope"))["declined"] == 1.0


def test_runner_end_to_end(tmp_path):
    dataset = tmp_path / "d.jsonl"
    dataset.write_text("\n".join(json.dumps(r) for r in [
        {"id": "a", "question": "q", "category": "prohibition", "expected_provisions": ["art_5"],
         "expected_answer": "", "must_contain": ["prohibited"]},
        {"id": "b", "question": "weather", "category": "out_of_scope", "expected_provisions": [],
         "expected_answer": ""},
    ]))
    summary = run_evaluation(FakeEngine(), dataset, tmp_path / "out")
    assert summary["overall"]["n"] == 2 and summary["by_category"]["prohibition"]["hit"] == 1.0
    assert (tmp_path / "out" / "eval_results.jsonl").exists()


def test_golden_set_is_valid(regulation):
    items = load_dataset(ROOT / "data" / "eval" / "golden_qa.jsonl")
    assert len(items) >= 50
    assert validate_dataset(items, regulation) == []
