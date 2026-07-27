"""Query-set construction and the fan-out budget. Pure logic, no I/O.

**The insight this implements:** the brief describes accumulating every user's search terms and
scraping the union. That is right for one tier and unnecessary for the others — roughly two thirds
of sources (direct ATS, board APIs) are feed-shaped rather than query-shaped and need no term
merging at all. Only the JobSpy tier consumes a query set, and it is also the tier that costs
bandwidth and carries ban risk (`HARVESTER_PLAN.md` §1).

So this module governs the expensive third, and its job is to make the query set as small as
honestly possible and then refuse to exceed a budget.

**A query is a normalised tuple, not a string.** `"Python Developer"`, `"python  developer"` and
`"developer python"` are one query. Folding on the tuple is what turns 50 users × 5 terms into
something far smaller, because users overlap heavily on common terms.

**Subsumption is deliberately not implemented.** `"python"` strictly contains `"python developer"`,
so in principle the broader query could replace the narrower. It cannot, safely: sources cap result
counts, so a broader query returns a *truncated* superset and silently drops the narrow query's
tail. Exact-tuple dedup captures most of the win with none of that risk (`HARVESTER_PLAN.md` §2).
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID

from libs.contracts.config import MAX_KEYWORDS_PER_USER

# Fan-out budget (HARVESTER_PLAN.md §3). Unbounded, 50 users × 5 terms × 3 sources = 750
# queries/cycle: a ban, a bandwidth bill, and a 15-minute cycle that takes an hour.
HARVEST_MAX_QUERIES_PER_CYCLE = 60

# Imported, not redefined. The same cap bounds the config form, and a second copy here is how the
# two drift: raise one and the other silently accepts terms it will never serve.
HARVEST_MAX_QUERIES_PER_USER = MAX_KEYWORDS_PER_USER

# Coarse buckets, so a user wanting 24h and one wanting 72h share the 72h fetch and the 24h user
# filters down locally. Fetching each exact hour figure separately would multiply the query set for
# results that are strict subsets of one another.
AGE_BUCKETS_HOURS = (24, 72, 168)

# Stripped before folding so they cannot make two identical searches look different. Deliberately
# tiny — an aggressive stop-word list starts merging queries that are genuinely distinct.
_STOP_WORDS = frozenset({"a", "an", "the", "and", "or", "of", "for", "in", "at", "to", "with"})
_NON_ALNUM = re.compile(r"[^a-z0-9+#]+")


class QueryKey(NamedTuple):
    """One distinct thing to ask a source for. Executed **once** per cycle regardless of demand."""

    source: str
    keyword_norm: str
    location_norm: str
    is_remote: bool
    max_age_hours: int


@dataclass(frozen=True)
class PlannedQuery:
    """A query selected for this cycle, with the users who wanted it.

    The user set exists for **attribution and quota accounting only** — results go to the shared
    corpus regardless of who asked, because a job posting is not personal data and dividing them by
    requester would defeat the entire shared-corpus design.
    """

    key: QueryKey
    user_ids: frozenset[UUID]

    @property
    def demand(self) -> int:
        return len(self.user_ids)


def normalise_keyword(term: str) -> str:
    """Lowercase, strip punctuation, drop stop-words, sort tokens.

    Sorting is what makes `"developer python"` and `"python developer"` the same key. It is safe
    here because these are bag-of-words search terms, not phrases — no source treats term order as
    meaningful enough to justify fetching both.

    `+` and `#` survive tokenisation on purpose: `c++` and `c#` are real, common search terms and
    stripping them would collapse both into `c`.
    """
    tokens = [t for t in _NON_ALNUM.sub(" ", (term or "").lower()).split() if t]
    kept = [t for t in tokens if t not in _STOP_WORDS]
    # If a term is *entirely* stop-words, keep them rather than producing an empty key — an empty
    # key would fold every such term together into one meaningless query.
    return " ".join(sorted(kept or tokens))


def bucket_age_hours(hours: int) -> int:
    """Round **up** to the coarsest bucket, so demand merges rather than fragmenting.

    Up, not down: rounding down would fetch a narrower window than a user asked for and silently
    lose postings they wanted. Over-fetching is corrected by a local filter; under-fetching is
    invisible.
    """
    for bucket in AGE_BUCKETS_HOURS:
        if hours <= bucket:
            return bucket
    return AGE_BUCKETS_HOURS[-1]


def build_query_set(
    user_configs: Mapping[UUID, Mapping],
    sources: Iterable[str],
) -> dict[QueryKey, frozenset[UUID]]:
    """Fold every active user's config into `{QueryKey: users who want it}`.

    Each user contributes at most `HARVEST_MAX_QUERIES_PER_USER` keywords — the same hard cap for
    everyone (`TENANT_SAFETY.md` §3). The cap is applied to *their keywords*, before the cross
    product with locations and sources, because that is the number the user chose and the one the
    form shows them.

    A config with no locations still produces queries: `location_norm=""` means "wherever", which is
    what the remote-only case actually wants.
    """
    sources = list(sources)

    # Age is deliberately NOT part of the fold key. It is resolved per group afterwards, as the
    # coarsest window any requesting user asked for — so a 24h user and a 48h user share one 72h
    # fetch and the 24h user filters down locally. Folding on the bucket instead would split them
    # into two fetches whose results are strict subsets of one another, which is precisely the
    # duplicate work the budget exists to avoid (HARVESTER_PLAN.md §2).
    demand: dict[tuple[str, str, str, bool], set[UUID]] = defaultdict(set)
    widest_age: dict[tuple[str, str, str, bool], int] = {}

    for user_id, config in user_configs.items():
        keywords = list(config.get("keywords") or [])[:HARVEST_MAX_QUERIES_PER_USER]
        locations = list(config.get("locations") or []) or [""]
        remote_policy = config.get("remote_policy") or "remote_or_hybrid"
        age = bucket_age_hours(int(config.get("posted_within_hours") or 72))

        for keyword in keywords:
            keyword_norm = normalise_keyword(keyword)
            if not keyword_norm:
                continue
            for location in locations:
                location_norm = str(location or "").strip().lower()
                # `remote_only` asks the source to filter server-side; the other policies do not,
                # because a source's "remote" flag is unreliable enough that excluding on it would
                # lose real hybrid roles. Those are filtered locally instead.
                is_remote = remote_policy == "remote_only" or location_norm.startswith("remote")
                for source in sources:
                    group = (source, keyword_norm, location_norm, is_remote)
                    demand[group].add(user_id)
                    widest_age[group] = max(widest_age.get(group, 0), age)

    return {
        QueryKey(*group, widest_age[group]): frozenset(users)
        for group, users in demand.items()
    }


def select_within_budget(
    query_set: Mapping[QueryKey, frozenset[UUID]],
    *,
    max_queries: int = HARVEST_MAX_QUERIES_PER_CYCLE,
    carried_over: Iterable[QueryKey] = (),
) -> tuple[list[PlannedQuery], list[PlannedQuery]]:
    """Choose this cycle's queries. Returns `(selected, deferred)`.

    **Round-robin over users, not list order.** Every user gets their first query served before any
    user gets their second, so a user with five niche terms cannot crowd out four other users'
    primary term. Ordering by demand instead would be worse in the same way — it would serve popular
    terms repeatedly and starve anyone with an unusual search, which is precisely the user for whom
    this product is most valuable.

    `carried_over` is last cycle's deferred keys; they are served first this time, so a query
    unlucky enough to fall outside the budget does not stay unlucky forever.

    Deterministic tie-breaking throughout — an unstable order would make the same inputs produce
    different harvests and make any bug here unreproducible.
    """
    if max_queries <= 0:
        return [], [PlannedQuery(k, v) for k, v in _stable(query_set)]

    remaining = dict(query_set)
    selected: list[PlannedQuery] = []

    # Carried-over queries first, if anyone still wants them.
    for key in carried_over:
        if len(selected) >= max_queries:
            break
        users = remaining.pop(key, None)
        if users:
            selected.append(PlannedQuery(key, users))

    # Per-user queues, each in a stable order, drained one round at a time.
    per_user: dict[UUID, list[QueryKey]] = defaultdict(list)
    for key, users in _stable(remaining):
        for user_id in sorted(users):
            per_user[user_id].append(key)

    taken: set[QueryKey] = set()
    round_index = 0
    while len(selected) < max_queries:
        progressed = False
        for user_id in sorted(per_user):
            queue = per_user[user_id]
            if round_index >= len(queue):
                continue
            progressed = True
            key = queue[round_index]
            if key in taken:
                continue
            taken.add(key)
            selected.append(PlannedQuery(key, remaining[key]))
            if len(selected) >= max_queries:
                break
        if not progressed:
            break
        round_index += 1

    deferred = [PlannedQuery(k, v) for k, v in _stable(remaining) if k not in taken]
    return selected, deferred


def _stable(query_set: Mapping[QueryKey, frozenset[UUID]]) -> list[tuple[QueryKey, frozenset[UUID]]]:
    """Sort by key so selection is reproducible for identical input."""
    return sorted(query_set.items(), key=lambda item: item[0])
