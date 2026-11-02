"""Reading a person's documents by rules: their name, contact, places, languages, jobs and skills.

Run on CVs from very different people, as the extractor actually hands them over (CLAUDE.md §0):

- a backend engineer's Word file, built here with python-docx, whose bullets are Word list items and
  so come back with no glyph at all;
- a nurse's two-column PDF and a sales rep's single-column one, both printed by Chrome and read by
  pypdf 6.21 — `tests/golden/cvs/` holds exactly the text pypdf returned: the sidebar first, the
  list bullets gone, a long bullet wrapped onto a second line, the dates at the end of the title line;
- an accountant's LinkedIn "Save to PDF" export, company above title, page footers in the middle;
- a teacher's plain-text CV with the dates first and a letter-spaced, bilingual heading.
"""
# ruff: noqa: RUF001 - CVs are written with en dashes, and the fixtures are written like CVs
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("docx")

import docx

from libs.contracts.config import Language
from libs.documents import extract_text
from libs.documents.read import LANGUAGE_NAMES, read

CVS = Path(__file__).parent / "golden" / "cvs"
TODAY = date(2026, 10, 28)


def _golden(name: str) -> str:
    """A golden text through the same extractor an upload goes through."""
    return extract_text(f"{name}.txt", (CVS / f"{name}.txt").read_bytes()).text


def _engineer_docx() -> str:
    document = docx.Document()
    for line in ("JANE DOE", "Backend Engineer",
                 "Budapest, Hungary | +36 30 123 4567 | jane.doe@example.com | "
                 "linkedin.com/in/janedoe | github.com/janedoe",
                 "PROFILE", "Backend engineer with 7 years building payment systems in Python and Go.",
                 "EXPERIENCE", "Senior Backend Engineer, Acme Payments\tJan 2021 – Present"):
        document.add_paragraph(line)
    for bullet in ("Designed the settlement service in Go, processing 2M transactions a day",
                   "Cut p99 latency from 900 ms to 120 ms by moving reconciliation into PostgreSQL",
                   "Ran the on-call rotation for a team of six"):
        document.add_paragraph(bullet, style="List Bullet")
    document.add_paragraph("Backend Engineer, Globex\tMar 2017 – Dec 2020")
    for bullet in ("Built REST APIs in Python and Django for 40,000 merchants",
                   "Migrated the monolith to Kubernetes on AWS"):
        document.add_paragraph(bullet, style="List Bullet")
    document.add_paragraph("Junior Developer, Initech\tJul 2015 – Feb 2017")
    document.add_paragraph("Maintained internal PHP tools for the finance department", style="List Bullet")
    for line in ("EDUCATION", "BSc Computer Science, Budapest University of Technology\t2011 – 2015",
                 "SKILLS", "Languages & Frameworks: Python, Go, Django, FastAPI",
                 "Data & Infrastructure: PostgreSQL, Kafka, Docker, Kubernetes, AWS",
                 "LANGUAGES", "Hungarian (native), English (C1), German (A2)"):
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return extract_text("cv.docx", buffer.getvalue()).text


def test_a_word_cv_whose_bullets_lost_their_glyphs_is_read_whole():
    """python-docx returns a Word list item as a plain line: the bullets must still be bullets."""
    text = _engineer_docx()
    assert "•" not in text, "the premise: python-docx drops Word's bullet glyphs"

    facts = read({"cv": text}, today=TODAY)

    assert facts.name == "Jane Doe"
    assert (facts.email, facts.phone) == ("jane.doe@example.com", "+36 30 123 4567")
    assert facts.links == ("linkedin.com/in/janedoe", "github.com/janedoe")
    assert (facts.location, facts.places) == ("Budapest, Hungary", ("hungary",))
    # German is listed at A2, which is not a language anybody can work in.
    assert facts.languages == ("hu", "en")
    assert facts.titles == ("Senior Backend Engineer", "Backend Engineer", "Junior Developer")
    assert [job.organisation for job in facts.jobs] == ["Acme Payments", "Globex", "Initech"]
    assert [len(job.bullets) for job in facts.jobs] == [3, 2, 1]
    assert facts.jobs[1].bullets[0] == "Built REST APIs in Python and Django for 40,000 merchants"
    assert facts.skills == ("Python", "Go", "Django", "FastAPI", "PostgreSQL", "Kafka", "Docker",
                            "Kubernetes", "AWS")
    # Jul 2015 to today, rather than the "7 years" the profile was written with.
    assert facts.years == 11
    (education,) = facts.cv.of("education")[0].entries
    assert (education.title, education.organisation, education.dates) == (
        "BSc Computer Science", "Budapest University of Technology", "2011 – 2015")


def test_a_two_column_pdf_is_read_although_its_sidebar_comes_first():
    """pypdf reads the sidebar before the main column, so the name arrives a dozen lines down."""
    text = _golden("nurse_two_column")
    assert text.splitlines()[0] == "CONTACT", "the premise: the first line is the sidebar's heading"

    facts = read({"cv": text}, today=TODAY)

    assert facts.name == "Mary O'Brien"
    assert (facts.email, facts.phone) == ("mary.obrien@mail.ie", "087 123 4567")
    assert facts.places == ("ireland",)
    # Irish is hers but is not a language the platform filters on; her name is not a language.
    assert facts.languages == ("en",)
    assert facts.cv.of("languages")[0].lines == ("English (native)", "Irish (conversational)")
    assert facts.titles == ("Staff Nurse", "Healthcare Assistant")
    assert [job.organisation for job in facts.jobs] == ["St. James's Hospital", "Beaumont Hospital"]
    # A bullet pypdf wrapped onto a second line is one bullet again.
    assert ("Administer medication and IV therapy, and monitor patients after cardiac procedures"
            in facts.jobs[0].bullets)
    assert "Wound care" in facts.skills
    assert facts.years == 8


def test_a_pdf_with_the_dates_at_the_end_of_the_title_line():
    """A flex row flattens to "Title | Company | City Jan 2022 – Present", and bullets keep their dash."""
    facts = read({"cv": _golden("sales_dates_at_line_end")}, today=TODAY)

    assert facts.name == "Carlos Mendes"
    assert facts.phone == "+351 912 345 678"
    # "the UK" in his summary is a market he sold to, not a place he lives.
    assert facts.places == ("portugal",), "Lisbon, offered as the country it is in"
    assert facts.languages == ("pt", "en", "es")
    assert [(job.title, job.organisation, job.dates) for job in facts.jobs] == [
        ("Account Executive", "Salesforce", "Jan 2022 – Present"),
        ("Sales Development Representative", "Zendesk", "Sep 2019 – Dec 2021"),
        ("Retail Sales Associate", "FNAC", "2017 – 2019")]
    assert facts.jobs[0].bullets[0] == "Closed €1.2M in new business in FY2024, 134% of quota"
    assert facts.skills[:3] == ("Salesforce CRM", "HubSpot", "Cold calling")
    assert facts.years == 9


def test_a_linkedin_export_with_the_company_above_the_title():
    facts = read({"linkedin": _golden("accountant_linkedin_export")}, today=TODAY)

    assert facts.name == "Anna Kovács"
    assert facts.links == ("www.linkedin.com/in/annakovacs",)
    assert facts.places == ("hungary",)
    # "German (Elementary)" is not a working language.
    assert facts.languages == ("hu", "en")
    assert [(job.title, job.organisation) for job in facts.jobs] == [
        ("Senior Accountant", "Morgan Stanley"), ("Audit Associate", "Deloitte")]
    # LinkedIn's page footer is not something she did.
    assert all("Page" not in bullet for job in facts.jobs for bullet in job.bullets)
    (degree,) = facts.cv.of("education")[0].entries
    assert (degree.title, degree.organisation) == ("Master of Science (MSc)",
                                                   "Corvinus University of Budapest")


def test_a_cv_with_the_dates_first_and_a_letter_spaced_heading():
    facts = read({"cv": _golden("teacher_dates_first")}, today=TODAY)

    assert facts.name == "Sophie Laurent"
    assert facts.phone == "06 12 34 56 78"
    assert facts.places == ("france",)
    # Under "LANGUES / LANGUAGES", in French; Spanish is A1 and left out.
    assert facts.languages == ("fr", "en")
    assert [(job.title, job.organisation) for job in facts.jobs] == [
        ("Primary School Teacher", "École Jules Ferry"), ("Teaching Assistant", "Lycée Victor Hugo (Lyon)")]
    assert facts.jobs[0].bullets[1] == "Introduced a reading programme that lifted reading scores by 15%"
    assert facts.years == 10


def test_contact_details_missing_from_the_cv_come_from_a_cover_letter():
    facts = read({"cv": "Ada Lovelace\nEXPERIENCE\nAnalyst, Babbage & Co\t2019 – 2021\n",
                  "cover_letter": "Dear team,\nI am writing...\nAda Lovelace\nada@example.org\n"
                                  "Mobile: +44 7700 900123"})
    assert (facts.email, facts.phone) == ("ada@example.org", "+44 7700 900123")


@pytest.mark.parametrize("text", ["", "\n\n\n", "•\n–\n—", "!!!", "2019 – 2021", "word " * 60_000,
                                  "EXPERIENCE\nEXPERIENCE\n2019 – present\n2018 – 2019"])
def test_text_the_reader_cannot_follow_gives_no_facts_rather_than_an_error(text):
    facts = read({"cv": text, "history": text})
    assert facts.name is None
    assert facts.titles == ()


def test_every_language_the_platform_offers_has_a_name():
    """The review page labels languages from this table, so a code without a name is a blank box."""
    assert set(LANGUAGE_NAMES) == {language.value for language in Language}
