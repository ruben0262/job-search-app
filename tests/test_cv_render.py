"""The CV render is the last step before a document goes out under a real person's name.

The rubric decides what a user is shown and the facts block grounds a generation; a mistake in
either is recoverable. This produces the HTML that becomes a PDF attached to a real application, so
the failures worth testing are the ones that still *look correct*: a section that quietly vanished,
somebody else's employment history, a marker that survived into the output as an invisible comment,
or a spoken-languages line built from the wrong field.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from libs.contracts.profile import ProfileSchema
from libs.templates import BASE_CV_TEMPLATE, load
from libs.templates.cv import render_cv

FULL = {
    "identity": {
        "name": "Alice Example",
        "location": "Berlin, Germany",
        "work_eligibility": ["European Union"],
        "links": {"portfolio": "https://alice.dev", "email": "alice@example.com"},
    },
    "education": [{"degree": "MSc Computer Science", "institution": "TU Berlin",
                   "field": "Distributed Systems", "thesis": "Consensus under partition"}],
    "experience": [{"role": "Backend Engineer", "org": "Northwind", "start": "2021",
                    "end": "present", "bullets": ["Built the billing service"],
                    "stack": ["Python", "Postgres"], "metrics": ["cut p99 to 40ms"]}],
    "projects": [{"name": "Tideline", "what_built": "A tide forecast API",
                  "stack": ["Go"], "metrics": ["3k requests/day"]}],
    "certifications": [{"name": "Solutions Architect", "issuer": "AWS", "year": "2024"}],
    "skills": {"languages": ["Python", "Go"], "frameworks": ["FastAPI"],
               "domains": ["Payments"], "tools": ["Docker"]},
    "targets": {"role_types": ["backend engineer"], "seniority": "Senior"},
    "constraints": {"languages": ["English (Native)", "German (C1)"]},
    "confirmed": True,
}

MARKER = re.compile(r"<!--[A-Z_]+-->")


def profile(**overrides) -> ProfileSchema:
    return ProfileSchema.model_validate({**FULL, **overrides})


def _body(rendered: str) -> str:
    """What the reader sees. The template's own doc comment names every section, so a test that
    asserts a section is *absent* has to read past it or it is asserting nothing."""
    return rendered.split("<body>", 1)[1]


# ── the template itself ───────────────────────────────────────────────────────────────────────

def test_the_template_carries_no_employment_history():
    """The identity check in `test_prompt_templates.py` covers the name and the contact details.

    This covers the rest of a person: an employer, a thesis, or a certification left in the layout
    would be a claim on every user's CV that no profile field put there and no test of the renderer
    would catch, because the renderer never touches it.
    """
    template = load(BASE_CV_TEMPLATE)

    for leak in ("Syntagma", "FlowSolutions", "Sheffield", "Bloomberg", "ByBit",
                 "Hungarian", "BEng", "All Saints"):
        assert leak not in template, f"{leak} is still in the CV template"


def test_the_template_keeps_only_the_sections_no_profile_fills():
    """References is the one section with no field behind it, and it is a fixed sentence.

    Everything else is a marker. Certifications was briefly dropped for want of a schema field, and
    that was the wrong direction: dropping the section silently removes a credential the candidate
    actually holds, so `ProfileSchema` grew `certifications` instead (schema version 2).
    """
    template = load(BASE_CV_TEMPLATE)

    assert "Available upon request" in template
    assert "<!--CERTIFICATIONS-->" in template


def test_a_certification_reaches_the_document_with_its_issuer_and_year():
    """The most checkable claim on a CV: an employer can verify it with the issuer in a minute."""
    section = _body(render_cv(profile())).split("Certifications</div>", 1)[1]

    assert "Solutions Architect" in section
    assert "AWS" in section and "2024" in section


def test_a_certification_with_no_issuer_or_year_still_renders():
    """Half a certification is what a CV extract usually yields, and it is still a credential."""
    section = _body(render_cv(profile(certifications=[{"name": "First aid"}]))).split(
        "Certifications</div>", 1)[1]

    assert "First aid" in section
    assert "()" not in section


# ── every marker is filled ────────────────────────────────────────────────────────────────────

def test_no_marker_survives_a_render():
    """A marker that reaches the PDF is an HTML comment: invisible on the page, and the content it
    stood for is permanently missing. Nothing downstream would report it."""
    assert not MARKER.search(render_cv(profile()))


def test_a_minimal_profile_leaves_no_marker_either():
    """The empty case is the one that leaks a marker, because most sections take the other branch."""
    assert not MARKER.search(render_cv(ProfileSchema.model_validate(
        {"identity": {"name": "Bo"}, "confirmed": True})))


def test_the_document_is_about_the_person_it_was_rendered_for():
    rendered = render_cv(profile())

    assert "Alice Example" in rendered
    assert "Backend Engineer &mdash; Northwind" in rendered
    assert "Built the billing service" in rendered
    assert "cut p99 to 40ms" in rendered
    assert "Tideline" in rendered
    assert "MSc Computer Science" in rendered


# ── sections appear only when there is something in them ──────────────────────────────────────

def test_a_section_with_no_data_is_omitted_whole():
    """An empty heading reads as a bug in the product, and for the user it is one: the tailoring
    prompt is told to keep the template's sections, so an empty Projects heading survives into a
    PDF that goes to an employer."""
    body = _body(render_cv(ProfileSchema.model_validate(
        {"identity": {"name": "Bo"}, "confirmed": True})))

    for absent in ("Core Competencies", "Experience", "Projects", "Education", "Languages",
                   "Certifications"):
        assert absent not in body, f"an empty {absent} section was rendered"
    assert "Bo" in body and "Available upon request" in body


def test_a_profile_with_only_experience_keeps_only_that_section():
    body = _body(render_cv(ProfileSchema.model_validate({
        "identity": {"name": "Bo"},
        "experience": [{"role": "Engineer", "org": "Acme"}],
        "confirmed": True,
    })))

    assert "Experience" in body
    assert "Projects" not in body
    assert "Education" not in body


# ── the fields it must not confuse ────────────────────────────────────────────────────────────

def test_the_languages_section_is_spoken_languages_not_programming_ones():
    """`skills.languages` is Go and Python. A CV claiming native fluency in Go would be memorable
    for the wrong reason — the same trap `libs/scoring/docprompt.py` documents."""
    rendered = render_cv(profile())
    section = rendered.split("Languages</div>", 1)[1]

    assert "English (Native)" in section
    assert "German (C1)" in section
    assert "Go" not in section.split("</div>", 1)[0] + section.split("</ul>", 1)[0]


def test_an_unstated_spoken_language_is_never_defaulted_to_english():
    """The prompt's rule is that the Languages line lists exactly what the profile gives. Defaulting
    it would be inventing a qualification, which is what the facts block exists to prevent."""
    assert "Languages" not in _body(render_cv(profile(constraints={"languages": []})))


def test_education_carries_no_dates_and_labels_the_thesis_research():
    """Both are the tailoring prompt's own rules. A base document that broke them would make the
    model correct the template instead of tailoring it."""
    section = render_cv(profile()).split("Education</div>", 1)[1]

    assert "Research:" in section
    assert "Consensus under partition" in section
    assert "entry-date" not in section.split("</div>\n</div>", 1)[0]


# ── untrusted text ────────────────────────────────────────────────────────────────────────────

def test_profile_text_is_escaped_into_the_document():
    """A profile is captured from documents the user uploaded, which are untrusted text, and this
    output is HTML that a headless Chrome renders. An unescaped `<` is a broken layout at best."""
    rendered = render_cv(profile(identity={"name": "Bo <script>alert(1)</script> Example",
                                           "links": {"site": "https://a.test/?x=1&y=2"}}))

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp;y=2" in rendered


def test_a_link_that_is_not_a_url_is_not_turned_into_one():
    """`links` is where a portfolio goes and also where somebody puts a phone number. Guessing
    `https://` in front of one produces a header that looks right and a dead link in the PDF."""
    rendered = render_cv(profile(identity={"name": "Bo", "links": {"phone": "+49 30 123456"}}))

    assert "https://+49" not in rendered
    assert "+49 30 123456" in rendered


def test_an_email_link_gets_a_mailto():
    assert 'href="mailto:alice@example.com"' in render_cv(profile())


# ── reproducibility ───────────────────────────────────────────────────────────────────────────

def test_the_same_profile_renders_the_same_bytes():
    """Same discipline as the rubric hash: a document that differs between two runs of the same
    input is unattributable when somebody asks what was sent."""
    assert render_cv(profile()) == render_cv(profile())


def test_the_caller_may_supply_the_profile_paragraph():
    """The tailoring prompt rewrites this paragraph per job; a caller that already has one should
    not have to post-process the HTML to get it in."""
    rendered = render_cv(profile(), summary="Backend engineer, payments.")

    assert "Backend engineer, payments." in rendered


def test_the_composed_paragraph_states_only_what_the_profile_holds():
    """Untailored, the opening line still has to be true. It is composed from confirmed fields with
    no adjectives and no achievements — the tailoring prompt supplies the persuasion."""
    rendered = render_cv(profile())
    paragraph = rendered.split('<p class="profile">', 1)[1].split("</p>", 1)[0]

    assert paragraph.startswith("Senior backend engineer")
    assert "Python" in paragraph


@pytest.mark.parametrize("field", ["targets", "skills"])
def test_a_profile_too_thin_for_a_paragraph_omits_it_rather_than_inventing_one(field):
    thin = {"identity": {"name": "Bo"}, "confirmed": True}
    if field == "targets":
        thin["skills"] = {"languages": ["Python"]}
    rendered = render_cv(ProfileSchema.model_validate(thin))

    assert not MARKER.search(rendered)
    if field == "skills":
        assert 'class="profile"' not in rendered


def test_the_rendered_document_is_still_the_hand_tuned_layout():
    """The whole point of filling a template rather than generating HTML: the A4 rules and the
    `@page` margin that stops Chrome drawing a header over the content survive the render."""
    rendered = render_cv(profile())

    assert "@page" in rendered and "size: A4" in rendered
    assert 'class="cv-header"' in rendered and 'class="cv-contact"' in rendered
    assert rendered.rstrip().endswith("</html>")


def test_the_template_on_disk_is_the_one_the_renderer_reads():
    """`tests/test_prompt_templates.py` reads it by path and the renderer reads it by package. Two
    paths to the same bytes is fine; two different files would be a test that guards nothing."""
    on_disk = pathlib.Path("libs/templates/base_cv.html").read_text(encoding="utf-8")

    assert load(BASE_CV_TEMPLATE) == on_disk.replace("\r\n", "\n")


# ── page geometry ─────────────────────────────────────────────────────────────────────────────

def test_page_margins_are_on_the_page_rule_not_the_body():
    """**Body padding is applied once to the whole flowed document.** It puts a gap above page 1
    and nothing above page 2, so a two-page CV starts hard against the paper edge on the second —
    which is what a recruiter sees when they scroll past the fold. `@page` margins repeat.
    """
    import pathlib
    import re

    for path in ("libs/templates/base_cv.html", "libs/scaffolds/base_cv.html"):
        css = pathlib.Path(path).read_text(encoding="utf-8")
        page_rule = re.search(r"@page\s*\{[^}]*\}", css, re.S)
        assert page_rule, f"{path} has no @page rule — the PDF would use Chrome's defaults"
        assert re.search(r"margin:\s*\d", page_rule.group(0)), (
            f"{path} sets @page margin to zero, so only the first page gets one")

        body_rule = re.search(r"\bbody\s*\{[^}]*\}", css, re.S)
        assert body_rule
        assert not re.search(r"padding:\s*\d+mm", body_rule.group(0)), (
            f"{path} still puts the page margin on the body, which only affects page one")
