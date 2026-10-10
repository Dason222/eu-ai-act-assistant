from __future__ import annotations

from pathlib import Path

import pytest

from aiact_qa.ingestion.chunker import chunk_regulation
from aiact_qa.ingestion.parser import parse_regulation
from aiact_qa.models import Regulation

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ai_act_L_202401689_EN.html"


@pytest.fixture(scope="session")
def regulation() -> Regulation:
    return parse_regulation(RAW.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def chunks(regulation):
    return chunk_regulation(regulation)
