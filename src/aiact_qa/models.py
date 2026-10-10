"""Domain objects shared by the ingestion, retrieval and generation layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Paragraph:
    """A numbered paragraph of an article, or a top-level block (point) of an annex."""

    number: str | None          # "1", "2" for article paragraphs; "1.", "(a)", "3.1." for annex points
    text: str                   # verbatim text; enumerations rendered as "(a) ..." lines
    anchor: str | None = None   # element id on the EUR-Lex page (e.g. "005.001") for deep links
    heading: str | None = None  # enclosing annex section heading, if any


@dataclass
class Provision:
    """A citable unit of the regulation: an article, a recital or an annex."""

    id: str                     # "art_5", "rct_27", "anx_III" (= EUR-Lex element id)
    kind: str                   # "article" | "recital" | "annex"
    number: str                 # "5", "27", "III"
    title: str = ""
    text: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    chapter: str | None = None  # e.g. "Chapter III: High-risk AI systems"
    section: str | None = None  # e.g. "Section 2: Requirements for high-risk AI systems"
    url: str = ""

    @property
    def label(self) -> str:
        return {"article": "Article", "recital": "Recital", "annex": "Annex"}[self.kind] + f" {self.number}"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Definition:
    """One of the definitions in Article 3."""

    number: int
    term: str
    text: str


@dataclass
class Chapter:
    number: str                  # roman numeral
    title: str
    sections: list[dict[str, Any]] = field(default_factory=list)  # {"number", "title", "articles"}
    articles: list[str] = field(default_factory=list)            # article numbers, in order


@dataclass
class Regulation:
    title: str
    source_url: str
    articles: list[Provision]
    recitals: list[Provision]
    annexes: list[Provision]
    definitions: list[Definition]
    chapters: list[Chapter]
    source_sha256: str = ""

    def provisions(self) -> list[Provision]:
        return [*self.articles, *self.recitals, *self.annexes]

    def by_id(self) -> dict[str, Provision]:
        return {p.id: p for p in self.provisions()}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Regulation:
        def prov(d: dict[str, Any]) -> Provision:
            d = dict(d)
            d["paragraphs"] = [Paragraph(**p) for p in d.get("paragraphs", [])]
            return Provision(**d)

        return cls(
            title=data["title"],
            source_url=data["source_url"],
            articles=[prov(p) for p in data["articles"]],
            recitals=[prov(p) for p in data["recitals"]],
            annexes=[prov(p) for p in data["annexes"]],
            definitions=[Definition(**d) for d in data["definitions"]],
            chapters=[Chapter(**c) for c in data["chapters"]],
            source_sha256=data.get("source_sha256", ""),
        )


@dataclass
class Chunk:
    """Retrieval unit. Small enough for the embedding model, self-describing via ``context``."""

    id: str                     # unique, e.g. "art_5#1", "art_3#def12", "rct_27", "anx_III#2"
    provision_id: str           # parent provision ("art_5")
    kind: str                   # "article" | "definition" | "recital" | "annex" | "overview"
    label: str                  # citation label, e.g. "Article 5(1)", "Article 3(12)", "Annex III"
    title: str
    text: str
    context: str = ""           # breadcrumb: chapter / section / heading
    url: str = ""
    paragraphs: list[str] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        """Text indexed by the retrievers: label + title + breadcrumb give the body its context."""
        head = f"{self.label} - {self.title}" if self.title else self.label
        if self.context:
            head += f" ({self.context})"
        return f"{head}\n{self.text}"

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
