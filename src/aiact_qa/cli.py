"""Command line: ``aiact-qa ingest | ask | search | eval | serve | ui``."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from aiact_qa.config import PROJECT_ROOT, get_settings


def get_engine(provider: str | None = None, model: str | None = None):
    from aiact_qa.pipeline import QAEngine

    return QAEngine.from_settings(get_settings()).for_provider(provider, model)


def cmd_ingest(args: argparse.Namespace) -> int:
    from aiact_qa.ingestion.build import build_knowledge_base

    print(json.dumps(build_knowledge_base(get_settings(), force=args.force), indent=2))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    engine = get_engine(args.provider, args.model)
    if args.json:
        print(json.dumps(engine.ask(args.question, top_k=args.top_k).to_dict(), indent=2, ensure_ascii=False))
        return 0
    final = None
    for event in engine.ask_stream(args.question, top_k=args.top_k):
        if event["type"] == "delta":
            print(event["text"], end="", flush=True)
        elif event["type"] == "done":
            final = event["answer"]
    print("\n\nSources:")
    for s in final["sources"]:
        print(f"  [{s['n']}] {s['label']} - {s['title'] or s['kind']}  {s['url']}")
    for warning in final["warnings"]:
        print(f"\nWarning: {warning}")
    print(f"\n({final['mode']}, {final['model'] or 'no LLM'}, {final['latency_ms']} ms)")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    for s in get_engine().search(args.query, k=args.k):
        print(f"{s.n:>2}. {s.label:<28} {s.score:.4f} {'+'.join(s.retrievers):<20} {s.title[:60]}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from aiact_qa.evaluation.runner import run_evaluation

    settings = get_settings()
    engine = get_engine(args.provider, args.model)
    dataset = Path(args.dataset) if args.dataset else settings.eval_dir / "golden_qa.jsonl"
    out = Path(args.out) if args.out else PROJECT_ROOT / "reports"
    run_evaluation(
        engine, dataset, out, k=args.k, judge=args.judge, limit=args.limit, retrieval_only=args.retrieval_only
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("aiact_qa.api:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    app = PROJECT_ROOT / "app" / "ui.py"
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port),
         "--server.address", args.host]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aiact-qa", description="Q&A over the EU AI Act")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="download, parse, chunk and index the regulation")
    p.add_argument("--force", action="store_true", help="re-download the source from EUR-Lex")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ask", help="ask a question")
    p.add_argument("question")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--provider", help="anthropic | openai | azure_openai | mistral | ollama | ...")
    p.add_argument("--model")
    p.add_argument("--json", action="store_true", help="print the full Answer object as JSON")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("search", help="retrieval only (no LLM)")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("eval", help="run the golden-set evaluation")
    p.add_argument("--dataset")
    p.add_argument("--out")
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--judge", action="store_true", help="LLM-as-judge scoring (needs ANTHROPIC_API_KEY)")
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("serve", help="run the REST API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ui", help="run the Streamlit chat UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8501)
    p.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
