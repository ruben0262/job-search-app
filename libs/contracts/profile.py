"""The captured profile: what the interview produces and the confirmation form edits.

One schema, three consumers — the capture adapter validates the agent's output against it, the
confirmation form is generated from it, and `libs.scoring` renders the rubric from it
(`PROFILE_CAPTURE_PLAN.md` §4.5). Same reason `UserConfigSchema` is one model: two definitions of
the same shape drift, and here the drift would be silent.

`not_list` carries the most weight and is the most dangerous field: a wrong entry becomes a hard
gate that suppresses good jobs without saying so. It is a *proposal* until the user confirms it
(§5), which is also the prompt-injection boundary — an uploaded CV is untrusted text, and nothing
the agent wrote reaches a hard gate without a human click.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bumped when the shape changes. `users.profile` rows keep the version they were written with, so a
# migration can read them rather than guess.
#
# 2 (2026-07-28) added `certifications`. Additive and defaulted, so a version-1 row still validates
# — the bump records *when* the field appeared, which is what tells a later reader that a v1 profile
# has no certifications because the schema had nowhere to put them, rather than because the
# candidate has none.
PROFILE_SCHEMA_VERSION = 2


class _Strict(BaseModel):
    # `forbid` so an agent inventing a field fails validation instead of having it silently dropped
    # — the dropped case looks like capture worked and produces a thinner profile than the user saw.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Identity(_Strict):
    name: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    # Drives the CV header location rule and the region gates, so it is a list of plain statements
    # ("EU citizen", "UK work visa") rather than an enum — the vocabulary is not ours to close.
    work_eligibility: list[str] = Field(default_factory=list, max_length=10)
    links: dict[str, str] = Field(default_factory=dict, max_length=10)


class Education(_Strict):
    degree: str = Field(max_length=120)
    institution: str = Field(max_length=160)
    field: str | None = Field(default=None, max_length=120)
    thesis: str | None = Field(default=None, max_length=400)


class Experience(_Strict):
    role: str = Field(max_length=160)
    org: str = Field(max_length=160)
    # Free text, not dates: CVs say "2019", "Mar 2019", "present". Parsing them here would reject
    # real profiles to gain a precision nothing downstream uses.
    start: str | None = Field(default=None, max_length=32)
    end: str | None = Field(default=None, max_length=32)
    bullets: list[str] = Field(default_factory=list, max_length=20)
    stack: list[str] = Field(default_factory=list, max_length=40)
    metrics: list[str] = Field(default_factory=list, max_length=20)


class Project(_Strict):
    name: str = Field(max_length=160)
    what_built: str = Field(default="", max_length=1000)
    stack: list[str] = Field(default_factory=list, max_length=40)
    metrics: list[str] = Field(default_factory=list, max_length=20)


class Certification(_Strict):
    """A course, licence or accreditation. Its own section on a CV, and a keyword an ATS looks for.

    `issuer` and `year` are free text and optional for the same reason experience dates are: a CV
    says "Bloomberg (2024)", "AWS, valid to 2027", or nothing at all, and parsing that would reject
    real profiles to gain a precision nothing downstream uses.
    """

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
    company_size: str | None = Field(default=None, max_length=40)


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
    education: list[Education] = Field(default_factory=list, max_length=10)
    experience: list[Experience] = Field(default_factory=list, max_length=30)
    projects: list[Project] = Field(default_factory=list, max_length=30)
    certifications: list[Certification] = Field(default_factory=list, max_length=20)
    skills: Skills = Field(default_factory=Skills)
    targets: Targets = Field(default_factory=Targets)
    constraints: Constraints = Field(default_factory=Constraints)

    # Domains with no supporting evidence in the documents. Each becomes a hard gate.
    not_list: list[str] = Field(default_factory=list, max_length=30)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=20)
    # "Never put Freelance Web Developer on a CV", generalised.
    exclusions: list[str] = Field(default_factory=list, max_length=20)

    # False until a human has seen the form. Nothing downstream may render from an unconfirmed
    # profile — that is the injection boundary, so it is a field rather than a convention.
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


def json_schema() -> dict:
    """What the confirmation form and the TypeScript types are generated from."""
    return ProfileSchema.model_json_schema()
