"""The user config schema."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class RemotePolicy(StrEnum):
    REMOTE_ONLY = "remote_only"
    REMOTE_OR_HYBRID = "remote_or_hybrid"
    ANY = "any"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    LEAD = "lead"
    MANAGER = "manager"


class Location(StrEnum):
    """Controlled vocabulary."""

    EUROPE = "europe"
    UNITED_KINGDOM = "united_kingdom"
    IRELAND = "ireland"
    SWITZERLAND = "switzerland"
    NORWAY = "norway"

    LONDON = "london"
    BERLIN = "berlin"
    AMSTERDAM = "amsterdam"
    DUBLIN = "dublin"
    PARIS = "paris"
    MADRID = "madrid"
    LISBON = "lisbon"
    WARSAW = "warsaw"
    BUDAPEST = "budapest"
    ZURICH = "zurich"
    STOCKHOLM = "stockholm"
    COPENHAGEN = "copenhagen"


# What a stored slug is called when it is handed to a job board.
LOCATION_SEARCH_NAMES = {
    "europe": "European Union",
    "united_kingdom": "United Kingdom",
    "ireland": "Ireland",
    "switzerland": "Switzerland",
    "norway": "Norway",
}


def location_search_name(value: str) -> str:
    """The name a board will recognise for a stored slug."""
    slug = (value or "").strip().lower()
    if not slug:
        return ""
    return LOCATION_SEARCH_NAMES.get(slug, slug.replace("_", " ").title())


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
    exclude_keywords: list[str] = Field(default_factory=list, max_length=10)
    locations: list[Location] = Field(default_factory=list,
                                  max_length=MAX_LOCATIONS_PER_USER)
    remote_policy: RemotePolicy = RemotePolicy.REMOTE_OR_HYBRID
    # 24 hours by default.
    posted_within_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("keywords", "exclude_keywords")
    @classmethod
    def _clean_terms(cls, terms: list[str]) -> list[str]:
        return _bounded_terms(terms)

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
    min_salary: int | None = Field(default=None, ge=0, le=500_000)
    salary_currency: Currency = Currency.EUR
    # Not asked at onboarding (removed from the form 2026-07-28).
    max_notifications_per_day: int = Field(default=20, ge=1, le=100)

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
    if "sections" in raw:
        return {**raw, "sections": [_migrate_locations(s) for s in raw.get("sections") or []]}

    section_fields = ("keywords", "exclude_keywords", "locations", "remote_policy",
                      "posted_within_hours")
    section = {key: raw[key] for key in section_fields if key in raw}
    section["platform"] = Platform.ALL.value
    rest = {key: value for key, value in raw.items() if key not in section_fields}
    return {"sections": [_migrate_locations(section)], **rest}


# What a location slug retired on 2026-08-04 means in the vocabulary that replaced it.
_RETIRED_LOCATIONS: dict[str, str | None] = {
    "european_union": "europe",
    "remote_eu": "europe",
    "remote_uk": "united_kingdom",
    # No US slug in the vocabulary, and no user has asked for one.
    "remote_us": None,
    # "Anywhere" is the absence of a place, and an empty location list already says that.
    "remote_worldwide": None,
}


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

