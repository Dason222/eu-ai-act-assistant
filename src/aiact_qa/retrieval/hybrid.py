"""Hybrid retrieval: explicit references + BM25 + dense, fused with Reciprocal Rank Fusion (RRF).

Why hybrid for legal text: BM25 nails exact terms ("biometric categorisation", "10^25"), dense
retrieval handles paraphrases ("can my chatbot pretend to be human?" -> Article 50), and explicit
references ("what does Article 26 require?") must win regardless of either score.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from aiact_qa.models import Chunk
from aiact_qa.retrieval.bm25 import BM25Index
from aiact_qa.retrieval.dense import DenseIndex
from aiact_qa.retrieval.references import Reference, find_references

log = logging.getLogger(__name__)

RRF_K = 60
_NUMERIC = re.compile(r"\d+(?:[/^.]\d+)+|\b\d{3,}\b")
# Recitals explain but do not bind; they paraphrase articles heavily and would otherwise crowd them out.
KIND_WEIGHT = {"article": 1.0, "definition": 1.0, "annex": 1.0, "recital": 0.75}
MAX_CHUNKS_PER_PROVISION = 3
# Lay terms -> the regulation's own wording (lexical expansion only; dense retrieval sees the raw query).
GLOSSARY = {
    "chatbot": "AI systems intended to interact directly with natural persons",
    "deepfake": "deep fake image audio video content",
    "facial recognition": "remote biometric identification",
    "fine": "administrative fines penalties",
    "fines": "administrative fines penalties",
    "penalty": "administrative fines penalties",
    "penalties": "administrative fines penalties",
    "gpai": "general-purpose AI model",
    "llm": "general-purpose AI model",
    "foundation model": "general-purpose AI model",
    "when": "entry into force apply from date",
    "sandbox": "AI regulatory sandboxes",
    "social scoring": "social behaviour evaluation classification social score",
    "cv": "recruitment or selection of natural persons filter job applications evaluate candidates employment",
    "cvs": "recruitment or selection of natural persons filter job applications evaluate candidates employment",
    "resume": "recruitment or selection of natural persons filter job applications evaluate candidates employment",
    "resumes": "recruitment or selection of natural persons filter job applications evaluate candidates employment",
    "hiring": "recruitment or selection of natural persons filter job applications evaluate candidates employment",
    "credit score": "creditworthiness credit score natural persons",
    "credit scoring": "creditworthiness credit score natural persons",
    "exam": "education vocational training evaluate learning outcomes",
    "police": "law enforcement authorities",
    "emotion": "emotion recognition system",
}
# whole words only: a substring match would expand "define" with "fine"
_GLOSSARY_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, GLOSSARY), key=len, reverse=True)) + r")\b"
)


def expand_query(query: str) -> str:
    lowered = query.lower().replace("résumé", "resume")
    extra = list(dict.fromkeys(GLOSSARY[m] for m in _GLOSSARY_RE.findall(lowered)))
    return f"{query} {' '.join(extra)}" if extra else query


_DEFINITION_LEAD = re.compile(
    r"^(?:what\s+(?:is|are)\s+|define\s+|(?:the\s+)?(?:definition|meaning)\s+of\s+"
    r"|what\s+does\s+(?=.*\bmean\b))(?:the\s+(?:definition|meaning)\s+of\s+)?",
    re.I,
)
_DEFINITION_TAIL = re.compile(
    r"\s+(?:according to|under|in|within|as defined in|for the purposes of)\s+(?:the\s+)?"
    r"(?:ai act|regulation|act)\b.*$|\s+mean\b.*$",
    re.I,
)


def definition_subject(query: str) -> str:
    """'What is a provider according to the regulation?' -> 'provider'; '' if not a definition question."""
    q = query.strip().strip(" ?.!").replace("\u2019", "'")
    if not _DEFINITION_LEAD.match(q):
        return ""
    q = _DEFINITION_TAIL.sub("", _DEFINITION_LEAD.sub("", q))
    q = re.sub(r"^(?:an?|the)\s+", "", q.strip(" '\"")).strip(" '\"").lower()
    return q if 0 < len(q.split()) <= 6 else ""


# Application dates live only in Article 113; a topical query ("when do the bans apply?") ranks
# Article 5 and Recital 179 above it, so timing questions pull Article 113 in explicitly.
_TIMING = re.compile(
    r"\b(?:when|from\s+what\s+date|by\s+what\s+date)\b.*\b(?:appl(?:y|ies|icable|ication)|start|"
    r"begin|enter|in\s+force|take\s+effect|kick\s+in)\b|\b(?:application|applicability)\s+date|"
    r"\bentry\s+into\s+force\b|\bdeadlines?\b|\btimeline\b",
    re.I,
)


def is_timing_question(query: str) -> bool:
    return bool(_TIMING.search(query))


def _term_forms(term: str) -> set[str]:
    return {term, term + "s", term.removesuffix("s")}


@dataclass
class Hit:
    chunk: Chunk
    score: float
    retrievers: list[str] = field(default_factory=list)
    dense_similarity: float | None = None


@dataclass
class RetrievalResult:
    hits: list[Hit]
    references: list[Reference]
    best_dense_similarity: float | None


class HybridRetriever:
    def __init__(
        self,
        chunks: list[Chunk],
        dense: DenseIndex | None = None,
        reranker_model: str = "",
        cache_dir=None,
    ) -> None:
        self.chunks = chunks
        self.bm25 = BM25Index([c.search_text for c in chunks])
        self.dense = dense if dense is not None and dense.matches(chunks) else None
        if dense is not None and self.dense is None:
            log.warning("dense index does not match the chunks; running lexical-only")
        self._by_provision: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            self._by_provision.setdefault(c.provision_id, []).append(i)
        self._reranker = None
        self._reranker_model = reranker_model
        self._cache_dir = cache_dir

    @property
    def mode(self) -> str:
        return "hybrid" if self.dense else "lexical"

    def _reference_hits(self, references: list[Reference]) -> list[int]:
        out: list[int] = []
        for ref in references:
            idxs = self._by_provision.get(ref.provision_id, [])
            if ref.paragraph:  # prefer the chunk(s) covering the referenced paragraph
                matching = [i for i in idxs if ref.paragraph in self.chunks[i].paragraphs]
                idxs = matching + [i for i in idxs if i not in matching]
            out.extend(idxs[:4])
        return out

    def _rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        if not self._reranker_model or not hits:
            return hits
        if self._reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            kwargs = {"cache_dir": str(self._cache_dir)} if self._cache_dir else {}
            self._reranker = TextCrossEncoder(self._reranker_model, **kwargs)
        scores = list(self._reranker.rerank(query, [h.chunk.search_text for h in hits]))
        for hit, s in zip(hits, scores, strict=True):
            hit.score = float(s) + (100.0 if "reference" in hit.retrievers else 0.0)
        return sorted(hits, key=lambda h: -h.score)

    def retrieve(
        self,
        query: str,
        k: int = 8,
        candidate_k: int = 40,
        use_bm25: bool = True,
        use_dense: bool = True,
        use_references: bool = True,
    ) -> RetrievalResult:
        references = find_references(query) if use_references else []
        fused: dict[int, Hit] = {}

        def add(rank_list: list[int], name: str, weight: float = 1.0) -> None:
            for rank, idx in enumerate(rank_list):
                hit = fused.setdefault(idx, Hit(self.chunks[idx], 0.0))
                kind_weight = 1.0 if name == "reference" else KIND_WEIGHT.get(hit.chunk.kind, 1.0)
                hit.score += weight * kind_weight / (RRF_K + rank + 1)
                if name not in hit.retrievers:
                    hit.retrievers.append(name)

        best_sim = None
        if references:
            # explicit references dominate: weight large enough to outrank any fused score
            add(self._reference_hits(references), "reference", weight=RRF_K)
        if use_bm25:
            # numbers and identifiers ("10^25", "2016/679") are where embeddings are weakest
            lexical_weight = 2.0 if _NUMERIC.search(query) else 1.0
            add([i for i, _ in self.bm25.search(expand_query(query), candidate_k)], "bm25", lexical_weight)
        if use_dense and self.dense is not None:
            dense_hits = self.dense.search(query, candidate_k)
            if dense_hits:
                best_sim = dense_hits[0][1]
            add([i for i, _ in dense_hits], "dense")
            for i, sim in dense_hits:
                fused[i].dense_similarity = sim

        # "What is a provider?" should surface the Article 3 definition of exactly that term, but
        # "What are the obligations of providers?" must not: boost only when the term IS the subject.
        subject = definition_subject(query)
        if subject:
            for idx in self._by_provision.get("art_3", []):
                c = self.chunks[idx]
                match = re.search(r"Definition of '([^']+)'", c.title)
                if c.kind == "definition" and match and subject in _term_forms(match.group(1).lower()):
                    hit = fused.setdefault(idx, Hit(c, 0.0))
                    hit.score += 2
                    if "exact_definition" not in hit.retrievers:
                        hit.retrievers.append("exact_definition")
        if is_timing_question(query):
            for idx in self._by_provision.get("art_113", []):
                hit = fused.setdefault(idx, Hit(self.chunks[idx], 0.0))
                hit.score += 1
                if "application_dates" not in hit.retrievers:
                    hit.retrievers.append("application_dates")
        hits = sorted(fused.values(), key=lambda h: -h.score)
        if self._reranker_model:
            hits = self._rerank(query, hits[: max(k * 3, 20)])
        # diversity: cap chunks per provision (explicitly referenced provisions are exempt)
        referenced = {r.provision_id for r in references}
        per_provision: dict[str, int] = {}
        diverse: list[Hit] = []
        for hit in hits:
            pid = hit.chunk.provision_id
            per_provision[pid] = per_provision.get(pid, 0) + 1
            if pid in referenced or per_provision[pid] <= MAX_CHUNKS_PER_PROVISION:
                diverse.append(hit)
        return RetrievalResult(hits=diverse[:k], references=references, best_dense_similarity=best_sim)
