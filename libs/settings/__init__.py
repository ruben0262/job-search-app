"""Settings the operator owns, and the defaults they start from."""
from __future__ import annotations

from pymongo.database import Database

from libs.db import now
from libs.db.models import PlatformSetting

# How often the platform's one-shots run, and the only schedule number in the tree.
PLATFORM_TICK_MIN = 15

# How fresh a posting must be, per source, in hours.
DEFAULT_SOURCE_FRESHNESS_HOURS: dict[str, int] = {
    # Per-user, proxied, lists within minutes: a wide window is mostly re-reading jobs already seen.
    "linkedin": 4,
    "indeed": 6,
    "google": 6,
    # Not a statement about Adzuna, which is a live feed — 27 postings inside sixty minutes when
    # probed directly. It is a statement about *us*: the free tier allows ~250 calls a day against
    # ninety-six cycles, so each (country, keyword) pair is asked roughly once every twenty-four
    # hours. A window shorter than the sweep asks for jobs posted since a moment we were not
    # looking, and returns nothing every time. Measured 2026-08-10: 2 of 243 pairs a cycle against
    # a six-hour window produced one row in three cycles.
    "adzuna": 48,
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
    "recruitee": 72, "breezy": 72, "workday": 168, "personio": 72, "workable": 72,
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
    #
    # `jooble` is deliberately absent. The platform holds a valid key and the international host
    # answers 200 from a laptop, but every request from the VPS comes back as an HTML 403 block
    # page — the country editions (`hu.`, `uk.`) refuse from anywhere. It is an IP block, not a
    # credential or a host problem, and a source that fails every cycle drowns the failures that
    # matter (CLAUDE.md §8). The adapter and the key stay; only the enrolment goes, so restoring
    # it is one line if the block lifts or the harvest moves.
    "findwork", "adzuna",
    # The per-user tier, on the user's own proxy.
    "linkedin",
)

# The sources that take a keyword query, as opposed to serving a feed.
QUERY_TAKING_SOURCES: frozenset[str] = frozenset(
    {"hiringcafe", "jooble"})

# How many queries a source may be asked in one 15-minute cycle, before the operator says otherwise.
DEFAULT_SOURCE_QUERIES_PER_CYCLE: dict[str, int] = {
    # Per user, and it was the tightest ceiling on the platform against the widest demand.
    "linkedin": 150,
    # Search-shaped boards on a shared client.
    "hiringcafe": 100,
    "jooble": 50,
    # Metered by the vendor, not by politeness: the free tier is roughly 250 calls a day and the
    # cycle runs ninety-six times, so two is what stays inside it. Everything else in this map is
    # a rate-limit guess; this one is an allowance. Raising it in the panel spends real quota, and
    # the sweep it implies is why `adzuna` carries a 48-hour window rather than a live one.
    "adzuna": 2,
}

# A source nobody has tuned.
DEFAULT_QUERIES_PER_CYCLE = 40


# How many results one query may bring back, per source.
DEFAULT_SOURCE_RESULTS_PER_QUERY: dict[str, int] = {
    "justjoinit": 100,
    "jobicy": 50,
    "hiringcafe": 40,
    # A search reaches back to the last one (`services.harvester.linkedin.search_window`): an hour holds
    # 23–47 jobs a query, measured 2026-11-02, and a query ends at its first short page, so 50 reads the
    # hour whole for about the requests 30 took out of 4 hours.
    "linkedin": 50,
}

# How many company boards the ATS family polls in one cycle.
DEFAULT_ATS_COMPANIES_PER_CYCLE = 400

# How far back the cycle reads posting URLs looking for company boards it does not already poll,
# and how many rows it will read. Both are guards rather than tuning: the pass is idempotent —
# `add_tokens` ignores a token already enrolled — so the lookback only has to cover the gap since
# the last cycle, with enough margin that a few missed ticks lose nothing. The row cap keeps one
# cycle's cost bounded when a backfill lands a very large batch at once.
ATS_DISCOVERY_LOOKBACK_HOURS = 48
ATS_DISCOVERY_MAX_ROWS = 20_000

# How many postings the shared cycle will fetch descriptions for in one pass.
DEFAULT_DESCRIBE_PER_CYCLE = 200

# How deep a paginated source reads in one cycle, per source.
DEFAULT_SOURCE_PAGES_PER_CYCLE: dict[str, int] = {
    # Relevance-sorted, so the top pages carry the matches and depth has falling returns.
    "hiringcafe": 3,
    # Newest-first at 20 a page, and the walk stops once a page is older than the freshness window.
    "workday": 5,
    # Newest-first at 100 a page, about ten offers an hour, and the walk stops the same way.
    "justjoinit": 5,
    # Newest-first at 20 a page, about 2,000 new jobs a day: five pages are its last hour, and a run comes
    # every fifteen minutes (measured 2026-11-03). It was asked once per word, 20 jobs each.
    "himalayas": 5,
    # Lists in no date order, read a slice a run (`http_apis._rotation`), measured 2026-11-03. The Muse:
    # 308 pages of 20, ~38 new a day anywhere among them; 26 a run reads it all every three hours, 104
    # requests an hour of the 500 it allows. No Fluff Jobs: 131 pages, ~620 new a day; 33 a run reads it
    # all every hour.
    "themuse": 26,
    "nofluffjobs": 33,
}

# How long a posting stays in the corpus before it is archived to a file and deleted
# (`libs.corpus.archive`). Fifteen days, set by the owner 2026-11-05 (thirty since 2026-08-11): no job
# older than eight days is anybody's match any more (`filters.candidate_since`), so the rest is the
# disk, the indexes and the backups. A setting since 2026-10-30, in Admin → Settings.
#
# One definition, because the number this replaced had two: `CORPUS_HOT_DAYS = 90` in the corpus
# repository against the 30 the owner had stated twice, and the code wins that argument silently.
DEFAULT_CORPUS_RETENTION_DAYS = 15

# Where the Help page's contact form is emailed, with the person's own address to reply to. The
# FlowSolutions inbox the owner's marketplace app already sends its support mail to.
DEFAULT_SUPPORT_EMAIL = "hello@flow-solutions.xyz"

# The hour of a person's own day (their timezone) their daily email of new jobs goes out, at the
# notifier's next sweep after it.
DEFAULT_EMAIL_DIGEST_HOUR = 8

# The database's size ceiling, in MB of data and indexes: Atlas's free tier stops accepting writes at
# 512 (ROADMAP Phase 9). The health alert pages at 80% of it, so it changes when the tier does.
DEFAULT_DATABASE_BUDGET_MB = 512

# How long one harvest cycle may spend before it must stop and let the next tick have the box.
# Below `harvester.service`'s TimeoutStartSec, which is what kills a cycle that ignores this — and
# a killed cycle skips the stages after the one that overran, silently (`test_schedules.py`).
HARVEST_CYCLE_BUDGET_SECONDS = 11 * 60

# How long one harvest run may spend searching the stored postings for people's new words, after the boards
# and LinkedIn: it stops there and goes on in the next run (`libs.corpus.terms`). Measured 2026-11-04 at
# 1.5 million postings, the whole store searched for one common word took 612 s, past the unit's 14
# minutes; the postings that can still be shown are about a quarter of it (`test_schedules.py`).
TERMS_BUDGET_SECONDS = 90

# The share of that budget above which a cycle is reported as close to its ceiling. Derived from the
# budget rather than being a second time bound, so raising one raises both.
#
# Measured 2026-08-10, when the ATS backfill took the polled board list from 337 to 641 and the
# cycle went from seconds to minutes: the peak was 8.4 minutes (76% of budget), the cycle after it
# 5.6 minutes (51%), and the one before 30.7 seconds (5%). 0.75 reports the one cycle worth knowing
# about and stays quiet for the routine ones — and a threshold that fires on the routine case is a
# line an operator learns to skip past, which is the same as no line (ROADMAP 6az).
HARVEST_CYCLE_WARN_FRACTION = 0.75

# How old a posting may be and still be worth scoring and sending. A different question from the
# freshness windows above, which say how far back to *ask* a source: this one is how long a posting
# already in the corpus stays a candidate, and it is the only age check between the corpus and a
# user's feed.
DEFAULT_JOB_MAX_AGE_HOURS = 24

# The floor a source's delivery window may not go below, because below it that source stops
# existing rather than getting stricter.
#
# Measured 2026-08-09 against the live corpus, counting open postings inside each window. At the
# operator's setting of 6 hours, **fifteen of seventeen sources returned nothing at all** — not
# because their jobs were poor but because a board that publishes a few times a day has nothing
# posted in the last six hours. `arbeitnow` is the extreme: 2,691 open jobs and zero inside 72
# hours, harvested every cycle and discarded in full.
#
# So one global window does not mean "fresher jobs", it means "LinkedIn only". These numbers are
# the smallest window at which each source still answers with a useful count rather than a token
# one or two — remeasured against the live corpus on 2026-08-10, and **the direction is worth
# stating because it is the opposite of the intuition**: a smaller number here is *stricter* and
# yields fewer jobs, not fresher ones. There is nothing to gain by lowering one below what its
# source can serve, and `hiringcafe` is what that costs — it sat at 12 hours, where the corpus
# holds one open posting, while at 168 it holds 526. It is also the richest non-software source
# on the platform, so the tightest window was on the board that mattered most.
#
# A floor below the harvest window (`source_freshness_hours`) discards rows the cycle just paid to
# fetch, so the two are set together in the admin panel.
SOURCE_MIN_AGE_HOURS: dict[str, int] = {
    # Live aggregators: they publish continuously, so an hour is a real window and not an outage.
    "linkedin": 1,          # 152 open postings inside 60 minutes
    # A few times an hour.
    "himalayas": 6,         # 1 at 1h, 42 at 6h
    "justjoinit": 6,        # 4 at 1h, 20 at 6h
    "profession": 12,       # 6 at 6h, 23 at 12h
    # The board APIs, matched to the window they are actually fetched over.
    "nofluffjobs": 48,      # 17 at 6h, 87 at 48h, and 48h is what the cycle asks for
    # Bounded by our own sweep rather than by the board: see the note in the harvest windows above.
    # A floor below the harvest window discards what the cycle just paid to fetch.
    "adzuna": 48,
    # Company boards, reached when the token list gets to them rather than when they publish, so
    # the whole family keeps the window it is polled over. A floor below that is not "fresher": it
    # is the describe and embed spend for rows that are dropped before anybody sees them, and
    # `recruitee` was the worst of it at 168 hours fetched against 24 delivered.
    "greenhouse": 72,       # 7 at 6h, 21 at 72h
    "lever": 72,            # 7 at 6h, 16 at 72h
    "ashby": 72,            # 30 at 6h, 152 at 72h
    "smartrecruiters": 72,  # 118 at 6h, 257 at 72h
    "recruitee": 72,        # 10 at 6h, 16 at 72h
    "personio": 72,
    # Weekly batches and slow boards. A tighter window here is not stricter, it is off.
    "hiringcafe": 168,      # **1 at 12h and 526 at 168h.** It was set to 12.
    "workday": 168,         # 6 at 24h, 133 at 168h
    "breezy": 168,          # 4 at 6h, 21 at 168h
    "jobicy": 168,          # 3 at 12h, 13 at 168h
    "arbeitnow": 168,       # nothing at all inside 72h, 1,075 at 168h
    "workingnomads": 168,   # nothing inside 72h, 14 at 168h
    "workable": 168,
    "themuse": 168,
    "findwork": 168,        # a developer board, and a thin one
}

# A source nobody measured gets the global window and nothing else.
DEFAULT_SOURCE_MIN_AGE_HOURS = 24


def source_min_ages(db: Database | Snapshot) -> dict[str, int]:
    """The delivery floor per source, with the operator's overrides applied over the measured map.

    The twin of `freshness_hours`, and it needed to become one. That setting says how far back a
    source is *asked*; this says how long what it returned stays a candidate. Both are per-source
    hour maps on adjacent halves of one pipeline, but only the first had a control — so an operator
    could widen a source's harvest window in the panel, watch the corpus fill with rows, and never
    learn that a floor they could not see was discarding them. Now they are set in the same place.
    """
    stored = get(db, "source_min_age_hours") or {}
    merged = dict(SOURCE_MIN_AGE_HOURS)
    merged.update({str(name).lower(): int(hours) for name, hours in stored.items()})
    return merged


# The model each provider writes a CV or cover letter with, until the operator says otherwise.
#
# **Haiku, deliberately, and not changed here**: the tailoring prompts were measured on it, and a
# model change is a measured change (CLAUDE.md §3a, §4). The Gemini and OpenAI entries run the same
# prompts unmeasured — they exist because the owner chose own-key providers, and they are settings
# so a better or cheaper model is a panel change rather than a deploy. Both were each provider's
# documented recommendation on 2026-10-28: "For any new projects, use our latest models: 3.5
# Flash-Lite or 3.8 Flash" (ai.google.dev), and GPT-6 Luna "for cost-sensitive, high-volume
# workloads" (developers.openai.com), whose `gpt-5-mini` is deprecated.
CV_MODEL = "claude-haiku-4-5"
DEFAULT_DOCUMENT_MODELS: dict[str, str] = {
    "anthropic": CV_MODEL,
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-6-luna",
}

# How long a built document waits to be downloaded before the docgen worker deletes it (D9:
# generated documents are ephemeral). An hour covers "build, then download" with room to spare; a
# person who comes back later builds it again.
DEFAULT_DOCUMENT_KEEP_HOURS = 1

# Whether the help chat answers (`services/api/support_routes.py`): 1 is on. Its prompt,
# `libs/prompts/support.md`, was approved by the owner on 2026-10-30 (CLAUDE.md §3a). It answers with
# `document_models`.
DEFAULT_SUPPORT_CHAT_ENABLED = 1


DEFAULTS: dict[str, object] = {
    "job_max_age_hours": DEFAULT_JOB_MAX_AGE_HOURS,
    "source_freshness_hours": DEFAULT_SOURCE_FRESHNESS_HOURS,
    # Its twin, and deliberately adjacent: one says how far back a source is asked, the
    # other how long what it returned stays deliverable. Kept in one place so an operator
    # cannot widen the first while an invisible second discards the result.
    "source_min_age_hours": SOURCE_MIN_AGE_HOURS,
    "default_freshness_hours": DEFAULT_FRESHNESS_HOURS,
    "source_queries_per_cycle": DEFAULT_SOURCE_QUERIES_PER_CYCLE,
    "source_results_per_query": DEFAULT_SOURCE_RESULTS_PER_QUERY,
    "source_pages_per_cycle": DEFAULT_SOURCE_PAGES_PER_CYCLE,
    "ats_companies_per_cycle": DEFAULT_ATS_COMPANIES_PER_CYCLE,
    "describe_per_cycle": DEFAULT_DESCRIBE_PER_CYCLE,
    "document_models": DEFAULT_DOCUMENT_MODELS,
    "document_keep_hours": DEFAULT_DOCUMENT_KEEP_HOURS,
    "support_chat_enabled": DEFAULT_SUPPORT_CHAT_ENABLED,
    "corpus_retention_days": DEFAULT_CORPUS_RETENTION_DAYS,
    "database_budget_mb": DEFAULT_DATABASE_BUDGET_MB,
    "support_email": DEFAULT_SUPPORT_EMAIL,
    "email_digest_hour": DEFAULT_EMAIL_DIGEST_HOUR,
}


class Snapshot(dict):
    """Every stored setting, read in one query, for a caller about to ask many. Pass it wherever a
    getter here takes `db`: the answers are the same, and the database is asked once, not once a
    question. `/admin/sources` asked 85 times, 2.7 s against Atlas (2026-11-03). Made per request, so
    it is never older than the request that made it."""


def snapshot(db: Database) -> Snapshot:
    rows = db[PlatformSetting.COLLECTION].find({}, {"value": 1})
    return Snapshot({row["_id"]: row["value"] for row in rows})


def get(db: Database | Snapshot, key: str) -> object:
    """One setting, or its default."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown platform setting {key!r}; known: {sorted(DEFAULTS)}")
    if isinstance(db, Snapshot):
        value = db.get(key)
    else:
        stored = db[PlatformSetting.COLLECTION].find_one({"_id": key}, {"value": 1})
        value = None if stored is None else stored["value"]
    return DEFAULTS[key] if value is None else value


def all_settings(db: Database) -> dict[str, object]:
    """Every setting with its current value — what the admin panel renders."""
    stored = snapshot(db)
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def put(db: Database, key: str, value: object, *, by: str | None = None) -> None:
    """Set one setting."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown platform setting {key!r}; known: {sorted(DEFAULTS)}")
    db[PlatformSetting.COLLECTION].update_one(
        {"_id": key}, {"$set": {"value": value, "updated_by": by, "updated_at": now()}}, upsert=True)




def queries_per_cycle_default(source: str) -> int:
    """The catalogue ceiling for a caller with no database — an adapter reading its own fallback.

    Split out so `services.harvester.sources` can share this map instead of restating a number
    beside it. A constant on each side plus a test asserting they are equal passes while both are
    wrong; one definition cannot disagree with itself (CLAUDE.md §5).
    """
    return DEFAULT_SOURCE_QUERIES_PER_CYCLE.get(source, DEFAULT_QUERIES_PER_CYCLE)


def queries_per_cycle(db: Database | Snapshot, source: str) -> int:
    """How many queries this source may be asked this cycle."""
    per_source = get(db, "source_queries_per_cycle") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_QUERIES_PER_CYCLE:
        return DEFAULT_SOURCE_QUERIES_PER_CYCLE[source]
    return DEFAULT_QUERIES_PER_CYCLE


def pages_per_cycle(db: Database | Snapshot, source: str) -> int | None:
    """How many pages this source may walk in one cycle, or `None` when it does not paginate."""
    stored = get(db, "source_pages_per_cycle") or {}
    if source in stored:
        return int(stored[source])
    return DEFAULT_SOURCE_PAGES_PER_CYCLE.get(source)


def results_per_query(db: Database | Snapshot, source: str) -> int | None:
    """How deep one query to this source may reach, or `None` when the source has no such control."""
    stored = get(db, "source_results_per_query") or {}
    if source in stored:
        return int(stored[source])
    return DEFAULT_SOURCE_RESULTS_PER_QUERY.get(source)


def ats_companies_per_cycle(db: Database | Snapshot) -> int:
    """How many company boards the ATS family polls in one cycle."""
    return int(get(db, "ats_companies_per_cycle") or DEFAULT_ATS_COMPANIES_PER_CYCLE)


def describe_per_cycle(db: Database) -> int:
    """How many postings the shared cycle fetches descriptions for in one pass."""
    return int(get(db, "describe_per_cycle") or DEFAULT_DESCRIBE_PER_CYCLE)


def document_model(db: Database, provider: str) -> str:
    """The model this provider writes documents with: the operator's choice, else the default."""
    chosen = (get(db, "document_models") or {}).get(provider)
    return str(chosen) if chosen else DEFAULT_DOCUMENT_MODELS[provider]


def document_keep_hours(db: Database) -> int:
    """How many hours a built document is kept for download."""
    return int(get(db, "document_keep_hours"))


def corpus_retention_days(db: Database) -> int:
    """How many days a posting stays in the corpus before it is archived."""
    return int(get(db, "corpus_retention_days"))


def database_budget_bytes(db: Database) -> int:
    """The size the database must stay under, data and indexes."""
    return int(get(db, "database_budget_mb")) * 1024**2


def freshness_hours(db: Database | Snapshot, source: str) -> int:
    """How fresh a posting from this source must be."""
    per_source = get(db, "source_freshness_hours") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_FRESHNESS_HOURS:
        return DEFAULT_SOURCE_FRESHNESS_HOURS[source]
    return int(get(db, "default_freshness_hours"))
