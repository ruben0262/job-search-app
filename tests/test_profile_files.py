"""The profile as files: what grounds a generation call, and what must never reach one."""
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


# Three files ship usable, unlike the rest — the module's own `SHIPS_USABLE`.
from libs.profiles import SHIPS_USABLE


def test_the_shipped_scaffolds_are_unfilled():
    """They are placeholders by design."""
    for name, _kind, content in seed_files():
        if name.endswith(".md") and name not in SHIPS_USABLE:
            assert not is_filled(content), f"{name} looks filled but is the shipped scaffold"


def test_the_framework_file_that_ships_usable_says_so():
    """A file with no placeholders is not an oversight here."""
    for name in SHIPS_USABLE:
        assert is_filled(scaffold(name))


def test_a_scaffold_carries_no_other_person():
    """These came from mahoraga-jobs, which was cleaned from the reference instance."""
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
    """The check is for placeholder markers, not length."""
    assert is_filled("# Alice Example\n\nBackend engineer, six years.")


# ── assembling a generation call ──────────────────────────────────────────────────────────────

def test_grounding_refuses_a_profile_that_is_still_a_scaffold():
    """The failure this prevents: a CV that says `[YOUR NAME]` at the top, sent to an employer."""
    with pytest.raises(ProfileIncomplete):
        grounding({name: scaffold(name) for name, _, _ in seed_files() if name.endswith(".md")})


def test_grounding_refuses_an_account_with_no_context_file():
    """`context.md` is the reference's CLAUDE.md — the document every prompt defers to."""
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
    """`label.md` is deliberately self-contained."""
    assert "label.md" not in GENERATION_FILES

    combined = grounding({"context.md": REAL_CONTEXT, "label.md": "SCORE ANCHORS: unmistakable"})
    assert "unmistakable" not in combined


def test_the_base_cv_is_not_grounding():
    """It is the document being edited, passed as the prompt's BASE TEMPLATE — not context."""
    assert "base_cv.html" not in GENERATION_FILES


def test_an_unfilled_file_is_skipped_rather_than_included():
    """A half-finished account still generates, on what it does have."""
    combined = grounding({"context.md": REAL_CONTEXT,
                          "02-behavioral-profile.md": scaffold("02-behavioral-profile.md")})

    assert "Alice Example" in combined
    assert "[FILLED BY" not in combined


def test_the_assembly_is_reproducible():
    """Same profile in, same bytes out — a generated document that differs between two runs of the same."""
    files = {"context.md": REAL_CONTEXT, "05-cv-templates.md": "# Templates\nreal"}
    assert grounding(files) == grounding(files)


# ── the retrain boundary ──────────────────────────────────────────────────────────────────────

def test_only_the_scoring_files_are_scoring_relevant():
    """The whole retraining policy is this set."""
    assert set(SCORING_RELEVANT_FILES) == {"label.md", "04-job-evaluation.md"}

    for name, _kind in SEED_FILES:
        if name in ("context.md", "base_cv.html", "01-candidate-profile.md"):
            assert name not in SCORING_RELEVANT_FILES, f"editing {name} must not cost a relabel"


# ── a prompt still carrying a placeholder is not finished (2026-08-12) ─────────────────────────

def test_the_writing_prompts_count_as_setup_files():
    """**They were exempt, and that is why nothing ever filled them.**

    `setup_files()` is `SEED_FILES` minus `SHIPS_USABLE`, and `tailor_cv.md` / `tailor_cover.md` sat
    in `SHIPS_USABLE` — so `missing_setup()` never checked them and `list_profile_files` reported them
    filled unconditionally. Guided setup could verify all day and be told they were fine while both
    still said `<<NAME>>`.
    """
    from libs.profiles import setup_files

    files = setup_files()
    assert "tailor_cv.md" in files
    assert "tailor_cover.md" in files


def test_a_writing_prompt_still_holding_a_substitution_marker_is_unfilled():
    """`<<NAME>>` and its siblings mean the prompt describes nobody yet.

    They were deliberately excluded from `PLACEHOLDER_MARKERS` on the reasoning that they are "filled
    at build time from one authoritative source". Measured on the live platform 2026-08-12, that
    source was two sources already disagreeing — one account's typed name lacked the accent its
    profile had, and its typed links held three entries where the profile held one. The prompts are
    filled at setup now, so a marker means unfinished.
    """
    from libs.profiles import is_filled

    for marker in ("<<NAME>>", "<<CONTACT>>", "<<LANGUAGES>>", "<<WORK_ELIGIBILITY>>",
                   "<<HOME_LOCATION>>"):
        assert not is_filled(f"# Tailoring prompt\n\nWrite for {marker}, honestly."), (
            f"a prompt still holding {marker} was counted as finished")

    assert is_filled("# Tailoring prompt\n\nWrite for Alice Example, honestly.")


# ── the typed half changing must not go unnoticed (2026-08-12) ─────────────────────────────────

def test_a_file_written_before_the_change_is_stale_and_one_written_after_is_not():
    """The gap this closes: one account added two links after setup and nothing was told.

    The prompts that carry a contact line were written from the old answer and stayed that way, with
    no error and nothing on any page saying so.
    """
    from datetime import UTC, datetime, timedelta

    from libs.profiles import stale_after

    changed = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    written = {
        "context.md": changed - timedelta(hours=1),
        "tailor_cv.md": changed + timedelta(hours=1),
        "label.md": changed - timedelta(minutes=1),
    }

    assert stale_after(written, changed) == ["context.md", "label.md"]
    assert "tailor_cv.md" not in stale_after(written, changed)


def test_rewriting_a_stale_file_clears_it_with_nothing_to_reset():
    """**Why this is a timestamp comparison and not a "needs refresh" flag.**

    A flag has to be cleared by whatever does the refresh. A clear that never happens is a permanent
    banner; one that happens too early leaves the files stale with nothing saying so. Both are sink
    states. A comparison has neither: the refresh moves `updated_at` past the change and the staleness
    ends by itself.
    """
    from datetime import UTC, datetime, timedelta

    from libs.profiles import stale_after

    changed = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    before = {"context.md": changed - timedelta(hours=1)}
    assert stale_after(before, changed) == ["context.md"]

    after = {"context.md": changed + timedelta(seconds=1)}
    assert stale_after(after, changed) == []


def test_an_account_that_never_changed_anything_is_never_stale():
    """A NULL stamp means "typed nothing since setup", not "everything is stale"."""
    from datetime import UTC, datetime

    from libs.profiles import stale_after

    written = {"context.md": datetime(2026, 1, 1, tzinfo=UTC)}
    assert stale_after(written, None) == []


def test_staleness_needs_no_map_from_field_to_file():
    """Derived, so a new seed file is covered the moment it exists.

    A map from "field the user edited" to "files that depend on it" is a second source of truth: add a
    file, forget the map, and it silently never refreshes. This asks only whether the file predates the
    change, so every setup file is in scope automatically.
    """
    from datetime import UTC, datetime, timedelta

    from libs.profiles import setup_files, stale_after

    changed = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    everything_old = {name: changed - timedelta(days=1) for name in setup_files()}

    assert stale_after(everything_old, changed) == sorted(setup_files())
