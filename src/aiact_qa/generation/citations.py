"""Extraction and validation of the citations in a generated answer."""

from __future__ import annotations

import re

from aiact_qa.config import ELI_URL
from aiact_qa.retrieval.references import find_references
from aiact_qa.schemas import Citation, Source

_BRACKETS = re.compile(r"\[([^\[\]]{3,80})\]")


def extract_citations(answer: str, sources: list[Source]) -> list[Citation]:
    """Every bracketed provision reference, verified against the sources given to the model."""
    # Source-level presence is not enough: verify the cited paragraph was
    # actually sent to the model, rather than merely present in the act.
    available = {s.provision_id for s in sources}
    covered = {}
    for s in sources:
        covered.setdefault(s.provision_id, set()).update(
            re.findall(r"\((\d+)\)", s.label)
        )
        if s.kind == "definition":
            covered[s.provision_id].update(re.findall(r"\((\d+)\)", s.label))
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
                    verified=(
                        ref.provision_id in available
                        and (not ref.paragraph or ref.paragraph in covered.get(ref.provision_id, set())
                             or any(s.provision_id == ref.provision_id and s.label == ref.text for s in sources))
                    ),
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
