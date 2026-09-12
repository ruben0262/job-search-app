"""The CV layout on disk, which becomes a PDF attached to a real application.

**Pointed at `libs/scaffolds/base_cv.html` since 2026-08-14**, which is the base CV a new account is
seeded with and the one every user's own copy descends from. It used to guard
`libs/templates/base_cv.html`, a second layout that nothing in the product read: measured on the live
platform, both accounts' base CVs carry the scaffold's class names and page rule, and neither
carries that file's. Deleting it left this file testing nothing, so it moved rather than went.
"""
from __future__ import annotations

import pathlib
import re

from libs.templates import SCAFFOLD_CV, load

# Read by path as well as through the package, because docgen reads the user's copy of this file
# out of the database and the two must not drift.
ON_DISK = pathlib.Path("libs/scaffolds/base_cv.html")


def test_the_scaffold_carries_no_employment_history():
    """It is seeded into every new account, so one person's history in it is a leak into everyone's.

    The identity check in `test_prompt_templates.py` covers the name and the contact details.
    """
    template = load(SCAFFOLD_CV)

    for leak in ("Syntagma", "FlowSolutions", "Sheffield", "Bloomberg", "ByBit",
                 "Cranfield", "Nottingham", "Jack Wills", "All Saints"):
        assert leak not in template, f"{leak} is still in the seeded CV"


def test_the_scaffold_keeps_the_sections_a_recruiter_scans_for():
    """The single-column order the ATS check in `pdf.inspect_pdf` also reads."""
    template = load(SCAFFOLD_CV)

    for section in ("Core Competencies", "Experience", "Education", "Certifications",
                    "References"):
        assert section in template, f"the seeded CV has no {section} section"
    assert "Available upon request" in template


def test_the_layout_is_the_hand_tuned_one():
    """The whole point of a canonical CSS block docgen restores on every build."""
    template = load(SCAFFOLD_CV)

    assert "@page" in template and "size: A4" in template
    assert 'class="cv-header"' in template and 'class="cv-contact"' in template
    assert template.rstrip().endswith("</html>")


def test_the_class_names_docgen_relies_on_are_all_present():
    """`strip_prose_emdashes` and the ATS checks address the document by these names.

    A renamed class does not raise anywhere: the em-dash strip simply stops matching, and the
    document goes to an employer with the punctuation the prompt bans.
    """
    template = load(SCAFFOLD_CV)

    for name in ("profile", "entry", "entry-head", "entry-title", "entry-date", "section",
                 "section-title", "cv-name", "plain-list"):
        assert f'class="{name}"' in template or f'class="{name} ' in template \
            or f' {name}"' in template, f"the seeded CV has no .{name} element"


def test_the_file_on_disk_is_the_one_the_package_serves():
    """`tests/test_prompt_templates.py` reads it by path and the seeder reads it by package."""
    assert load(SCAFFOLD_CV) == ON_DISK.read_text(encoding="utf-8").replace("\r\n", "\n")


# ── page geometry ─────────────────────────────────────────────────────────────────────────────

def test_page_margins_are_on_the_page_rule_not_the_body():
    """Body padding is applied once to the whole flowed document.

    So it puts a gap above page one and nothing above page two, and a two-page CV starts hard
    against the top edge — which a recruiter opening page two sees and a one-page test does not.
    """
    css = ON_DISK.read_text(encoding="utf-8")

    page_rule = re.search(r"@page\s*\{[^}]*\}", css, re.S)
    assert page_rule, "no @page rule — the PDF would use Chrome's defaults"
    assert re.search(r"margin:\s*\d", page_rule.group(0)), (
        "@page margin is zero, so only the first page gets one")

    body_rule = re.search(r"\bbody\s*\{[^}]*\}", css, re.S)
    assert body_rule
    assert not re.search(r"padding:\s*\d+mm", body_rule.group(0)), (
        "the page margin is on the body, which only affects page one")
