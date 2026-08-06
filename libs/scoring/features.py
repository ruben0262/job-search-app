"""Deterministic, text-only features appended to the sentence embedding."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from libs.contracts.profile import ProfileSchema

FEATURE_NAMES = [
    "years_required",       # max "N years" mentioned, capped at 15, /15
    "core_stack_count",     # distinct core-stack tokens hit, /8
    "offstack_count",       # distinct NOT-list tokens hit, /5
    "remote_flag",          # description signals fully-remote
    "hybrid_flag",          # description signals hybrid
    "onsite_flag",          # description signals on-site / office-based
    "region_lock_flag",     # region-locked remote the user cannot work
    "junior_flag",          # junior / graduate / entry-level wording
    "senior_flag",          # senior / lead / principal / staff wording
]
N_FEATURES = len(FEATURE_NAMES)

# Bumped only when the *meaning* of an existing column changes.
FEATURE_SCHEMA_VERSION = 1

# Words that carry no matching power on their own.
_NOISE = frozenset({
    "at", "in", "of", "the", "a", "an", "and", "or", "with", "for", "to", "on", "no",
    "depth", "professional", "experience", "work", "working", "level", "years", "year",
    "core", "primary", "pure", "only", "any", "not", "some", "roles", "role", "development",
    "engineer", "engineering", "developer",
    # Words that are only meaningful inside a compound.
    "boot", "stack", "system", "systems", "service", "services", "design", "analysis", "tools",
    "cloud", "data", "web", "software", "engine",
})

# Splits a free-text phrase into candidate terms.
_TERM_RE = re.compile(r"[a-z0-9.](?:[a-z0-9+.#-]*[a-z0-9+#])?", re.I)

_REMOTE_RE = re.compile(r"fully remote|100% remote|remote[- ]first|work from anywhere|"
                        r"remote (?:position|role|job)", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"on-?site|in-office|office-based|on premises|on-premise", re.I)
# The en-dashes are deliberate: "1–2 years" is as common in a posting as with a hyphen.
_JUNIOR_RE = re.compile(r"\bjunior\b|\bgraduate\b|entry[- ]level|new[- ]grad|\bintern(?:ship)?\b|"
                        r"0[-–]2 years|1[-–]2 years", re.I)  # noqa: RUF001
_SENIOR_RE = re.compile(r"\bsenior\b|\bsr\.?\b|\blead\b|\bprincipal\b|\bstaff\b", re.I)
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.I)

# Region locks, keyed by the eligibility a user would need to pass them.
_REGION_LOCKS: dict[str, re.Pattern[str]] = {
    "us": re.compile(
        r"us only|u\.s\. only|us[- ]based only|only.{0,15}united states|"
        r"must be (?:authori[sz]ed|eligible) to work in the (?:us|u\.s\.|united states)|"
        r"authori[sz]ed to work in the (?:us|united states)|must reside in the (?:us|united states)|"
        r"green card|\bgc\b required", re.I),
    "uk": re.compile(r"uk only|united kingdom only|must have (?:the )?right to work in the uk", re.I),
    "eu": re.compile(r"eu only|european union only|eu[- ]based only|"
                     r"must be (?:based|resident) in the (?:eu|european union)", re.I),
    "ca": re.compile(r"canada only|must be (?:authori[sz]ed|eligible) to work in canada", re.I),
}

# Which eligibility statements clear which lock.
_ELIGIBILITY_HINTS: dict[str, tuple[str, ...]] = {
    "us": ("us citizen", "u.s. citizen", "united states", "green card", "us work", "usa"),
    "uk": ("uk", "united kingdom", "british"),
    "eu": ("eu", "european union", "schengen"),
    "ca": ("canada", "canadian"),
}


@dataclass(frozen=True)
class FeatureSpec:
    """One user's token lists. Built from their confirmed profile, used at train *and* serve time."""

    core: tuple[re.Pattern[str], ...]
    offstack: tuple[re.Pattern[str], ...]
    region_locks: tuple[re.Pattern[str], ...]


def feature_spec(profile: ProfileSchema) -> FeatureSpec:
    """Derive the per-user token lists. Deterministic — same profile, same spec."""
    core_terms = _terms(
        profile.skills.languages + profile.skills.frameworks
        + profile.skills.domains + profile.skills.tools
        + [tech for entry in profile.experience for tech in entry.stack]
        + [tech for entry in profile.projects for tech in entry.stack])
    off_terms = _terms(profile.not_list)

    # A term in both lists is a contradiction — the NOT-list wins.
    core_terms = [t for t in core_terms if t not in set(off_terms)]

    eligibility = " ".join(profile.identity.work_eligibility).casefold()
    locks = tuple(pattern for region, pattern in _REGION_LOCKS.items()
                  if not any(hint in eligibility for hint in _ELIGIBILITY_HINTS[region]))

    return FeatureSpec(core=tuple(_compile(t) for t in core_terms),
                       offstack=tuple(_compile(t) for t in off_terms),
                       region_locks=locks)


def job_features(spec: FeatureSpec, title: str, description: str) -> list[float]:
    """Fixed-length (`N_FEATURES`) vector from title + description."""
    text = f"{title or ''}\n{description or ''}"
    return [
        _max_years(text) / 15.0,
        min(_hits(spec.core, text), 8) / 8.0,
        min(_hits(spec.offstack, text), 5) / 5.0,
        1.0 if _REMOTE_RE.search(text) else 0.0,
        1.0 if _HYBRID_RE.search(text) else 0.0,
        1.0 if _ONSITE_RE.search(text) else 0.0,
        1.0 if any(lock.search(text) for lock in spec.region_locks) else 0.0,
        1.0 if _JUNIOR_RE.search(text) else 0.0,
        1.0 if _SENIOR_RE.search(text) else 0.0,
    ]


# ── term extraction ───────────────────────────────────────────────────────────────────────────

def _terms(phrases: list[str]) -> list[str]:
    """Free-text skill phrases to matchable terms, order-preserving and deduplicated."""
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        # Spaces and hyphens only.
        term = term.strip(" -").casefold()
        if len(term) < 2 or term in _NOISE or term in seen:
            return
        seen.add(term)
        out.append(term)

    for phrase in phrases:
        words = [w.casefold() for w in _TERM_RE.findall(phrase)]
        kept = [w for w in words if w not in _NOISE]
        if len(kept) > 1:
            add(" ".join(kept))
        for word in kept:
            add(word)
    return out


@lru_cache(maxsize=4096)
def _compile(term: str) -> re.Pattern[str]:
    """Word-boundary match that survives `c#`, `.net`, and `c++`."""
    parts = [re.escape(p) for p in re.split(r"[\s-]+", term) if p]
    body = r"[\s-]+".join(parts) or re.escape(term)
    return re.compile(rf"(?<![a-z0-9+#.]){body}(?![a-z0-9+#])", re.I)


def _hits(patterns: tuple[re.Pattern[str], ...], text: str) -> int:
    return sum(1 for pattern in patterns if pattern.search(text))


def _max_years(text: str) -> float:
    years = [int(m) for m in _YEARS_RE.findall(text) if m.isdigit()]
    return float(min(max(years), 15)) if years else 0.0


# The minimum `ProfileSchema` will construct: every derived term list comes out empty.
_EMPTY_PROFILE = ProfileSchema.model_validate({"identity": {"name": "unknown"}})


def spec_for(profile: dict | None) -> FeatureSpec:
    """A feature spec from the capture flow's profile JSON, or an empty one when there is none."""
    return feature_spec(ProfileSchema.model_validate(profile) if profile else _EMPTY_PROFILE)
