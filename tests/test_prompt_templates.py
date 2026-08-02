"""The generation prompts must not carry one person's identity into another person's documents.

`label_template.md` was generalised in Phase 2 — `<<NAME>>`, `<<WHO>>`, `<<HARD_GATES>>` filled from
the profile. The task prompts were ported from the reference instance and generalised the same way,
and this test is what stops a half-way state from shipping: a prompt is only allowed into
`SEEDED_PROMPTS` once it has no hardcoded identity left, because a scaffold carrying a stranger's
name is seeded into *every new account*.

**These read the scaffolds, not a wired-in file.** Since 2026-07-29 the task prompts are per-user
(`libs/profiles`), so what is checked here is what a new account is seeded with — the platform's
default, and the only version a user who never edits theirs will ever run.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from libs.profiles import scaffold as load

# Every prompt seeded into a new account. **Adding a name here is the deliberate act** the tests
# below then check — a scaffold is copied to every user, so an identity left in one propagates.
SEEDED_PROMPTS: tuple[str, ...] = ("tailor_cv.md", "tailor_cover.md", "score.md", "interview.md",
                                   "outreach.md", "build_cvs.md")

# Kept under the same name so the parametrised tests read unchanged: what docgen uses is a subset
# of what is seeded, and both need the identical guarantee.
DOCGEN_TEMPLATES = SEEDED_PROMPTS

# Anything ported but not yet generalised. Empty since 2026-07-28, and kept so the state is
# asserted rather than assumed the next time something is ported in.
PORTED_NOT_YET_GENERALISED: tuple[str, ...] = ()

# The reference instance's owner. A generalised prompt reads its subject from the profile.
_IDENTITY = re.compile(r"\bRuben\b|\bLekli\b", re.IGNORECASE)

# **A name is the easy half.** These are the reference instance's *specifics* — its employers, its
# tools, its metrics, its entry titles — and they survived the first generalisation pass because
# they name no person. They are worse than a stray name in one way: "keep the quant tooling
# (Bloomberg, ByBit API)" is an instruction to surface something a stranger's profile may not
# contain, and the model will find a way to comply.
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
    """The guard that matters. These are byte-identical ports and still carry an identity, so the
    failure this prevents is real: a user receiving a CV written about somebody else."""
    assert _IDENTITY.search(load(name)), (
        f"{name} looks generalised now — move it into DOCGEN_TEMPLATES so it is checked")
    assert name not in DOCGEN_TEMPLATES, f"{name} is wired in but still names a person"


# ── what the placeholders must survive ────────────────────────────────────────────────────────

def test_every_placeholder_a_prompt_uses_is_one_the_renderer_fills():
    """A prompt reaching Claude with a literal `<<LANGUAGES>>` in it would ask the model to invent
    the candidate's languages — which is the fabrication the whole facts-block design exists to
    stop."""
    import re

    from libs.contracts.profile import ProfileSchema
    from libs.scoring.docprompt import render_cover_prompt, render_cv_prompt

    profile = ProfileSchema.model_validate({
        "identity": {"name": "Alice Example", "location": "Berlin, Germany",
                     "work_eligibility": ["European Union"],
                     "links": {"portfolio": "https://alice.dev"}},
        "constraints": {"languages": ["English (Native)", "German (C1)"]},
    })

    # `<<ROLE_ANGLES>>` is filled a stage later, at build time, from the user's own
    # `08-role-angles.md` — the renderer knows the profile and not the file. Everything else must
    # be gone by here.
    for rendered in (render_cv_prompt(profile), render_cover_prompt(profile)):
        left = set(re.findall(r"<<[A-Z_]+>>", rendered))
        assert left <= {"<<ROLE_ANGLES>>"}, f"an unfilled placeholder reached the prompt: {left}"
        assert "Alice Example" in rendered
        assert "German (C1)" in rendered


def test_the_role_angles_placeholder_never_survives_to_the_model():
    """An unfilled `<<ROLE_ANGLES>>` would ask the model to write against a literal token, and an
    empty heading would read as "this was meant to say something" and invite it to invent one."""
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
    """An imported hand-tuned prompt has its angles written inline and no placeholder. Injecting a
    heading into it would be editing somebody's SACRED prompt behind their back."""
    from services.docgen.build import fill_role_angles

    assert fill_role_angles("no placeholder here", "angles") == "no placeholder here"


def test_a_profile_with_no_languages_still_yields_a_writable_prompt():
    """A prompt told the candidate writes no languages has no language to write in, and the failure
    would be a blank CV rather than an error."""
    from libs.contracts.profile import ProfileSchema
    from libs.scoring.docprompt import DEFAULT_LANGUAGES, render_cv_prompt

    profile = ProfileSchema.model_validate({"identity": {"name": "Alice Example"}})
    assert DEFAULT_LANGUAGES in render_cv_prompt(profile)


def test_unstated_work_eligibility_is_never_invented():
    """The prompt's own rule is "never claim a location the candidate has no right to work in", and
    an invented eligibility is the claim that gets somebody into an interview they cannot accept."""
    from libs.contracts.profile import ProfileSchema
    from libs.scoring.docprompt import render_cv_prompt

    profile = ProfileSchema.model_validate({"identity": {"name": "Alice Example"}})
    assert "do not claim any" in render_cv_prompt(profile)


def test_the_language_rule_is_about_the_candidate_not_one_language():
    """The original chose between English and Hungarian because that is what its owner writes. A
    French speaker must get a French CV for a French posting, and an English-only speaker must never
    get a CV they cannot read.

    Case-insensitive, which is not pedantry: the cover letter's rule said "HUNGARIAN" in capitals
    and passed this test for a day, complete with the Hungarian formal-address grammar under it.
    """
    for name in ("tailor_cv.md", "tailor_cover.md"):
        body = load(name)
        assert not re.search(r"hungarian", body, re.IGNORECASE), f"{name} names one language"
        assert "<<LANGUAGES>>" in body


@pytest.mark.parametrize("name", DOCGEN_TEMPLATES)
def test_a_wired_prompt_carries_none_of_the_reference_instance_s_specifics(name):
    """A prompt that names no person can still be about one.

    Left in, these are dead instructions for most users and an active hazard for the rest: told to
    "keep the quant tooling", a model writing for somebody who has never touched a trading terminal
    will still find something to put there.
    """
    body = load(name)

    for specific in _REFERENCE_SPECIFICS:
        # Word-bounded: "sharper entries" is style advice and must not read as the Sharpe ratio.
        assert not re.search(rf"\b{re.escape(specific)}\b", body, re.IGNORECASE), (
            f"{name} still carries {specific!r} from the reference instance")


# ── the CV template ───────────────────────────────────────────────────────────────────────────

def test_the_cv_template_carries_no_identity():
    """It is the layout every user's CV is built from. A name, address or link left in it would
    appear on a document a stranger sends to an employer — and it would look entirely correct."""
    template = (pathlib.Path("libs/templates/base_cv.html")).read_text(encoding="utf-8")

    assert not _IDENTITY.search(template)
    for leak in ("@gmail", "+44", "rubenlekli.com", "github.com/ruben"):
        assert leak not in template, f"{leak} is still in the CV template"


def test_the_cv_template_keeps_the_layout_rules_that_are_load_bearing():
    """A4 sizing and the `@page` margin rule are why the PDF does not get a Chrome header drawn
    over it. Losing them during a generalisation pass is the silent kind of damage."""
    template = (pathlib.Path("libs/templates/base_cv.html")).read_text(encoding="utf-8")

    assert "@page" in template
    assert "cv-header" in template and "cv-contact" in template


# ── the platform-owned prompts ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ("SKILL.md", "03-writing-style.md",
                                  "06-cover-letter-templates.md", "eval_cv.md"))
def test_a_platform_prompt_is_the_same_bytes_for_everyone(name):
    """These are not seeded per user — every account reads the identical file, so an identity in one
    is not one user's problem but everyone's.

    `eval_cv.md` is here on the strength of a measurement rather than a hope: the reference
    instance's own copy names its owner zero times, which is why it could be lifted as-is while the
    four task prompts beside it had to be generalised first.
    """
    from libs.prompts import load as platform

    body = platform(name)
    assert not _IDENTITY.search(body), f"{name} names a person and is served to every user"
    for specific in _REFERENCE_SPECIFICS:
        assert not re.search(rf"\b{re.escape(specific)}\b", body, re.IGNORECASE), (
            f"{name} still carries {specific!r} from the reference instance")


def test_every_command_forbids_naming_internals_to_the_user():
    """**One block, every command.** The rule lives in `ENVIRONMENT`, which is appended to each
    prompt, so a new command cannot be added without it.

    Written after a real setup transcript ended with a table of `context.md`, `base_cv.html` and
    `08-role-angles.md`, plus an instruction to run `powershell -File cv/compile.ps1`. Those are the
    platform's names for its own storage and a script that does not exist on the user's machine.
    """
    from libs.agents import STARTABLE, system_prompt

    for name in STARTABLE:
        prompt = system_prompt(name)
        assert "Never name a file, path, script or tool" in prompt, name
        assert "Never tell them to run a shell command" in prompt, name


def test_every_command_says_how_to_reach_another_one():
    """A conversation runs one command and cannot hand off inside itself. The transcript that
    prompted this said "start it as a new command", which a user reasonably read as "type it here"
    -- and typing it there does nothing."""
    from libs.agents import STARTABLE, system_prompt

    for name in STARTABLE:
        prompt = system_prompt(name)
        assert "Open a new chat and run" in prompt, name
        assert "new chat, every time" in prompt, name
