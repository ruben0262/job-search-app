"""Which Telegram chat a job goes to.

**Why more than one chat.** A single feed of everything is a feed people stop reading. The reference
instance splits by the distinction its owner actually makes when scanning — jobs at home, jobs on
LinkedIn, everything else — and the split is what makes each stream skimmable. Ported from
`watcher/feed.py` with its ordering intact, because the ordering is the part that is easy to get
subtly wrong.

**The order matters and is not arbitrary.** Hungarian jobs win first, *including a LinkedIn job in
Budapest* — somebody who wants to see local roles wants to see them wherever they were posted, and
routing that one to the LinkedIn channel would hide it in the stream they skim least. LinkedIn wins
second because it is the highest-volume source and the one worth isolating. Everything else lands
third.

**A missing route is a fallback, never a drop.** Every unrouted job goes to the personal chat. A
routing table is a convenience; losing a job because a channel was not configured would be the
feature actively taking something away.
"""
from __future__ import annotations

from dataclasses import dataclass

# Sources that only carry Hungarian listings, so their location string does not have to say so.
# Carried verbatim from `pipeline/lib/salary.py`.
HU_SOURCES = frozenset({"profession", "dreamjobs", "nofluffjobs_hu", "jooble"})

# Substring match on a lowercased location, accented and unaccented. A posting writes its location
# however it likes, and "Győr" and "Gyor" are the same place to everybody except a string compare.
HU_LOCATIONS = ("hungary", "budapest", "magyarorsz", "debrecen", "szeged", "győr", "gyor",
                "pécs", "pecs", "miskolc")

# The keys a user's `telegram_routes` may carry. Anything else is ignored rather than rejected, so
# an old routing table survives a rename here instead of breaking delivery.
ROUTES = ("hu", "linkedin", "other")


@dataclass(frozen=True)
class Routable:
    """The little a routing decision needs. Deliberately not the whole corpus row."""

    source: str | None
    location: str | None


def is_hungarian(job: Routable) -> bool:
    """A HU-only source, or a location that names a Hungarian place."""
    if (job.source or "").lower() in HU_SOURCES:
        return True
    location = (job.location or "").lower()
    return any(place in location for place in HU_LOCATIONS)


def route_key(job: Routable) -> str:
    """Which route this job belongs to, before any user's configuration is considered."""
    if is_hungarian(job):
        return "hu"
    if (job.source or "").lower() == "linkedin":
        return "linkedin"
    return "other"


def chat_for(job: Routable, routes: dict | None, fallback: str) -> str:
    """The chat id for one job. `fallback` is the user's personal chat and is always valid.

    Resolved per job rather than per digest because one delivery legitimately spans channels, and
    deciding once for a whole batch would put a Budapest job in the LinkedIn stream whenever it
    happened to be batched with one.
    """
    chat = (routes or {}).get(route_key(job))
    return str(chat) if chat else fallback


def group_by_chat(jobs: list, routes: dict | None, fallback: str) -> dict[str, list]:
    """Split a digest's items into one list per chat, preserving order within each.

    Order is preserved because the caller sorted by score, and a digest whose items are shuffled by
    a grouping step reads as arbitrary — the top item in each channel should still be the best one.
    """
    grouped: dict[str, list] = {}
    for job in jobs:
        chat = chat_for(Routable(getattr(job, "source", None), getattr(job, "location", None)),
                        routes, fallback)
        grouped.setdefault(chat, []).append(job)
    return grouped
