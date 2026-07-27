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
CONFIG_SCHEMA_VERSION = 1

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

    `REMOTE_*` are not places but delivery regions, which is exactly how the sources treat them.
    """

    # Region-level targets. Added 2026-07-27: the first real config searched "European Union",
    # "United Kingdom" and "Europe", and the vocabulary had none of them. They are NOT the same as
    # the REMOTE_* values below - "United Kingdom" means jobs located in the UK, including hybrid
    # and onsite, while REMOTE_UK means remote work for a UK-region employer. Collapsing them would
    # have silently narrowed what an existing user sees.
    EUROPEAN_UNION = "european_union"
    UNITED_KINGDOM = "united_kingdom"
    EUROPE = "europe"
    # Added 2026-07-27 for the second migrated account. Not the same as DUBLIN, for exactly the
    # reason UNITED_KINGDOM is not LONDON: a country-level search returns roles the city one never
    # sees, and folding them would silently narrow what that user is shown.
    IRELAND = "ireland"

    REMOTE_WORLDWIDE = "remote_worldwide"
    REMOTE_EU = "remote_eu"
    REMOTE_UK = "remote_uk"
    REMOTE_US = "remote_us"
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

    # Enforced here rather than only at harvest time, so a user is told at the form instead of
    # being silently under-served later.
    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS_PER_USER)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=10)

    locations: list[Location] = Field(default_factory=list, max_length=5)
    remote_policy: RemotePolicy = RemotePolicy.REMOTE_OR_HYBRID
    seniority: list[Seniority] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)

    min_salary: int | None = Field(default=None, ge=0, le=500_000)
    salary_currency: Currency = Currency.EUR

    max_notifications_per_day: int = Field(default=20, ge=1, le=100)
    # 6 hours to a week. Below 6 h a cycle would mostly return nothing; beyond a week the postings
    # are stale enough that applying is usually pointless.
    posted_within_hours: int = Field(default=72, ge=6, le=168)

    @field_validator("keywords", "exclude_keywords")
    @classmethod
    def _bounded_terms(cls, terms: list[str]) -> list[str]:
        """2–40 characters each, whitespace-collapsed, case-insensitively deduplicated.

        Deduplication matters beyond tidiness: two spellings of one term become two entries in the
        harvest query set and spend the fan-out budget twice for identical results.

        Rejects rather than truncates. A silently dropped keyword is a user wondering why their
        search does not work.
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

    @field_validator("locations", "seniority", "languages")
    @classmethod
    def _deduplicate(cls, values: list) -> list:
        """Order-preserving dedup. The form can emit duplicates; they must not reach the query set."""
        seen, out = set(), []
        for v in values:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out


def json_schema() -> dict:
    """The JSON Schema the frontend generates its form and TypeScript types from.

    Exported from the same model the server validates with, so a field cannot drift between them —
    a changed shape becomes a compile error in the frontend rather than a runtime `undefined`
    (`CODE_STRUCTURE.md` §8).
    """
    return UserConfigSchema.model_json_schema()
