"""How a CV and a cover letter look: the templates, the picker's shelf, the pick, and what each makes.

A template is a stylesheet over the one markup every document carries (`libs.templates`). The failures
worth a test here are silent: a look whose headings an applicant tracking system cannot read, a font the
docgen image does not have, a sector with nothing in it, a pick that is saved and never used.
"""
# ruff: noqa: RUF001 - CVs are written with en dashes, and the fixtures are written like CVs
from __future__ import annotations

import io
import os
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest

from libs import templates
from libs.documents import instant

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy" / "docker" / "Dockerfile.docgen"
CHROME = os.environ.get("CHROME_BIN", "")


# ── the catalogue ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template", templates.CATALOGUE, ids=lambda each: f"{each.kind}-{each.id}")
def test_every_look_is_an_a4_page_with_margins(template):
    """A look with no page rule is printed on Letter, or with no margin at all, and nothing says so."""
    style = template.style
    assert re.search(r"@page\s*{[^}]*size:\s*A4", style), f"{template.id} is not laid out on A4"
    assert re.search(r"@page\s*{[^}]*margin:", style), f"{template.id} has no @page margin"
    assert "<style" in template.head, f"{template.id}'s head, the AI letter's <HEAD_BLOCK>, has no style"


def test_the_shelf_starts_with_the_most_popular_and_has_every_sector_with_a_look_of_each_kind():
    for kind in templates.KINDS:
        shelf = templates.groups(kind)
        title, popular = shelf[0]
        assert title == "Most popular" and popular == list(templates.POPULAR[kind])
        assert [title for title, _ in shelf[1:]] == list(templates.SECTORS), (
            f"a sector has no {kind} look, so the picker would skip it")
        ids = {each.id for each in templates.of_kind(kind)}
        shown = [id_ for _, group in shelf for id_ in group]
        assert set(shown) <= ids, f"the {kind} shelf names a look that does not exist"
        assert ids <= {id_ for _, group in shelf[1:] for id_ in group}, f"a {kind} look is in no sector"
        assert all(len(set(group)) == len(group) for _, group in shelf)


def test_one_look_can_be_in_several_sectors():
    """The owner's rule: IT, finance and engineering can share a look."""
    assert sum(len(each.sectors) > 1 for each in templates.CATALOGUE) >= len(templates.CATALOGUE) - 1


def test_someone_who_never_picked_gets_the_look_everyone_had():
    """Meridian is the scaffold itself, so an instant CV made by someone who never picked is unchanged."""
    from libs.templates import SCAFFOLD_CV, load, split_style

    nobody = type("U", (), {"id": uuid.uuid4(), "cv_template": None, "cover_template": None})()
    assert templates.chosen(nobody, "cv").style == split_style(load(SCAFFOLD_CV))[1]
    assert templates.chosen(nobody, "cover").id == templates.DEFAULT


def test_a_look_taken_out_after_it_was_picked_makes_the_default_and_says_so(caplog):
    someone = type("U", (), {"id": uuid.uuid4(), "cv_template": "retired-look", "cover_template": None})()
    assert templates.chosen(someone, "cv").id == templates.DEFAULT
    assert "retired-look" in caplog.text


def test_every_face_a_look_asks_for_first_is_installed_and_checked_in_the_docgen_image():
    """Chrome falls back to another face without a word. The image's `fc-list` checks make a missing one
    fail the build; this makes a face a template names but the image never checks fail here."""
    docker = DOCKERFILE.read_text(encoding="utf-8").casefold()
    for template in templates.CATALOGUE:
        body = re.search(r"(?s)\bbody\s*{[^}]*font-family:\s*([^;]+);", template.style)
        assert body, f"{template.id} sets no font for the page"
        face = body.group(1).split(",")[0].strip().strip("'\"")
        assert f'fc-list | grep -qi "{face.casefold()}"' in docker, (
            f"{template.kind}/{template.id} asks for {face!r}, which the docgen image does not check for")


# ── what each look makes ──────────────────────────────────────────────────────────────────────

# A made-up person, from nowhere real, written like an uploaded CV.
SAMPLE_CV = """ALEX MORGAN
Lisbon, Portugal | alex.morgan@example.com | +351 912 345 678
PROFILE
Project manager who plans carefully, keeps people informed and delivers on time.
EXPERIENCE
Senior Project Manager, Northwind Logistics\tMar 2021 – Present
• Led a team of 12 across three countries to deliver a new warehouse system two weeks early
• Cut delivery errors by 38% by redesigning the picking process with the floor teams
• Managed a €2.4M budget and reported to the board every month
• Brought in weekly planning reviews that every regional office now uses
Project Coordinator, Contoso Retail\tJun 2017 – Feb 2021
• Planned 40 store openings in Portugal and Spain, each on its date
• Built the weekly report six regional managers use to plan their stock
• Trained 25 new coordinators in planning and supplier contracts
Operations Assistant, Fabrikam\tSep 2015 – May 2017
• Scheduled deliveries for 300 customers a week
• Kept stock records accurate to 99.5% across two depots
EDUCATION
MSc Management, Nova School of Business and Economics\t2013 – 2015
BSc Economics, University of Porto\t2010 – 2013
SKILLS
Project planning, Budgeting, Stakeholder management, Supplier contracts, Excel, Power BI, Jira, Lean
LANGUAGES
Portuguese (native), English (C2), Spanish (B2)
CERTIFICATIONS
PRINCE2 Practitioner, 2020
"""
SAMPLE_IDENTITY = {"full_name": "Alex Morgan", "links": ["linkedin.com/in/alexmorgan"]}
SAMPLE_JOB = SimpleNamespace(
    title="Project Manager", company="Lumen Energy", location="Amsterdam, Netherlands",
    url="https://example.com/jobs/project-manager",
    description="Lead project planning and budgeting for our warehouse and delivery teams. You will "
                "manage suppliers, report to stakeholders and train coordinators.")


_PREVIEW_TEXT = re.compile(r">([^<]+)<")
_PREVIEW_WORD = re.compile(r"\S+")
# Each word a bar in its own colour, a little under half the line's height. Its letters are there only to
# give it its width, and are not drawn.
_BAR = (".bar { -webkit-text-fill-color: transparent; border-radius: 0.3em; "
        "background: linear-gradient(currentColor, currentColor) 0 62% / 100% 0.5em no-repeat; }")

# A longer CV than the previews' sample, to run every look onto a second page.
LONG = SAMPLE_CV.replace("EDUCATION\n", """Logistics Analyst, Tailspin Toys\tJan 2014 – Aug 2015
• Mapped the routes of 120 vans and cut fuel use by 9%
• Wrote the weekly stock forecast for 18 shops
• Ran the yearly stocktake across three warehouses
Warehouse Supervisor, Wingtip Goods\tMar 2012 – Dec 2013
• Ran a shift of 14 pickers and loaders with no lost-time accident in two years
• Set up the returns desk that handled 900 parcels a week
EDUCATION
""") + """PROJECTS
Route planner for a food bank's 30 volunteer drivers
Stock dashboard for a community shop, used every week since 2019
PUBLICATIONS
Morgan, A. (2019). Planning the last mile. Logistics Quarterly, 12(3), 44–51.
"""


def _render(template, cv: str = SAMPLE_CV) -> str:
    return instant.render(template.kind, documents={"cv": [cv]}, identity=SAMPLE_IDENTITY,
                          job=SAMPLE_JOB, template=template)


@pytest.mark.skipif(not os.path.exists(CHROME), reason="needs CHROME_BIN: a real Chrome renders the PDF")
@pytest.mark.parametrize("template", templates.CATALOGUE, ids=lambda each: f"{each.kind}-{each.id}")
def test_every_look_makes_a_pdf_an_ats_reads_in_order_in_the_face_it_asked_for(template):
    """What an applicant tracking system reads is the PDF's text, in the order it was written.

    Four looks failed this when they were first drawn (2026-11-07), each looking right on the page: wide
    letter-spacing gave "E X P E R I E N C E", small capitals "A LEX M ORGAN", floated margin headings
    reached the text all together before the name, and a date drawn left of its title ran into it
    ("Northwind LogisticsMar 2021").
    """
    from pypdf import PdfReader

    from services.docgen import pdf

    for cv in ((SAMPLE_CV, LONG) if template.kind == "cv" else (SAMPLE_CV,)):
        document = pdf.render(_render(template, cv))
        report = pdf.inspect_pdf(document, kind=template.kind)
        assert report.ats_ok, f"{template.id}: {report.issues}"
        assert report.pages == 1 if template.kind == "cover" else report.pages in (1, 2, 3)

        reader = PdfReader(io.BytesIO(document))
        lines = [line.strip().casefold() for page in reader.pages
                 for line in (page.extract_text() or "").split("\n") if line.strip()]
        assert lines[0] == "alex morgan", f"{template.id} does not start with the name: {lines[:3]}"
        assert not any("logisticsmar" in line for line in lines), f"{template.id} ran a title into its dates"
        if template.kind == "cv":
            heading = next(index for index, line in enumerate(lines) if line.startswith("experience"))
            assert "senior project manager" in " ".join(lines[heading:heading + 2]), (
                f"{template.id}: the Experience heading is not followed by its first job: "
                f"{lines[heading:heading + 3]}")
            assert lines.index(next(line for line in lines if line.startswith("education"))) > heading
        else:
            assert "application for project manager" in lines, f"{template.id} lost the subject line"

        fonts = {str(font.get_object().get("/BaseFont")).split("+")[-1].casefold()
                 for page in reader.pages
                 for font in (page.get("/Resources", {}).get("/Font", {}) or {}).values()}
        face = re.search(r"(?s)\bbody\s*{[^}]*font-family:\s*([^;,]+)", template.style).group(1)
        wanted = face.strip().strip("'\"").replace(" ", "").casefold()
        assert any(font.startswith(wanted) for font in fonts), f"{template.id} fell back to {fonts}"
    pdf._discard()


@pytest.mark.skipif(not os.path.exists(CHROME), reason="needs CHROME_BIN: a real Chrome renders the PDF")
@pytest.mark.parametrize("template", templates.CATALOGUE, ids=lambda each: f"{each.kind}-{each.id}")
def test_a_long_contact_line_stays_on_the_page(template):
    """Each part of the contact line was kept whole, with nothing to break between them, so a person with
    a long email and three links had the last one cut off the page: missing from every new look's PDF
    (2026-11-07). The line now wraps between its parts, and inside a part wider than the line."""
    from pypdf import PdfReader

    from services.docgen import pdf

    links = ["https://www.linkedin.com/in/alexandra-morgan-project-management",
             "https://github.com/alexandra-morgan-portfolio", "https://alexandramorgan-portfolio.example.com",
             # Wider than any look's line at its contact size: ~150 characters.
             "https://example.com/portfolio/" + "a-very-long-address-with-no-break-in-it-" * 3 + "end"]
    html = instant.render(template.kind, documents={"cv": [SAMPLE_CV]}, job=SAMPLE_JOB,
                          identity={"full_name": "Alexandra Morgan", "links": links}, template=template)
    text = "".join(PdfReader(io.BytesIO(pdf.render(html))).pages[0].extract_text().split())
    pdf._discard()
    for link in links:
        assert link.split("://")[1].removeprefix("www.") in text, f"{template.id} cut {link} off the page"


@pytest.mark.parametrize("template", templates.CATALOGUE, ids=lambda each: f"{each.kind}-{each.id}")
def test_a_picture_is_the_look_in_abstract_shapes_with_nothing_written(template):
    """The picker shows the look, not a document (owner, 2026-11-07): its own style laying out shapes in the
    markup every document carries, and no word, letter or character anywhere in it."""
    html = templates.picture(template)
    head, body = html.split("<body>", 1)
    assert template.style in head and f"body {{ padding: {template.page_margin}; }}" in head
    assert f"<title>{template.name}</title>" in head
    assert "".join(re.findall(r">([^<]*)<", body)).strip() == "", f"{template.id}'s picture has writing"
    parts = ("cv-name", "cv-contact", "header-rule") + (
        ("letter-to", "letter-date", "letter-subject", "letter") if template.kind == "cover"
        else ("profile", "section-title", "entry-title", "entry-date"))
    assert all(f'class="{part}"' in body for part in parts)


# ── the picker's API ──────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def http(db, user, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield client


def test_the_picker_shows_the_most_popular_then_each_sector_and_the_look_in_use(http):
    shelves = http.get("/templates").json()
    for kind in ("cv", "cover"):
        shelf = shelves[kind]
        assert shelf["chosen"] == "meridian"
        assert shelf["groups"][0] == {"title": "Most popular", "templates": list(templates.POPULAR[kind])}
        assert len(shelf["groups"]) == 1 + len(templates.SECTORS)
        named = {card["id"]: card for card in shelf["templates"]}
        # A name for each, and nothing else: the descriptions went (owner, 2026-11-07).
        assert all(set(named[id_]) == {"id", "name"} and named[id_]["name"] for group in shelf["groups"]
                   for id_ in group["templates"])


def test_a_pick_is_kept_for_its_own_kind_only(http, db, user):
    from libs.db import get
    from libs.db.models import User

    answer = http.put("/templates/cv", json={"id": "ledger"})
    assert answer.status_code == 200
    assert answer.json()["cv"]["chosen"] == "ledger" and answer.json()["cover"]["chosen"] == "meridian"
    assert http.get("/templates").json()["cv"]["chosen"] == "ledger"
    assert get(db, User, user.id).cv_template == "ledger"


def test_a_look_that_does_not_exist_is_refused_and_nothing_changes(http):
    assert http.put("/templates/cover", json={"id": "quill"}).status_code == 404, "Quill is a CV look only"
    assert http.put("/templates/cv", json={"id": "nope"}).status_code == 404
    assert http.put("/templates/letter", json={"id": "meridian"}).status_code == 422
    assert http.get("/templates").json()["cover"]["chosen"] == "meridian"


def test_a_preview_needs_no_sign_in_and_holds_no_words(http):
    http.cookies.clear()
    answer = http.get("/templates/cv/quill/preview")
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/html")
    assert answer.text == templates.picture(templates.find("cv", "quill"))
    assert http.get("/templates/cv/nope/preview").status_code == 404
    assert http.get("/templates").status_code == 401
