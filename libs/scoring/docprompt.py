"""Filling the CV and cover-letter prompts from a confirmed profile."""
from __future__ import annotations

from libs.contracts.profile import ProfileSchema
from libs.profiles import scaffold

# What a Languages line says when the profile does not say.
DEFAULT_LANGUAGES = "English"


def render_cv_prompt(profile: ProfileSchema, template: str | None = None) -> str:
    """The CV prompt, about this candidate."""
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
    """The languages the CV may be written in, at the level the profile gives."""
    # `constraints.languages`, not `skills.languages`: the second is Go and Python.
    stated = [language for language in profile.constraints.languages if language.strip()]
    return ", ".join(stated) if stated else DEFAULT_LANGUAGES


def _eligibility(profile: ProfileSchema) -> str:
    """Where the candidate may legally work."""
    stated = [place for place in profile.identity.work_eligibility if place.strip()]
    return ", ".join(stated) if stated else "not stated — do not claim any"


def _contact(profile: ProfileSchema) -> str:
    """The contact line, from the profile's own links and location."""
    parts = [value for value in profile.identity.links.values() if value]
    return " · ".join(parts) if parts else "use the contact details in the template"
