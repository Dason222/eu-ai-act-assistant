"""Small, dependency-free BM25 (Okapi) index over the chunks.

The corpus is tiny (hundreds of chunks), so the index is rebuilt at start-up in well under a second
and nothing needs to be persisted. Tokens are lower-cased, Snowball-stemmed English words; numbers
and legal identifiers ("2024/1689", "10^25") are kept so that exact references score highly.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np
import snowballstemmer

_TOKEN = re.compile(r"[a-z0-9]+(?:[./^-][a-z0-9]+)*")
STOPWORDS = frozenset(
    """a an and are as at be by for from has have in into is it its of on or that the their this to was were
    will with which shall should may such any all other than these those not no""".split()  # noqa: SIM905
)
_stemmer = snowballstemmer.stemmer("english")


def tokenize(text: str) -> list[str]:
    words = _TOKEN.findall(text.lower().replace("’", "'"))
    return _stemmer.stemWords([w for w in words if w not in STOPWORDS and len(w) > 1])


class BM25Index:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs = [tokenize(d) for d in documents]
        self._lengths = np.array([len(d) for d in self._docs], dtype=np.float32)
        self._avg_len = float(self._lengths.mean()) if len(self._docs) else 0.0
        self._tf: list[Counter[str]] = [Counter(d) for d in self._docs]
        df: Counter[str] = Counter()
        for counts in self._tf:
            df.update(counts.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        # inverted index: term -> (doc indices, term frequencies)
        self._postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        acc: dict[str, list[tuple[int, int]]] = {}
        for i, counts in enumerate(self._tf):
            for t, c in counts.items():
                acc.setdefault(t, []).append((i, c))
        for t, pairs in acc.items():
            idx, tf = zip(*pairs, strict=True)
            self._postings[t] = (np.array(idx), np.array(tf, dtype=np.float32))

    def __len__(self) -> int:
        return len(self._docs)

    def scores(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self._docs), dtype=np.float32)
        for term in tokenize(query):
            if term not in self._postings:
                continue
            idx, tf = self._postings[term]
            norm = self.k1 * (1 - self.b + self.b * self._lengths[idx] / self._avg_len)
            scores[idx] += self._idf[term] * tf * (self.k1 + 1) / (tf + norm)
        return scores

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        scores = self.scores(query)
        top = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]
