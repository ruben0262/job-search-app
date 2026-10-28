"""Whether a posting is remote, hybrid or on site, read from its own words.

What a person *accepts* is their remote policy, applied in `libs.tenant.filters`; this module only
classifies. It used to also hold an on-site allowance defaulting to "hungary,budapest" — the reference
instance owner's own — which silently dropped every other on-site job for every platform user.
"""
from __future__ import annotations

import re

# Title/location signals (short text, safe to match loosely).
_REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b", re.I)
_ONSITE_RE = re.compile(r"\bon-?site\b|\boffice-based\b|\bin-office\b|\bno remote\b", re.I)

# Description signals (long text, only STRONG phrases count).
_DESC_HYBRID_RE = re.compile(r"\bhybrid[- ](work(ing)?|model|role|schedule|setup|arrangement)", re.I)
_DESC_REMOTE_RE = re.compile(r"fully remote|100% remote|remote[- ]first|work from anywhere", re.I)
_DESC_ONSITE_RE = re.compile(r"on-?site only|fully on-?site|no remote|office-based|"
                             r"this (role|position) is on-?site", re.I)


def classify(title: str, location: str, description: str | None,
             is_remote: "bool | None", explicit: "str | None" = None) -> str | None:
    """'remote' | 'hybrid' | 'onsite' | None (= unknown, keep)."""
    if explicit in ("remote", "hybrid", "onsite"):
        return explicit
    text = f"{title or ''} {location or ''}"
    if "hybrid" in text.lower():           # plain 'hybrid' is unambiguous in title/location
        return "hybrid"
    if is_remote is True or _REMOTE_RE.search(text):
        return "remote"
    if _ONSITE_RE.search(text):
        return "onsite"
    # Description signals BEFORE the is_remote=False fallback: several sources flag any non-fully-remote job as not-remote, so falling to "onsite" first misclassified (and dropped) hybrid jobs whose hybrid-ness only shows in the JD text.
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
