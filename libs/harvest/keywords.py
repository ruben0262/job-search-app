"""How a search term becomes a query key, and how many distinct ones the platform is asking for.

**A fold key, never a search string.** `normalise_keyword` sorts tokens and strips anything outside
`a-z0-9+#` so that two users who typed the same search in different words produce one query. Sending
that string to a board asks for `engineer stress` and truncates every accented term —
`szoftverfejlesztő` to `szoftverfejleszt`. `services.harvester.query_set.original_terms` is the
other half: the key identifies, the phrase the user wrote is what gets sent.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

# Stripped before folding so they cannot make two identical searches look different. Deliberately
# tiny — an aggressive stop-word list starts merging queries that are genuinely distinct.
_STOP_WORDS = frozenset({"a", "an", "the", "and", "or", "of", "for", "in", "at", "to", "with"})
_NON_ALNUM = re.compile(r"[^a-z0-9+#]+")


def normalise_keyword(term: str) -> str:
    """Lowercase, strip punctuation, drop stop-words, sort tokens.

    Sorting is what makes `"developer python"` and `"python developer"` the same key.

    `+` and `#` survive tokenisation on purpose: `c++` and `c#` are real, common search terms and
    stripping them would collapse both into `c`.
    """
    tokens = [t for t in _NON_ALNUM.sub(" ", (term or "").lower()).split() if t]
    kept = [t for t in tokens if t not in _STOP_WORDS]
    # If a term is *entirely* stop-words, keep them rather than producing an empty key — an empty
    # key would fold every such term together into one meaningless query.
    return " ".join(sorted(kept or tokens))


def distinct_keywords(configs: Mapping) -> set[str]:
    """Every distinct fold key across the given configs.

    **This is how many queries a query-taking source has**, now that location has left the query
    axis: one per keyword, per source. The admin panel reports it beside each source's per-cycle
    ceiling, because a ceiling of 40 means nothing until you know whether 28 queries are waiting or
    300 are.

    Reads sections and the flat shape, like everything else that touches a stored config: the schema
    puts keywords in `sections`, and reading only the top level is a bug this codebase has shipped
    twice.
    """
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
