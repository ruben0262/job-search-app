"""Deterministic, text-only features appended to the sentence embedding.

Ported from the reference instance's `scorer/features.py`, with the one change that matters: the
token lists are **per user**, built from `users.profile`, instead of hardcoding one person's stack.
That was missed in every earlier draft of the plan — `_CORE_TOKENS` and the off-stack list are the
strengths and NOT-list of a specific candidate, so the extractor is the *third* personal artifact
alongside the rubric and the facts block (`PROFILE_CAPTURE_PLAN.md` §6.3).

Two rules carried over verbatim, because breaking either is silent:

- **Training and serving MUST build these identically.** Both import this one function. A second
  copy that drifts does not crash; it scores against different semantics.
- **`FEATURE_NAMES` order is platform-owned and global. Append new features at the END** so older
  cached artifacts stay readable. Only the *token lists* vary per user — if the column order varied
  too, no artifact would be portable and the append-only rule would mean nothing.

Which is why a model artifact records `profile_generation`: when a user's core or NOT-list tokens
change, `core_stack_count` and `offstack_count` *mean* something different, and a model fit against
the old meaning must not serve. The scorer fails closed on a mismatch, the same discipline the
embedder applies to `(model, dim, version)`.
"""
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

# Bumped only when the *meaning* of an existing column changes. Appending a column does not need it;
# that is the point of the append-only rule.
FEATURE_SCHEMA_VERSION = 1

# Words that carry no matching power on their own. A NOT-list entry reads "enterprise Java/C#/.NET
# at depth" — the signal is the technologies, not "at depth", and matching \bat\b would fire on
# every posting ever written.
_NOISE = frozenset({
    "at", "in", "of", "the", "a", "an", "and", "or", "with", "for", "to", "on", "no",
    "depth", "professional", "experience", "work", "working", "level", "years", "year",
    "core", "primary", "pure", "only", "any", "not", "some", "roles", "role", "development",
    "engineer", "engineering", "developer",
    # Words that are only meaningful inside a compound. Splitting "Spring Boot" into its words
    # yields "boot", which then fires on "bootstrap" and "bootcamp"; "full-stack" yields "stack",
    # which fires on "tech stack" in every posting ever written. The compound itself is kept.
    "boot", "stack", "system", "systems", "service", "services", "design", "analysis", "tools",
    "cloud", "data", "web", "software", "engine",
})

# Splits a free-text phrase into candidate terms. A leading dot is part of the term, not
# punctuation: `.net` must stay `.net`, because `net` would match "network" and, worse, would not
# match ".NET" itself — the dot sits inside the boundary lookbehind.
_TERM_RE = re.compile(r"[a-z0-9.](?:[a-z0-9+.#-]*[a-z0-9+#])?", re.I)

_REMOTE_RE = re.compile(r"fully remote|100% remote|remote[- ]first|work from anywhere|"
                        r"remote (?:position|role|job)", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"on-?site|in-office|office-based|on premises|on-premise", re.I)
# The en-dashes are deliberate and must stay: job postings are typeset, and "1–2 years" with an
# en dash is at least as common as with a hyphen. Both are matched.
_JUNIOR_RE = re.compile(r"\bjunior\b|\bgraduate\b|entry[- ]level|new[- ]grad|\bintern(?:ship)?\b|"
                        r"0[-–]2 years|1[-–]2 years", re.I)  # noqa: RUF001
_SENIOR_RE = re.compile(r"\bsenior\b|\bsr\.?\b|\blead\b|\bprincipal\b|\bstaff\b", re.I)
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.I)

# Region locks, keyed by the eligibility a user would need to pass them. A user with US work rights
# must NOT get a region_lock_flag on a US-only posting — under the reference's single-tenant list
# they would have, which is exactly the hardcoding this module exists to remove.
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

# Which eligibility statements clear which lock. Substring match against the user's own free-text
# `work_eligibility`, lowercased — the vocabulary is not ours to close, so this is a hint list, not
# a validator.
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

    # A term in both lists is a contradiction — the NOT-list wins. Otherwise a stale strength entry
    # would quietly cancel a confirmed hard gate, and the gate is the one the user reviewed.
    core_terms = [t for t in core_terms if t not in set(off_terms)]

    eligibility = " ".join(profile.identity.work_eligibility).casefold()
    locks = tuple(pattern for region, pattern in _REGION_LOCKS.items()
                  if not any(hint in eligibility for hint in _ELIGIBILITY_HINTS[region]))

    return FeatureSpec(core=tuple(_compile(t) for t in core_terms),
                       offstack=tuple(_compile(t) for t in off_terms),
                       region_locks=locks)


def job_features(spec: FeatureSpec, title: str, description: str) -> list[float]:
    """Fixed-length (`N_FEATURES`) vector from title + description. Order matches `FEATURE_NAMES`.

    Title and description only — never company, location, or salary — so this respects the same
    de-bias rule as the embedding text. Roughly normalised so raw scales are comparable to the
    embedding dimensions.
    """
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


def feature_matrix(spec: FeatureSpec, rows: list[dict]):
    """Feature matrix for label/job dicts (`title` + `description`). numpy, imported lazily."""
    import numpy as np  # noqa: PLC0415 - keeps libs.scoring importable without numpy

    return np.array([job_features(spec, r.get("title", ""), r.get("description", ""))
                     for r in rows], dtype=np.float32)


# ── term extraction ───────────────────────────────────────────────────────────────────────────

def _terms(phrases: list[str]) -> list[str]:
    """Free-text skill phrases to matchable terms, order-preserving and deduplicated.

    Multi-word phrases are kept whole *as well as* split, so "machine learning" matches as a phrase
    and "pytorch" from "PyTorch (CV/RL research)" matches on its own.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        # Spaces and hyphens only. Stripping dots here would undo the leading-dot handling above
        # and turn `.net` back into `net`. A trailing dot is already impossible: `_TERM_RE` cannot
        # end on one.
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
    """Word-boundary match that survives `c#`, `.net`, and `c++`.

    `\\b` is defined against word characters, so `\\bc#\\b` never matches — the boundary after `#`
    requires a following word character. Lookarounds over the actual token alphabet do work.

    Spaces and hyphens are interchangeable in the text being matched: a profile saying "full-stack"
    must match a posting saying "full stack", and vice versa. Without it the hyphenated spelling
    silently matches nothing — invisible, because the feature simply reads 0.
    """
    parts = [re.escape(p) for p in re.split(r"[\s-]+", term) if p]
    body = r"[\s-]+".join(parts) or re.escape(term)
    return re.compile(rf"(?<![a-z0-9+#.]){body}(?![a-z0-9+#])", re.I)


def _hits(patterns: tuple[re.Pattern[str], ...], text: str) -> int:
    return sum(1 for pattern in patterns if pattern.search(text))


def _max_years(text: str) -> float:
    years = [int(m) for m in _YEARS_RE.findall(text) if m.isdigit()]
    return float(min(max(years), 15)) if years else 0.0
