"""Workplace-mode filter — Ruben wants FULLY REMOTE work; hybrid is acceptable; direct
office (on-site) work is out of scope unless the job is in Hungary.

classify() infers remote / hybrid / onsite from the strongest available signal first:
the source's structured is_remote flag and the title/location text, falling back to a few
STRONG phrases in the description (weak signals are ignored — a JD merely mentioning
"remote" proves nothing, and "hybrid" in a description is usually hybrid CLOUD, so there
it must be followed by work/model/role-type words).

Fails OPEN like the language filter: unknown mode = keep and let the scorer judge. Only an
explicit on-site classification outside the allowed countries is dropped. The mode is also
persisted on processed entries ("workplace") so apply-order ranking can prefer remote
(see store.priority_key).

Config: ONSITE_LOCATIONS env (comma-separated substrings, default "hungary,budapest";
empty = allow on-site anywhere, disabling the drop).
"""
from __future__ import annotations

import os
import re

_ONSITE_DEFAULT = "hungary,budapest"

# Title/location signals (short text, safe to match loosely).
_REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.I)
_ONSITE_RE = re.compile(r"\bon-?site\b|\boffice-based\b|\bin-office\b|\bno remote\b", re.I)

# Description signals (long text, only STRONG phrases count).
_DESC_HYBRID_RE = re.compile(r"\bhybrid[- ](work(ing)?|model|role|schedule|setup|arrangement)", re.I)
_DESC_REMOTE_RE = re.compile(r"fully remote|100% remote|remote[- ]first|work from anywhere", re.I)
_DESC_ONSITE_RE = re.compile(r"on-?site only|fully on-?site|no remote|office-based|"
                             r"this (role|position) is on-?site", re.I)


def onsite_locations(allowed: "list[str] | str | None" = None) -> list[str]:
    """Where on-site is acceptable. **Per user here, an env var in the reference.**

    `constraints.onsite_locations` on the profile is the platform's source for this; the default
    is kept so a call with nothing passed behaves exactly as the reference did.
    """
    if isinstance(allowed, list):
        return [c.strip().lower() for c in allowed if c and c.strip()]
    raw = allowed if isinstance(allowed, str) else os.environ.get("ONSITE_LOCATIONS", _ONSITE_DEFAULT)
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def classify(title: str, location: str, description: str | None,
             is_remote: "bool | None", explicit: "str | None" = None) -> str | None:
    """'remote' | 'hybrid' | 'onsite' | None (= unknown, keep).

    `explicit` is an authoritative workplace type already parsed from the posting itself
    (scorer.fetch_desc._parse_workplace: LinkedIn's workplace pill / schema.org
    jobLocationType). When present it wins over every heuristic below — it is the real tag on
    the page, not an inference from title/JD text."""
    if explicit in ("remote", "hybrid", "onsite"):
        return explicit
    text = f"{title or ''} {location or ''}"
    if "hybrid" in text.lower():           # plain 'hybrid' is unambiguous in title/location
        return "hybrid"
    if is_remote is True or _REMOTE_RE.search(text):
        return "remote"
    if _ONSITE_RE.search(text):
        return "onsite"
    # Description signals BEFORE the is_remote=False fallback: several sources flag any
    # non-fully-remote job as not-remote, so falling to "onsite" first misclassified (and
    # dropped) hybrid jobs whose hybrid-ness only shows in the JD text.
    d = (description or "")[:3000]
    if _DESC_HYBRID_RE.search(d):
        return "hybrid"
    if _DESC_REMOTE_RE.search(d):
        return "remote"
    if _DESC_ONSITE_RE.search(d):
        return "onsite"
    if is_remote is False:                 # source explicitly said not-remote, no other signal
        return "onsite"
    return None


def is_allowed(mode: str | None, location: str,
               allowed_locations: "list[str] | None" = None) -> bool:
    """True = keep. Only explicit on-site outside the allowed countries is dropped."""
    if mode != "onsite":
        return True
    allowed = onsite_locations(allowed_locations)
    if not allowed:
        return True
    loc = (location or "").lower()
    return any(c in loc for c in allowed)
