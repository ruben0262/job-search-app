"""Whether a posting is remote, hybrid or on site, read from its own words.

What a person *accepts* is the ways of working they switched on, applied in `libs.tenant.filters`;
this module only classifies. It used to also hold an on-site allowance defaulting to "hungary,budapest"
— the reference instance owner's own — which silently dropped every other on-site job for every user.
"""
from __future__ import annotations

import re

from libs.db.models.corpus import WORKPLACES

# What job sites call each one in a title or a location: "Engineer (Remote)", "Madrid (Híbrido)",
# "praca zdalna". Matched as whole phrases. A word that also names something else in a title stays out:
# a bare "office" is "Front Office Supervisor" more often than it is on site (31 of 8,896 titles,
# 2026-11-03), and "hybrid" is matched anywhere, as before, so "hybride" is hybrid too.
NAMES = {
    "remote": ("remote", "work from home", "wfh", "telecommute", "telework", "home based", "home-based",
               "remoto", "teletrabajo", "zdalna", "zdalnie"),
    "hybrid": ("hybrid", "híbrido", "hibrido", "hybrydowa", "hybrydowo"),
    "onsite": ("on-site", "onsite", "on site", "office-based", "in-office", "in office", "no remote"),
}


def _named(kind: str) -> re.Pattern:
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, NAMES[kind])) + r")(?!\w)", re.I)


_REMOTE_RE, _ONSITE_RE = _named("remote"), _named("onsite")
_HYBRID_RE = re.compile("|".join(map(re.escape, NAMES["hybrid"])), re.I)

# Description signals (long text, only STRONG phrases count).
_DESC_HYBRID_RE = re.compile(r"\bhybrid[- ](work(ing)?|model|role|schedule|setup|arrangement)", re.I)
_DESC_REMOTE_RE = re.compile(r"fully remote|100% remote|remote[- ]first|work from anywhere", re.I)
_DESC_ONSITE_RE = re.compile(r"on-?site only|fully on-?site|no remote|office-based|"
                             r"this (role|position) is on-?site", re.I)


def classify(title: str, location: str, description: str | None,
             is_remote: "bool | None", explicit: "str | None" = None) -> str | None:
    """'remote' | 'hybrid' | 'onsite' | None (= unknown, keep)."""
    if explicit in WORKPLACES:
        return explicit
    text = f"{title or ''} {location or ''}"
    if _HYBRID_RE.search(text):            # 'hybrid' is unambiguous in title/location
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
