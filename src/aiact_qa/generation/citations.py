"""Extraction and validation of the citations in a generated answer."""

from __future__ import annotations

import re

from aiact_qa.config import ELI_URL
from aiact_qa.retrieval.references import find_references
from aiact_qa.schemas import Citation, Source

_BRACKETS = re.compile(r"\[([^\[\]]{3,80})\]")


_LABEL_PARAGRAPHS = re.compile(r"^Article \d+\s*\((\d+)\)(?:\([a-z]+\))?(?:-\((\d+)\))?")


def covered_paragraphs(label: str) -> set[str] | None:
    """Paragraphs an excerpt label covers: "Article 50(4)-(6)" -> {4, 5, 6}, "Article 5(1)(a)-(b)" -> {1};
    None when the label names the whole provision ("Article 18", "Recital 27", annex points)."""
    m = _LABEL_PARAGRAPHS.match(label)
    if not m:
        return None
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    return {str(n) for n in range(first, last + 1)}


def extract_citations(answer: str, sources: list[Source], anchors: set[str] | None = None) -> list[Citation]:
    """Every bracketed provision reference, verified against the sources given to the model."""
    # Source-level presence is not enough: verify the cited paragraph was
    # actually sent to the model, rather than merely present in the act.
    available = {s.provision_id for s in sources}
    covered: dict[str, set[str] | None] = {}  # None = the whole provision was sent
    for s in sources:
        paragraphs = covered_paragraphs(s.label)
        if paragraphs is None or covered.get(s.provision_id, set()) is None:
            covered[s.provision_id] = None
        else:
            covered.setdefault(s.provision_id, set()).update(paragraphs)
    urls = {s.provision_id: s.url.split("#")[0] + "#" + s.provision_id for s in sources}
    citations: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for match in _BRACKETS.finditer(answer):
        inner = match.group(1).strip()
        for ref in find_references(inner):
            key = (inner, ref.provision_id)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                Citation(
                    text=inner,
                    provision_id=ref.provision_id,
                    url=_deep_link(ref, urls.get(ref.provision_id, f"{ELI_URL}#{ref.provision_id}"), anchors),
                    verified=(
                        ref.provision_id in available
                        and (not ref.paragraph or covered.get(ref.provision_id) is None
                             or ref.paragraph in covered[ref.provision_id]
                             or any(s.provision_id == ref.provision_id and s.label == ref.text for s in sources))
                    ),
                )
            )
    return citations


def _deep_link(ref, url: str, anchors: set[str] | None) -> str:
    """Point an article citation at its paragraph ("Article 50(5)" -> #050.005) when EUR-Lex has that anchor."""
    if not anchors or not ref.paragraph or not ref.provision_id.startswith("art_"):
        return url
    anchor = f"{int(ref.provision_id[4:]):03d}.{int(ref.paragraph):03d}"
    return f"{url.split('#')[0]}#{anchor}" if anchor in anchors else url


def link_citations(answer: str, citations: list[Citation]) -> str:
    """Markdown with every bracketed citation turned into a link to the official text:
    "[Article 5(1)(f)]" -> "[Article 5(1)(f)](https://eur-lex...#005.001)"."""
    by_text: dict[str, list[Citation]] = {}
    for c in citations:
        by_text.setdefault(c.text, []).append(c)

    def replace(match: re.Match) -> str:
        inner = match.group(1).strip()
        cited = by_text.get(inner)
        if not cited:
            return match.group(0)
        if len(cited) == 1:
            return f"[{inner}]({cited[0].url})"
        out = inner  # several provisions in one bracket: link each reference in place
        for ref, c in zip(find_references(inner), cited, strict=False):
            out = out.replace(ref.text, f"[{ref.text}]({c.url})", 1)
        return f"({out})"

    return _BRACKETS.sub(replace, answer)


def unverified_warning(citations: list[Citation]) -> str | None:
    bad = sorted({c.text for c in citations if not c.verified})
    if not bad:
        return None
    return (
        "Some cited provisions were not among the retrieved excerpts and could not be verified: "
        + ", ".join(bad)
    )
