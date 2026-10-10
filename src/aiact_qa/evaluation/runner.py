"""Evaluation runner: retrieval metrics, answer metrics, optional LLM judge, reports on disk."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from aiact_qa.evaluation.dataset import EvalItem, load_dataset
from aiact_qa.evaluation.metrics import answer_metrics, mean, retrieval_metrics

log = logging.getLogger(__name__)

RETRIEVAL_KEYS = ("hit", "recall", "mrr", "precision")
ANSWER_KEYS = ("citation_precision", "expected_citation_recall", "must_contain", "declined", "resisted",
               "faithfulness", "completeness")

STRATEGIES = {
    "bm25 only": {"use_dense": False, "use_references": False},
    "dense only": {"use_bm25": False, "use_references": False},
    "bm25 + dense (RRF)": {"use_references": False},
    "hybrid + references": {},
}


def compare_retrieval_strategies(retriever: Any, items: list[EvalItem], k: int = 8) -> dict[str, dict]:
    """Retrieval metrics per strategy on the answerable items (standalone questions only)."""
    items = [i for i in items if i.answerable and not i.history]
    table: dict[str, dict] = {}
    for name, flags in STRATEGIES.items():
        rows = []
        for item in items:
            hits = retriever.retrieve(item.question, k=k, **flags).hits
            rows.append(retrieval_metrics(item, [h.chunk.provision_id for h in hits], k))
        table[name] = {key: mean([r[key] for r in rows]) for key in RETRIEVAL_KEYS} | {"n": len(rows)}
    return table


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(rows)}
        for key in (*RETRIEVAL_KEYS, *ANSWER_KEYS):
            value = mean([r["metrics"].get(key) for r in rows])
            if value is not None:
                out[key] = value
        out["latency_ms"] = mean([r.get("latency_ms") for r in rows])
        out["errors"] = sum(1 for r in rows if r.get("error"))
        return out

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
    tokens = defaultdict(int)
    for r in results:
        for key, value in (r.get("usage") or {}).items():
            if isinstance(value, int):
                tokens[key] += value
    return {
        "overall": agg(results),
        "by_category": {c: agg(rows) for c, rows in sorted(by_category.items())},
        "modes": dict(sorted({m: sum(1 for r in results if r.get("mode") == m) for m in
                              {r.get("mode") for r in results}}.items(), key=lambda x: str(x[0]))),
        "token_usage": dict(tokens),
    }


def markdown_table(summary: dict[str, Any]) -> str:
    keys = [k for k in (*RETRIEVAL_KEYS, *ANSWER_KEYS) if k in summary["overall"]]
    lines = ["| category | n | " + " | ".join(keys) + " |", "|---" * (len(keys) + 2) + "|"]
    rows = [("overall", summary["overall"]), *summary["by_category"].items()]
    for name, row in rows:
        cells = [f"{row[k]:.2f}" if isinstance(row.get(k), float) else "-" for k in keys]
        lines.append(f"| {name} | {row['n']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_evaluation(
    engine: Any,
    dataset_path: Path,
    out_dir: Path,
    k: int = 8,
    judge: bool = False,
    limit: int | None = None,
    retrieval_only: bool = False,
) -> dict[str, Any]:
    items = load_dataset(dataset_path)[:limit]
    scorer = None
    if judge:
        from aiact_qa.evaluation.judge import Judge

        scorer = Judge()
    results: list[dict[str, Any]] = []
    for item in items:
        record: dict[str, Any] = {"id": item.id, "category": item.category, "question": item.question}
        metrics: dict[str, Any] = {}
        try:
            if item.answerable and not item.history:
                ranked = [s.provision_id for s in engine.search(item.question, k=k)]
                metrics.update(retrieval_metrics(item, ranked, k))
            if not retrieval_only:
                started = time.perf_counter()
                answer = engine.ask(item.question, history=item.history or None, top_k=k)
                record["latency_ms"] = int((time.perf_counter() - started) * 1000)
                if item.answerable and item.history:  # follow-ups: retrieval judged on the rewritten query
                    metrics.update(retrieval_metrics(item, [s.provision_id for s in answer.sources], k))
                metrics.update(answer_metrics(item, answer))
                if scorer and item.answerable and answer.mode == "llm":
                    graded = scorer.score(item, answer)
                    if graded:
                        metrics["faithfulness"] = graded["faithfulness"]
                        metrics["completeness"] = graded["completeness"]
                        record["judge_reasoning"] = graded.get("reasoning")
                record.update(
                    answer=answer.answer, mode=answer.mode, model=answer.model, usage=answer.usage,
                    cited=[c.text for c in answer.citations],
                    sources=[s.provision_id for s in answer.sources],
                )
        except Exception as exc:  # one failing item must not stop the run
            log.exception("item %s failed", item.id)
            record["error"] = str(exc)
        record["metrics"] = metrics
        results.append(record)

    summary = summarize(results)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "eval_results.jsonl").open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "eval_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(markdown_table(summary))
    return summary
