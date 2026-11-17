"""The writing prompts: about whoever they are run for, and in step with the code around them.

Every account's CV and cover letter are written from the same two prompts (`libs/prompts/write_cv.md`,
`write_cover.md`, owner 2026-11-10), so a name in one is a name on everybody's documents. What the
prompts ask for must also be what the code keeps: a class name the cleaner drops, or a heading the ATS
check does not know, fails nobody's build and quietly makes every document worse.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from libs.prompts import load
from libs.templates import SCAFFOLD_CV
from services.docgen import build

WRITING = tuple(build.PROMPTS.values())

# The reference instance's owner, by each word they went by in its files: their names, their site's and their
# accounts'. Kept as digests, so this file names them no more than the rest of the project does (owner,
# 2026-11-17: "remove ... from the site and the project completely").
_IDENTITY = frozenset({"b04e95d0b09d1c38", "3497ea51d03887eb", "df386986e7c1337b", "40c2ca3b21e962c7",
                       "e909296ac38ce5c9", "7c47233d5104d91a"})


def _identity(text: str) -> set[str]:
    """The words in `text` that are the reference instance's owner's."""
    return {word for word in re.findall(r"\w+", text.lower())
            if hashlib.sha256(word.encode()).hexdigest()[:16] in _IDENTITY}

# A name is the easy half.
_REFERENCE_SPECIFICS = (
    "Bloomberg", "ByBit", "Syntagma", "FlowSolutions", "Global Liquidity", "Crypto Screener",
    "Sharpe", "Sortino", "DSR/PSR", "Lotka", "Lyapunov", "magázás", "Ön/Önök",
)


@pytest.mark.parametrize("name", (*WRITING, "eval_cv.md"))
def test_a_prompt_every_account_is_written_with_names_nobody(name):
    body = load(name)

    assert not _identity(body), f"{name} names a person and is used for everyone"
    for specific in _REFERENCE_SPECIFICS:
        # Word-bounded: "sharper entries" is style advice and must not read as the Sharpe ratio.
        assert not re.search(rf"\b{re.escape(specific)}\b", body, re.IGNORECASE), (
            f"{name} still carries {specific!r} from the reference instance")


@pytest.mark.parametrize("name", WRITING)
def test_every_class_a_prompt_asks_for_is_one_the_cleaner_keeps(name):
    """`build.clean` keeps the markup's own class names and drops the rest. A prompt asking for one it
    drops would have every document lose that part of its look, with nothing failing."""
    example = re.search(r"(?s)<example>(.*?)</example>", load(name)).group(1)
    asked = {each for found in re.findall(r'class="([^"]+)"', example) for each in found.split()}

    assert asked, "the example shows no markup"
    assert asked <= build._CLASSES, f"{name} asks for {sorted(asked - build._CLASSES)}, which are dropped"


@pytest.mark.parametrize("kind", build.PROMPTS)
def test_each_prompt_asks_for_its_answer_in_the_tags_it_is_read_from(kind):
    tag = build.TAGS[kind]

    assert f"between <{tag}> and </{tag}>" in load(build.PROMPTS[kind])


def test_the_cv_prompt_shows_the_section_names_an_ats_looks_for():
    """An ATS finds a CV's parts by their headings, and 93% of CVs say "Experience" or a standard synonym
    (`libs/templates/README.md`). One account's writer once renamed it "Employment" on every build. The
    ATS check now reads whatever headings a CV has, in any language, so the English names are the
    prompt's to show."""
    headings = {heading.strip().lower() for heading in
                re.findall(r'section-title">([^<]{1,40})<', load(build.PROMPTS["cv"]))}

    assert {"skills", "experience", "education"} <= headings, f"the CV prompt shows {sorted(headings)}"


# ── Meridian's look ───────────────────────────────────────────────────────────────────────────

def test_meridians_stylesheet_carries_no_identity():
    """Meridian, the look a person has until they pick one, is the old scaffold's stylesheet."""
    template = Path(SCAFFOLD_CV).read_text(encoding="utf-8")

    # Their site and their GitHub account are words of `_IDENTITY`.
    assert not _identity(template)
    for leak in ("@gmail", "+44"):
        assert leak not in template, f"{leak} is still in the CV template"
    # A4 and the `@page` margin rule are why the PDF does not get a Chrome header drawn over it.
    assert "@page" in template
