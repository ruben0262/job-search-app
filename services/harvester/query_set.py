"""Query-set construction and the fan-out budget."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID

from libs.contracts.config import MAX_KEYWORDS_PER_USER, migrate_config
from libs.harvest.keywords import normalise_keyword
from libs.settings import DEFAULT_QUERIES_PER_CYCLE

# Imported, not redefined.
HARVEST_MAX_QUERIES_PER_USER = MAX_KEYWORDS_PER_USER

# What "Unlimited" means to a slice: every keyword the account has.
_UNBOUNDED = 10_000

# Coarse buckets, so a user wanting 24h shares the 72h fetch and filters down locally.
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
    """A query selected for this cycle, with the users who wanted it."""

    key: QueryKey
    user_ids: frozenset[UUID]

    @property
    def demand(self) -> int:
        return len(self.user_ids)




def original_terms(user_configs: Mapping[UUID, Mapping]) -> dict[str, str]:
    """`normalised keyword -> the phrase a board should actually be asked for`."""
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
    """Round up to the coarsest bucket, so demand merges rather than fragmenting."""
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
    budgets: Mapping[UUID, int | None] | None = None,
) -> dict[QueryKey, frozenset[UUID]]:
    """Fold every active user's config into `{QueryKey: users who want it}`."""
    sources = list(sources)
    # `None` means "every source honours this".
    locates = set(sources) if locating_sources is None else set(locating_sources)
    remote_aware = set(sources) if remote_aware_sources is None else set(remote_aware_sources)

    # Age is deliberately NOT part of the fold key.
    demand: dict[tuple[str, str, str, bool], set[UUID]] = defaultdict(set)
    widest_age: dict[tuple[str, str, str, bool], int] = {}

    for user_id, config in user_configs.items():
        # Read through `migrate_config`, so a stored v1 row folds exactly as it did before.
        sections = migrate_config(dict(config)).get("sections") or []
        # The account's own ceiling, resolved by the caller from `libs.limits`. The constant is the
        # fallback for a caller with no session, not the rule: an operator who raised an account to
        # 100 search terms watched it keep harvesting 25, because this line read a module constant
        # and the panel read the catalogue. `None` is Unlimited and must not collapse to the
        # constant — that is how "Unlimited" silently became 25.
        allowed = (budgets or {}).get(user_id, HARVEST_MAX_QUERIES_PER_USER)
        budget = _UNBOUNDED if allowed is None else int(allowed)

        for section in sections:
            if budget <= 0:
                break
            # The cap spans the user's sections rather than each one: five sections buy no extra fan-out.
            keywords = list(section.get("keywords") or [])[:budget]
            budget -= len(keywords)

            locations = list(section.get("locations") or []) or [""]
            remote_policy = section.get("remote_policy") or "remote_or_hybrid"
            age = bucket_age_hours(int(section.get("posted_within_hours") or 24))

            # A section naming a platform emits queries for that source only; `all` emits for every source.
            platform = section.get("platform") or "all"
            section_sources = sources if platform == "all" else [s for s in sources if s == platform]

            for keyword in keywords:
                keyword_norm = normalise_keyword(keyword)
                if not keyword_norm:
                    continue
                for location in locations:
                    location_norm = str(location or "").strip().lower()
                    # `remote_only` filters server-side; the others do not, the flag being unreliable.
                    is_remote = remote_policy == "remote_only" or location_norm.startswith("remote")
                    for source in section_sources:
                        # Per axis, per source: a board with no geography is asked once per keyword.
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
    """Choose this cycle's queries, per source."""
    limits = limits or {}
    ranked = list(carried_over)
    rank = {key: index for index, key in enumerate(ranked)}

    # Carried-over first, everything else in key order behind it.
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
