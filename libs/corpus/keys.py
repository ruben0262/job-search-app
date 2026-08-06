"""Dedup keys: canonical URL and cross-source content key."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# "(Remote)", "(m/f/d)", "(EU)" — parenthesised groups are dropped wholesale before tokenising.
_PAREN_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Words that describe *arrangement*, not identity.
_TITLE_NOISE = {"remote", "hybrid", "onsite", "fully"}
_LOC_NOISE = {"remote", "hybrid", "onsite", "on", "site", "fully", "based"}

# Legal-entity suffixes.
_COMPANY_SUFFIX = {"inc", "ltd", "llc", "gmbh", "kft", "bv", "ag", "co", "corp",
                   "limited", "srl", "sro", "zrt", "plc", "ab", "as", "oy"}

# job-boards.greenhouse.io and boards.greenhouse.io are the same board behind two hostnames.
_GH_HOST = {"job-boards.greenhouse.io": "boards.greenhouse.io"}


def _norm_tokens(s: str) -> list[str]:
    """Lowercase, drop parenthesised groups, split on anything non-alphanumeric."""
    return [t for t in _NON_ALNUM_RE.sub(" ", _PAREN_RE.sub(" ", (s or "").lower())).split() if t]


def content_key(title: str, company: str, location: str = "") -> str | None:
    """Stable cross-source identity for a job, or ``None`` if title/company is unusable."""
    t = [w for w in _norm_tokens(title) if w not in _TITLE_NOISE]
    c = _norm_tokens(company)
    while len(c) > 1 and c[-1] in _COMPANY_SUFFIX:
        c.pop()
    if not t or not c:
        return None
    loc = [w for w in _norm_tokens(location) if w not in _LOC_NOISE]
    return " ".join(c) + "|" + " ".join(t) + ("|" + " ".join(loc) if loc else "")


def canonical_url(url: str) -> str | None:
    """Host + path, stripped of everything that varies without changing the job."""
    p = urlparse((url or "").strip())
    if not p.netloc:
        return None
    host = p.netloc.lower().removeprefix("www.")
    host = _GH_HOST.get(host, host)
    return f"{host}{p.path.rstrip('/')}".lower()   # boards are case-insensitive on tokens
