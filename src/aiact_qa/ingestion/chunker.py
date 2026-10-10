"""Structure-aware chunking of the parsed regulation.

Design rules
* Never cross a provision boundary: every chunk belongs to exactly one article / recital / annex,
  so citations stay exact.
* Prefer whole paragraphs. A paragraph longer than ``max_words`` is split between its enumerated
  points, and every piece repeats the paragraph's lead-in sentence ("1. The following AI practices
  shall be prohibited:") so it remains self-explanatory.
* Article 3 is split into one chunk per definition (68 chunks) - "what is X?" questions then hit
  the exact definition.
* ``max_words`` keeps chunk + header inside the 512-token window of the embedding model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from aiact_qa.models import Chunk, Paragraph, Provision, Regulation

MAX_WORDS = 280
_POINT = re.compile(r"^\(([a-z]{1,2}|[ivx]+)\)\s")


def _words(text: str) -> int:
    return len(text.split())


def _units(text: str) -> list[str]:
    """Split a paragraph into top-level units: the lead-in, each point (with its sub-points), ..."""
    units: list[str] = []
    for line in text.split("\n"):
        if line.startswith("  ") and units:
            units[-1] += "\n" + line
        else:
            units.append(line)
    return units


def _pack(units: Iterable[str], max_words: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for unit in units:
        n = _words(unit)
        if current and size + n > max_words:
            groups.append(current)
            current, size = [], 0
        current.append(unit)
        size += n
    if current:
        groups.append(current)
    return groups


def _split_long_text(text: str, max_words: int) -> list[str]:
    """Split a long paragraph between its points; fall back to sentences for running text."""
    units = _units(text)
    if len(units) == 1:  # running text: split on sentence boundaries
        units = re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)
    lead = units[0] if len(units) > 1 and _words(units[0]) <= 60 else None
    body = units[1:] if lead else units
    pieces = []
    for group in _pack(body, max_words - (_words(lead) if lead else 0)):
        pieces.append("\n".join([lead, *group]) if lead else "\n".join(group))
    return pieces


def _point_range(text: str) -> str:
    points = [m.group(1) for line in text.split("\n") if (m := _POINT.match(line))]
    if not points:
        return ""
    return f"({points[0]})" if len(points) == 1 else f"({points[0]})-({points[-1]})"


def _context(provision: Provision) -> str:
    return " | ".join(part for part in (provision.chapter, provision.section) if part)


def _article_chunks(article: Provision, max_words: int) -> list[Chunk]:
    base = dict(
        provision_id=article.id, kind="article", title=article.title,
        context=_context(article), url=article.url,
    )
    if article.word_count <= max_words:
        numbers = [p.number for p in article.paragraphs if p.number]
        return [Chunk(id=article.id, label=article.label, text=article.text, paragraphs=numbers, **base)]

    chunks: list[Chunk] = []
    group: list[Paragraph] = []

    def flush() -> None:
        if not group:
            return
        numbers = [p.number for p in group if p.number]
        if not numbers:
            label = article.label
        elif len(numbers) == 1:
            label = f"{article.label}({numbers[0]})"
        else:
            label = f"{article.label}({numbers[0]})-({numbers[-1]})"
        anchor = group[0].anchor or article.id
        chunks.append(
            Chunk(
                id=f"{article.id}#{numbers[0] if numbers else len(chunks)}",
                label=label,
                text="\n".join(p.text for p in group),
                paragraphs=numbers,
                **{**base, "url": f"{article.url.split('#')[0]}#{anchor}"},
            )
        )
        group.clear()

    for paragraph in article.paragraphs:
        n = _words(paragraph.text)
        if n > max_words:
            flush()
            pieces = _split_long_text(paragraph.text, max_words)
            for i, piece in enumerate(pieces):
                points = _point_range(piece)
                number = paragraph.number
                label = f"{article.label}({number}){points}" if number else article.label
                chunks.append(
                    Chunk(
                        id=f"{article.id}#{number or 'x'}.{i + 1}",
                        label=label,
                        text=piece,
                        paragraphs=[number] if number else [],
                        **{**base, "url": f"{article.url.split('#')[0]}#{paragraph.anchor or article.id}"},
                    )
                )
            continue
        if group and sum(_words(p.text) for p in group) + n > max_words:
            flush()
        group.append(paragraph)
    flush()
    return chunks


def _definition_chunks(regulation: Regulation, article_3: Provision) -> list[Chunk]:
    chunks = []
    for definition in regulation.definitions:
        chunks.append(
            Chunk(
                id=f"{article_3.id}#def{definition.number}",
                provision_id=article_3.id,
                kind="definition",
                label=f"{article_3.label}({definition.number})",
                title=f"Definition of '{definition.term}'" if definition.term else "Definition",
                text=f"({definition.number}) {definition.text}",
                context=f"{_context(article_3)} | Article 3: Definitions",
                url=article_3.url,
                paragraphs=[str(definition.number)],
            )
        )
    return chunks


def _recital_chunks(recital: Provision, max_words: int) -> list[Chunk]:
    pieces = [recital.text] if recital.word_count <= max_words else _split_long_text(recital.text, max_words)
    return [
        Chunk(
            id=recital.id if len(pieces) == 1 else f"{recital.id}#{i + 1}",
            provision_id=recital.id,
            kind="recital",
            label=recital.label,
            title="",
            text=piece,
            context="Preamble (recitals)",
            url=recital.url,
        )
        for i, piece in enumerate(pieces)
    ]


def _annex_chunks(annex: Provision, max_words: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    group: list[Paragraph] = []

    def label_for(blocks: list[Paragraph]) -> str:
        numbers = [b.number.rstrip(".") for b in blocks if b.number and b.number != "—"]
        if not numbers:
            return annex.label
        if len(numbers) == 1:
            return f"{annex.label}, point {numbers[0]}"
        return f"{annex.label}, points {numbers[0]}-{numbers[-1]}"

    def emit(blocks: list[Paragraph], text: str) -> None:
        heading = blocks[0].heading
        context = f"Annex {annex.number}: {annex.title}" + (f" | {heading}" if heading else "")
        chunks.append(
            Chunk(
                id=f"{annex.id}#{len(chunks) + 1}",
                provision_id=annex.id,
                kind="annex",
                label=label_for(blocks),
                title=annex.title,
                text=text,
                context=context,
                url=annex.url,
                paragraphs=[b.number for b in blocks if b.number],
            )
        )

    def flush() -> None:
        if group:
            emit(list(group), "\n".join(b.text for b in group))
            group.clear()

    for block in annex.paragraphs:
        n = _words(block.text)
        if n > max_words:
            flush()
            for piece in _split_long_text(block.text, max_words):
                emit([block], piece)
            continue
        if group and (
            sum(_words(b.text) for b in group) + n > max_words or block.heading != group[0].heading
        ):
            flush()
        group.append(block)
    flush()
    return chunks


def chunk_regulation(regulation: Regulation, max_words: int = MAX_WORDS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for article in regulation.articles:
        if article.number == "3" and regulation.definitions:
            chunks.extend(_definition_chunks(regulation, article))
        else:
            chunks.extend(_article_chunks(article, max_words))
    for annex in regulation.annexes:
        chunks.extend(_annex_chunks(annex, max_words))
    for recital in regulation.recitals:
        chunks.extend(_recital_chunks(recital, max_words))
    ids = [c.id for c in chunks]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate chunk ids: {sorted(duplicates)[:5]}")
    return chunks
