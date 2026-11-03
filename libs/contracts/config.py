"""The user config schema."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from libs.corpus.geo import CITY_COUNTRY, COUNTRIES
from libs.db.models.corpus import WORKPLACES

# Current shape version, stored on `user_config.schema_version`.
CONFIG_SCHEMA_VERSION = 2

# The per-user search-term cap (`TENANT_SAFETY.md` §3, `HARVESTER_PLAN.md` §3).
MAX_KEYWORDS_PER_USER = 25

# Queries are keywords x locations x sources, so the keyword cap applies before the cross product.
MAX_LOCATIONS_PER_USER = 12


class Platform(StrEnum):
    """Which sources a search section targets."""

    ALL = "all"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GOOGLE = "google"


# The ways of working a person can switch on: remote, hybrid, on site. More than one is either of them.
Workplace = StrEnum("Workplace", {name.upper(): name for name in WORKPLACES})

# What each answer to the retired "how often would you go in" question switches on.
_POLICY_WORKPLACES = {"remote_only": ["remote"], "remote_or_hybrid": ["remote", "hybrid"],
                      "any": list(WORKPLACES)}


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    LEAD = "lead"
    MANAGER = "manager"


# The places a person can pick: `libs.corpus.geo`'s countries. Built from them, so a place cannot be
# offered here and unknown there.
Location = StrEnum("Location", {slug.upper(): slug for slug in COUNTRIES})


def location_search_name(value: str) -> str:
    """What a stored place is called, on the page and to a job board."""
    slug = (value or "").strip().lower()
    if not slug:
        return ""
    return COUNTRIES[slug].label if slug in COUNTRIES else slug.replace("_", " ").title()


class Language(StrEnum):
    """ISO 639-1 codes the platform offers. Also a controlled list, for the same reason."""

    EN = "en"
    DE = "de"
    FR = "fr"
    ES = "es"
    NL = "nl"
    HU = "hu"
    PL = "pl"
    PT = "pt"
    IT = "it"
    SV = "sv"
    DA = "da"


class Currency(StrEnum):
    EUR = "EUR"
    GBP = "GBP"
    USD = "USD"
    CHF = "CHF"
    HUF = "HUF"
    PLN = "PLN"
    SEK = "SEK"
    DKK = "DKK"


def _bounded_terms(terms: list[str]) -> list[str]:
    """2–40 characters each, whitespace-collapsed, case-insensitively deduplicated."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = " ".join((raw or "").split())
        if not 2 <= len(term) <= 40:
            raise ValueError(
                f"keyword {raw!r} must be 2-40 characters after trimming (got {len(term)})")
        if term.lower() in seen:
            continue
        seen.add(term.lower())
        cleaned.append(term)
    return cleaned


def _deduplicate(values: list) -> list:
    """Order-preserving dedup. The form can emit duplicates; they must not reach the query set."""
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


class SearchSection(BaseModel):
    """One search, targeted at one platform or at all of them."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    platform: Platform = Platform.ALL
    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS_PER_USER)
    # As many red words as green: the form offers 25 of each, and the 11th used to be refused.
    exclude_keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS_PER_USER)
    locations: list[Location] = Field(default_factory=list,
                                  max_length=MAX_LOCATIONS_PER_USER)
    # Switched on, any of them admits a job; none switched on narrows nothing. Remote or hybrid by
    # default, as the question it replaced was.
    workplaces: list[Workplace] = Field(default_factory=lambda: list(_POLICY_WORKPLACES["remote_or_hybrid"]))
    # 24 hours by default.
    posted_within_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("keywords", "exclude_keywords")
    @classmethod
    def _clean_terms(cls, terms: list[str]) -> list[str]:
        return _bounded_terms(terms)

    @model_validator(mode="before")
    @classmethod
    def _retired_answers(cls, values):
        # A page loaded before the switches still sends the old answer on Save.
        return _migrate_section(values) if isinstance(values, dict) else values

    @field_validator("workplaces")
    @classmethod
    def _clean_workplaces(cls, values: list) -> list:
        return _deduplicate(values)

    @field_validator("locations", mode="before")
    @classmethod
    def _retired_locations(cls, values: list) -> list:
        # A page loaded before a place was retired still sends it on Save: read it as its successor.
        return _migrate_locations({"locations": values}).get("locations") or []

    @field_validator("locations")
    @classmethod
    def _clean_locations(cls, values: list) -> list:
        return _deduplicate(values)


class UserConfigSchema(BaseModel):
    """Everything a user can configure about what they want to see."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    sections: list[SearchSection] = Field(min_length=1, max_length=5)

    # Person-level, not search-level: a candidate is not more senior on one job board than another.
    seniority: list[Seniority] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    # A year's pay in `salary_currency`. `libs.matching` drops a posting whose stated top is below it.
    min_salary: int | None = Field(default=None, ge=0, le=500_000)
    salary_currency: Currency = Currency.EUR

    @field_validator("seniority", "languages")
    @classmethod
    def _clean_enums(cls, values: list) -> list:
        return _deduplicate(values)

    @field_validator("sections")
    @classmethod
    def _cap_total_keywords(cls, sections: list[SearchSection]) -> list[SearchSection]:
        """The cap spans every section, not each one."""
        total = sum(len(section.keywords) for section in sections)
        if total > MAX_KEYWORDS_PER_USER:
            raise ValueError(
                f"{total} keywords across {len(sections)} sections exceeds the limit of "
                f"{MAX_KEYWORDS_PER_USER}")

        platforms = [section.platform for section in sections]
        if len(set(platforms)) != len(platforms):
            # Two sections for one platform is a mistake, and the fold would silently merge them.
            raise ValueError("each platform may appear in at most one section")
        return sections


def migrate_config(raw: dict) -> dict:
    """Read a config of any stored version as the current shape."""
    # Deleted 2026-10-28: nothing read it — the daily cap is `libs.limits`' `notifications_per_day`.
    raw = {key: value for key, value in raw.items() if key != "max_notifications_per_day"}
    if "sections" in raw:
        return {**raw, "sections": [_migrate_section(s) for s in raw.get("sections") or []]}

    section_fields = ("keywords", "exclude_keywords", "locations", "remote_policy", "workplaces",
                      "posted_within_hours")
    section = {key: raw[key] for key in section_fields if key in raw}
    section["platform"] = Platform.ALL.value
    rest = {key: value for key, value in raw.items() if key not in section_fields}
    return {"sections": [_migrate_section(section)], **rest}


# What a retired location slug means in the vocabulary that replaced it.
_RETIRED_LOCATIONS: dict[str, str | None] = {
    "european_union": "europe",
    "remote_eu": "europe",
    "remote_uk": "united_kingdom",
    # No US slug in the vocabulary, and no user has asked for one.
    "remote_us": None,
    # "Anywhere" is the absence of a place, and an empty location list already says that.
    "remote_worldwide": None,
    # Cities, retired 2026-11-02 (owner): My search picks countries. Each always admitted its whole
    # country, so reading it as that country changes nobody's jobs.
    **CITY_COUNTRY,
}


def _migrate_section(section: dict) -> dict:
    """One stored section in the current shape: its places, and its ways of working as switches.

    A section saved before the switches has the answer they replaced, or none, which was read as
    remote or hybrid; each becomes the switches that admit the same jobs."""
    section = _migrate_locations(section)
    if not section:
        return section
    switched = _POLICY_WORKPLACES.get(section.get("remote_policy")) or _POLICY_WORKPLACES["remote_or_hybrid"]
    current = {key: value for key, value in section.items() if key != "remote_policy"}
    current.setdefault("workplaces", list(switched))
    return current


def _migrate_locations(section: dict) -> dict:
    """Rewrite a section's locations into the current vocabulary, dropping what has no successor."""
    stored = (section or {}).get("locations")
    if not stored:
        return section
    moved: list[str] = []
    for value in stored:
        slug = str(value or "").strip().lower()
        replacement = _RETIRED_LOCATIONS.get(slug, slug)
        if replacement and replacement not in moved:
            moved.append(replacement)
    return {**section, "locations": moved}

