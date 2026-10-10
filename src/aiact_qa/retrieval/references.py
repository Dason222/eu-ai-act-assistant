"""Explicit legal references: "Article 6(2)", "Art. 5", "Articles 9 to 15", "Annex III", "recital 27".

Used twice: on the question (direct lookup of the referenced provisions, which semantic search
handles poorly) and on the generated answer (citation extraction and validation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_ARABIC_TO_ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]

_ARTICLE = re.compile(
    r"\b(?:Articles?|Arts?\.?)\s*(\d{1,3})((?:\(\w{1,4}\))*)"
    r"(?:\s*(?:to|-|–|and)\s*(\d{1,3})\b)?",
    re.IGNORECASE,
)
_ANNEX = re.compile(r"\bAnnex(?:es)?\s+([IVXL]{1,5}|\d{1,2})\b", re.IGNORECASE)
_RECITAL = re.compile(r"\bRecitals?\s*\(?(\d{1,3})\)?(?:\s*(?:to|-|–|and)\s*\(?(\d{1,3})\)?)?", re.IGNORECASE)


def roman_to_int(numeral: str) -> int:
    total, prev = 0, 0
    for ch in reversed(numeral.upper()):
        value = _ROMAN[ch]
        total += -value if value < prev else value
        prev = max(prev, value)
    return total


def int_to_roman(number: int) -> str:
    out = ""
    for value, numeral in _ARABIC_TO_ROMAN:
        while number >= value:
            out += numeral
            number -= value
    return out


@dataclass(frozen=True)
class Reference:
    provision_id: str   # "art_6", "anx_III", "rct_27"
    paragraph: str | None = None  # "2" for Article 6(2)
    text: str = ""      # matched text


def find_references(text: str, max_range: int = 12) -> list[Reference]:
    """All provisions referenced in ``text``, in order of appearance, without duplicates."""
    found: list[tuple[int, Reference]] = []
    for m in _ARTICLE.finditer(text):
        start, end = int(m.group(1)), m.group(3)
        paragraph = None
        if m.group(2):
            first = re.match(r"\((\d+)\)", m.group(2))
            paragraph = first.group(1) if first else None
        numbers = [start]
        if end and start < int(end) <= start + max_range:
            numbers = list(range(start, int(end) + 1))
        elif end:
            numbers.append(int(end))
        for i, n in enumerate(numbers):
            if 1 <= n <= 113:
                found.append((m.start(), Reference(f"art_{n}", paragraph if i == 0 else None, m.group(0))))
    for m in _ANNEX.finditer(text):
        raw = m.group(1)
        number = int(raw) if raw.isdigit() else roman_to_int(raw)
        if 1 <= number <= 13:
            found.append((m.start(), Reference(f"anx_{int_to_roman(number)}", None, m.group(0))))
    for m in _RECITAL.finditer(text):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        for n in range(start, min(end, start + max_range) + 1):
            if 1 <= n <= 180:
                found.append((m.start(), Reference(f"rct_{n}", None, m.group(0))))
    seen: set[str] = set()
    ordered: list[Reference] = []
    for _, ref in sorted(found, key=lambda x: x[0]):
        if ref.provision_id not in seen:
            seen.add(ref.provision_id)
            ordered.append(ref)
    return ordered
