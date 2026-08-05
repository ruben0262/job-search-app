"""Settings the operator owns, and the defaults they start from.

**These are properties of the product, not preferences.** The app streams the newest postings, so
how fresh a posting has to be and how often the feed is sent are decisions about what the thing
*is* — and a user who could set "every four hours" was asking to be told about a two-hour-old job.

Two consequences worth stating, because they are why this file exists rather than a config column:

- **One window per source means one filter to pass each site.** Freshness used to be per user, so
  every source was fetched at the widest window anybody asked for and filtered back down per person
  at delivery. With one operator-set number per source there is nothing to reconcile.
- **Per source, not global**, because sites differ in how fast they list and how far back their
  filters reach. LinkedIn surfaces a posting within minutes; a small board may take hours, and
  giving it a four-hour window would mean never seeing its jobs at all.

Read through `get`, which falls back to the default for any key an operator has not set — so a fresh
database behaves exactly like a configured one, and adding a source does not require a migration.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import PlatformSetting

# How often every user's feed is sent. **Fixed, and the same for everyone.** The smallest useful
# window: the product's whole claim is that a posting reaches you while it is still worth applying
# to, and a longer cadence spends that lead time doing nothing.
DELIVERY_INTERVAL_MIN = 15

# **How often the platform's one-shots run, and the only schedule number in the tree.**
#
# Harvest, the LinkedIn tier and the health probe all tick at this rate; the systemd units carry the
# same figure because systemd cannot read Python, and `tests/test_schedules.py` fails if they ever
# disagree. That test is the mechanism — the numbers were identical by hand and one of them was
# still wrong for weeks (ROADMAP 6ap).
#
# The invariant that matters more than the value: **a one-shot must finish inside its own tick.**
# `linkedin.service` carried `TimeoutStartSec=30min` against a 15-minute timer, so a stuck pass
# blocked two whole ticks before systemd would even kill it — which is exactly how LinkedIn ingest
# stopped for five hours.
PLATFORM_TICK_MIN = 15

# How fresh a posting must be, per source, in hours. The operator tunes these; the defaults are the
# starting point rather than a ceiling.
#
# LinkedIn is tighter than the boards on purpose: it lists within minutes and carries the most
# volume, so a wide window there is mostly re-reading jobs already seen. The aggregators are slower
# to publish, and a window as tight as LinkedIn's would mean their postings never qualified.
#
# **The ATS family needs a wide window and that is not slack.** Those boards belong to a company,
# and the platform discovers a posting whenever its harvested token list reaches that company —
# which has nothing to do with when the job went up. Measured over three days of real ingestion, a
# uniform 6-hour window would have kept:
#
#     arbeitnow  99.9%    himalayas  99.7%    hiringcafe  53%    nofluffjobs  59%
#     greenhouse   41%    smartrecruiters 41%    ashby  34%
#     recruitee     4%    workday      2%
#
# So one number is not a simplification here, it is a 96% loss on two sources. The aggregators
# publish a feed of what is new and can be read tightly; the ATS boards cannot.
DEFAULT_SOURCE_FRESHNESS_HOURS: dict[str, int] = {
    # Per-user, proxied, lists within minutes and carries the most volume — a wide window is mostly
    # re-reading jobs already seen.
    "linkedin": 4,
    "indeed": 6,
    "google": 6,
    # Feed-shaped aggregators: what they publish is what is new.
    "himalayas": 12,
    "justjoinit": 12,
    "workingnomads": 24,
    "jobicy": 24,
    "themuse": 24,
    # Search-shaped boards, relevance-sorted rather than date-sorted.
    "hiringcafe": 48,
    "nofluffjobs": 48,
    "profession": 24,
    # Company boards, reached when our token list gets there.
    "greenhouse": 72, "ashby": 72, "lever": 72, "smartrecruiters": 72,
    "recruitee": 168, "breezy": 72, "workday": 168, "personio": 72, "workable": 72,
}

# Anything not named above. Wide enough that a slow board still contributes, narrow enough that the
# feed stays a stream of new postings rather than a backlog.
DEFAULT_FRESHNESS_HOURS = 6

# Every source the harvester collects from. **Here rather than in the harvester** because both the
# harvester and the admin panel need it, and a service may not import another service — the panel
# reading the harvester's list directly is exactly the coupling the boundary contract forbids.
# `services/harvester/source_config.py` imports this as its enabled set.
# **Narrowed 2026-08-04 to sources that answer a keyword.** Seven were pinged from the production
# box with a real term, an unrelated term and nonsense, answered identically to all three, and were
# removed (`services/harvester/source_config.DEFERRED_SOURCES`, ROADMAP 6t). This list is what the
# admin panel offers a freshness control for, so a source that is not harvested must not appear.
HARVEST_SOURCES: tuple[str, ...] = (
    # Remote-work aggregators.
    "himalayas", "workingnomads", "jobicy",
    # ATS boards, polled directly per company.
    "greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
    "breezy", "workday", "personio", "workable",
    # Aggregators and VC portfolio boards.
    "hiringcafe", "themuse",
    # European boards.
    "nofluffjobs", "profession", "justjoinit",
    # Key-gated, enabled only with a credential.
    "jooble", "findwork", "adzuna",
    # The per-user tier, on the user's own proxy. JobSpy, so a real keyword search — and the same
    # adapter reaches Indeed and Google, which is the cheapest way to widen coverage after beta.
    "linkedin",
)

# The sources that take a keyword query, as opposed to serving a feed. **Here for the same reason as
# `HARVEST_SOURCES`**: the admin panel has to say how many queries a source has, and a service may
# not import another service. `services/harvester/source_config.py` imports this rather than keeping
# its own copy, because two lists of source names is two lists that drift.
QUERY_TAKING_SOURCES: frozenset[str] = frozenset(
    {"himalayas", "hiringcafe", "nofluffjobs", "jooble"})

# How many queries a source may be asked in one 15-minute cycle, before the operator says otherwise.
#
# **Per source, because rate limits are.** The single number this replaced was sized for the JobSpy
# tier's ban risk and ended up governing unproxied REST boards, which then queued behind each other:
# one board could go seventy-five minutes without being asked anything while another finished.
#
# Set from what each board tolerated when it was measured. Arbeitnow answered 13 of 33 burst queries
# with something that was not JSON — that is what a ceiling set too high looks like, and it is the
# reason this is a control rather than a constant.
DEFAULT_SOURCE_QUERIES_PER_CYCLE: dict[str, int] = {
    # **Per user, and it was the tightest ceiling on the platform against the widest demand.** 12
    # against an account with 23 keywords, which then crossed them with 4 locations — so LinkedIn
    # was asked for the alphabetically first three terms and never saw the other twenty, on the
    # source that produced 337 of the founder's 348 jobs at or above the delivery cutoff.
    #
    # **Sized to cover keyword × location rather than to truncate it.** LinkedIn keeps the location
    # axis (unlike the shared cycle) because it is per user, runs on that user's own proxy, and does
    # geo search properly -- so a place the board filters is bandwidth never spent. The founder's
    # account is 23 keywords × 3 locations = 69, and a ceiling below that silently drops whole
    # search terms rather than trimming evenly: it takes them in key order, so the last locations
    # simply never run.
    "linkedin": 150,
    # Search-shaped boards on a shared client. Comfortably above the 28 distinct keywords the
    # platform currently has, so nothing defers and every source is served every cycle.
    "himalayas": 100,
    "hiringcafe": 100,
    "nofluffjobs": 100,
    "jooble": 50,
}

# A source nobody has tuned. Matches `services.harvester.query_set.DEFAULT_QUERIES_PER_CYCLE`; the
# test in `tests/test_schema.py` pins the two together so they cannot drift apart silently.
DEFAULT_QUERIES_PER_CYCLE = 40


# How many results one query may bring back, per source. **Only the sources that actually take a
# result count**: TheMuse and Profession take a *page* count and Working Nomads returns its whole
# list, so a number here would be a lever attached to nothing — the same reason a feed source shows
# no query ceiling.
#
# This is the other half of the query budget. Queries decide how many times we ask; this decides how
# deep each ask reaches, and a board's own ceiling is usually the binding one — Getro returns 20 a
# page, HiringCafe 40. Raising it past what the board serves changes nothing.
DEFAULT_SOURCE_RESULTS_PER_QUERY: dict[str, int] = {
    "himalayas": 100,
    "justjoinit": 100,
    "nofluffjobs": 100,
    "jobicy": 50,
    "hiringcafe": 40,
    "linkedin": 30,
}

# How many company boards the ATS family polls in one cycle.
#
# **The token list grows on its own** from harvested apply-URLs, so this is the number that decides
# whether a cycle reaches all of them or a fifth of them — and an operator setting it needs to know
# how many we hold, which is why `/admin/sources` reports the total beside the control.
DEFAULT_ATS_COMPANIES_PER_CYCLE = 400

# How many postings the shared cycle will fetch descriptions for in one pass.
#
# A real throughput control rather than plumbing: a row with no description cannot be embedded, so
# it cannot be scored or delivered, and this number decides how fast a cold-start backlog clears.
# Bounded because each fetch is an HTTP request with retries.
DEFAULT_DESCRIBE_PER_CYCLE = 200

# How deep a paginated source reads in one cycle, per source.
#
# **The third half of the query budget, and the only one that was invisible.** `queries_per_cycle`
# decides how many times we ask, `results_per_query` how much one ask returns — and for the sources
# that paginate, this decides how many pages we walk. Both numbers here were constants inside their
# adapters (`hiringcafe._MAX_PAGES`, `ats._WD_PAGES`), which meant HiringCafe was capped at 3 × 40 =
# 120 rows a cycle by a value nobody could see and no panel reported (ROADMAP 6ag).
#
# Platform-wide rather than per user: a page is a request to a board, and how hard we lean on
# somebody else's server is a platform decision, not one account's preference. Contrast the
# applicant cap, which is a judgement about one person's odds and is therefore per user.
#
# A source absent from this map does not paginate, and adding a key here does nothing on its own —
# the adapter has to read it. That is the trap this project keeps hitting, so the test that pins
# every key to a consumer is part of the change rather than a follow-up.
DEFAULT_SOURCE_PAGES_PER_CYCLE: dict[str, int] = {
    # Relevance-sorted, so the top pages carry the matches and depth has falling returns. It is
    # also proxied, which is why this one is worth an operator's attention.
    "hiringcafe": 3,
    # Newest-first at 20 a page, and the walk stops early once a page is older than the freshness
    # window — so this is a ceiling rather than a fixed cost.
    "workday": 5,
}

# How many users' LinkedIn passes run in one tick. **Not a per-user limit** — per-user proxies give
# IP diversity, which handles one account's ban risk; this is the platform not issuing hundreds of
# LinkedIn requests at once, and it is also the `linkedin` lane's worker count.
#
# At four users a tick and a fifteen-minute timer, fifty accounts take over three hours for one
# rotation. That is the scaling wall on this tier, and it is a number an operator should be able to
# move rather than a constant they have to find.
DEFAULT_LINKEDIN_CONCURRENT_USERS = 4


DEFAULTS: dict[str, object] = {
    "delivery_interval_min": DELIVERY_INTERVAL_MIN,
    "source_freshness_hours": DEFAULT_SOURCE_FRESHNESS_HOURS,
    "default_freshness_hours": DEFAULT_FRESHNESS_HOURS,
    "source_queries_per_cycle": DEFAULT_SOURCE_QUERIES_PER_CYCLE,
    "source_results_per_query": DEFAULT_SOURCE_RESULTS_PER_QUERY,
    "source_pages_per_cycle": DEFAULT_SOURCE_PAGES_PER_CYCLE,
    "ats_companies_per_cycle": DEFAULT_ATS_COMPANIES_PER_CYCLE,
    "describe_per_cycle": DEFAULT_DESCRIBE_PER_CYCLE,
    "linkedin_concurrent_users": DEFAULT_LINKEDIN_CONCURRENT_USERS,
}


def get(session: Session, key: str) -> object:
    """One setting, or its default. Unknown keys raise rather than returning `None`.

    A typo that silently reads as "unset" is a setting that quietly stops applying, which is the
    failure this module exists to make impossible.
    """
    if key not in DEFAULTS:
        raise KeyError(f"unknown platform setting {key!r}; known: {sorted(DEFAULTS)}")
    stored = session.scalar(select(PlatformSetting.value).where(PlatformSetting.key == key))
    return DEFAULTS[key] if stored is None else stored


def all_settings(session: Session) -> dict[str, object]:
    """Every setting with its current value — what the admin panel renders."""
    stored = dict(session.execute(
        select(PlatformSetting.key, PlatformSetting.value)).all())
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def put(session: Session, key: str, value: object, *, by: str | None = None) -> None:
    """Set one setting. Does not commit — the caller owns the transaction."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown platform setting {key!r}; known: {sorted(DEFAULTS)}")
    session.execute(
        pg_insert(PlatformSetting)
        .values(key=key, value=value, updated_by=by)
        .on_conflict_do_update(index_elements=["key"],
                               set_={"value": value, "updated_by": by}))




def queries_per_cycle(session: Session, source: str) -> int:
    """How many queries this source may be asked this cycle.

    Same override ladder as `freshness_hours`, and for the same reason: a stored map names the
    sources an operator has touched, so treating it as a replacement would reset every other source
    to a flat default the moment one was tuned.
    """
    per_source = get(session, "source_queries_per_cycle") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_QUERIES_PER_CYCLE:
        return DEFAULT_SOURCE_QUERIES_PER_CYCLE[source]
    return DEFAULT_QUERIES_PER_CYCLE


def pages_per_cycle(session: Session, source: str) -> int | None:
    """How many pages this source may walk in one cycle, or `None` when it does not paginate.

    Same override ladder as the rest. `None` is a real answer and the adapters treat it as "this
    control does not apply", so a source that fetches one response is not handed a page count it
    would have to invent a meaning for.
    """
    stored = get(session, "source_pages_per_cycle") or {}
    if source in stored:
        return int(stored[source])
    return DEFAULT_SOURCE_PAGES_PER_CYCLE.get(source)


def results_per_query(session: Session, source: str) -> int | None:
    """How deep one query to this source may reach, or `None` when the source has no such control.

    Same override ladder as the rest. `None` is a real answer: Working Nomads returns its whole list
    and TheMuse counts pages, so there is no result cap to set and the panel must not offer one.
    """
    stored = get(session, "source_results_per_query") or {}
    if source in stored:
        return int(stored[source])
    return DEFAULT_SOURCE_RESULTS_PER_QUERY.get(source)


def ats_companies_per_cycle(session: Session) -> int:
    """How many company boards the ATS family polls in one cycle."""
    return int(get(session, "ats_companies_per_cycle") or DEFAULT_ATS_COMPANIES_PER_CYCLE)


def describe_per_cycle(session: Session) -> int:
    """How many postings the shared cycle fetches descriptions for in one pass."""
    return int(get(session, "describe_per_cycle") or DEFAULT_DESCRIBE_PER_CYCLE)


def linkedin_concurrent_users(session: Session) -> int:
    """How many users' LinkedIn passes run in one tick."""
    return int(get(session, "linkedin_concurrent_users") or DEFAULT_LINKEDIN_CONCURRENT_USERS)


def freshness_hours(session: Session, source: str) -> int:
    """How fresh a posting from this source must be. The number passed to the site's own filter.

    **A stored map is a set of overrides, not a replacement**, and the difference is not academic.
    The live setting names three sources; `get` returns a stored value whole, so every source it
    does *not* name used to fall through to the flat `default_freshness_hours` of six hours. Applied
    to the ATS family that is a 6-hour window on boards we reach whenever our token list gets to
    them — measured, it would keep 4% of Recruitee's rows and 2% of Workday's.

    So the ladder is: what the operator set for this source, then what this source is known to need,
    then the flat default. An operator narrowing one board must not silently narrow every other one.
    """
    per_source = get(session, "source_freshness_hours") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_FRESHNESS_HOURS:
        return DEFAULT_SOURCE_FRESHNESS_HOURS[source]
    return int(get(session, "default_freshness_hours"))
