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
import unicodedata
import uuid
from html import unescape

import pytest

from libs import templates

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
    scaffold = split_style(load(SCAFFOLD_CV))[1].rpartition("</style>")[0]
    assert templates.chosen(nobody, "cv").style == f"{scaffold}{templates.EVERY_LOOK}</style>"
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

# A made-up person, from nowhere real, in the markup every CV and letter carries: what the writer fills a
# look's body with. Written here, not made by the platform: no code writes a document since the instant
# one was removed (2026-11-08), and the AI pipeline's own renderer is still to come.
CONTACT = ["Lisbon, Portugal", "+351 912 345 678",
           ("alex.morgan@example.com", "mailto:alex.morgan@example.com"),
           ("linkedin.com/in/alexmorgan", "https://linkedin.com/in/alexmorgan")]
JOBS = [
    ("Senior Project Manager — Northwind Logistics", "Mar 2021 – Present", [
        "Led a team of 12 across three countries to deliver a new warehouse system two weeks early",
        "Cut delivery errors by 38% by redesigning the picking process with the floor teams",
        "Managed a €2.4M budget and reported to the board every month",
        "Brought in weekly planning reviews that every regional office now uses"]),
    ("Project Coordinator — Contoso Retail", "Jun 2017 – Feb 2021", [
        "Planned 40 store openings in Portugal and Spain, each on its date",
        "Built the weekly report six regional managers use to plan their stock",
        "Trained 25 new coordinators in planning and supplier contracts"]),
    ("Operations Assistant — Fabrikam", "Sep 2015 – May 2017", [
        "Scheduled deliveries for 300 customers a week",
        "Kept stock records accurate to 99.5% across two depots"]),
]
# A longer CV, to run every look onto a second page, with a job written without dates.
MORE_JOBS = [
    ("Logistics Analyst — Tailspin Toys", "Jan 2014 – Aug 2015", [
        "Mapped the routes of 120 vans and cut fuel use by 9%",
        "Wrote the weekly stock forecast for 18 shops",
        "Ran the yearly stocktake across three warehouses"]),
    ("Warehouse Supervisor — Wingtip Goods", "Mar 2012 – Dec 2013", [
        "Ran a shift of 14 pickers and loaders with no lost-time accident in two years",
        "Set up the returns desk that handled 900 parcels a week"]),
    ("Volunteer Coordinator — Lisbon Food Bank", "", ["Organised 30 volunteer drivers every weekend"]),
]
MORE_SECTIONS = [
    ("Projects", ["Route planner for a food bank's 30 volunteer drivers",
                  "Stock dashboard for a community shop, used every week since 2019"]),
    ("Publications", ["Morgan, A. (2019). Planning the last mile. Logistics Quarterly, 12(3), 44–51."]),
]


def _entry(title: str, dates: str, bullets) -> str:
    date = f'<span class="entry-date">{dates}</span>' if dates else ""
    items = "".join(f"<li>{bullet}</li>" for bullet in bullets)
    return (f'<div class="entry"><div class="entry-head"><span class="entry-title">{title}</span>{date}</div>'
            f'{f"<ul>{items}</ul>" if items else ""}</div>')


def _section(title: str, body: str) -> str:
    return f'<div class="section"><div class="section-title">{title}</div>{body}</div>'


def _plain(lines) -> str:
    return '<ul class="plain-list">' + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"


def _render(template, *, long: bool = False, name: str = "Alex Morgan", contact=CONTACT) -> str:
    """A whole document in this look, as the docgen worker hands Chrome one."""
    parts = "".join(f'<span><a href="{part[1]}">{part[0]}</a></span>' if isinstance(part, tuple)
                    else f"<span>{part}</span>" for part in contact)
    header = (f'<div class="cv-header"><div class="cv-name">{name}</div><div class="cv-contact">{parts}</div>'
              '</div><hr class="header-rule">')
    if template.kind == "cv":
        jobs = JOBS + (MORE_JOBS if long else [])
        body = (header + '<p class="profile"><strong>Project Manager</strong></p>'
                '<p class="profile">Project manager who plans carefully, keeps people informed and delivers '
                "on time.</p>"
                + _section("Experience", "".join(_entry(*job) for job in jobs))
                + _section("Education", _entry("MSc Management — Nova School of Business and Economics",
                                               "2013 – 2015", ())
                           + _entry("BSc Economics — University of Porto", "2010 – 2013", ()))
                + _section("Skills", '<p class="entry-desc">Project planning · Budgeting · Stakeholder '
                           "management · Supplier contracts · Excel · Power BI · Jira · Lean</p>")
                + _section("Languages", _plain(["Portuguese (native), English (C2), Spanish (B2)"]))
                + _section("Certifications", _plain(["PRINCE2 Practitioner, 2020"]))
                + "".join(_section(title, _plain(lines)) for title, lines in (MORE_SECTIONS if long else [])))
    else:
        body = (header + '<div class="letter-meta"><p class="letter-to">Lumen Energy<br>Amsterdam, '
                'Netherlands</p></div><p class="letter-subject">Application for Project Manager</p>'
                '<div class="letter"><p>Dear Hiring Manager,</p>'
                "<p>I am applying for the Project Manager role at Lumen Energy.</p>"
                "<p>Some of what I have done that fits it:</p>"
                + _plain([bullet for _title, _dates, bullets in JOBS for bullet in bullets[:1]])
                + "<p>I would welcome the chance to talk about it.</p>"
                '<p class="closing">Kind regards,</p><p class="signature">' + name + "</p></div>")
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n'
            f"<title>{name}</title>\n{template.style}\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def _read(document: bytes) -> dict[str, str]:
    """A PDF's text as two kinds of ATS reader take it: pypdf in the order Chrome wrote it, and pdfminer
    by where each word sits on the page, which is how a reader finds columns."""
    from pdfminer.high_level import extract_text
    from pypdf import PdfReader

    return {"pypdf": "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(document)).pages),
            "pdfminer": extract_text(io.BytesIO(document))}


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))


@pytest.mark.skipif(not os.path.exists(CHROME), reason="needs CHROME_BIN: a real Chrome renders the PDF")
@pytest.mark.parametrize("template", templates.CATALOGUE, ids=lambda each: f"{each.kind}-{each.id}")
def test_every_look_makes_a_pdf_an_ats_reads_in_order_in_the_face_it_asked_for(template):
    """What an applicant tracking system reads is the PDF's text, in the order it was written.

    Four looks failed this when they were first drawn (2026-11-07), each looking right on the page: wide
    letter-spacing gave "E X P E R I E N C E", small capitals "A LEX M ORGAN", floated margin headings
    reached the text all together before the name, and a date drawn left of its title ran into it
    ("Northwind LogisticsMar 2021"). Then, read by pdfminer too (2026-11-08): every Lato and Carlito look
    gave "oﬃce" for "office" as ligatures, one character each, and Quill's headings in a margin column
    were read apart from their sections as "E DUC AT I O N".
    """
    from pypdf import PdfReader

    from services.docgen import pdf

    for long in ((False, True) if template.kind == "cv" else (False,)):
        html = _render(template, long=long)
        document = pdf.render(html)
        report = pdf.inspect_pdf(document, kind=template.kind)
        assert report.ats_ok, f"{template.id}: {report.issues}"
        assert report.pages == 1 if template.kind == "cover" else report.pages in (1, 2, 3)

        # Every word it says, whole and nothing else, and its sections in order, by both kinds of reader.
        written = _words(unescape(re.sub(r"<[^>]+>", " ", html.split("<body>", 1)[1])))
        headings = [unescape(each).casefold() for each in re.findall(r'class="section-title">([^<]*)<', html)]
        for name, text in _read(document).items():
            assert not re.search("[\ufb00-\ufb06]", text), f"{template.id}: {name} read a ligature"
            read = _words(text)
            assert read == written, (f"{template.id}: {name} read {sorted(read - written)[:8]} "
                                     f"and lost {sorted(written - read)[:8]}")
            flat = " ".join(text.casefold().split())
            assert flat.startswith("alex morgan"), f"{template.id}: {name} does not start with the name"
            at = [flat.find(heading) for heading in headings]
            assert -1 not in at and at == sorted(at), f"{template.id}: {name} read the headings {at}"

        reader = PdfReader(io.BytesIO(document))
        # Their links can be followed from the PDF, on a CV and a letter alike.
        uris = {annotation.get_object().get("/A", {}).get("/URI") for page in reader.pages
                for annotation in page.get("/Annots") or []}
        assert {"https://linkedin.com/in/alexmorgan", "mailto:alex.morgan@example.com"} <= uris, (
            f"{template.id}: the links are not links: {uris}")
        lines = [line.strip().casefold() for page in reader.pages
                 for line in (page.extract_text() or "").split("\n") if line.strip()]
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
    (2026-11-07). The line now wraps between its parts, and inside a part wider than the line. And no part
    narrower than the line is broken over two: Meridian's broke anywhere, and a phone came out as "91234-"
    over "5678", which every reader read wrongly (2026-11-08)."""
    from pypdf import PdfReader

    from services.docgen import pdf

    links = ["https://www.linkedin.com/in/alexandra-morgan-project-management",
             "https://github.com/alexandra-morgan-portfolio", "https://alexandramorgan-portfolio.example.com",
             # Wider than any look's line at its contact size: ~150 characters.
             "https://example.com/portfolio/" + "a-very-long-address-with-no-break-in-it-" * 3 + "end"]
    phone = "+351 91234-5678"
    shown = [link.split("://")[1].removeprefix("www.") for link in links]
    html = _render(template, name="Alexandra Morgan", contact=[
        "Lisbon, Portugal", phone, ("alex.morgan@example.com", "mailto:alex.morgan@example.com"),
        *zip(shown, links, strict=True)])
    lines = PdfReader(io.BytesIO(pdf.render(html))).pages[0].extract_text().split("\n")
    pdf._discard()
    text = "".join("".join(lines).split())
    parts = [link.split("://")[1].removeprefix("www.") for link in links]
    for part in parts:
        assert part in text, f"{template.id} cut {part} off the page"
    for part in ("Lisbon, Portugal", phone, "alex.morgan@example.com", *parts[:-1]):
        assert any(part in line for line in lines), f"{template.id} broke {part!r} over two lines"


@pytest.mark.skipif(not os.path.exists(CHROME), reason="needs CHROME_BIN: a real Chrome lays the page out")
def test_every_cv_look_puts_a_jobs_dates_at_the_right_and_shows_none_where_there_are_none():
    """A job's dates at the right end of its line in every look, and a job with none has no date and its
    title where the others are (owner, 2026-11-08: "dates ... should go on the right", and where there
    are none, "leave it out"). Tabula had them in a column on the left, and an undated title slid into it.
    Laid out as Chrome prints it."""
    sync_api = pytest.importorskip("playwright.sync_api")
    measure = """() => [...document.querySelectorAll(".entry-head")].map((head) => {
        const box = (part) => part && part.getBoundingClientRect();
        const title = box(head.querySelector(".entry-title")), date = box(head.querySelector(".entry-date"));
        return {title: head.querySelector(".entry-title").innerText, left: title.left, right: title.right,
                end: box(head.closest(".section")).right, date: date && [date.left, date.right]};
    })"""
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 794, "height": 1123})
        page.emulate_media(media="print")
        for template in templates.of_kind("cv"):
            page.set_content(_render(template, long=True))
            heads = page.evaluate(measure)
            dated = [head for head in heads if head["date"]]
            undated = [head for head in heads if not head["date"]]
            assert len(dated) >= 5 and [head["title"] for head in undated] == [
                "Volunteer Coordinator — Lisbon Food Bank"], f"{template.id}: {heads}"
            for head in dated:
                left, right = head["date"]
                assert left >= head["right"] and abs(right - head["end"]) <= 1, (
                    f"{template.id}: {head['title']!r}'s dates are not at the right of its line: {head}")
            assert abs(undated[0]["left"] - dated[0]["left"]) <= 1, f"{template.id}: an undated title moved"
        browser.close()

@pytest.mark.parametrize("template", templates.CATALOGUE, ids=lambda each: f"{each.kind}-{each.id}")
def test_a_picture_is_the_look_in_abstract_shapes_with_nothing_written(template):
    """The picker shows the look, not a document (owner, 2026-11-07): its own style laying out shapes in the
    markup every document carries, and no word, letter or character anywhere in it. Not even a title: in
    the page, a picture's title was the look's name a second time on its card."""
    html = templates.picture(template)
    head, body = html.split("<body>", 1)
    assert template.style in head and f"body {{ padding: {template.page_margin}; }}" in head
    written = re.sub(r"(?s)<style>.*?</style>", "", html)
    assert "".join(re.findall(r">([^<]*)<", written)).strip() == "", f"{template.id}'s picture has writing"
    parts = ("cv-name", "cv-contact", "header-rule") + (
        ("letter-to", "letter-subject", "letter") if template.kind == "cover"
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
        # A name and a picture for each, and nothing else: the descriptions went (owner, 2026-11-07). The
        # pictures come with the shelf, so the sheet draws them without asking for each (2026-11-08).
        assert all(set(named[id_]) == {"id", "name", "picture"} and named[id_]["name"]
                   and named[id_]["picture"] == templates.picture(templates.find(kind, id_))
                   for group in shelf["groups"] for id_ in group["templates"])
    http.cookies.clear()
    assert http.get("/templates").status_code == 401


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
