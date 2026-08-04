"""The user config schema. One Pydantic model behind server validation *and* the React form.

**Invalid states are unrepresentable rather than validated away** (`TENANT_SAFETY.md` §4). The
requirement is that a non-technical user cannot produce a config that is broken or unrealistic, and
the way to get that is bounded types and controlled vocabularies — not free text plus a wall of
error messages.

Two consumers, one definition:

- the API validates against this model — **the server is the authority**, client validation is a
  convenience;
- the React form is generated from its JSON Schema export, so a field cannot exist in one and not
  the other (`CODE_STRUCTURE.md` §8).

**Locations are a picklist, never free text**, and that is not only a UI decision: harvest dedup
folds queries on a normalised location, so free text would multiply the query set with spellings of
the same place and quietly spend the fan-out budget on duplicates (`HARVESTER_PLAN.md` §2).

**Nothing is silently clamped.** An out-of-range value is a rejection naming the field and the
allowed range. Silent truncation is how a user concludes the product is broken rather than that they
hit a limit.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Current shape version, stored on `user_config.schema_version`. Bump when the shape changes so a
# migration can read old rows and know what they meant, instead of guessing.
# 2 (2026-07-28): the flat keyword list became per-platform sections
# (`ONBOARDING_AND_PROCESSING.md` §3). A v1 row is readable — `migrate_config` wraps it in a single
# `all` section — so old rows keep their version until the user next saves.
CONFIG_SCHEMA_VERSION = 2

# The per-user search-term cap (`TENANT_SAFETY.md` §3, `HARVESTER_PLAN.md` §3).
#
# **It lives here, in libs, because two things need it and they cannot share a definition anywhere
# else.** The form and the server validate against it, and the harvester's fold enforces it when
# spending the fan-out budget. It was briefly duplicated — a literal here and a constant in
# `services/harvester` — which is exactly how a rule drifts: raise one and the other silently
# accepts terms it will never serve. `libs/contracts` is the only home both may import from, since
# `libs` must never import a service.
#
# At Phase 3 this becomes a row in `plans.limits` rather than a constant, so a paid tier is an
# INSERT rather than a migration plus an audit of every hardcoded limit. The seam is the same.
#
# **Raised 5 -> 25 on 2026-07-27, after measuring against a real config rather than a planned one.**
# The reference instance's owner searches 23 distinct terms; a cap of 5 would have dropped 18 of
# them and made the platform demonstrably worse than the stack it replaces, failing the D6
# comparison by construction. 5 was a planning number that had never met a user.
#
# The volume is fine, and the reason is the selection algorithm rather than the cap: 23 terms x 2
# regions x 3 sources is ~138 queries against a 60-per-cycle budget, and round-robin serves every
# user's first term before anyone's second, so one heavy user is covered across ~3 cycles (45
# minutes) without crowding anybody out. The cap's real job is fairness between users, and
# round-robin already provides that - the cap is a backstop against absurdity, not the mechanism.
MAX_KEYWORDS_PER_USER = 25

# **Locations multiply, which is why there is a cap at all**: queries are keywords x locations x
# sources, and `build_query_set` caps the keywords before that cross product rather than after.
#
# It was 5, and the form offers about twenty choices across remote scopes, regions and cities. Any
# honest answer to "tick everything that applies" went past it -- remote from the EU and the UK,
# plus the EU, Europe and the UK, plus one city is six -- and the only thing that said so was a
# validation error after the tick. Twelve leaves room for a real answer while still bounding the
# fan-out, and the fold shares identical queries between users, so the marginal cost of a location
# is only the combinations nobody else asked for.
MAX_LOCATIONS_PER_USER = 12


class Platform(StrEnum):
    """Which sources a search section targets.

    Only sources that *take a query* can be named. The ATS and board families are crawled rather
    than searched — naming one here would silently produce no queries at all, which is exactly the
    kind of configuration that looks fine and returns nothing.
    """

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
    """Controlled vocabulary. **Adding a value is a deliberate act, not a user typing.**

    Every entry here becomes a distinct axis of the harvest query set, so this list is a direct
    input to the fan-out budget. Growing it without measuring the cycle cost is how a 15-minute
    harvest becomes an hour (`HARVESTER_PLAN.md` §3).

    **This is a list of places and nothing else** (2026-08-04). It used to carry `remote_worldwide`,
    `remote_eu`, `remote_uk` and `remote_us`, which are not places — they made "where" and "how" one
    question whose answers could contradict each other, and duplicated the `remote_policy` field
    every section already has. Working arrangement is `remote_policy`; this is geography.

    **One region, and it is `europe`.** `european_union` and `europe` were two slugs nobody reading
    a checkbox would tell apart, and getting it wrong silently changes which jobs somebody sees.
    `europe` means the 27 member states; the UK, Switzerland and Norway are named separately,
    because the UK not being in the EU is the case this whole filter exists for.

    Every value here must resolve to countries in `libs.corpus.geo`, or a user can select something
    the country filter cannot act on — and `test_geo` fails if one does not.
    """

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


# **`LOCATION_SEARCH_NAMES` was removed on 2026-08-04 with its only caller.** It translated a stored
# slug into the place name a job board would recognise, for the LinkedIn query set — and the query
# set no longer carries a location at all, on any tier. Country is a filter against the corpus now
# (ROADMAP 6u), so nothing needs to name a place to a board.


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
    """2–40 characters each, whitespace-collapsed, case-insensitively deduplicated.

    Deduplication matters beyond tidiness: two spellings of one term become two entries in the
    harvest query set and spend the fan-out budget twice for identical results.

    Rejects rather than truncates. A silently dropped keyword is a user wondering why their search
    does not work.
    """
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
    """One search, targeted at one platform or at all of them.

    Most users have exactly one section, `platform: all`, and never see the concept. It exists for
    the case a flat config cannot express: different terms or a different workplace policy on
    LinkedIn than on the job boards (`ONBOARDING_AND_PROCESSING.md` §3).

    A section names *search* parameters only. Things that are true of the person rather than of a
    search — seniority, languages, salary floor — stay on the config, because a user is not a
    different candidate on a different site.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    platform: Platform = Platform.ALL
    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS_PER_USER)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=10)
    locations: list[Location] = Field(default_factory=list,
                                  max_length=MAX_LOCATIONS_PER_USER)
    remote_policy: RemotePolicy = RemotePolicy.REMOTE_OR_HYBRID
    # 24 hours by default. The harvest runs every 15 minutes, so a wider window mostly re-reads
    # postings already deduplicated away — but a brand new account needs one wider pass or its first
    # impression of the product is a blank page, which is what `COLD_START_PLAN.md` covers.
    #
    # **The floor is 1, not 6.** The reference's frequent loop runs at two hours
    # (`WATCHER_MAX_AGE_HOURS`) and that is the whole point of it: being first to a posting is the
    # single biggest thing a candidate controls, and a six-hour floor made the platform's tightest
    # setting three times looser than the instance it was ported from.
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
    """Everything a user can configure about what they want to see.

    Bounds come from `TENANT_SAFETY.md` §4 and are enforced here **and** in the database. Two layers
    on purpose: the form is the usability layer, the database is the authority, and a write path
    that bypassed the form must not be able to bypass the bounds.

    Note what is *not* here: `notify_interval_min`, `timezone`, and the LinkedIn interval live as
    indexed columns on `users`, because the scheduler's dispatcher reads them every 60 seconds
    across every active user. The split is by access pattern, and the form writes across both
    without caring.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    sections: list[SearchSection] = Field(min_length=1, max_length=5)

    # Person-level, not search-level: a candidate is not more senior on one job board than another.
    seniority: list[Seniority] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    min_salary: int | None = Field(default=None, ge=0, le=500_000)
    salary_currency: Currency = Currency.EUR
    # **Not asked at onboarding** (removed from the form 2026-07-28). It is a plan limit rather
    # than a search preference — putting it in front of a free user implies it is theirs to choose,
    # when it is the first thing a paid tier will raise. Kept in the schema with its default so
    # nothing downstream changes and a stored config stays valid; the field becomes a tier setting
    # when subscriptions exist rather than a question.
    max_notifications_per_day: int = Field(default=20, ge=1, le=100)

    @field_validator("seniority", "languages")
    @classmethod
    def _clean_enums(cls, values: list) -> list:
        return _deduplicate(values)

    @field_validator("sections")
    @classmethod
    def _cap_total_keywords(cls, sections: list[SearchSection]) -> list[SearchSection]:
        """The cap spans every section, not each one.

        Per-section caps would let five sections buy five times the fan-out budget, which is the
        one thing the cap exists to prevent — and it would do it silently, by making each individual
        section look compliant.
        """
        total = sum(len(section.keywords) for section in sections)
        if total > MAX_KEYWORDS_PER_USER:
            raise ValueError(
                f"{total} keywords across {len(sections)} sections exceeds the limit of "
                f"{MAX_KEYWORDS_PER_USER}")

        platforms = [section.platform for section in sections]
        if len(set(platforms)) != len(platforms):
            # Two sections for one platform is almost always a mistake, and the fold would silently
            # merge them — so the user would see terms they thought they had separated.
            raise ValueError("each platform may appear in at most one section")
        return sections


def migrate_config(raw: dict) -> dict:
    """Read a config of any stored version as the current shape.

    A v1 row is a flat keyword list; it becomes one `all` section carrying exactly what it carried
    before. Done on read rather than by rewriting every row, so a user's stored config is never
    changed by a deploy they did not ask for — it upgrades the next time they save.
    """
    if "sections" in raw:
        return {**raw, "sections": [_migrate_locations(s) for s in raw.get("sections") or []]}

    section_fields = ("keywords", "exclude_keywords", "locations", "remote_policy",
                      "posted_within_hours")
    section = {key: raw[key] for key in section_fields if key in raw}
    section["platform"] = Platform.ALL.value
    rest = {key: value for key, value in raw.items() if key not in section_fields}
    return {"sections": [_migrate_locations(section)], **rest}


# What a location slug retired on 2026-08-04 means in the vocabulary that replaced it. Applied on
# read, like the v1 upgrade above, so nobody's stored config is rewritten by a deploy — but the
# filter, the query set and the form all see the current shape immediately.
#
# `remote_*` slugs carried two claims at once. The **place** half is kept; the **remote** half is
# dropped here rather than translated, because `remote_policy` is a per-section field the user has
# already set and overwriting it from a location would be inventing an answer they did not give.
_RETIRED_LOCATIONS: dict[str, str | None] = {
    "european_union": "europe",
    "remote_eu": "europe",
    "remote_uk": "united_kingdom",
    # No US slug in the vocabulary, and no user has asked for one. Dropping it leaves the section
    # with whatever else it named, or with nothing — which correctly means "no country filter".
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


def json_schema() -> dict:
    """The JSON Schema the frontend generates its form and TypeScript types from.

    Exported from the same model the server validates with, so a field cannot drift between them —
    a changed shape becomes a compile error in the frontend rather than a runtime `undefined`
    (`CODE_STRUCTURE.md` §8).
    """
    return UserConfigSchema.model_json_schema()
