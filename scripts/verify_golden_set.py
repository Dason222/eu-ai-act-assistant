"""Validate the golden set against the parsed regulation (exits non-zero on problems)."""

import sys
from pathlib import Path

from aiact_qa.evaluation.dataset import load_dataset, validate_dataset
from aiact_qa.ingestion.parser import parse_regulation

ROOT = Path(__file__).resolve().parents[1]

regulation = parse_regulation((ROOT / "data/raw/ai_act_L_202401689_EN.html").read_text(encoding="utf-8"))
items = load_dataset(ROOT / "data/eval/golden_qa.jsonl")
problems = validate_dataset(items, regulation)
for p in problems:
    print("PROBLEM:", p)
print(f"{len(items)} items checked, {len(problems)} problems")
sys.exit(1 if problems else 0)
