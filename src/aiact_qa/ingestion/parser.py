"""Structure-aware parser for the EUR-Lex HTML rendition of Regulation (EU) 2024/1689.

The Official Journal HTML (EUR-Lex "CONVEX" format) carries stable element ids, which are used
both to segment the act and to build deep links back to the official text:

* ``rct_N``                    - recital N
* ``cpt_X`` / ``cpt_X.sct_N``  - chapter / section containers (titles in ``<id>.tit_1``)
* ``art_N``                    - article N (title in ``art_N.tit_1``), paragraphs in ``NNN.MMM`` divs
* ``anx_X``                    - annex X (section headings are ``p.oj-ti-grseq-1``)

Enumerated points - (a), (i), 1., 3.1., "—" - are two-cell tables ``[label | content]`` that can be
nested; they are rendered as indented "label text" lines so the structure survives in plain text.
"""

from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning

from aiact_qa.config import ELI_URL
from aiact_qa.models import Chapter, Definition, Paragraph, Provision, Regulation

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_WS = re.compile(r"\s+")
# Anchored: BeautifulSoup applies id patterns with re.search().
_ARTICLE_ID = re.compile(r"^art_(\d+)$")
_RECITAL_ID = re.compile(r"^rct_(\d+)$")
_ANNEX_ID = re.compile(r"^anx_([IVXLC]+)$")
_CHAPTER_ID = re.compile(r"^cpt_([IVXLC]+)$")
_SECTION_ID = re.compile(r"^cpt_([IVXLC]+)\.sct_(\d+)$")
_PARAGRAPH_ID = re.compile(r"^(\d{3})\.(\d{3})$")
_LEADING_LABEL = re.compile(r"^(\(?[0-9]+(?:\.[0-9]+)*[.)]|\([a-z]+\)|\([ivx]+\)|—)\s")
_ACRONYMS = {"ai": "AI", "eu": "EU", "ict": "ICT"}
_INLINE = {"span", "a", "em", "strong", "i", "b", "sup", "sub", "abbr", "q", "u", "font"}


def clean(text: str) -> str:
    """Normalise whitespace and the spacing artefacts of the OJ markup."""
    text = text.replace("\xa0", " ").replace(" ", " ").replace("​", "")
    text = _WS.sub(" ", text).strip()
    text = re.sub(r"\s+([,;:)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return text


def sentence_case(title: str) -> str:
    """'HIGH-RISK AI SYSTEMS' -> 'High-risk AI systems' (chapter titles are upper-case in the OJ)."""
    if not title.isupper():
        return title
    words = title.lower().split()
    words = [_ACRONYMS.get(w.strip(",;:"), w) if w.strip(",;:") in _ACRONYMS else w for w in words]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


# --------------------------------------------------------------------------------------------- #
# Rendering of block elements into text lines
# --------------------------------------------------------------------------------------------- #
def _rows(table: Tag) -> list[Tag]:
    rows = table.find_all("tr", recursive=False)
    for body in table.find_all("tbody", recursive=False):
        rows.extend(body.find_all("tr", recursive=False))
    return rows


def _render_table(table: Tag, depth: int) -> list[str]:
    lines: list[str] = []
    for tr in _rows(table):
        cells = tr.find_all("td", recursive=False)
        if len(cells) >= 2:
            # [label | content] or, in some annexes, [empty | label | content]
            label = " ".join(t for t in (clean(c.get_text()) for c in cells[:-1]) if t)
            body = render(cells[-1], depth + 1)
            if body:
                lines.append("  " * depth + f"{label} {body[0].strip()}".strip())
                lines.extend(body[1:])
            elif label:
                lines.append("  " * depth + label)
        else:
            for cell in cells:
                lines.extend(render(cell, depth))
    return lines


def render(element: Tag, depth: int = 0) -> list[str]:
    """Render the children of ``element`` as text lines (one per paragraph / enumerated point).

    Consecutive inline content (text, spans, links) is joined into a single line; block elements
    (p, div, table) start new lines.
    """
    lines: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = clean("".join(buffer))
        if text:
            lines.append("  " * depth + text)
        buffer.clear()

    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            buffer.append(str(child))
        elif isinstance(child, Tag):
            if child.name in _INLINE and not child.find(["p", "div", "table"]):
                buffer.append(child.get_text())
            else:
                flush()
                lines.extend(render_block(child, depth))
    flush()
    return lines


def render_block(block: Tag, depth: int = 0) -> list[str]:
    if block.name == "table":
        return _render_table(block, depth)
    if block.name in {"div", "tbody", "section"}:
        return render(block, depth)
    if block.name in {"col", "colgroup", "hr", "img", "br", "script", "style"}:
        return []
    text = clean(block.get_text())
    return ["  " * depth + text] if text else []


# --------------------------------------------------------------------------------------------- #
# Provision parsers
# --------------------------------------------------------------------------------------------- #
def _container_heading(container: Tag) -> tuple[str, str]:
    """('CHAPTER III', 'HIGH-RISK AI SYSTEMS') for a chapter/section container."""
    number_el = container.find("p", class_="oj-ti-section-1", recursive=False)
    title_el = container.find(id=f"{container['id']}.tit_1")
    number = clean(number_el.get_text()) if number_el else ""
    title = sentence_case(clean(title_el.get_text())) if title_el else ""
    return number, title


def _breadcrumb(article: Tag) -> tuple[str | None, str | None]:
    chapter = section = None
    for parent in article.parents:
        pid = parent.get("id") if isinstance(parent, Tag) else None
        if not pid:
            continue
        if section is None and (match := _SECTION_ID.fullmatch(pid)):
            section = f"Section {match.group(2)}: {_container_heading(parent)[1]}"
        elif chapter is None and (match := _CHAPTER_ID.fullmatch(pid)):
            chapter = f"Chapter {match.group(1)}: {_container_heading(parent)[1]}"
    return chapter, section


def _parse_article(div: Tag, base_url: str) -> Provision:
    number = _ARTICLE_ID.fullmatch(div["id"]).group(1)
    title_el = div.find(id=f"{div['id']}.tit_1")
    # Article 1's title carries a stray back-tick in the OJ ("Subject matter`").
    title = clean(title_el.get_text()).rstrip("`").strip() if title_el else ""
    paragraphs: list[Paragraph] = []
    loose: list[str] = []
    for child in div.children:
        if not isinstance(child, Tag):
            continue
        child_id = child.get("id") or ""
        if "oj-ti-art" in (child.get("class") or []) or child_id.endswith(".tit_1"):
            continue
        match = _PARAGRAPH_ID.fullmatch(child_id)
        if match:
            lines = render(child)
            paragraphs.append(
                Paragraph(number=str(int(match.group(2))), text="\n".join(lines), anchor=child_id)
            )
        else:
            loose.extend(render_block(child))
    if loose:  # articles without numbered paragraphs (e.g. Articles 3 and 4)
        paragraphs.insert(0, Paragraph(number=None, text="\n".join(loose), anchor=div["id"]))
    chapter, section = _breadcrumb(div)
    return Provision(
        id=div["id"],
        kind="article",
        number=number,
        title=title,
        text="\n".join(p.text for p in paragraphs),
        paragraphs=paragraphs,
        chapter=chapter,
        section=section,
        url=f"{base_url}#{div['id']}",
    )


def _parse_recital(div: Tag, base_url: str) -> Provision:
    number = _RECITAL_ID.fullmatch(div["id"]).group(1)
    text = "\n".join(render(div))
    text = re.sub(rf"^\({number}\)\s*", "", text)
    return Provision(
        id=div["id"],
        kind="recital",
        number=number,
        title="",
        text=text,
        paragraphs=[Paragraph(number=None, text=text, anchor=div["id"])],
        url=f"{base_url}#{div['id']}",
    )


def _parse_annex(div: Tag, base_url: str) -> Provision:
    number = _ANNEX_ID.fullmatch(div["id"]).group(1)
    doc_titles = div.find_all("p", class_="oj-doc-ti", recursive=False)
    title = clean(doc_titles[1].get_text()) if len(doc_titles) > 1 else ""
    blocks: list[Paragraph] = []
    heading: str | None = None
    previous_was_heading = False
    for child in div.children:
        if not isinstance(child, Tag):
            continue
        classes = child.get("class") or []
        if "oj-doc-ti" in classes:
            continue
        if "oj-ti-grseq-1" in classes:  # section heading; consecutive headings are merged
            text = clean(child.get_text())
            heading = f"{heading}: {text}" if previous_was_heading and heading else text
            previous_was_heading = True
            continue
        previous_was_heading = False
        lines = render_block(child)
        if not lines:
            continue
        label_match = _LEADING_LABEL.match(lines[0])
        blocks.append(
            Paragraph(
                number=label_match.group(1) if label_match else None,
                text="\n".join(lines),
                heading=heading,
            )
        )
    text_lines: list[str] = []
    last_heading = None
    for block in blocks:
        if block.heading and block.heading != last_heading:
            text_lines.append(block.heading)
            last_heading = block.heading
        text_lines.append(block.text)
    return Provision(
        id=div["id"],
        kind="annex",
        number=number,
        title=title,
        text="\n".join(text_lines),
        paragraphs=blocks,
        url=f"{base_url}#{div['id']}",
    )


def _parse_definitions(article_3: Tag) -> list[Definition]:
    definitions: list[Definition] = []
    for table in article_3.find_all("table", recursive=False):
        lines = _render_table(table, 0)
        if not lines:
            continue
        match = re.match(r"^\((\d+)\)\s+(.*)$", lines[0])
        if not match:
            continue
        body = "\n".join([match.group(2), *lines[1:]])
        term_match = re.match(r"^[‘'\"]([^’'\"]+)[’'\"]", match.group(2))
        definitions.append(
            Definition(number=int(match.group(1)), term=term_match.group(1) if term_match else "", text=body)
        )
    return definitions


def _parse_chapters(soup: BeautifulSoup) -> list[Chapter]:
    chapters: list[Chapter] = []
    for container in soup.find_all("div", id=_CHAPTER_ID):
        number = _CHAPTER_ID.fullmatch(container["id"]).group(1)
        _, title = _container_heading(container)
        chapter = Chapter(number=number, title=title)
        for article in container.find_all("div", id=_ARTICLE_ID):
            chapter.articles.append(_ARTICLE_ID.fullmatch(article["id"]).group(1))
        for section in container.find_all("div", id=_SECTION_ID, recursive=False):
            sec_number = _SECTION_ID.fullmatch(section["id"]).group(2)
            _, sec_title = _container_heading(section)
            chapter.sections.append(
                {
                    "number": sec_number,
                    "title": sec_title,
                    "articles": [
                        _ARTICLE_ID.fullmatch(a["id"]).group(1)
                        for a in section.find_all("div", id=_ARTICLE_ID)
                    ],
                }
            )
        chapters.append(chapter)
    return chapters


def parse_regulation(html: str, base_url: str = ELI_URL, source_sha256: str = "") -> Regulation:
    """Parse the full act into articles, recitals, annexes, definitions and the chapter structure."""
    soup = BeautifulSoup(html, "lxml")
    # Footnote call-outs such as "( 1 )" are noise for Q&A; the footnotes themselves are not parsed.
    for note_call in soup.select('a[id^="ntc"]'):
        note_call.decompose()

    title_el = soup.find(id="tit_1")
    title = clean(title_el.get_text(" ")) if title_el else "Regulation (EU) 2024/1689"

    articles = [_parse_article(div, base_url) for div in soup.find_all("div", id=_ARTICLE_ID)]
    recitals = [_parse_recital(div, base_url) for div in soup.find_all("div", id=_RECITAL_ID)]
    annexes = [_parse_annex(div, base_url) for div in soup.find_all("div", id=_ANNEX_ID)]
    article_3 = soup.find("div", id="art_3")
    definitions = _parse_definitions(article_3) if article_3 else []

    return Regulation(
        title=title,
        source_url=base_url,
        articles=articles,
        recitals=recitals,
        annexes=annexes,
        definitions=definitions,
        chapters=_parse_chapters(soup),
        source_sha256=source_sha256,
    )
