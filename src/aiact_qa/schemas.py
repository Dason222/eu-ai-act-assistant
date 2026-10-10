"""Public result types of the Q&A engine (shared by the API, the UI, the CLI and the evaluation)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Source:
    """A retrieved excerpt that was (or could be) given to the LLM as evidence."""

    n: int                 # 1-based position in the context sent to the LLM
    chunk_id: str          # e.g. "art_5#1.2"
    provision_id: str      # e.g. "art_5", "rct_27", "anx_III"
    kind: str              # "article" | "definition" | "recital" | "annex"
    label: str             # citation label, e.g. "Article 5(1)(c)-(e)"
    title: str
    text: str
    url: str               # deep link into the official text on EUR-Lex
    score: float = 0.0     # fused retrieval score (higher = more relevant)
    retrievers: list[str] = field(default_factory=list)  # which retrievers found it: bm25/dense/reference

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Citation:
    """A provision cited in the generated answer, e.g. ``[Article 5(1)(h)]``."""

    text: str              # as written by the model, without brackets: "Article 5(1)(h)"
    provision_id: str      # normalised: "art_5"
    url: str
    verified: bool         # True if the cited provision is among the sources given to the model

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    question: str
    answer: str                                   # Markdown with inline citations like [Article 6(2)]
    sources: list[Source] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    # "llm" | "extractive" (no LLM configured) | "out_of_scope" | "refused" | "error"
    mode: str = "llm"
    standalone_question: str = ""                 # follow-up rewritten into a self-contained query
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)  # token usage reported by the LLM API
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Answer:
        data = dict(data)
        data["sources"] = [Source(**s) for s in data.get("sources", [])]
        data["citations"] = [Citation(**c) for c in data.get("citations", [])]
        return cls(**data)
