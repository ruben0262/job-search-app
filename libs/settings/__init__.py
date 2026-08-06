"""Settings the operator owns, and the defaults they start from."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import PlatformSetting

# How often every user's feed is sent.
DELIVERY_INTERVAL_MIN = 15

# How often the platform's one-shots run, and the only schedule number in the tree.
PLATFORM_TICK_MIN = 15

# How fresh a posting must be, per source, in hours.
DEFAULT_SOURCE_FRESHNESS_HOURS: dict[str, int] = {
    # Per-user, proxied, lists within minutes: a wide window is mostly re-reading jobs already seen.
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

# Anything not named above.
DEFAULT_FRESHNESS_HOURS = 6

# Every source the harvester collects from.
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
    # The per-user tier, on the user's own proxy.
    "linkedin",
)

# The sources that take a keyword query, as opposed to serving a feed.
QUERY_TAKING_SOURCES: frozenset[str] = frozenset(
    {"himalayas", "hiringcafe", "nofluffjobs", "jooble"})

# How many queries a source may be asked in one 15-minute cycle, before the operator says otherwise.
DEFAULT_SOURCE_QUERIES_PER_CYCLE: dict[str, int] = {
    # Per user, and it was the tightest ceiling on the platform against the widest demand.
    "linkedin": 150,
    # Search-shaped boards on a shared client.
    "himalayas": 100,
    "hiringcafe": 100,
    "nofluffjobs": 100,
    "jooble": 50,
}

# A source nobody has tuned.
DEFAULT_QUERIES_PER_CYCLE = 40


# How many results one query may bring back, per source.
DEFAULT_SOURCE_RESULTS_PER_QUERY: dict[str, int] = {
    "himalayas": 100,
    "justjoinit": 100,
    "nofluffjobs": 100,
    "jobicy": 50,
    "hiringcafe": 40,
    "linkedin": 30,
}

# How many company boards the ATS family polls in one cycle.
DEFAULT_ATS_COMPANIES_PER_CYCLE = 400

# How many postings the shared cycle will fetch descriptions for in one pass.
DEFAULT_DESCRIBE_PER_CYCLE = 200

# How deep a paginated source reads in one cycle, per source.
DEFAULT_SOURCE_PAGES_PER_CYCLE: dict[str, int] = {
    # Relevance-sorted, so the top pages carry the matches and depth has falling returns.
    "hiringcafe": 3,
    # Newest-first at 20 a page, and the walk stops once a page is older than the freshness window.
    "workday": 5,
}

# How many users' LinkedIn passes run in one tick.
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
    """One setting, or its default."""
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
    """How many queries this source may be asked this cycle."""
    per_source = get(session, "source_queries_per_cycle") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_QUERIES_PER_CYCLE:
        return DEFAULT_SOURCE_QUERIES_PER_CYCLE[source]
    return DEFAULT_QUERIES_PER_CYCLE


def pages_per_cycle(session: Session, source: str) -> int | None:
    """How many pages this source may walk in one cycle, or `None` when it does not paginate."""
    stored = get(session, "source_pages_per_cycle") or {}
    if source in stored:
        return int(stored[source])
    return DEFAULT_SOURCE_PAGES_PER_CYCLE.get(source)


def results_per_query(session: Session, source: str) -> int | None:
    """How deep one query to this source may reach, or `None` when the source has no such control."""
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
    """How fresh a posting from this source must be."""
    per_source = get(session, "source_freshness_hours") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_FRESHNESS_HOURS:
        return DEFAULT_SOURCE_FRESHNESS_HOURS[source]
    return int(get(session, "default_freshness_hours"))
