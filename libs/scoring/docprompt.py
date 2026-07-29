"""Filling the CV and cover-letter prompts from a confirmed profile.

The same shape as `libs.scoring.rubric`: an agent captures, deterministic code renders. The prompts
themselves are **SACRED** and ported byte-identical from the reference instance apart from having
their subject removed — every rule in them earned real job offers, so the generalisation replaced
one person with a placeholder and changed nothing else.

**The language rule is the one that had to become genuinely general.** The original decided between
English and Hungarian because that is what its owner writes. Now the prompt is told which languages
the candidate writes, and picks from those — so a French speaker gets a French CV for a French
posting, and somebody who writes only English never gets a CV they cannot read.
"""
from __future__ import annotations

from libs.contracts.profile import ProfileSchema
from libs.profiles import scaffold

# What a Languages line says when the profile does not say. Never invented as a level: claiming
# fluency nobody stated is exactly the fabrication the facts block exists to prevent.
DEFAULT_LANGUAGES = "English"


def render_cv_prompt(profile: ProfileSchema, template: str | None = None) -> str:
    """The CV prompt, about this candidate.

    `template` is the user's own copy where they have one (`libs/profiles`); the platform's
    field-agnostic version otherwise. Filling a prompt that already names its subject is a no-op,
    so a hand-tuned import passes through unchanged.
    """
    return _fill(template or scaffold("tailor_cv.md"), profile)


def render_cover_prompt(profile: ProfileSchema, template: str | None = None) -> str:
    """The cover-letter prompt, about this candidate."""
    return _fill(template or scaffold("tailor_cover.md"), profile)


def _fill(template: str, profile: ProfileSchema) -> str:
    return (template
            .replace("<<NAME>>", profile.identity.name)
            .replace("<<LANGUAGES>>", _languages(profile))
            .replace("<<WORK_ELIGIBILITY>>", _eligibility(profile))
            .replace("<<HOME_LOCATION>>", profile.identity.location or "not stated")
            .replace("<<CONTACT>>", _contact(profile)))


def _languages(profile: ProfileSchema) -> str:
    """The languages the CV may be written in, at the level the profile gives.

    Falls back to English rather than to nothing: a prompt told the candidate writes *no* languages
    would have no language to write in, and the failure would be a blank CV rather than an error.
    """
    # `constraints.languages`, not `skills.languages`: the second is Go and Python, and a CV
    # written in Go would be a memorable bug.
    stated = [language for language in profile.constraints.languages if language.strip()]
    return ", ".join(stated) if stated else DEFAULT_LANGUAGES


def _eligibility(profile: ProfileSchema) -> str:
    """Where the candidate may legally work.

    **"not stated" rather than a guess.** The prompt's rule is "never claim a location the candidate
    has no right to work in", and an invented eligibility is precisely the claim that gets somebody
    into an interview they cannot accept.
    """
    stated = [place for place in profile.identity.work_eligibility if place.strip()]
    return ", ".join(stated) if stated else "not stated — do not claim any"


def _contact(profile: ProfileSchema) -> str:
    """The contact line, from the profile's own links and location.

    Deliberately does **not** fall back to the account email: the address somebody signs in with is
    not necessarily the one they want a recruiter to have, and quietly putting it on a document sent
    to an employer is not a decision this code gets to make.
    """
    parts = [value for value in profile.identity.links.values() if value]
    return " · ".join(parts) if parts else "use the contact details in the template"
