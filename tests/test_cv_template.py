"""The CV layout on disk, which becomes a PDF attached to a real application."""
from __future__ import annotations

import pathlib
import re

from libs.templates import BASE_CV_TEMPLATE, load


def test_the_template_carries_no_employment_history():
    """The identity check in `test_prompt_templates.py` covers the name and the contact details."""
    template = load(BASE_CV_TEMPLATE)

    for leak in ("Syntagma", "FlowSolutions", "Sheffield", "Bloomberg", "ByBit",
                 "Hungarian", "BEng", "All Saints"):
        assert leak not in template, f"{leak} is still in the CV template"


def test_the_template_keeps_only_the_sections_no_profile_fills():
    """References is the one section with no field behind it, and it is a fixed sentence."""
    template = load(BASE_CV_TEMPLATE)

    assert "Available upon request" in template
    assert "<!--CERTIFICATIONS-->" in template


def test_the_layout_is_the_hand_tuned_one():
    """The whole point of filling a template rather than generating HTML."""
    template = load(BASE_CV_TEMPLATE)

    assert "@page" in template and "size: A4" in template
    assert 'class="cv-header"' in template and 'class="cv-contact"' in template
    assert template.rstrip().endswith("</html>")


def test_the_template_on_disk_is_the_one_the_package_serves():
    """`tests/test_prompt_templates.py` reads it by path and docgen reads it by package."""
    on_disk = pathlib.Path("libs/templates/base_cv.html").read_text(encoding="utf-8")

    assert load(BASE_CV_TEMPLATE) == on_disk.replace("\r\n", "\n")


# ── page geometry ─────────────────────────────────────────────────────────────────────────────

def test_page_margins_are_on_the_page_rule_not_the_body():
    """Body padding is applied once to the whole flowed document."""
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
