"""The generation prompts must not carry one person's identity into another person's documents."""
from __future__ import annotations

import pathlib
import re

import pytest

from libs.profiles import scaffold as load

# Every prompt seeded into a new account.
SEEDED_PROMPTS: tuple[str, ...] = ("tailor_cv.md", "tailor_cover.md", "score.md", "interview.md",
                                   "outreach.md", "build_cvs.md")

# Kept under the same name so the parametrised tests read unchanged.
DOCGEN_TEMPLATES = SEEDED_PROMPTS

# Anything ported but not yet generalised.
PORTED_NOT_YET_GENERALISED: tuple[str, ...] = ()

# The reference instance's owner. A generalised prompt reads its subject from the profile.
_IDENTITY = re.compile(r"\bRuben\b|\bLekli\b", re.IGNORECASE)

# A name is the easy half.
_REFERENCE_SPECIFICS = (
    "Bloomberg", "ByBit", "Syntagma", "FlowSolutions", "Global Liquidity", "Crypto Screener",
    "Sharpe", "Sortino", "DSR/PSR", "Lotka", "Lyapunov", "magázás", "Ön/Önök",
)


@pytest.mark.parametrize("name", DOCGEN_TEMPLATES)
def test_a_wired_prompt_names_nobody(name):
    """Every prompt docgen actually uses must be about whoever it is run for."""
    assert not _IDENTITY.search(load(name)), (
        f"{name} still hardcodes a person — it would put their name on somebody else's CV")


@pytest.mark.parametrize("name", PORTED_NOT_YET_GENERALISED)
def test_an_ungeneralised_prompt_is_not_wired_in(name):
    """The guard that matters."""
    assert _IDENTITY.search(load(name)), (
        f"{name} looks generalised now — move it into DOCGEN_TEMPLATES so it is checked")
    assert name not in DOCGEN_TEMPLATES, f"{name} is wired in but still names a person"


# ── what the placeholders must survive ────────────────────────────────────────────────────────

def test_every_marker_a_scaffold_uses_is_one_the_completeness_check_can_see():
    """**The guarantee that replaced the renderer, and it is the stronger one.**

    `libs/scoring/docprompt.py` substituted five markers from `ProfileSchema` at every build, so a
    marker nothing filled would reach the model literally — asking it to invent the candidate. It is
    deleted: the writing prompts are per-user files that `setup-prompts` writes with real details in
    them, and `_require_personalised` refuses one that is still a scaffold.

    That refusal only works if every marker a scaffold carries is in `PLACEHOLDER_MARKERS`. A new
    marker missing from that tuple is a scaffold the check reads as finished — which is exactly how
    both writing prompts passed every gate while saying `<<NAME>>`.

    `<<ROLE_ANGLES>>` is the exception, filled a stage later from the user's own `08-role-angles.md`.
    """
    import re

    from libs.profiles import PLACEHOLDER_MARKERS, scaffold

    for name in ("tailor_cv.md", "tailor_cover.md"):
        markers = set(re.findall(r"<<[A-Z_]+>>", scaffold(name)))
        unseen = markers - set(PLACEHOLDER_MARKERS) - {"<<ROLE_ANGLES>>"}
        assert not unseen, (
            f"{name} carries {sorted(unseen)}, which `is_filled` cannot see — the scaffold would "
            "count as personalised and reach the model with the marker in it")


def test_the_role_angles_placeholder_never_survives_to_the_model():
    """An unfilled `<<ROLE_ANGLES>>` would ask the model to write against a literal token."""
    from libs.profiles import scaffold
    from services.docgen.build import fill_role_angles

    template = "before\n<<ROLE_ANGLES>>\nafter"

    assert "<<ROLE_ANGLES>>" not in fill_role_angles(template, None)
    assert "<<ROLE_ANGLES>>" not in fill_role_angles(template, "   ")
    # The unedited scaffold is not content: it is instructions to the agent about what to write.
    assert "<<ROLE_ANGLES>>" not in fill_role_angles(template, scaffold("08-role-angles.md"))
    assert "ROLE ANGLES" not in fill_role_angles(template, scaffold("08-role-angles.md"))

    filled = fill_role_angles(template, "ANGLE 1 - district nurse\n  Lead with: ward experience")
    assert "district nurse" in filled and "ROLE ANGLES FOR THIS CANDIDATE" in filled


def test_a_prompt_that_predates_the_split_is_left_alone():
    """An imported hand-tuned prompt has its angles written inline and no placeholder."""
    from services.docgen.build import fill_role_angles

    assert fill_role_angles("no placeholder here", "angles") == "no placeholder here"


# Two tests were here: a profile with no languages still yielding a writable prompt, and unstated work
# eligibility never being invented. Both asserted fallbacks inside `render_cv_prompt` —
# `DEFAULT_LANGUAGES` and "do not claim any" — which the renderer substituted when the schema was
# silent. The renderer is deleted and there is no schema in that path to be silent: the language rule
# and the eligibility rule are written into each person's own prompt by `setup-prompts`, from their
# documents. `test_the_language_rule_is_about_the_candidate_not_one_language` below still checks the
# scaffold states the rule at all, which is the part that lives in this repository.


def test_the_language_rule_is_about_the_candidate_not_one_language():
    """The original chose between English and Hungarian because that is what its owner writes."""
    for name in ("tailor_cv.md", "tailor_cover.md"):
        body = load(name)
        assert not re.search(r"hungarian", body, re.IGNORECASE), f"{name} names one language"
        assert "<<LANGUAGES>>" in body


@pytest.mark.parametrize("name", DOCGEN_TEMPLATES)
def test_a_wired_prompt_carries_none_of_the_reference_instance_s_specifics(name):
    """A prompt that names no person can still be about one."""
    body = load(name)

    for specific in _REFERENCE_SPECIFICS:
        # Word-bounded: "sharper entries" is style advice and must not read as the Sharpe ratio.
        assert not re.search(rf"\b{re.escape(specific)}\b", body, re.IGNORECASE), (
            f"{name} still carries {specific!r} from the reference instance")


# ── the CV template ───────────────────────────────────────────────────────────────────────────

def test_the_cv_template_carries_no_identity():
    """It is seeded into every new account, so an identity in it is one every user starts with."""
    template = (pathlib.Path("libs/scaffolds/base_cv.html")).read_text(encoding="utf-8")

    assert not _IDENTITY.search(template)
    for leak in ("@gmail", "+44", "rubenlekli.com", "github.com/ruben"):
        assert leak not in template, f"{leak} is still in the CV template"


def test_the_cv_template_keeps_the_layout_rules_that_are_load_bearing():
    """A4 sizing and the `@page` margin rule are why the PDF does not get a Chrome header drawn over it."""
    template = (pathlib.Path("libs/scaffolds/base_cv.html")).read_text(encoding="utf-8")

    assert "@page" in template
    assert "cv-header" in template and "cv-contact" in template


# ── the platform-owned prompts ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ("SKILL.md", "03-writing-style.md",
                                  "06-cover-letter-templates.md", "eval_cv.md"))
def test_a_platform_prompt_is_the_same_bytes_for_everyone(name):
    """These are not seeded per user — every account reads the identical file."""
    from libs.prompts import load as platform

    body = platform(name)
    assert not _IDENTITY.search(body), f"{name} names a person and is served to every user"
    for specific in _REFERENCE_SPECIFICS:
        assert not re.search(rf"\b{re.escape(specific)}\b", body, re.IGNORECASE), (
            f"{name} still carries {specific!r} from the reference instance")


def test_every_command_forbids_naming_internals_to_the_user():
    """One block, every command."""
    from libs.agents import STARTABLE, system_prompt

    for name in STARTABLE:
        prompt = system_prompt(name)
        assert "Never name a file, path, script or tool" in prompt, name
        assert "Never tell them to run a shell command" in prompt, name


def test_every_command_says_how_to_reach_another_one():
    """A conversation runs one command and cannot hand off inside itself."""
    from libs.agents import STARTABLE, system_prompt

    # Every command except the director, which runs all four steps itself.
    for name in STARTABLE:
        prompt = system_prompt(name)
        if name == "setup-director":
            assert "There is no other chat" in prompt
            assert "Open a new chat and run" not in prompt
            continue
        assert "Open a new chat and run" in prompt, name
        assert "new chat, every time" in prompt, name


def test_every_command_is_told_which_commands_exist_here():
    """The ported instructions name commands from the single-user checkout -- /setup-boards."""
    from libs.agents import STARTABLE, system_prompt

    for name in STARTABLE:
        prompt = system_prompt(name)
        assert "Only these commands exist here" in prompt, name
        for absent in ("/setup-boards", "/setup-companies", "/apply"):
            assert absent in prompt, f"{name} must name {absent} as unavailable"
        assert "Which jobs get searched for is not yours to set" in prompt, name


def test_the_base_cv_scaffold_uses_the_section_names_the_ats_check_looks_for():
    """`pdf.inspect_pdf` searches the extracted text for the literal words the checker names.

    A CV headed `Employment` or `Work History` fails it — and one account's did, on every build, after
    the setup agent renamed the section while writing its base CV. It is a warning rather than a
    refusal, so the CV delivered anyway and nothing but the log said so.

    The scaffold is the default every new account starts from, so it is the one place that can stop
    the rename being the norm. Read from `pdf` rather than restated, so the two cannot drift.
    """
    import re
    from pathlib import Path

    from libs.profiles import scaffold

    wanted = {"experience", "education"}
    checker = (Path(__file__).resolve().parents[1] / "services" / "docgen" / "pdf.py").read_text(
        encoding="utf-8")
    named = {name for pair in re.findall(r'for section in \(([^)]*)\)', checker)
             for name in re.findall(r'"(\w+)"', pair)}
    assert wanted <= named, (
        f"pdf.py checks for {sorted(named)}, not {sorted(wanted)} — point this test at whatever it "
        "checks now rather than guarding a rule that moved")

    headings = {h.strip().lower() for h in
                re.findall(r'section-title[^>]*>([^<]{1,40})', scaffold("base_cv.html"))}
    missing = wanted - headings
    assert not missing, (
        f"the shipped base CV has no {sorted(missing)} heading, so every account starting from it "
        f"fails the ATS check. Headings present: {sorted(headings)}")
