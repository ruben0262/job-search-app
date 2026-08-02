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

# How fresh a posting must be, per source, in hours. The operator tunes these; the defaults are the
# starting point rather than a ceiling.
#
# LinkedIn is tighter than the boards on purpose: it lists within minutes and carries the most
# volume, so a wide window there is mostly re-reading jobs already seen. The aggregators are slower
# to publish, and a window as tight as LinkedIn's would mean their postings never qualified.
DEFAULT_SOURCE_FRESHNESS_HOURS: dict[str, int] = {
    "linkedin": 4,
    "indeed": 6,
    "google": 6,
}

# Anything not named above. Wide enough that a slow board still contributes, narrow enough that the
# feed stays a stream of new postings rather than a backlog.
DEFAULT_FRESHNESS_HOURS = 6

# Every source the harvester collects from. **Here rather than in the harvester** because both the
# harvester and the admin panel need it, and a service may not import another service — the panel
# reading the harvester's list directly is exactly the coupling the boundary contract forbids.
# `services/harvester/source_config.py` imports this as its enabled set.
HARVEST_SOURCES: tuple[str, ...] = (
    # Remote-work aggregators.
    "remotive", "himalayas", "remoteok", "arbeitnow",
    "weworkremotely", "workingnomads", "jobicy",
    # ATS boards, polled directly per company.
    "greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
    "breezy", "workday", "personio", "workable",
    # Aggregators and VC portfolio boards.
    "hiringcafe", "consider", "getro", "themuse",
    # European boards.
    "nofluffjobs", "nofluffjobs_hu", "profession", "dreamjobs", "justjoinit",
    # Key-gated, enabled only with a credential.
    "jooble", "findwork", "adzuna",
    # The per-user tier, on the user's own proxy.
    "linkedin",
)

# **Whether a user may read the profile files written about them.** On by default, because the
# files are theirs and reading them is how somebody corrects a wrong fact the scorer is acting on.
# The operator can close it while the wording of those files is still being settled -- they contain
# an assessment of a person, and one half-written sentence read out of context is worse than a page
# that says nothing yet.
#
# **Enforced on the read, not only in the page.** Hiding the tiles while the endpoint still served
# the contents would be a curtain, not a setting.
PROFILE_FILES_VISIBLE = True

DEFAULTS: dict[str, object] = {
    "profile_files_visible": PROFILE_FILES_VISIBLE,
    "delivery_interval_min": DELIVERY_INTERVAL_MIN,
    "source_freshness_hours": DEFAULT_SOURCE_FRESHNESS_HOURS,
    "default_freshness_hours": DEFAULT_FRESHNESS_HOURS,
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


def freshness_hours(session: Session, source: str) -> int:
    """How fresh a posting from this source must be. The number passed to the site's own filter."""
    per_source = get(session, "source_freshness_hours") or {}
    if source in per_source:
        return int(per_source[source])
    return int(get(session, "default_freshness_hours"))
