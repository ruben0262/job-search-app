"""The CV layout on disk, which becomes a PDF attached to a real application.

**Pointed at `libs/scaffolds/base_cv.html` since 2026-08-14**, which is the base CV a new account is
seeded with and the one every user's own copy descends from. It used to guard
`libs/templates/base_cv.html`, a second layout that nothing in the product read: measured on the live
platform, both accounts' base CVs carry the scaffold's class names and page rule, and neither
carries that file's. Deleting it left this file testing nothing, so it moved rather than went.

Its body has been read by nothing since 2026-11-10, when a person's own uploads replaced the profile
files (`services/docgen/build.py`). What is read of it is its stylesheet, which is Meridian, the look
of everyone who never picked one (`libs.templates`).
"""
from __future__ import annotations

import re

from libs.templates import SCAFFOLD_CV, load


def test_the_scaffold_carries_no_employment_history():
    """It is seeded into every new account, so one person's history in it is a leak into everyone's.

    The identity check in `test_prompt_templates.py` covers the name and the contact details.
    """
    template = load(SCAFFOLD_CV)

    for leak in ("Syntagma", "FlowSolutions", "Sheffield", "Bloomberg", "ByBit",
                 "Cranfield", "Nottingham", "Jack Wills", "All Saints"):
        assert leak not in template, f"{leak} is still in the seeded CV"


# ── page geometry ─────────────────────────────────────────────────────────────────────────────

def test_page_margins_are_on_the_page_rule_not_the_body():
    """Body padding is applied once to the whole flowed document.

    So it puts a gap above page one and nothing above page two, and a two-page CV starts hard
    against the top edge — which a recruiter opening page two sees and a one-page test does not.
    """
    css = load(SCAFFOLD_CV)

    page_rule = re.search(r"@page\s*\{[^}]*\}", css, re.S)
    assert page_rule, "no @page rule — the PDF would use Chrome's defaults"
    assert re.search(r"margin:\s*\d", page_rule.group(0)), (
        "@page margin is zero, so only the first page gets one")

    body_rule = re.search(r"\bbody\s*\{[^}]*\}", css, re.S)
    assert body_rule
    assert not re.search(r"padding:\s*\d+mm", body_rule.group(0)), (
        "the page margin is on the body, which only affects page one")
