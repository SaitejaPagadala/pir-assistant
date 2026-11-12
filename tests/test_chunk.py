"""Chunker test on a small synthetic paper that mimics the real layout."""
from pipeline.chunk import chunk_pir_paper

META = {"id": "pir-consultation-paper", "title": "PIR paper", "publisher": "IRDAI", "version": "Aug 2026"}


def page(n, text):
    return {"pdf_page": n, "page": n, "text": text}


PAGES = [
    page(1, "PUBLIC INSURANCE REGISTRY\nPUBLIC CONSULTATION PAPER\n"),
    page(2, "INDEX\nSection 1 - Overview of the Public Insurance Registry (PIR)...................... 3\n"
            "8.1 User Cohort 1: Public and policyholders Including Small\nand Medium Enterprises.......... 18\n2\n"),
    page(3, "3\nSection 1 - Overview of the Public Insurance Registry (PIR)\n"
            "1. The Public Insurance Registry is conceptualized as a Digital Public Infrastructure.\n"
            "2. PIR will adopt a DPI-based approach using technology, governance and market.\n"
            "Section 6 \u2013 Legal Anchor2\n"),  # out of sequence heading must be ignored
    page(4, "4\nSection 2 - Strategic Importance of the Public Insurance Registry\n"
            "1. PIR strengthens the sector. Consent is a theme.\n"),
    page(5, "Section 3 - Economic Proposition\n1. Economic text.\n"),
    page(6, "Section 4 - Business case\n1. Business text.\n"),
    page(7, "Section 5 - Design Principles1\n1. Three pillars.\n2. Technology Design:\n"
            "a) Interoperable and Unified, not Uniform: " + ("lorem ipsum dolor. " * 80) + "\n"
            "b) Minimalist building blocks: " + ("sit amet consectetur. " * 80) + "\n"),
    page(8, "Section 6 \u2013 Legal Anchor2\n1. Anchored in the SBSR Act, 2025.\n"),
    page(9, "Section 7 - Challenges faced across stakeholder cohorts\n7.1 Public and policyholders\n"
            "1. The public finds it hard to compare.\n7.2. Insurers\n2. Insurers lack visibility.\n"),
    page(10, "Section 8 \u2013 User Stories by Stakeholder Cohorts\nPIR has been designed through a user-story-led approach.\n"
             "8.1 User Cohort 1: Public and policyholders\n"
             "1: Public assesses suitability of class of products\nappropriate to their needs\n"
             "Users\u2019 Needs\nAs a prospective customer I want suitability.\nCurrent Challenges\nNo neutral tool.\n"
             "2: Public discover and compare insurance products easily\nUsers\u2019 Needs\nCompare products.\n"),
    page(11, "11\n8.2 User Cohort 2: Insurers\nExhibit 6.2\n"
             "3: Insurers identify protection gaps\nUsers\u2019 Needs\nBusiness head wants coverage data.\nPIR enabled Scenario\nAggregated data.\n"),
    page(12, "Section 9 \u2013 Stakeholder Value Exchange\n1. Value exchange intro.\n"),
    page(13, "Section 10 - Privacy\n1. Overview privacy.\n"),
    page(14, "Section 11 - Data Repositories\n1. Overview repos.\n"),
    page(15, "Section 12 - Data Standards\n1. Standards.\n"),
    page(16, "Section 13 - Phasing\n1. Phased.\n"),
    page(17, "Section 14 - Governance\n1. Governance.\n"),
    page(18, "Section 15 - Consultation Questions\nStakeholders are invited to provide views.\n"
             "A. Strategic case and public value of PIR (Section 2 to 4)\n"
             "1. Is there any other outcome that PIR should seek?\n"
             "B. User stories, stakeholder needs and coverage gaps\n"
             "2. Could PIR address additional needs?\n"),
    page(19, "Section 16 - How to send comments\nA. Web consultation.\n"),
    page(20, "Section 17 \u2013 Glossary\n1. API \u2013 Application Programming Interface\n2. B2B \u2013 Business-to-Business\n"
             "3. BQP / BQPs \u2013 Broker Qualified Person /\nBroker Qualified Persons\n"),
]


def run():
    return chunk_pir_paper(PAGES, META)


def test_toc_and_cover_skipped():
    chunks, *_ = run()
    assert all("INDEX" not in c["text"] for c in chunks)
    assert all("PUBLIC CONSULTATION PAPER" not in c["text"] for c in chunks)


def test_numbered_paragraphs_and_cites():
    chunks, *_ = run()
    c = next(c for c in chunks if c["id"].endswith("s1:p2"))
    assert c["section"] == 1 and c["para"] == 2
    assert "DPI-based" in c["text"]
    assert "\u00a71 \u00b62" in c["cite"] and "p.3" in c["cite"]
    assert "consent" in next(c for c in chunks if c["id"].endswith("s2:p1"))["themes"]


def test_heading_footnote_marker_removed():
    chunks, *_ = run()
    assert next(c for c in chunks if c["section"] == 5)["section_title"] == "Design Principles"


def test_long_paragraph_split_at_subitems():
    chunks, *_ = run()
    parts = [c for c in chunks if c["section"] == 5 and c.get("para") == 2]
    assert len(parts) >= 2


def test_subsection_tags_in_section_7():
    chunks, *_ = run()
    p2 = next(c for c in chunks if c["section"] == 7 and c.get("para") == 2)
    assert p2["subsection"] == "Insurers"


def test_stories():
    chunks, stories, *_ = run()
    assert [s["story_no"] for s in stories] == [1, 2, 3]
    assert stories[0]["title"].startswith("Public assesses suitability") and "needs" in stories[0]["title"]
    assert stories[0]["cohort_no"] == 1 and stories[2]["cohort_no"] == 2
    s3 = next(c for c in chunks if c.get("story_no") == 3)
    assert "Aggregated data" in s3["text"] and s3["page_start"] == 11
    # cohort-2 exhibit caption must not leak into story 2
    assert "Exhibit 6.2" not in next(c for c in chunks if c.get("story_no") == 2)["text"]


def test_questions_with_groups():
    chunks, _, questions, _ = run()
    assert [q["question_no"] for q in questions] == [1, 2]
    q2 = next(c for c in chunks if c.get("question_no") == 2)
    assert q2["group"].startswith("B.")


def test_glossary():
    *_, glossary = run()
    assert glossary["API"] == "Application Programming Interface"
    assert glossary["BQP / BQPs"] == "Broker Qualified Person / Broker Qualified Persons"
