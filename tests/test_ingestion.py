"""Parser and chunker checked against the structure of the Official Journal publication."""

from aiact_qa.ingestion.chunker import MAX_WORDS
from aiact_qa.ingestion.parser import clean, sentence_case


def test_counts_match_official_journal(regulation):
    assert [int(a.number) for a in regulation.articles] == list(range(1, 114))
    assert [int(r.number) for r in regulation.recitals] == list(range(1, 181))
    assert [a.number for a in regulation.annexes] == [
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII"]
    assert [d.number for d in regulation.definitions] == list(range(1, 69))
    assert len(regulation.chapters) == 13


def test_article_structure_and_metadata(regulation):
    art5 = regulation.by_id()["art_5"]
    assert art5.title == "Prohibited AI practices"
    assert art5.chapter == "Chapter II: Prohibited AI practices"
    assert [p.number for p in art5.paragraphs] == [str(i) for i in range(1, 9)]
    assert "(c) the placing on the market" in art5.paragraphs[0].text
    assert "\n  (i) detrimental or unfavourable treatment" in art5.paragraphs[0].text  # nested point kept
    assert art5.url.endswith("#art_5")
    art9 = regulation.by_id()["art_9"]
    assert art9.section == "Section 2: Requirements for high-risk AI systems"
    assert regulation.by_id()["art_1"].title == "Subject matter"  # stray back-tick in the OJ removed


def test_definitions_and_footnotes(regulation):
    terms = {d.number: d.term for d in regulation.definitions}
    assert terms[1] == "AI system" and terms[4] == "deployer" and terms[60] == "deep fake"
    assert "( 1 )" not in regulation.by_id()["rct_9"].text  # footnote call-outs removed


def test_annex_with_three_column_tables(regulation):
    annex1 = regulation.by_id()["anx_I"]
    assert annex1.word_count > 800
    assert "Directive 2006/42/EC" in annex1.text
    assert "4. Employment, workers’ management" in regulation.by_id()["anx_III"].text


def test_chunks_are_bounded_and_unique(chunks):
    assert len({c.id for c in chunks}) == len(chunks)
    assert max(c.word_count for c in chunks) <= MAX_WORDS
    kinds = {c.kind for c in chunks}
    assert kinds == {"article", "definition", "recital", "annex"}
    labels = {c.label for c in chunks if c.provision_id == "art_5"}
    assert "Article 5(1)(h)" in labels
    split = [c for c in chunks if c.label.startswith("Article 5(1)(")]
    assert all(c.text.startswith("1. The following AI practices shall be prohibited:") for c in split)


def test_text_helpers():
    assert clean("Article\xa05 ( 1 ) ,  text") == "Article 5 (1), text"
    assert sentence_case("HIGH-RISK AI SYSTEMS") == "High-risk AI systems"
    assert sentence_case("EU DATABASE FOR HIGH-RISK AI SYSTEMS") == "EU database for high-risk AI systems"
