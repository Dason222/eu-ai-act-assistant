"""Extraction and validation of the citations in a generated answer."""

from __future__ import annotations

import re

from aiact_qa.config import ELI_URL
from aiact_qa.retrieval.references import find_references
from aiact_qa.schemas import Citation, Source

_BRACKETS = re.compile(r"\[([^\[\]]{3,80})\]")


def extract_citations(answer: str, sources: list[Source]) -> list[Citation]:
    """Every bracketed provision reference, verified against the sources given to the model."""
    available = {s.provision_id for s in sources}
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
                    url=urls.get(ref.provision_id, f"{ELI_URL}#{ref.provision_id}"),
                    verified=ref.provision_id in available,
                )
            )
    return citations


def unverified_warning(citations: list[Citation]) -> str | None:
    bad = sorted({c.text for c in citations if not c.verified})
    if not bad:
        return None
    return (
        "Some cited provisions were not among the retrieved excerpts and could not be verified: "
        + ", ".join(bad)
    )
