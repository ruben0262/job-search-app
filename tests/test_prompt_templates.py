"""The generation prompts must not carry one person's identity into another person's documents.

`label_template.md` was generalised in Phase 2 — `<<NAME>>`, `<<WHO>>`, `<<HARD_GATES>>` filled from
the profile. `tailor_cv.md` and `tailor_cover.md` are ported verbatim from the reference instance
and **still carry its owner's name 28 times between them**, because the port is deliberately
byte-identical: they are SACRED (`libs/prompts/__init__.py`), and generalising during a copy would
make the diff against the original unreviewable.

This test is what stops the half-way state from shipping. A prompt is only allowed into
`DOCGEN_TEMPLATES` once it has no hardcoded identity left — until then, wiring it in would put a
stranger's name on a real user's CV.
"""
from __future__ import annotations

import re

import pytest

from libs.prompts import load

# Prompts docgen is allowed to use. **Empty until each has been generalised** — adding a name here
# is the deliberate act that the test below then checks.
DOCGEN_TEMPLATES: tuple[str, ...] = ("tailor_cv.md", "tailor_cover.md")

# Anything ported but not yet generalised. Empty since 2026-07-28, and kept so the state is
# asserted rather than assumed the next time something is ported in.
PORTED_NOT_YET_GENERALISED: tuple[str, ...] = ()

# The reference instance's owner. A generalised prompt reads its subject from the profile.
_IDENTITY = re.compile(r"\bRuben\b|\bLekli\b", re.IGNORECASE)


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

    for rendered in (render_cv_prompt(profile), render_cover_prompt(profile)):
        assert not re.search(r"<<[A-Z_]+>>", rendered), "an unfilled placeholder reached the prompt"
        assert "Alice Example" in rendered
        assert "German (C1)" in rendered


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
    get a CV they cannot read."""
    from libs.prompts import TAILOR_COVER_TEMPLATE, TAILOR_CV_TEMPLATE, load

    for name in (TAILOR_CV_TEMPLATE, TAILOR_COVER_TEMPLATE):
        body = load(name)
        assert "Hungarian" not in body
        assert "<<LANGUAGES>>" in body
