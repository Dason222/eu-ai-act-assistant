"""Golden evaluation set: loading and validation against the regulation text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from aiact_qa.models import Regulation


@dataclass
class EvalItem:
    id: str
    question: str
    category: str
    expected_provisions: list[str]
    expected_answer: str
    must_contain: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)

    @property
    def answerable(self) -> bool:
        return self.category not in {"out_of_scope"} and bool(self.expected_provisions)


def load_dataset(path: Path) -> list[EvalItem]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(EvalItem(**json.loads(line)))
    return items


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("’", "'")).replace(" %", "%")


def validate_dataset(items: list[EvalItem], regulation: Regulation) -> list[str]:
    """Every expected provision must exist; every must_contain string must occur in one of them."""
    by_id = regulation.by_id()
    problems: list[str] = []
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        problems.append("duplicate item ids")
    for item in items:
        missing = [p for p in item.expected_provisions if p not in by_id]
        if missing:
            problems.append(f"{item.id}: unknown provisions {missing}")
            continue
        corpus = normalise(" ".join(by_id[p].text for p in item.expected_provisions))
        for needle in item.must_contain:
            if normalise(needle) not in corpus:
                problems.append(f"{item.id}: '{needle}' not found in {item.expected_provisions}")
    return problems
