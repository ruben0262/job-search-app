"""Settings the operator owns, and the defaults they start from."""
from __future__ import annotations

from pymongo.database import Database

from libs.db import now
from libs.db.models import PlatformSetting

# How often the platform's one-shots run, and the only schedule number in the tree.
PLATFORM_TICK_MIN = 15

# How fresh a posting must be, per source, in hours.
DEFAULT_SOURCE_FRESHNESS_HOURS: dict[str, int] = {
    # The furthest a sweep of a place reaches back: a place nobody has swept yet, or one not swept since
    # (`services.harvester.linkedin.sweep_window`).
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
    # Newest first by when a posting went up or was renewed: everything renewed in the last hour is on its
    # first four pages, and a run walks until a page has nothing inside the window (measured 2026-11-04).
    "nofluffjobs": 2,
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
    # Aggregators.
    "themuse",
    # European boards.
    "nofluffjobs", "profession", "justjoinit",
    # Key-gated, enabled only with a credential.
    "findwork", "adzuna",
    # The per-user tier, on the user's own proxy.
    "linkedin",
)

# How many calls a source may make in one 15-minute cycle, for the one source that is metered by the call.
# No source is asked for a word (owner, 2026-11-04), and the two that could only be asked for one, Hiring
# Cafe and Jooble, were removed on 2026-11-06 with the query set that asked them.
DEFAULT_SOURCE_QUERIES_PER_CYCLE: dict[str, int] = {
    # The free tier is roughly 250 calls a day and the cycle runs ninety-six times, so two is what stays
    # inside it: two countries a run, each at its newest.
    "adzuna": 2,
}


# How many results one query may bring back, per source.
DEFAULT_SOURCE_RESULTS_PER_QUERY: dict[str, int] = {
    "justjoinit": 100,
    "jobicy": 50,
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

# How many postings the shared cycle will fetch descriptions for in one pass: the boards', not LinkedIn's,
# whose text is fetched for the rest of its pass, `proxy_exits` pages at a time.
DEFAULT_DESCRIBE_PER_CYCLE = 200

# How many addresses the site proxy (`HARVEST_PROXY`) sends from, each new connection from the next in
# turn. LinkedIn is read that many pages at once (`services.harvester.linkedin`): its limit is a rate per
# address, ~40 pages a minute (2026-11-04: 240 asked a minute of four addresses got ~180 through, 360
# almost none), and four readers on four addresses reached it. Measured through the proxy, 2026-11-02: one
# reader took 1.1–1.4 s a page, four 0.24–0.47 s, eight on four addresses gave up on 3 of 24. The owner
# went to six addresses on 2026-11-05, and the count stayed four in code, reading at most two thirds of
# what six allow.
DEFAULT_PROXY_EXITS = 6

# How deep a paginated source reads in one cycle, per source.
DEFAULT_SOURCE_PAGES_PER_CYCLE: dict[str, int] = {
    # Newest-first at 20 a page, and the walk stops once a page is older than the freshness window.
    "workday": 5,
    # Newest-first at 100 a page, about ten offers an hour, and the walk stops the same way.
    "justjoinit": 5,
    # Newest-first at 20 a page, about 2,000 new jobs a day: five pages are its last hour, and a run comes
    # every fifteen minutes (measured 2026-11-03). It was asked once per word, 20 jobs each.
    "himalayas": 5,
    # In no date order, ~38 new a day anywhere among them (measured 2026-11-03), and no page served past
    # the 100th (2026-11-04): 100 a run read all it serves, 400 requests an hour of the 500 it allows.
    "themuse": 100,
    # Newest first, and the walk stops at a page with nothing new: four pages hold the last hour
    # (measured 2026-11-04). The most a run walks, if the harvest was down a while.
    "nofluffjobs": 10,
    # Newest first, unmeasured (no key here); the walk stops once a page is older than the window.
    "findwork": 5,
}

# How long a posting stays in the corpus before it is archived to a file and deleted
# (`libs.corpus.archive`). Thirty days, the job shelf life (owner, 2026-11-12: "show jobs as old as 30 days
# and retire them afterwards from corpus too"); fifteen from 2026-11-05, thirty from 2026-08-11. A setting
# since 2026-10-30, in Admin → Settings.
#
# One definition, because the number this replaced had two: `CORPUS_HOT_DAYS = 90` in the corpus
# repository against the 30 the owner had stated twice, and the code wins that argument silently.
DEFAULT_CORPUS_RETENTION_DAYS = 30

# Where the Help page's contact form is emailed, with the person's own address to reply to. The
# FlowSolutions inbox the owner's marketplace app already sends its support mail to.
DEFAULT_SUPPORT_EMAIL = "hello@flow-solutions.xyz"

# The hour of a person's own day (their timezone) their daily email of new jobs goes out, at the
# notifier's next sweep after it.
DEFAULT_EMAIL_DIGEST_HOUR = 8


# How long one harvest cycle may spend before it must stop and let the next tick have the box.
# Below `harvester.service`'s TimeoutStartSec, which is what kills a cycle that ignores this — and
# a killed cycle skips the stages after the one that overran, silently (`test_schedules.py`).
HARVEST_CYCLE_BUDGET_SECONDS = 11 * 60

# How long one harvest run may spend searching the stored postings for people's new words, after the boards
# and LinkedIn: it stops there and goes on in the next run (`libs.corpus.terms`). Measured 2026-11-04 at
# 1.5 million postings, the whole store searched for one common word took 612 s, past the unit's 14
# minutes; the postings that can still be shown are about a quarter of it (`test_schedules.py`).
TERMS_BUDGET_SECONDS = 90

# How long one harvest run may spend looking for the company boards of the companies its postings name
# (`services.harvester.cycle._boards_by_name`), last in the run and inside its budget: the rest waits for the
# next run. Measured 2026-11-06, a company took about 1.3 s of requests on seven ATS at eight at once, and
# the laptop's postings named about 5,000 new companies a day, some 50 a run.
BOARD_LOOKUP_BUDGET_SECONDS = 60

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
# user's feed. Thirty days (owner, 2026-11-12: "instead of 15 days its 30 days now"), so a free account,
# a week behind (`free_delay_hours`), still has three weeks of them; a week from 2026-11-07, a day before.
# The Jobs page opens on the last 24 hours, and "Posted" reaches back the rest.
DEFAULT_JOB_MAX_AGE_HOURS = 720

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
# holds one open posting, while at 168 it holds 526 (it was removed on 2026-11-06, unreadable).
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
    # The board APIs.
    "nofluffjobs": 48,      # 17 at 6h, 87 at 48h
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
# **Haiku, deliberately**: the fast tier, for a CV done in under 20 s. Haiku 5.5 since 2026-11-10 (owner),
# chosen before the new CV and cover-letter prompts are written and tuned on it, Claude's first; a model
# change is a measured change (CLAUDE.md §3a, §4). It thinks adaptively only, and answers a token budget
# with a 400 (`libs.llm.client._thinking`). The Gemini and OpenAI entries run the same
# prompts unmeasured — they exist because the owner chose own-key providers, and they are settings
# so a better or cheaper model is a panel change rather than a deploy. Both were each provider's
# documented recommendation on 2026-10-28: "For any new projects, use our latest models: 3.5
# Flash-Lite or 3.8 Flash" (ai.google.dev), and GPT-6 Luna "for cost-sensitive, high-volume
# workloads" (developers.openai.com), whose `gpt-5-mini` is deprecated.
CV_MODEL = "claude-haiku-5-5"
DEFAULT_DOCUMENT_MODELS: dict[str, str] = {
    "anthropic": CV_MODEL,
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-6-luna",
    # A ChatGPT sign-in, through Codex (`libs.llm.cli`): empty is Codex's own choice, which knows the
    # models the person's plan includes; an API model name may not be one of them.
    "codex": "",
}

# How long a built document waits to be downloaded before the docgen worker deletes it (D9:
# generated documents are ephemeral). An hour covers "build, then download" with room to spare; a
# person who comes back later builds it again.
DEFAULT_DOCUMENT_KEEP_HOURS = 1

# Whether the help chat answers (`services/api/support_routes.py`): 1 is on. Its prompt,
# `libs/prompts/support.md`, was approved by the owner on 2026-10-30 (CLAUDE.md §3a). It answers with
# `document_models`.
DEFAULT_SUPPORT_CHAT_ENABLED = 1

# How many days free a membership starts with (owner, 2026-11-11: "a 7 day free trial"), for somebody who
# never had one; 0 is none. The price is Stripe's, picked with `STRIPE_PRICE_ID` (`billing_routes`).
DEFAULT_TRIAL_DAYS = 7

# How far behind a member a free account sees the jobs, in hours (owner, 2026-11-12: "only jobs that have
# passed the 7 day mark should appear for them"): a job reaches its list that long after a member's, and
# leaves it with everybody's (`libs.membership.jobs_as_of`). 0 shows free accounts the jobs as they come; the
# documents and the alerts stay members'.
DEFAULT_FREE_DELAY_HOURS = 168

# What the landing page's two pricing cards say, Free and Member (owner, 2026-11-12), the operator's to change
# in Admin (`services.api.pricing_routes`). The membership's price is Stripe's, its free days `trial_days` and
# how far behind a free account is `free_delay_hours`, each filled in where the cards are drawn, so no wording
# here should name them. `left_out` is what the card lists as not included.
DEFAULT_PRICING_CARDS: dict[str, dict[str, object]] = {
    "free": {
        "name": "Free",
        "tagline": "Every job that fits you, to look around as long as you like.",
        "benefits": [
            "Every job that fits you, from every source",
            "Every filter: your words, countries, languages and way of working",
            "Every application followed, to the interview and the offer",
        ],
        "left_out": [
            "New jobs the moment they appear",
            "Alerts on your phone",
            "CVs and cover letters made for each job",
        ],
        "button": "Start free",
        "note": "No card needed. Upgrade whenever you like.",
    },
    "member": {
        "name": "Member",
        "tagline": "Every job the moment it appears, and a CV for each.",
        "benefits": [
            "New jobs the moment they're found, not a week later",
            "Alerts on Telegram, in your browser or by email",
            "A CV and a cover letter for any job, in one tap, with your own AI account",
            "Every CV read the way an applicant tracking system reads it",
            "Everything in Free",
        ],
        "left_out": [],
        "button": "Start your free trial",
        "note": "A card is needed to start the trial. Nothing is taken until it ends, and if you cancel "
                "before then you pay nothing.",
    },
}


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
    "proxy_exits": DEFAULT_PROXY_EXITS,
    "document_models": DEFAULT_DOCUMENT_MODELS,
    "document_keep_hours": DEFAULT_DOCUMENT_KEEP_HOURS,
    "support_chat_enabled": DEFAULT_SUPPORT_CHAT_ENABLED,
    "corpus_retention_days": DEFAULT_CORPUS_RETENTION_DAYS,
    "support_email": DEFAULT_SUPPORT_EMAIL,
    "email_digest_hour": DEFAULT_EMAIL_DIGEST_HOUR,
    "trial_days": DEFAULT_TRIAL_DAYS,
    "free_delay_hours": DEFAULT_FREE_DELAY_HOURS,
    "pricing_cards": DEFAULT_PRICING_CARDS,
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




def queries_per_cycle(db: Database | Snapshot, source: str) -> int | None:
    """How many calls this source may make this cycle, or `None` for a source not metered by the call."""
    per_source = get(db, "source_queries_per_cycle") or {}
    if source in per_source and source in DEFAULT_SOURCE_QUERIES_PER_CYCLE:
        return int(per_source[source])
    return DEFAULT_SOURCE_QUERIES_PER_CYCLE.get(source)


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


def proxy_exits(db: Database) -> int:
    """How many addresses the site proxy sends from: how many LinkedIn pages are read at once."""
    return max(1, int(get(db, "proxy_exits")))


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


def freshness_hours(db: Database | Snapshot, source: str) -> int:
    """How fresh a posting from this source must be."""
    per_source = get(db, "source_freshness_hours") or {}
    if source in per_source:
        return int(per_source[source])
    if source in DEFAULT_SOURCE_FRESHNESS_HOURS:
        return DEFAULT_SOURCE_FRESHNESS_HOURS[source]
    return int(get(db, "default_freshness_hours"))
