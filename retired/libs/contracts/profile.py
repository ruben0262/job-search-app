"""The captured profile: what the interview produces and the confirmation form edits."""
from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Bumped when the shape changes. 3 removed `targets.company_size` (2026-08-12).
#
# Nothing reads `users.profile_schema_version` today, so this number is bookkeeping rather than a
# safety mechanism — what makes a shape change safe is the migration that moves the data with it.
PROFILE_SCHEMA_VERSION = 3


class _Strict(BaseModel):
    # `forbid` so an agent inventing a field fails validation instead of having it silently dropped.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _fit_to_bounds(cls, data):
        """Trim a value that overran its bound instead of rejecting the whole profile.

        **A length limit here is a storage and prompt-cost bound, not a statement about the
        candidate.** Enforced as a hard failure it throws away everything: on 2026-08-11 one
        account's processing dead-lettered because the model wrote 1,000-plus characters into one
        project's `what_built` and a `company_size` longer than forty characters. Two long strings,
        and the result was no profile at all, no onboarding, and a `process` job in the dead letter
        queue — for a profile that was otherwise entirely correct.

        So an overlong value is cut to its bound and the fact is logged. What is *not* softened:
        `extra="forbid"` still rejects an invented field, and a missing required value still fails.
        Those are the model getting the shape wrong, which is worth failing on. A description that
        ran forty characters long is not.

        Fields are named in the log, never values — a profile is the candidate's own history.
        """
        if not isinstance(data, dict):
            return data

        trimmed: list[str] = []
        out = dict(data)
        for name, field in cls.model_fields.items():
            if name not in out:
                continue
            limit = next((getattr(rule, "max_length", None) for rule in field.metadata
                          if getattr(rule, "max_length", None) is not None), None)
            value = out[name]
            if limit is None or not isinstance(value, str | list) or len(value) <= limit:
                continue
            out[name] = value[:limit]
            trimmed.append(f"{cls.__name__}.{name}")

        if trimmed:
            logger.warning("profile: trimmed %s to fit; the value was longer than the schema allows",
                           ", ".join(trimmed))
        return out


class Identity(_Strict):
    name: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    # Plain statements ("EU citizen", "UK work visa") rather than an enum: the vocabulary is not ours.
    work_eligibility: list[str] = Field(default_factory=list, max_length=10)
    links: dict[str, str] = Field(default_factory=dict, max_length=10)


class Education(_Strict):
    degree: str = Field(max_length=120, description="e.g. 'BEng Software Engineering'")
    institution: str = Field(max_length=160, description="The university, school or awarding body")
    field: str | None = Field(default=None, max_length=120)
    thesis: str | None = Field(default=None, max_length=400)


class Experience(_Strict):
    role: str = Field(max_length=160)
    org: str = Field(max_length=160)
    # Free text, not dates: CVs say "2019", "Mar 2019", "present".
    start: str | None = Field(default=None, max_length=32)
    end: str | None = Field(default=None, max_length=32)
    bullets: list[str] = Field(
        default_factory=list, max_length=20,
        description=("What they did in this role, in their own words, one achievement per bullet. "
                     "Copy the substance of every bullet the documents give for this role — these "
                     "become the CV, so a role summarised in one line loses the work."))
    stack: list[str] = Field(default_factory=list, max_length=40,
                             description="Technologies used in THIS role")
    metrics: list[str] = Field(
        default_factory=list, max_length=20,
        description="Numbers this role achieved, exactly as stated (e.g. 'AUC 0.82', '10k records')")


class Project(_Strict):
    name: str = Field(max_length=160)
    what_built: str = Field(default="", max_length=1000)
    stack: list[str] = Field(default_factory=list, max_length=40)
    metrics: list[str] = Field(default_factory=list, max_length=20)


class Certification(_Strict):
    """A course, licence or accreditation."""

    name: str = Field(max_length=160)
    issuer: str | None = Field(default=None, max_length=120)
    year: str | None = Field(default=None, max_length=32)


class Skills(_Strict):
    languages: list[str] = Field(default_factory=list, max_length=40)
    frameworks: list[str] = Field(default_factory=list, max_length=60)
    domains: list[str] = Field(default_factory=list, max_length=40)
    tools: list[str] = Field(default_factory=list, max_length=60)


class Targets(_Strict):
    role_types: list[str] = Field(default_factory=list, max_length=10)
    seniority: str | None = Field(default=None, max_length=40)
    # `company_size` was here and had no consumer anywhere — not the CV prompt, not the cover letter,
    # not the scoring features, not the panel. Captured, validated, stored, read by nothing, and one
    # of the two fields whose length cap dead-lettered an account's onboarding on 2026-08-11.
    # Removed 2026-08-12; `b2c4e8f60a19` strips the key from stored profiles in the same change,
    # because `extra="forbid"` turns a leftover key into a refusal rather than a shrug.


class Constraints(_Strict):
    remote_policy: str | None = Field(default=None, max_length=40)
    onsite_locations: list[str] = Field(default_factory=list, max_length=10)
    languages: list[str] = Field(default_factory=list, max_length=10)
    min_salary: int | None = Field(default=None, ge=0, le=500_000)


class Conflict(_Strict):
    """Something the interview could not resolve. Surfaces in the form as an explicit either/or."""

    field: str = Field(max_length=80)
    value_a: str = Field(max_length=400)
    source_a: str = Field(default="", max_length=200)
    value_b: str = Field(max_length=400)
    source_b: str = Field(default="", max_length=200)


class ProfileSchema(_Strict):
    identity: Identity
    education: list[Education] = Field(default_factory=list, max_length=10,
                                       description="Every qualification, most recent first")
    # The field that was silently coming back empty.
    experience: list[Experience] = Field(
        default_factory=list, max_length=30,
        description=("EVERY role, position and engagement in the documents, most recent first — "
                     "employment, contract, freelance and substantial independent work. This is "
                     "what the candidate's CV is built from, so a missing role cannot appear on "
                     "any application. Never summarise several roles into one entry."))
    projects: list[Project] = Field(
        default_factory=list, max_length=30,
        description=("Substantial things they built that are not a job: side projects, research "
                     "systems, published work."))
    certifications: list[Certification] = Field(
        default_factory=list, max_length=20,
        description="Courses, licences and accreditations, with the issuer and year where stated")
    skills: Skills = Field(default_factory=Skills)
    targets: Targets = Field(default_factory=Targets)
    constraints: Constraints = Field(default_factory=Constraints)

    # Domains with no supporting evidence in the documents. Each becomes a hard gate.
    not_list: list[str] = Field(default_factory=list, max_length=30)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=20)
    # "Never put Freelance Web Developer on a CV", generalised.
    exclusions: list[str] = Field(default_factory=list, max_length=20)

    # False until a human has seen the form.
    confirmed: bool = False

    @field_validator("not_list", "exclusions", mode="after")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        seen, out = set(), []
        for value in values:
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                out.append(value)
        return out

