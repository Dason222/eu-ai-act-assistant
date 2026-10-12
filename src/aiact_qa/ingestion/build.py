"""Knowledge-base build: fetch the official text, parse, chunk, embed and persist everything."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from aiact_qa.config import LINK_URL, Settings, get_settings
from aiact_qa.ingestion.chunker import chunk_regulation
from aiact_qa.ingestion.parser import parse_regulation
from aiact_qa.models import Chunk, Regulation

log = logging.getLogger(__name__)


def fetch_html(url: str, dest: Path, force: bool = False, timeout: int = 60) -> Path:
    """Download the EUR-Lex HTML rendition (once); the snapshot is versioned with the repository."""
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", url)
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "aiact-qa/0.1 (prototype)"})
    response.raise_for_status()
    if 'id="art_1"' not in response.text:
        raise ValueError("unexpected EUR-Lex response: no article markup found")
    dest.write_text(response.text, encoding="utf-8")
    return dest


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_processed(regulation: Regulation, chunks: list[Chunk], processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    (processed_dir / "regulation.json").write_text(
        json.dumps(regulation.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (processed_dir / "chunks.json").write_text(
        json.dumps([c.to_dict() for c in chunks], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_processed(processed_dir: Path) -> tuple[Regulation, list[Chunk]]:
    regulation = Regulation.from_dict(json.loads((processed_dir / "regulation.json").read_text(encoding="utf-8")))
    chunks = [Chunk(**c) for c in json.loads((processed_dir / "chunks.json").read_text(encoding="utf-8"))]
    return regulation, chunks


def processed_is_fresh(settings: Settings) -> bool:
    processed = settings.processed_dir
    if not (processed / "regulation.json").exists() or not (processed / "chunks.json").exists():
        return False
    if not settings.raw_html_path.exists():
        return True
    try:
        stored = json.loads((processed / "regulation.json").read_text(encoding="utf-8")).get("source_sha256")
    except (OSError, ValueError):
        return False
    return stored == sha256_of(settings.raw_html_path)


def build_knowledge_base(settings: Settings | None = None, force: bool = False) -> dict[str, Any]:
    """Full offline pipeline. Returns statistics for the CLI / notebook."""
    from aiact_qa.retrieval.dense import DenseIndex  # heavy import (onnxruntime) kept local

    settings = settings or get_settings()
    started = time.time()
    raw = fetch_html(settings.source_url, settings.raw_html_path, force=force)
    digest = sha256_of(raw)
    regulation = parse_regulation(raw.read_text(encoding="utf-8"), base_url=LINK_URL, source_sha256=digest)
    chunks = chunk_regulation(regulation)
    save_processed(regulation, chunks, settings.processed_dir)

    stats: dict[str, Any] = {
        "source": settings.source_url,
        "source_sha256": digest,
        "articles": len(regulation.articles),
        "recitals": len(regulation.recitals),
        "annexes": len(regulation.annexes),
        "definitions": len(regulation.definitions),
        "chunks": len(chunks),
        "embedding_model": settings.embedding_model,
    }
    if settings.embedding_model.lower() != "none":
        index = DenseIndex(settings.embedding_model, cache_dir=settings.model_cache_dir)
        index.build(chunks)
        index.save(settings.index_dir)
        stats["embeddings"] = list(index.vectors.shape)
    stats["seconds"] = round(time.time() - started, 1)
    log.info("knowledge base built: %s", stats)
    return stats
