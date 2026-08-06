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

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID

from libs.contracts.config import MAX_KEYWORDS_PER_USER, migrate_config
from libs.harvest.keywords import normalise_keyword
from libs.settings import DEFAULT_QUERIES_PER_CYCLE

# Imported, not redefined. The same cap bounds the config form, and a second copy here is how the
# two drift: raise one and the other silently accepts terms it will never serve.
HARVEST_MAX_QUERIES_PER_USER = MAX_KEYWORDS_PER_USER

# Coarse buckets, so a user wanting 24h and one wanting 72h share the 72h fetch and the 24h user
# filters down locally. Fetching each exact hour figure separately would multiply the query set for
# results that are strict subsets of one another.
AGE_BUCKETS_HOURS = (24, 72, 168)



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




def original_terms(user_configs: Mapping[UUID, Mapping]) -> dict[str, str]:
    """`normalised keyword -> the phrase a board should actually be asked for`.

    The fold key answers "did two users ask for the same thing"; this answers "what do we type into
    the search box", and they are not the same string. Sorting tokens and stripping accents is right
    for the first and destructive for the second — `stress engineer` becomes `engineer stress`, and
    a Hungarian term loses its ending and stops matching anything.

    Ties are broken by taking the alphabetically first original, so a cycle is reproducible: several
    users phrasing one search differently must not make the emitted term depend on which of them the
    database returned first.
    """
    best: dict[str, str] = {}
    for stored in user_configs.values():
        config = migrate_config(stored or {})
        for section in config.get("sections") or []:
            for keyword in (section or {}).get("keywords") or []:
                term = str(keyword or "").strip()
                key = normalise_keyword(term)
                if not key:
                    continue
                if key not in best or term.lower() < best[key].lower():
                    best[key] = term
    return best


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
    *,
    locating_sources: Iterable[str] | None = None,
    remote_aware_sources: Iterable[str] | None = None,
) -> dict[QueryKey, frozenset[UUID]]:
    """Fold every active user's config into `{QueryKey: users who want it}`.

    Each user contributes at most `HARVEST_MAX_QUERIES_PER_USER` keywords **across all their
    sections** — the same hard cap for everyone (`TENANT_SAFETY.md` §3). Applied to their keywords,
    before the cross product with locations and sources, because that is the number the user chose
    and the one the form shows them.

    A section naming a platform contributes queries for that source only
    (`ONBOARDING_AND_PROCESSING.md` §3); a section with `platform: all` — which is what most users
    have and never think about — contributes to every source the cycle is running.

    A config with no locations still produces queries: `location_norm=""` means "wherever", which is
    what the remote-only case actually wants.

    **`locating_sources` is what stops the query set being mostly duplicates.** Most boards take a
    search term and nothing else — no country, no city, no region — so crossing their keywords with
    four location slugs produces four *byte-identical* requests, spends four times the budget, and
    returns one set of results. A source not named there is asked once per keyword.

    The two axes are separate because sources are: Arbeitnow has a remote flag and no geography at
    all, so collapsing both together would either send it four identical searches or lose the one
    filter it does support. Omit both arguments and every source is assumed to honour everything,
    which is the old behaviour and what the per-user LinkedIn pass wants.

    **Collapsing an axis is not a narrowing.** The user still asked for those locations; the source
    was never going to apply them, and the local filters do that work either way.
    """
    sources = list(sources)
    # `None` means "every source honours this". An empty iterable means "none does", and the two are
    # very different answers — a falsy check here would silently collapse every axis.
    locates = set(sources) if locating_sources is None else set(locating_sources)
    remote_aware = set(sources) if remote_aware_sources is None else set(remote_aware_sources)

    # Age is deliberately NOT part of the fold key. It is resolved per group afterwards, as the
    # coarsest window any requesting user asked for — so a 24h user and a 48h user share one 72h
    # fetch and the 24h user filters down locally. Folding on the bucket instead would split them
    # into two fetches whose results are strict subsets of one another, which is precisely the
    # duplicate work the budget exists to avoid (HARVESTER_PLAN.md §2).
    demand: dict[tuple[str, str, str, bool], set[UUID]] = defaultdict(set)
    widest_age: dict[tuple[str, str, str, bool], int] = {}

    for user_id, config in user_configs.items():
        # Read through `migrate_config`, so a stored v1 row (one flat keyword list) folds exactly as
        # it did before — as a single `all` section. A user's config is not rewritten by a deploy.
        sections = migrate_config(dict(config)).get("sections") or []
        budget = HARVEST_MAX_QUERIES_PER_USER

        for section in sections:
            if budget <= 0:
                break
            # The cap spans the user's sections rather than each one, matching the schema: five
            # sections must not buy five times the fan-out budget.
            keywords = list(section.get("keywords") or [])[:budget]
            budget -= len(keywords)

            locations = list(section.get("locations") or []) or [""]
            remote_policy = section.get("remote_policy") or "remote_or_hybrid"
            age = bucket_age_hours(int(section.get("posted_within_hours") or 24))

            # A section naming a platform emits queries for that source only; `all` emits for every
            # source the cycle is running. Intersected rather than trusted, so a section naming a
            # source this cycle is not running produces nothing instead of an invalid query.
            platform = section.get("platform") or "all"
            section_sources = sources if platform == "all" else [s for s in sources if s == platform]

            for keyword in keywords:
                keyword_norm = normalise_keyword(keyword)
                if not keyword_norm:
                    continue
                for location in locations:
                    location_norm = str(location or "").strip().lower()
                    # `remote_only` asks the source to filter server-side; the other policies do
                    # not, because a source's "remote" flag is unreliable enough that excluding on
                    # it would lose real hybrid roles. Those are filtered locally instead.
                    is_remote = remote_policy == "remote_only" or location_norm.startswith("remote")
                    for source in section_sources:
                        # Per axis, per source: a board with no geography is asked once per keyword,
                        # and one with a remote flag and no geography keeps the flag.
                        group = (source,
                                 keyword_norm,
                                 location_norm if source in locates else "",
                                 is_remote and source in remote_aware)
                        demand[group].add(user_id)
                        widest_age[group] = max(widest_age.get(group, 0), age)

    return {
        QueryKey(*group, widest_age[group]): frozenset(users)
        for group, users in demand.items()
    }


def select_within_budget(
    query_set: Mapping[QueryKey, frozenset[UUID]],
    *,
    limits: Mapping[str, int] | None = None,
    carried_over: Iterable[QueryKey] = (),
) -> tuple[list[PlannedQuery], list[PlannedQuery]]:
    """Choose this cycle's queries, **per source**. Returns `(selected, deferred)`.

    **One budget for every source was the wrong instrument** and it is gone. That number was sized
    for the JobSpy tier's ban risk and ended up governing unproxied REST boards, where it meant
    nothing except that they queued behind each other: sources drained one at a time, so a board
    could go seventy-five minutes without being asked anything while another was finished.
    Remotive being starved has nothing to do with HiringCafe being expensive, and one cap made them
    compete for a resource they do not share. Each source now has its own, set by the operator.

    `carried_over` is last cycle's deferred keys, served first this time, so a query unlucky enough
    to fall outside its source's budget does not stay unlucky.

    Stable order throughout — the same inputs must produce the same harvest, or a bug here is not
    reproducible.
    """
    limits = limits or {}
    ranked = list(carried_over)
    rank = {key: index for index, key in enumerate(ranked)}

    # Carried-over first, everything else in key order behind it. One sort, and `_stable` keeps the
    # order inside each group fixed.
    ordered = sorted(_stable(query_set), key=lambda item: rank.get(item[0], len(ranked)))

    used: dict[str, int] = defaultdict(int)
    selected: list[PlannedQuery] = []
    deferred: list[PlannedQuery] = []
    for key, users in ordered:
        cap = limits.get(key.source, DEFAULT_QUERIES_PER_CYCLE)
        if used[key.source] < cap:
            used[key.source] += 1
            selected.append(PlannedQuery(key, users))
        else:
            deferred.append(PlannedQuery(key, users))
    return selected, deferred


def _stable(query_set: Mapping[QueryKey, frozenset[UUID]]) -> list[tuple[QueryKey, frozenset[UUID]]]:
    """Sort by key so selection is reproducible for identical input."""
    return sorted(query_set.items(), key=lambda item: item[0])
