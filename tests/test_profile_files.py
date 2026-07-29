"""The profile as files: what grounds a generation call, and what must never reach one.

This is the port of `cwd=REPO_ROOT` — the reference instance's CLI loads `CLAUDE.md` and seven skill
files off disk on every call, and its 2026-07-15 A/B measured what happens without them (an invented
employer, fabricated metrics). The rules worth testing are the ones that fail *quietly*: a scaffold
grounding a real CV, a platform file going missing from the assembly, or a scoring-relevant edit
slipping through without advancing the retrain counter.
"""
from __future__ import annotations

import pytest

from libs.db.models import SCORING_RELEVANT_FILES
from libs.profiles import (
    GENERATION_FILES,
    PLATFORM_FILES,
    SEED_FILES,
    ProfileIncomplete,
    grounding,
    is_filled,
    platform_file,
    scaffold,
    seed_files,
)

REAL_CONTEXT = "# Job Application Assistant for Alice Example\n\nBEng, backend engineer.\n"


# ── the scaffolds ─────────────────────────────────────────────────────────────────────────────

def test_every_seeded_file_exists_on_disk():
    """A missing scaffold would make signup fail for the next user rather than this one."""
    assert len(seed_files()) == len(SEED_FILES)
    for name, _kind, content in seed_files():
        assert content.strip(), f"{name} is empty"


# `05-cv-templates.md` ships **usable**, unlike the rest. It is framework rules — the A4 layout,
# the compile step, what to change per posting — and `/setup` personalises exactly one section of
# it ("CV Content Judgement — Tailoring by Role-Type Lens"), replacing the generic dev/quant framing
# with the user's own role directions. So it carries no `[PLACEHOLDER]` tokens and is legitimately
# filled from the moment it is seeded.
SHIPS_USABLE = ("05-cv-templates.md",)


def test_the_shipped_scaffolds_are_unfilled():
    """They are placeholders by design. If one ever reads as filled, `grounding()` would ground a
    real CV on `[YOUR NAME]` and the document would look correct to whoever generated it."""
    for name, _kind, content in seed_files():
        if name.endswith(".md") and name not in SHIPS_USABLE:
            assert not is_filled(content), f"{name} looks filled but is the shipped scaffold"


def test_the_framework_file_that_ships_usable_says_so():
    """A file with no placeholders is not an oversight here — it is the one whose value is the
    framework rather than the person. Asserted so that a future edit adding `[PLACEHOLDER]` tokens
    to it, or stripping them from another file, is a test failure rather than a surprise."""
    for name in SHIPS_USABLE:
        assert is_filled(scaffold(name))


def test_a_scaffold_carries_no_other_person():
    """These came from mahoraga-jobs, which was cleaned from the reference instance. A name left in
    one would be seeded into every new account."""
    import re

    identity = re.compile(r"\bRuben\b|\bLekli\b|\bAndras\b|\bAndrás\b", re.IGNORECASE)
    for name, _kind, content in seed_files():
        assert not identity.search(content), f"{name} names a real person"


# ── what counts as filled ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "",
    "   \n  ",
    "# Candidate Profile\n- **Name:** [YOUR NAME]",
    "<!-- [FILLED BY /setup] -->\n# Profile",
    "Replace every [BRACKETED] block below",
])
def test_an_unfinished_file_is_not_filled(content):
    assert not is_filled(content)


def test_a_short_real_profile_is_filled():
    """The check is for placeholder markers, not length. Somebody with a two-line history has a
    complete profile, and a length threshold would tell them otherwise."""
    assert is_filled("# Alice Example\n\nBackend engineer, six years.")


# ── assembling a generation call ──────────────────────────────────────────────────────────────

def test_grounding_refuses_a_profile_that_is_still_a_scaffold():
    """The failure this prevents: a CV that says `[YOUR NAME]` at the top, sent to an employer, by
    a product that reported success."""
    with pytest.raises(ProfileIncomplete):
        grounding({name: scaffold(name) for name, _, _ in seed_files() if name.endswith(".md")})


def test_grounding_refuses_an_account_with_no_context_file():
    """`context.md` is the reference's CLAUDE.md — the document every prompt defers to. Without it
    there is nothing grounding the writing, which is the measured fabrication case."""
    with pytest.raises(ProfileIncomplete):
        grounding({"01-candidate-profile.md": "# Profile\nReal content."})


def test_grounding_carries_the_platform_files_and_the_users_own():
    combined = grounding({"context.md": REAL_CONTEXT,
                          "01-candidate-profile.md": "# Profile\nBackend engineer at Northwind."})

    for name in PLATFORM_FILES:
        marker = platform_file(name).splitlines()[0].lstrip("# ").strip()
        assert marker in combined, f"{name} is missing from the grounding"
    assert "Alice Example" in combined
    assert "Northwind" in combined


def test_the_labelling_rubric_is_not_part_of_a_generation_call():
    """`label.md` is deliberately self-contained — the labeller runs it without the context files to
    keep labelling cheap, and it repeats the profile facts for exactly that reason. Including it
    here would pay for the same facts twice on every CV."""
    assert "label.md" not in GENERATION_FILES

    combined = grounding({"context.md": REAL_CONTEXT, "label.md": "SCORE ANCHORS: unmistakable"})
    assert "unmistakable" not in combined


def test_the_base_cv_is_not_grounding():
    """It is the document being edited, passed as the prompt's BASE TEMPLATE — not context. Sending
    it twice would spend the tokens twice and invite the model to treat it as reference rather than
    as the thing to rewrite."""
    assert "base_cv.html" not in GENERATION_FILES


def test_an_unfilled_file_is_skipped_rather_than_included():
    """A half-finished account still generates, on what it does have — one scaffolded section must
    not take the whole profile down with it."""
    combined = grounding({"context.md": REAL_CONTEXT,
                          "02-behavioral-profile.md": scaffold("02-behavioral-profile.md")})

    assert "Alice Example" in combined
    assert "[FILLED BY" not in combined


def test_the_assembly_is_reproducible():
    """Same profile in, same bytes out — a generated document that differs between two runs of the
    same input is unattributable when somebody asks what was sent."""
    files = {"context.md": REAL_CONTEXT, "05-cv-templates.md": "# Templates\nreal"}
    assert grounding(files) == grounding(files)


# ── the retrain boundary ──────────────────────────────────────────────────────────────────────

def test_only_the_scoring_files_are_scoring_relevant():
    """The whole retraining policy is this set. `context.md` and the CV change what gets *written*;
    these two change what counts as a *fit*, which is what a label answers."""
    assert set(SCORING_RELEVANT_FILES) == {"label.md", "04-job-evaluation.md"}

    for name, _kind in SEED_FILES:
        if name in ("context.md", "base_cv.html", "01-candidate-profile.md"):
            assert name not in SCORING_RELEVANT_FILES, f"editing {name} must not cost a relabel"
