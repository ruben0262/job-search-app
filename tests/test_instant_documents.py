"""The free CV and cover letter, by code: their own lines, picked for the job, in the scaffold's look."""
# ruff: noqa: RUF001 - CVs are written with en dashes, and the fixtures are written like CVs
from __future__ import annotations

import pathlib
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from libs.documents import extract_text
from libs.documents.instant import render
from libs.profiles import PLACEHOLDER_MARKERS
from libs.templates import SCAFFOLD_CV, load
from services.docgen import pdf

CV = """JANE DOE
Budapest, Hungary | jane.doe@example.com | +36 30 123 4567
PROFILE
Backend engineer who likes boring, reliable services.
EXPERIENCE
Senior Backend Engineer, Acme Payments\tJan 2021 – Present
• Designed the settlement service in Go, processing 2M transactions a day
• Organised the office summer party for 80 people
• Ran the on-call rotation for a team of six
• Wrote the incident review template the whole company uses
• Cut p99 latency from 900 ms to 120 ms by moving reconciliation into PostgreSQL
• Mentored two interns
Backend Engineer, Globex\tMar 2017 – Dec 2020
• Migrated the monolith to Kubernetes on AWS
• Built REST APIs in Python and Django for 40,000 merchants
EDUCATION
BSc Computer Science, Budapest University of Technology\t2011 – 2015
SKILLS
Go, Python, Django, PostgreSQL, Kubernetes, Terraform
LANGUAGES
English (C1), Hungarian (native)
CERTIFICATIONS
AWS Certified Developer, 2022
"""
DOCUMENTS = {"cv": CV}
IDENTITY = {"full_name": "Jane Doe", "links": [{"url": "https://github.com/janedoe", "platform": "github"}]}
JOB = SimpleNamespace(title="Python Developer (m/w/d)", company="Payfast",
                      description="You will build REST APIs in Python and Django on PostgreSQL. "
                                  "Kubernetes is a plus.",
                      location="Budapest", url="https://example.com/jobs/1")


class _Text(HTMLParser):
    """The text a reader sees: every text node outside <head>, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[str] = []
        self._head = False

    def handle_starttag(self, tag, attrs):
        self._head = self._head or tag == "head"

    def handle_endtag(self, tag):
        self._head = self._head and tag != "head"

    def handle_data(self, data):
        if not self._head and data.strip():
            self.nodes.append(data.strip())


def _text(html: str) -> list[str]:
    parser = _Text()
    parser.feed(html)
    return parser.nodes


def test_the_cv_leads_with_the_jobs_and_bullets_the_posting_is_about():
    nodes = _text(render("cv", documents=DOCUMENTS, identity=IDENTITY, job=JOB))
    lines = [node for node in nodes if node.startswith(("Built", "Migrated", "Designed", "Organised",
                                                        "Ran", "Wrote", "Cut", "Mentored"))]

    # Globex is older, and it is where they built Python and Django APIs: it goes first.
    assert nodes.index("Backend Engineer — Globex") < nodes.index("Senior Backend Engineer — Acme Payments")
    assert lines[0] == "Built REST APIs in Python and Django for 40,000 merchants"
    # Acme has six bullets and keeps four: the PostgreSQL one first, the two least relevant gone.
    acme = lines[2:]
    assert len(acme) == 4
    assert acme[0].startswith("Cut p99 latency")
    assert "Mentored two interns" not in nodes
    assert "Wrote the incident review template the whole company uses" not in nodes


def test_every_word_about_them_is_from_their_own_documents():
    """The frame is ours; everything inside it traces to a line they wrote or typed."""
    frame = {"Experience", "Education", "Skills", "Languages", "Certifications",
             "Dear Hiring Manager,", "I am applying for the Python Developer role at Payfast.",
             "Some of what I have done that fits it:", "I would welcome the chance to talk about it.",
             "Kind regards,", "Python Developer"}
    theirs = CV + " ".join([IDENTITY["full_name"], "github.com/janedoe"])
    for kind in ("cv", "cover"):
        for node in _text(render(kind, documents=DOCUMENTS, identity=IDENTITY, job=JOB)):
            if node in frame:
                continue
            # An entry's parts are joined with " — " and skills with " · ": each part is theirs.
            for part in filter(None, (each.strip() for each in re.split(r"[—·]", node))):
                assert part in theirs, f"{kind}: {part!r} is in no document of theirs"


def test_the_cv_says_the_postings_title_only_when_their_documents_back_every_word_of_it():
    def headline(title: str) -> str:
        html = render("cv", documents=DOCUMENTS, identity=IDENTITY,
                      job=SimpleNamespace(**{**vars(JOB), "title": title}))
        return re.search(r'<p class="profile"><strong>(.*?)</strong>', html).group(1)

    assert headline("Backend Engineer - Remote") == "Backend Engineer"
    # They have never written "Rust", so the CV does not claim it: it says what they were.
    assert headline("Senior Rust Engineer") == "Senior Backend Engineer"


def test_the_skills_the_posting_asks_for_come_first():
    html = render("cv", documents=DOCUMENTS, identity=IDENTITY, job=JOB)
    skills = re.search(r'Skills</div>\s*<p class="entry-desc">(.*?)</p>', html).group(1)
    assert skills == ("<strong>Python</strong> · <strong>Django</strong> · <strong>PostgreSQL</strong> · "
                      "<strong>Kubernetes</strong> · Go · Terraform")


def test_the_cover_letter_quotes_their_most_relevant_achievements_and_signs_off_with_their_contact():
    nodes = _text(render("cover", documents=DOCUMENTS, identity=IDENTITY, job=JOB))

    assert nodes[nodes.index("Dear Hiring Manager,") + 1] == (
        "I am applying for the Python Developer role at Payfast.")
    quoted = nodes[nodes.index("Some of what I have done that fits it:") + 1:
                   nodes.index("I would welcome the chance to talk about it.")]
    assert quoted == ["Built REST APIs in Python and Django for 40,000 merchants",
                      "Cut p99 latency from 900 ms to 120 ms by moving reconciliation into PostgreSQL",
                      "Migrated the monolith to Kubernetes on AWS"]
    assert nodes[-3:] == ["Kind regards,", "Jane Doe", "jane.doe@example.com · +36 30 123 4567"]


@pytest.mark.parametrize("kind", ["cv", "cover"])
@pytest.mark.parametrize("documents, job", [
    (DOCUMENTS, JOB),
    # The sparse case is where a template leaks: no contact line, no company, no posting text.
    ({"cv": "EXPERIENCE\nCook\t2020 – present\n"}, SimpleNamespace(
        title=None, company=None, description=None, location=None, url="https://example.com/2")),
])
def test_no_placeholder_or_empty_value_is_left_in_either_document(kind, documents, job):
    html = render(kind, documents=documents, identity={"full_name": "Sam Cook"}, job=job)
    body = html.split("</head>", 1)[1]

    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")
    assert not [marker for marker in PLACEHOLDER_MARKERS if marker in html]
    for leak in ("None", "null", "undefined", "[", "]", "{", "}", "<<", ">>"):
        assert leak not in body, f"{leak!r} in the {kind}"


def test_it_wears_the_scaffold_cvs_style_read_from_the_scaffold_itself():
    """The real block, on lines of its own, and not the comment above it that mentions one."""
    style = re.search(r"(?ms)^<style>$.*?^</style>$", load(SCAFFOLD_CV)).group(0)
    html = render("cv", documents=DOCUMENTS, identity=IDENTITY, job=JOB)
    assert style in html
    assert html.count("<style") == 1


def test_a_nurse_gets_one_built_from_a_two_column_pdf_too():
    nurse = extract_text("cv.txt", (Path(__file__).parent / "golden" / "cvs" / "nurse_two_column.txt")
                         .read_bytes()).text
    job = SimpleNamespace(title="Staff Nurse - Cardiac Ward", company="Mater Hospital", location="Dublin",
                          description="Monitor patients after cardiac procedures and administer IV "
                                      "medication.", url="https://example.com/3")
    nodes = _text(render("cv", documents={"cv": nurse}, identity={}, job=job))

    assert nodes[0] == "Mary O'Brien"
    assert "Staff Nurse" in nodes
    assert nodes[nodes.index("Staff Nurse — St. James's Hospital — Dublin") + 2] == (
        "Administer medication and IV therapy, and monitor patients after cardiac procedures")
    assert "Certifications" in nodes


def test_only_a_cv_or_a_cover_letter_can_be_asked_for():
    with pytest.raises(ValueError, match="cv"):
        render("letter", documents=DOCUMENTS, identity=IDENTITY, job=JOB)


@pytest.mark.skipif(not pathlib.Path(pdf.chrome_binary()).exists()
                    and shutil.which(pdf.chrome_binary()) is None,
                    reason="no Chrome on this machine (set CHROME_BIN to run it)")
def test_the_instant_cv_passes_the_ats_check_docgen_applies():
    """The check that reads "Experience", "Education" and an email out of the compiled PDF."""
    html = render("cv", documents=DOCUMENTS, identity=IDENTITY, job=JOB)
    report = pdf.inspect_pdf(pdf.render(html))

    assert report.ats_ok, report.issues
    assert report.pages == 1
