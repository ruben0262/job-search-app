"""Query-set folding and the fan-out budget.

Pure logic, no database, no network — these run anywhere. That is deliberate: this module decides
how much scraping the platform does, and a decision that consequential should be provable without
standing anything up.

Two properties carry the weight:

- **Folding** is what turns 50 users × 5 terms into something a 15-minute cycle can execute. If it
  under-folds, the budget is spent on duplicate work; if it over-folds, users silently stop getting
  results they asked for.
- **Round-robin selection** is what stops one user with five niche terms crowding out four other
  users' primary term. Ordering by demand instead would starve exactly the user with an unusual
  search — the one for whom this product is most valuable.
"""
from __future__ import annotations

import uuid

from services.harvester.query_set import (
    AGE_BUCKETS_HOURS,
    HARVEST_MAX_QUERIES_PER_USER,
    QueryKey,
    bucket_age_hours,
    build_query_set,
    normalise_keyword,
    select_within_budget,
)

SOURCES = ["linkedin", "indeed"]


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _config(keywords, locations=(), posted_within_hours=72, remote_policy="remote_or_hybrid"):
    return {"keywords": list(keywords), "locations": list(locations),
            "posted_within_hours": posted_within_hours, "remote_policy": remote_policy}


# ── normalisation ─────────────────────────────────────────────────────────────────────────────

def test_word_order_and_spacing_do_not_make_a_new_query():
    """The fold's whole value. Three spellings of one search must cost one fetch, not three."""
    assert (normalise_keyword("Python Developer")
            == normalise_keyword("python  developer")
            == normalise_keyword("developer python")
            == "developer python")


def test_stop_words_are_stripped():
    assert normalise_keyword("Head of Engineering") == normalise_keyword("head engineering")


def test_a_term_that_is_entirely_stop_words_still_produces_a_key():
    """Otherwise every such term folds into one empty key and they all fetch the same thing."""
    assert normalise_keyword("the and of") != ""


def test_plus_and_hash_survive_because_they_are_real_search_terms():
    """`c++` and `c#` must not both collapse into `c`. They are different jobs."""
    assert normalise_keyword("C++ Engineer") == "c++ engineer"
    assert normalise_keyword("C# Developer") == "c# developer"
    assert normalise_keyword("C++ Engineer") != normalise_keyword("C# Engineer")


def test_age_rounds_up_never_down():
    """Rounding down would fetch a narrower window than a user asked for and lose postings silently.

    Over-fetching is corrected by a local filter. Under-fetching is invisible.
    """
    assert bucket_age_hours(6) == 24
    assert bucket_age_hours(24) == 24
    assert bucket_age_hours(25) == 72
    assert bucket_age_hours(72) == 72
    assert bucket_age_hours(100) == 168
    assert bucket_age_hours(9999) == AGE_BUCKETS_HOURS[-1]


# ── folding ───────────────────────────────────────────────────────────────────────────────────

def test_two_users_wanting_the_same_thing_produce_one_query():
    alice, bob = _uid(), _uid()
    qs = build_query_set(
        {alice: _config(["Python Developer"], ["london"]),
         bob: _config(["developer  python"], ["london"])},
        SOURCES)

    assert len(qs) == len(SOURCES), "one query per source, not per user"
    for users in qs.values():
        assert users == frozenset({alice, bob}), "both users attributed to the shared fetch"


def test_users_wanting_different_age_windows_share_the_coarser_fetch():
    """A 24h user and a 72h user share the 72h fetch; the 24h user filters down locally."""
    alice, bob = _uid(), _uid()
    qs = build_query_set(
        {alice: _config(["python"], ["london"], posted_within_hours=24),
         bob: _config(["python"], ["london"], posted_within_hours=48)},
        ["linkedin"])
    assert len(qs) == 1, "one fetch, not one per requested window"
    assert next(iter(qs)).max_age_hours == 72, "the coarser window wins; the 24h user filters locally"
    assert next(iter(qs.values())) == frozenset({alice, bob})


def test_the_same_term_in_two_cities_is_two_queries():
    """Location is part of the key — these are genuinely different fetches."""
    qs = build_query_set({_uid(): _config(["python"], ["london", "berlin"])}, ["linkedin"])
    assert len(qs) == 2
    assert {k.location_norm for k in qs} == {"london", "berlin"}


def test_a_user_with_no_location_still_gets_queries():
    """Empty location means "wherever", which is what remote-only actually wants."""
    qs = build_query_set({_uid(): _config(["python"], [])}, ["linkedin"])
    assert len(qs) == 1
    assert next(iter(qs)).location_norm == ""


def test_the_per_user_keyword_cap_is_enforced_at_the_fold():
    """The cap is on the user's keywords — the number the form shows them — not on the cross product."""
    user = _uid()
    qs = build_query_set(
        {user: _config([f"term{i}" for i in range(20)], ["london"])}, ["linkedin"])
    assert len(qs) == HARVEST_MAX_QUERIES_PER_USER


def test_remote_only_marks_the_query_remote():
    qs = build_query_set(
        {_uid(): _config(["python"], ["london"], remote_policy="remote_only")}, ["linkedin"])
    assert next(iter(qs)).is_remote is True

    qs = build_query_set(
        {_uid(): _config(["python"], ["london"], remote_policy="any")}, ["linkedin"])
    assert next(iter(qs)).is_remote is False


# ── budget and selection ──────────────────────────────────────────────────────────────────────

def test_a_small_query_set_is_served_whole():
    qs = build_query_set({_uid(): _config(["python"], ["london"])}, SOURCES)
    selected, deferred = select_within_budget(qs, max_queries=60)
    assert len(selected) == len(qs)
    assert deferred == []


def test_one_greedy_user_cannot_crowd_out_everyone_elses_first_term():
    """**The point of round-robin.**

    A user with five niche terms and four users with one term each: with only 4 slots, ordering by
    list or by demand would spend them all on the greedy user. Round-robin gives every user their
    first query before anyone gets a second.
    """
    greedy = _uid()
    others = [_uid() for _ in range(4)]
    configs = {greedy: _config([f"niche{i}" for i in range(5)], ["london"])}
    for i, other in enumerate(others):
        configs[other] = _config([f"common{i}"], ["london"])

    qs = build_query_set(configs, ["linkedin"])
    selected, deferred = select_within_budget(qs, max_queries=4)

    assert len(selected) == 4
    # The property that matters: four slots went to four DIFFERENT users, not to four of the
    # greedy user's terms. List order or demand order would have produced the latter.
    served_users = {next(iter(q.user_ids)) for q in selected}
    assert len(served_users) == 4, f"slots went to {len(served_users)} distinct users, want 4"
    assert deferred, "the greedy user's remaining terms are deferred, not dropped"

    # And with room for everyone, everyone is served.
    selected_all, _ = select_within_budget(qs, max_queries=5)
    assert {next(iter(q.user_ids)) for q in selected_all} == {greedy, *others}


def test_deferred_queries_are_served_first_next_cycle():
    """Otherwise an unlucky query stays unlucky forever and that user is permanently under-served."""
    # Distinct terms per user, or folding collapses them and there is nothing to defer.
    configs = {_uid(): _config([f"u{u}term{i}" for i in range(5)], ["london"]) for u in range(3)}
    qs = build_query_set(configs, ["linkedin"])

    first_selected, first_deferred = select_within_budget(qs, max_queries=5)
    assert first_deferred

    carried = [q.key for q in first_deferred]
    second_selected, _ = select_within_budget(qs, max_queries=5, carried_over=carried)

    assert [q.key for q in second_selected] == carried[:5], "deferred keys go first next cycle"
    assert first_selected, "the first cycle still served a full budget"


def test_selection_is_deterministic():
    """Same input, same harvest. An unstable order makes any bug here unreproducible."""
    configs = {_uid(): _config([f"term{i}" for i in range(4)], ["london", "berlin"])
               for _ in range(5)}
    qs = build_query_set(configs, SOURCES)

    first = [q.key for q in select_within_budget(qs, max_queries=17)[0]]
    second = [q.key for q in select_within_budget(qs, max_queries=17)[0]]
    assert first == second


def test_nothing_is_selected_twice():
    configs = {_uid(): _config(["python", "golang"], ["london"]) for _ in range(6)}
    qs = build_query_set(configs, SOURCES)
    selected, deferred = select_within_budget(qs, max_queries=3)

    keys = [q.key for q in selected]
    assert len(keys) == len(set(keys))
    assert not (set(keys) & {q.key for q in deferred}), "a query is selected or deferred, never both"


def test_a_zero_budget_defers_everything_rather_than_silently_scraping():
    """A kill-switch-shaped case. It must stop work, not fail open into an unbounded harvest."""
    qs = build_query_set({_uid(): _config(["python"], ["london"])}, SOURCES)
    selected, deferred = select_within_budget(qs, max_queries=0)
    assert selected == []
    assert len(deferred) == len(qs)


def test_the_budget_is_never_exceeded():
    """The whole reason this function exists: 50 users × 5 terms × 3 sources = 750 queries a cycle."""
    # Distinct terms per user: this is the unbounded worst case the budget exists for.
    configs = {_uid(): _config([f"u{u}term{i}" for i in range(5)], ["london", "berlin"])
               for u in range(50)}
    qs = build_query_set(configs, ["linkedin", "indeed", "google"])
    assert len(qs) > 60, "the unbounded set really is larger than the budget"

    selected, deferred = select_within_budget(qs, max_queries=60)
    assert len(selected) == 60
    assert len(selected) + len(deferred) == len(qs), "nothing is lost, only postponed"


def test_attribution_survives_selection():
    """User sets exist for quota accounting; results still go to the shared corpus regardless."""
    alice, bob = _uid(), _uid()
    qs = build_query_set(
        {alice: _config(["python"], ["london"]), bob: _config(["python"], ["london"])},
        ["linkedin"])
    selected, _ = select_within_budget(qs, max_queries=10)
    assert selected[0].user_ids == frozenset({alice, bob})
    assert selected[0].demand == 2


def test_query_keys_are_hashable_and_comparable():
    """They are dict keys and they get sorted for deterministic selection."""
    a = QueryKey("linkedin", "python", "london", False, 72)
    b = QueryKey("linkedin", "python", "london", False, 72)
    assert a == b and hash(a) == hash(b)
    assert sorted([QueryKey("z", "a", "", False, 24), a])[0].source == "linkedin"
