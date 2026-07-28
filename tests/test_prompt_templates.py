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
DOCGEN_TEMPLATES: tuple[str, ...] = ()

# Ported verbatim, not yet generalised. Listed so the port is recorded rather than looking like an
# oversight, and so the test below proves they are still unusable rather than merely unused.
PORTED_NOT_YET_GENERALISED = ("tailor_cv.md", "tailor_cover.md")

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
