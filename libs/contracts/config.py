"""The user config schema."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from libs.corpus.geo import CITY_COUNTRY, COUNTRIES
from libs.db.models.corpus import WORKPLACES

# Current shape version, stored on `user_config.schema_version`. 3 (2026-11-06): one section, with no board
# of its own (`migrate_config`).
CONFIG_SCHEMA_VERSION = 3

# The per-user search-term cap (`TENANT_SAFETY.md` §3, `HARVESTER_PLAN.md` §3).
MAX_KEYWORDS_PER_USER = 25

# Countries one search may pick.
MAX_LOCATIONS_PER_USER = 12

# The least a search may have (owner, 2026-11-04): three green words, and at least one country, one way
# of working and one language. Jobs are fetched country by country and the words matched here, so a
# search with no country has no jobs coming in; three words are what make a list worth reading.
MIN_GREEN_WORDS = 3


# The ways of working a person can switch on: remote, hybrid, on site. More than one is either of them.
Workplace = StrEnum("Workplace", {name.upper(): name for name in WORKPLACES})

# What each is called, wherever a person reads it (My search, an alert, the admin), and what it means.
# Job sites' own names for them are `libs.corpus.workfilter.NAMES`.
WORKPLACE_NAMES = {"remote": ("Remote", "From home, every day"),
                   "hybrid": ("Hybrid", "Some days at home, some in"),
                   "onsite": ("On-site", "In the workplace every day")}

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
    """A person's search: the words, places and ways of working My search shows."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS_PER_USER)
    # As many red words as green: the form offers 25 of each, and the 11th used to be refused.
    exclude_keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS_PER_USER)
    locations: list[Location] = Field(default_factory=list,
                                  max_length=MAX_LOCATIONS_PER_USER)
    # Switched on, any of them admits a job; at least one is on. Remote or hybrid by default, as the
    # question it replaced was.
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

    @model_validator(mode="after")
    def _somewhere_somehow(self) -> SearchSection:
        if not self.locations:
            raise PydanticCustomError("no_country", "pick at least one country")
        if not self.workplaces:
            raise PydanticCustomError("no_workplace", "switch on at least one way of working")
        return self


class UserConfigSchema(BaseModel):
    """Everything a user can configure about what they want to see."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # One, in a list as every stored config keeps it (`migrate_config`).
    sections: list[SearchSection] = Field(min_length=1, max_length=1)

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

    @model_validator(mode="before")
    @classmethod
    def _current_shape(cls, values):
        # A page loaded before a change still sends the shape it read: saved as it is read.
        return migrate_config(values) if isinstance(values, dict) else values

    @model_validator(mode="after")
    def _enough_to_search(self) -> UserConfigSchema:
        if not self.languages:
            raise PydanticCustomError("no_language", "pick at least one language")
        if len({word.lower() for section in self.sections for word in section.keywords}) < MIN_GREEN_WORDS:
            raise PydanticCustomError("few_words", "add at least {least} green words",
                                      {"least": MIN_GREEN_WORDS})
        return self


def complete_search(config: dict | None) -> bool:
    """Whether a stored search has what a search saved today must: one rule, the schema's."""
    try:
        UserConfigSchema.model_validate(migrate_config(config or {}))
    except ValidationError:
        return False
    return True


def migrate_config(raw: dict) -> dict:
    """Read a config of any stored version as the current shape."""
    # Deleted 2026-10-28: nothing read it — the daily cap is `libs.limits`' `notifications_per_day`.
    raw = {key: value for key, value in raw.items() if key != "max_notifications_per_day"}
    if "sections" in raw:
        return {**raw, "sections": _one_section([_migrate_section(s) for s in raw.get("sections") or []])}

    section_fields = ("keywords", "exclude_keywords", "locations", "remote_policy", "workplaces",
                      "posted_within_hours")
    section = {key: raw[key] for key in section_fields if key in raw}
    rest = {key: value for key, value in raw.items() if key not in section_fields}
    return {"sections": [_migrate_section(section)], **rest}


def _one_section(sections: list[dict]) -> list[dict]:
    """Every stored section as the one My search shows (2026-11-06). A search could once be split by board,
    a section each. Nothing read a section's board after no board was asked for a word, and matching read
    every section's words, red words, countries and ways of working as one search, while My search showed
    and edited the first section only. Merged, they find the same jobs, and all that finds them is shown."""
    if len(sections) < 2:
        return sections
    merged: dict = {}
    for field in ("keywords", "exclude_keywords", "locations"):
        # Each once, the first spelling kept, in the order the sections had them.
        kept: dict[str, object] = {}
        for section in sections:
            for value in section.get(field) or []:
                kept.setdefault(str(value).lower(), value)
        merged[field] = list(kept.values())
    # A section with none switched on admitted every way of working (`filters._workplaces`).
    ways = {way for section in sections for way in section.get("workplaces") or WORKPLACES}
    merged["workplaces"] = [way for way in WORKPLACES if way in ways]
    windows = [section["posted_within_hours"] for section in sections if section.get("posted_within_hours")]
    if windows:
        merged["posted_within_hours"] = max(windows)
    return [merged]


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
    # Which board a section was for: none since 2026-11-06 (`_one_section`).
    current = {key: value for key, value in section.items() if key not in ("remote_policy", "platform")}
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

