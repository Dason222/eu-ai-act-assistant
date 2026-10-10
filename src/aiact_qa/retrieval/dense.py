"""Dense retrieval with a local ONNX sentence-embedding model (fastembed) and a numpy vector store.

A few hundred 384-d vectors do not justify a vector database; an exact dot product over a
normalised matrix is ~1 ms. The production design swaps this class for pgvector/Qdrant.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from aiact_qa.models import Chunk

log = logging.getLogger(__name__)


class DenseIndex:
    def __init__(self, model_name: str, cache_dir: Path | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.chunk_ids: list[str] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self._model = None

    # -- model -------------------------------------------------------------------------------------
    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            kwargs = {"cache_dir": str(self.cache_dir)} if self.cache_dir else {}
            log.info("loading embedding model %s", self.model_name)
            self._model = TextEmbedding(self.model_name, **kwargs)
        return self._model

    @staticmethod
    def _normalise(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        vectors = np.array(list(self.model.embed(texts, batch_size=32)), dtype=np.float32)
        return self._normalise(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        vector = np.array(list(self.model.query_embed([text]))[0], dtype=np.float32)
        return self._normalise(vector[None, :])[0]

    # -- index -------------------------------------------------------------------------------------
    def build(self, chunks: list[Chunk]) -> None:
        self.chunk_ids = [c.id for c in chunks]
        self.vectors = self.embed_documents([c.search_text for c in chunks])

    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / "embeddings.npy", self.vectors)
        meta = {"model": self.model_name, "chunk_ids": self.chunk_ids, "dim": int(self.vectors.shape[1])}
        (index_dir / "embeddings.json").write_text(json.dumps(meta), encoding="utf-8")

    @classmethod
    def load(cls, index_dir: Path, model_name: str, cache_dir: Path | None = None) -> DenseIndex | None:
        meta_path = index_dir / "embeddings.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("model") != model_name:
            log.warning("embedding index was built with %s, expected %s", meta.get("model"), model_name)
            return None
        index = cls(model_name, cache_dir)
        index.chunk_ids = meta["chunk_ids"]
        index.vectors = np.load(index_dir / "embeddings.npy")
        return index

    def matches(self, chunks: list[Chunk]) -> bool:
        return self.chunk_ids == [c.id for c in chunks]

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        """Top-k (chunk index, cosine similarity)."""
        if not len(self.chunk_ids):
            return []
        q = self.embed_query(query)
        sims = self.vectors @ q
        top = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in top]
