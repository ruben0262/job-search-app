"""How a search term becomes a query key, and how many distinct ones the platform is asking for."""
from __future__ import annotations

import re
from collections.abc import Mapping

# Stripped before folding so they cannot make two identical searches look different.
_STOP_WORDS = frozenset({"a", "an", "the", "and", "or", "of", "for", "in", "at", "to", "with"})
_NON_ALNUM = re.compile(r"[^a-z0-9+#]+")


def normalise_keyword(term: str) -> str:
    """Lowercase, strip punctuation, drop stop-words, sort tokens."""
    tokens = [t for t in _NON_ALNUM.sub(" ", (term or "").lower()).split() if t]
    kept = [t for t in tokens if t not in _STOP_WORDS]
    # A term that is entirely stop-words keeps them: an empty key would fold them all together.
    return " ".join(sorted(kept or tokens))


def distinct_keywords(configs: Mapping) -> set[str]:
    """Every distinct fold key across the given configs."""
    keys: set[str] = set()
    for stored in configs.values():
        config = stored or {}
        terms = list(config.get("keywords") or [])
        for section in config.get("sections") or []:
            terms += list((section or {}).get("keywords") or [])
        for term in terms:
            key = normalise_keyword(str(term or ""))
            if key:
                keys.add(key)
    return keys
