"""Which Telegram chat a job goes to."""
from __future__ import annotations

from dataclasses import dataclass

from libs.corpus.salary import HU_SOURCES

# Substring match on a lowercased location, accented and unaccented.
HU_LOCATIONS = ("hungary", "budapest", "magyarorsz", "debrecen", "szeged", "győr", "gyor",
                "pécs", "pecs", "miskolc")

# The keys a user's `telegram_routes` may carry (`libs.db.models.user.TELEGRAM_ROUTE_KEYS`).


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
    """The chat id for one job."""
    chat = (routes or {}).get(route_key(job))
    return str(chat) if chat else fallback


def group_by_chat(jobs: list, routes: dict | None, fallback: str) -> dict[str, list]:
    """Split a digest's items into one list per chat, preserving order within each."""
    grouped: dict[str, list] = {}
    for job in jobs:
        chat = chat_for(Routable(getattr(job, "source", None), getattr(job, "location", None)),
                        routes, fallback)
        grouped.setdefault(chat, []).append(job)
    return grouped
